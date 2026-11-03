"""OpenCode CLI backend for both LLM roles (SPEC §16).

Command shape (auditor-verified):
    opencode run --model <id> --dir <sandbox> --pure --format json
        -f <prompt-file> -- "<short instruction>"
`--` is required before the message; `--auto` is never passed.

Notes:
- The prompt file holds the messages flattened with ### SYSTEM / ### USER /
  ### ASSISTANT headers; it is the only file in the per-call sandbox, which is
  deleted after the call.
- Subprocess stdout is captured via pipes (never shell redirection, which can
  re-encode bytes on Windows) and stored verbatim under run/<sid>/raw_llm/.
- Never reads opencode's auth/credential files; the CLI uses its own login.
- Observed stream events: `step_start`, `text` (part.text), `step_finish`
  (part.tokens {input, output, reasoning, cache {read, write}}, part.cost in
  USD, top-level sessionID). Any `tool_use` event invalidates the reply.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

SHORT_INSTRUCTION = "Follow the prompt file. Reply with JSON only, no prose and no tool calls."

TOOL_EVENT_TYPES = {"tool_use", "tool_call", "tooluse"}

# R10: deny all tool use before the model starts. Written into every sandbox
# as opencode.json and passed via OPENCODE_CONFIG_CONTENT on the subprocess.
OPENCODE_DENY_CONFIG = {"$schema": "https://opencode.ai/config.json",
                        "permission": "deny"}

# R11/R14/R17: prompt renderer version. Bump when render_prompt_parts changes shape.
RENDERER_VERSION = "3"
MAX_PROMPT_LINE = 1000
MAX_PROMPT_LINES = 2000

# R17: measured opencode -f attachment limit ≈ 50 KB (51,200 bytes; the model
# sees file lines 1–788 of a larger attachment). Prompt parts stay at most
# 60% of that limit in UTF-8 bytes.
PROMPT_PART_MAX_BYTES = 30720
# Per-part byte reserve for the part header and the part-count line, so the
# finished file (content + header lines) still fits the byte cap.
_PART_HEADER_RESERVE = 512
MAX_PROMPT_PARTS = 8

# R14: fixed instruction, added once at the end of the system section, telling
# the model to echo the END-OF-PROMPT token as prompt_canary.
CANARY_INSTRUCTION = ('Your JSON reply MUST include the top-level field '
                      '"prompt_canary" whose value is the token on the '
                      'END-OF-PROMPT line.')


class InvalidStream(ValueError):
    """The event stream has no usable text, no step_finish, or undecodable bytes."""


def resolve_binary(configured: str | None) -> str:
    """Resolve the opencode binary. On Windows, npm/powershell shims mangle the
    `--` separator, so prefer a real `opencode.exe` sitting next to the shim."""
    name = configured or "opencode"
    if os.path.isabs(name) and Path(name).exists():
        return name
    found = shutil.which(name)
    if not found:
        return name
    if os.name == "nt" and found.lower().endswith((".ps1", ".cmd", ".bat")):
        here = Path(found).parent
        for cand in (here / "opencode.exe",
                     here / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"):
            if cand.exists():
                return str(cand)
    return found


def flatten_messages(messages: list) -> str:
    """Flatten chat messages into the prompt-file format (legacy single-file
    helper; new code paths use render_prompt_parts)."""
    blocks = []
    for m in messages or []:
        role = str(m.get("role", "user")).upper()
        blocks.append(f"### {role}\n{str(m.get('content', ''))}")
    return "\n\n".join(blocks) + "\n"


def _pretty_block(text: str) -> str:
    """Pretty-print embedded JSON (indent=1) so long single lines cannot
    survive into the prompt file."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return text
    try:
        return json.dumps(obj, indent=1, ensure_ascii=False)
    except (TypeError, ValueError):
        return text


def _render_message_lines(message: dict) -> list:
    role = str(message.get("role", "user")).upper()
    content = message.get("content", "")
    if not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            content = str(content)
    lines = ["### " + role]
    # wrap after pretty-printing
    wrapped: list[str] = []
    for raw in _pretty_block(content).split("\n"):
        chunk = raw
        if chunk == "":
            wrapped.append("")
            continue
        while len(chunk) > MAX_PROMPT_LINE:
            wrapped.append(chunk[:MAX_PROMPT_LINE])
            chunk = chunk[MAX_PROMPT_LINE:]
        wrapped.append(chunk)
    lines.extend(wrapped)
    return lines


def _utf8_size(line: str) -> int:
    """UTF-8 bytes of one file line including its newline."""
    return len(line.encode("utf-8")) + 1


def render_prompt_parts(messages: list, max_bytes: int = PROMPT_PART_MAX_BYTES,
                        max_lines: int = MAX_PROMPT_LINES) -> list:
    """Render messages to one or more prompt-file texts.

    R11: no line exceeds 1000 chars (JSON pretty-printed + hard-wrapped).
    R14: the fixed canary instruction is appended once, at the end of the
    first system section (a minimal system section is added when the
    messages have none).
    R17: parts hold at most max_bytes UTF-8 bytes (60% of the measured
    -f attachment limit) and max_lines lines, splitting only at line
    boundaries. Every part starts with `=== PROMPT PART i OF N ===` and
    the system section states N. The END-OF-PROMPT canary is appended
    later, only to the last part file, by run_opencode_call.
    """
    blocks = [_render_message_lines(m) for m in messages or []]
    for b in blocks:
        if b and b[0] == "### SYSTEM":
            b.append(CANARY_INSTRUCTION)
            break
    else:
        blocks.insert(0, ["### SYSTEM", CANARY_INSTRUCTION])
    flat = [ln for b in blocks for ln in b]
    try:
        canary_pos = flat.index(CANARY_INSTRUCTION)
    except ValueError:
        canary_pos = 0
    budget = max_bytes - _PART_HEADER_RESERVE
    line_cap = max_lines - 2  # room for the part header + part-count lines
    parts: list[list[str]] = []
    part_of_flat: list[int] = []
    cur: list[str] = []
    cur_bytes = 0
    for i, ln in enumerate(flat):
        size = _utf8_size(ln)
        if cur and (cur_bytes + size > budget or len(cur) + 1 > line_cap):
            parts.append(cur)
            cur, cur_bytes = [], 0
        part_of_flat.append(len(parts))
        cur.append(ln)
        cur_bytes += size
    if cur:
        parts.append(cur)
    if not parts:
        parts = [[]]
        part_of_flat = [0]
    n = len(parts)
    count_line = (f"This prompt is delivered in {n} part(s); each file starts "
                  f"with === PROMPT PART i OF {n} ===. Read all parts before replying.")
    # insert the part-count line right after the canary instruction, then
    # head every part file with its part header
    target = part_of_flat[canary_pos] if canary_pos < len(part_of_flat) else 0
    insert_at = parts[target].index(CANARY_INSTRUCTION) + 1
    parts[target].insert(insert_at, count_line)
    return ["\n".join([f"=== PROMPT PART {i + 1} OF {n} ==="] + p) + "\n"
            for i, p in enumerate(parts)]


def write_sandbox_config(sandbox: Path) -> Path:
    """R10: deny all tool use before the model starts. The file lives next to
    the prompt file(s) and is deleted with the sandbox after the call."""
    path = sandbox / "opencode.json"
    path.write_text(json.dumps(OPENCODE_DENY_CONFIG, indent=2), encoding="utf-8")
    return path


def sandbox_env() -> dict:
    """R10: pass the same deny config via the environment (belt and braces
    with the sandbox opencode.json file)."""
    env = dict(os.environ)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(OPENCODE_DENY_CONFIG)
    return env


def build_opencode_cmd(binary: str, model_id: str, sandbox: Path,
                       prompt_file: Path | list,
                       instruction: str = SHORT_INSTRUCTION) -> list:
    """SPEC §16.2 command (one -f per prompt file; usually exactly one).
    Never includes `--auto`."""
    if isinstance(prompt_file, (str, Path)):
        files = [str(prompt_file)]
    else:
        files = [str(p) for p in prompt_file]
    cmd = [binary, "run", "--model", model_id, "--dir", str(sandbox),
           "--pure", "--format", "json"]
    for f in files:
        cmd += ["-f", f]
    return cmd + ["--", instruction]


def parse_stream(raw: bytes) -> dict:
    """Parse `--format json` stdout.

    Returns {text, tokens{...}, cost, session_id, tool_used, tool_completed,
    tool_statuses}. Raises InvalidStream when the bytes do not decode,
    when no usable text arrived, or when no step_finish event arrived.
    """
    try:
        text_all = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise InvalidStream(f"stdout is not utf-8: {e}")
    events = []
    for line in text_all.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            raise InvalidStream("stdout contains a non-JSON line")
    texts: list[str] = []
    tool_used = False
    tool_completed = False
    tool_statuses: list[str] = []
    total_in = total_out = total_reason = total_r = total_w = 0
    cost = 0.0
    session_id: str | None = None
    saw_finish = False
    for ev in events:
        if not isinstance(ev, dict):
            raise InvalidStream("stream event is not an object")
        sid = ev.get("sessionID") or ev.get("session_id")
        if isinstance(sid, str) and sid and session_id is None:
            session_id = sid
        etype = str(ev.get("type", ""))
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        ptype = str(part.get("type", ""))
        if etype in TOOL_EVENT_TYPES or ptype in TOOL_EVENT_TYPES:
            tool_used = True
            status = part.get("status", ev.get("status", ""))
            if isinstance(status, str) and status:
                tool_statuses.append(status)
                if status.lower() == "completed":
                    tool_completed = True
        if etype == "text":
            t = part.get("text", ev.get("text", ""))
            if not isinstance(t, str) or not t:
                raise InvalidStream("text event has no text")
            texts.append(t)
        if etype == "step_finish":
            saw_finish = True
            tok = part.get("tokens", {}) if isinstance(part.get("tokens"), dict) else {}
            cache = tok.get("cache", {}) if isinstance(tok.get("cache"), dict) else {}
            try:
                total_in += int(tok.get("input", 0))
                total_out += int(tok.get("output", 0))
                total_reason += int(tok.get("reasoning", 0))
                total_r += int(cache.get("read", 0))
                total_w += int(cache.get("write", 0))
                cost += float(part.get("cost", 0.0))
            except (TypeError, ValueError) as e:
                raise InvalidStream(f"bad tokens/cost payload: {e}")
    joined = "".join(texts)
    if not joined or not saw_finish:
        raise InvalidStream("stream has no text or no step_finish")
    return {"text": joined,
            "tokens": {"input": total_in, "output": total_out, "reasoning": total_reason,
                       "cache_read": total_r, "cache_write": total_w},
            "cost": cost, "session_id": session_id or "", "tool_used": tool_used,
            "tool_completed": tool_completed, "tool_statuses": tool_statuses}


def contains_tool_use(raw: bytes) -> bool:
    """Predicate over a captured stream: True if any tool_use event is present."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        if (str(ev.get("type", "")) in TOOL_EVENT_TYPES
                or str(part.get("type", "")) in TOOL_EVENT_TYPES):
            return True
    return False


def _model_prices(cfg, role: str) -> tuple:
    """(price_in, price_out, price_cached) per 1M tokens for fallback costing.
    The cache price defaults to the input price when unconfigured."""
    mcfg = (cfg.pipeline.get("models", {}) or {}).get(role, {}) or {}
    try:
        pi = float(mcfg.get("price_input_per_1m", 0.0))
    except (TypeError, ValueError):
        pi = 0.0
    try:
        po = float(mcfg.get("price_output_per_1m", 0.0))
    except (TypeError, ValueError):
        po = 0.0
    try:
        pc = float(mcfg.get("price_cached_per_1m", pi))
    except (TypeError, ValueError):
        pc = pi
    return pi, po, pc


def run_opencode_call(cfg, root: Path, series_id: str, role: str, messages: list,
                      budget, chunk_id: str | None = None,
                      stage: str | None = None):
    """Run one budgeted opencode call. Returns (content, Usage with .info).

    R10: every call runs with tools denied — opencode.json in the sandbox
    plus OPENCODE_CONFIG_CONTENT on the subprocess environment.
    R11: the prompt is rendered with real newlines (JSON pretty-printed,
    lines hard-wrapped to 1000 chars); the last line carries an
    END-OF-PROMPT canary whose value is returned in info["prompt_canary"]
    for the caller to require.
    R17: the rendered prompt is split into <= MAX_PROMPT_PARTS files of
    at most PROMPT_PART_MAX_BYTES UTF-8 bytes each (headers included),
    passed as repeated -f flags; the canary goes only in the last part.
    R12: the stream cost is used when reported; otherwise priced from the
    model rates with cache.read at the cache price. cache_read/cache_write
    are logged separately, not folded silently into input tokens.
    R15: the cache key binds renderer_version, the rendered prompt template
    sha and the backend config sha; every cache hit is recorded with
    cached:true, and the stored canary (when present) is handed to the
    caller for validation.
    Retries (each re-checked against budget): non-zero exit, timeout, empty
    text, missing step_finish. Exhaustion raises RuntimeError (exit 1).
    A reply containing tool_use is returned with info["tool_used"]=True; the
    caller (chat_json) runs the repair retry and refuses on a second strike.
    """
    from .cache import cache_key, cache_get, cache_put
    from .client import Usage, estimate_tokens
    from ..io import sha256_text
    mcfg = cfg.pipeline["models"][role]
    model_id = mcfg["model_id"]
    binary = resolve_binary(mcfg.get("binary", "opencode"))
    timeout = int(mcfg.get("timeout_seconds", 240))
    max_tokens = int(mcfg.get("max_tokens", 1500))
    prompt_parts = render_prompt_parts(messages)
    # R17(d): hard limit — never send more than MAX_PROMPT_PARTS files.
    if len(prompt_parts) > MAX_PROMPT_PARTS:
        raise SystemExit(
            f"REFUSAL stage=llm check=prompt_too_large role={role} "
            f"parts={len(prompt_parts)} max_parts={MAX_PROMPT_PARTS} "
            f"chunk_id={chunk_id or ''} (split the chunk instead)")
    prompt_hash = sha256_text(json.dumps(messages, ensure_ascii=False))
    # R15: the cache key binds the renderer, the rendered prompt template and
    # the backend config, so stale entries (e.g. replies generated from a
    # truncated prompt under an older renderer) can never hit.
    op_cfg = cfg.pipeline.get("opencode", {}) or {}
    params = {"backend": "opencode",
              "renderer_version": RENDERER_VERSION,
              "prompt_template_sha": sha256_text("".join(prompt_parts)),
              "backend_config_sha": sha256_text(
                  json.dumps({"model": mcfg, "opencode": op_cfg},
                             sort_keys=True, ensure_ascii=False, default=str))}
    key = cache_key(model_id, messages, params)
    cache_dir = root / "state" / "llm_cache"
    hit = cache_get(cache_dir, key)
    if hit is not None:
        # R15: every cache hit is logged with cached:true. A stored canary is
        # handed to the caller so canary-required calls validate the cached
        # reply; entries without one fail that check and are never used there.
        usage = Usage(hit.get("input_tokens", 0),
                      hit.get("output_tokens", 0), 0.0, True)
        usage.info = {"tool_used": False, "tool_completed": False,
                      "tool_statuses": [], "session_id": "",
                      "cache_read": 0, "cache_write": 0}
        if hit.get("prompt_canary"):
            usage.info["prompt_canary"] = hit["prompt_canary"]
        budget.record(role, model_id, usage, prompt_hash, 0.0,
                      chunk_id=chunk_id, stage=stage,
                      extras={"backend": "opencode", "cache_hit": True})
        return hit["content"], usage
    pi, po, pc = _model_prices(cfg, role)
    last_err = "no attempt"
    for attempt in range(3):
        # every attempt (including the first) is pre-checked against budget
        budget.precheck(role, messages, max_tokens)
        sandbox = root / "state" / "llm_sandbox" / uuid.uuid4().hex
        sandbox.mkdir(parents=True, exist_ok=True)
        canary = uuid.uuid4().hex
        try:
            prompt_files = []
            for i, part in enumerate(prompt_parts):
                name = "prompt.md" if i == 0 else f"prompt-{i + 1:02d}.md"
                if i == len(prompt_parts) - 1:
                    part = part + f"END-OF-PROMPT {canary}\n"
                pf = sandbox / name
                pf.write_text(part, encoding="utf-8")
                prompt_files.append(pf)
            write_sandbox_config(sandbox)
            cmd = build_opencode_cmd(binary, model_id, sandbox, prompt_files)
            t0 = time.time()
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                      cwd=str(root), env=sandbox_env())
            except FileNotFoundError:
                last_err = f"binary not found: {binary}"
                break
            except subprocess.TimeoutExpired:
                last_err = f"timeout after {timeout}s"
                time.sleep(2 ** attempt)
                continue
            latency = time.time() - t0
            if proc.returncode != 0:
                last_err = (proc.stderr.decode("utf-8", errors="replace").strip()
                            or f"exit code {proc.returncode}")
                time.sleep(2 ** attempt)
                continue
            try:
                parsed = parse_stream(proc.stdout)
            except InvalidStream as e:
                last_err = str(e)
                time.sleep(2 ** attempt)
                continue
            tok = parsed["tokens"]
            stream_cost = float(parsed["cost"])
            if stream_cost > 0:
                cost = stream_cost
            else:
                cost = ((tok["input"] + tok["cache_write"]) * pi
                        + (tok["output"] + tok["reasoning"]) * po
                        + tok["cache_read"] * pc) / 1e6
            usage = Usage(tok["input"] + tok["cache_write"],
                          tok["output"] + tok["reasoning"],
                          cost, False)
            usage.info = {"tool_used": parsed["tool_used"],
                          "tool_completed": parsed.get("tool_completed", False),
                          "tool_statuses": parsed.get("tool_statuses", []),
                          "session_id": parsed["session_id"] or sandbox.name,
                          "cache_read": tok["cache_read"],
                          "cache_write": tok["cache_write"],
                          "prompt_canary": canary}
            cache_put(cache_dir, key, {"content": parsed["text"],
                                       "input_tokens": usage.input_tokens,
                                       "output_tokens": usage.output_tokens,
                                       "model": model_id,
                                       "prompt_canary": canary})
            raw_dir = root / "run" / series_id / "raw_llm"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"opencode_{usage.info['session_id']}.txt").write_bytes(proc.stdout)
            budget.record(role, model_id, usage, prompt_hash, latency,
                          chunk_id=chunk_id, stage=stage,
                          extras={"backend": "opencode",
                                  "session_id": usage.info["session_id"],
                                  "tokens": tok,
                                  "cache_read_tokens": tok["cache_read"],
                                  "cache_write_tokens": tok["cache_write"]})
            return parsed["text"], usage
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)
    raise RuntimeError(f"opencode call failed after retries: {last_err}")
