"""OpenAI-compatible chat client + budget middleware + spend ledger."""
from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path
from dataclasses import dataclass, field


from .cache import cache_key, cache_get, cache_put
from ..io import sha256_text


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    info: dict = field(default_factory=dict)

def estimate_tokens(messages: list) -> int:
    chars = sum(len(m.get("content", "")) for m in messages)
    return max(1, chars // 4)


def model_prices(cfg, role: str) -> tuple[float, float]:
    m = cfg.pipeline["models"][role]
    return float(m.get("price_input_per_1m", 0)), float(m.get("price_output_per_1m", 0))


class BudgetExceeded(Exception):
    pass


class LLMBudget:
    def __init__(self, cfg, root: Path, series_id: str, chunk_id: str | None = None,
                 state_path: Path | None = None):
        self.cfg = cfg
        self.root = root
        self.series_id = series_id
        self.chunk_id = chunk_id
        self.state_path = state_path
        # §17.6: locked budget wins; pipeline values ignored (mismatch logged).
        try:
            from ..quality import eff_budget
            _b = eff_budget(cfg, "writer")
            self.ep_token_cap = int(_b["ep_token_cap"])
            self.ep_cost_cap = float(_b["ep_cost_cap"])
            self.timeout = int(_b["timeout"])
            self.daily_cap = float(_b["daily_cap"])
        except Exception:
            lim = cfg.pipeline["circuit_breakers"]["episode_limits"]
            self.ep_token_cap = int(lim["max_total_tokens"])
            self.ep_cost_cap = float(lim["hard_cost_cap_usd"])
            self.timeout = int(lim.get("timeout_seconds", 90))
            self.daily_cap = float(cfg.pipeline["circuit_breakers"]["global_daily_safety"]["max_daily_api_spend_usd"])
        self.ep_tokens = 0
        self.ep_cost = 0.0
        self.active_seconds = 0.0
        self._last_event_ts = time.time()
        if state_path is not None and state_path.exists():
            try:
                prev = json.loads(state_path.read_text(encoding="utf-8"))
                self.ep_tokens = int(prev.get("ep_tokens", 0))
                self.ep_cost = float(prev.get("ep_cost", 0.0))
                self.active_seconds = float(prev.get("active_seconds", 0.0))
            except Exception:
                pass
        self.save()

    def save(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(
                {"ep_tokens": self.ep_tokens, "ep_cost": self.ep_cost,
                 "active_seconds": self.active_seconds}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _note_activity(self) -> None:
        """Fold wall time since the last budget event into active_seconds.

        Every precheck/record accounts the time elapsed since the previous
        event, so the total is the sum of measured call durations plus local
        processing while stages run. The clock resets on load, so calendar
        pauses between resumes never count (R22).
        """
        now = time.time()
        self.active_seconds += max(0.0, now - self._last_event_ts)
        self._last_event_ts = now

    def daily_spend(self) -> float:
        from datetime import date
        today = date.today().isoformat()
        ledger = self.root / "state" / "spend_ledger.jsonl"
        total = 0.0
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                    if e.get("date") == today:
                        total += float(e.get("cost_usd", 0))
                except Exception:
                    continue
        return total

    def _backend_of(self, role: str) -> str:
        try:
            models = self.cfg.pipeline.get("models", {})
            return str((models.get(role, {}) or {}).get("backend", "http"))
        except Exception:
            return "http"

    def _episode_token_cap(self, role: str) -> int:
        """Effective episode token cap from the quality lock (§17.4)."""
        try:
            from ..quality import eff_budget
            return int(eff_budget(self.cfg, role)["ep_token_cap"])
        except Exception:
            pass
        if self._backend_of(role) == "opencode":
            try:
                return int(self.cfg.pipeline.get("opencode", {}).get("max_total_tokens", 150000))
            except (TypeError, ValueError):
                return 150000
        cb = self.cfg.pipeline.get("circuit_breakers", {}).get("episode_limits", {})
        try:
            return int(cb.get("max_total_tokens", 35000))
        except (TypeError, ValueError):
            return 35000

    def precheck(self, role: str, messages: list, max_tokens: int):
        # R22: the cap compares accumulated active seconds, never timestamps.
        self._note_activity()
        if self.active_seconds > self.timeout:
            raise BudgetExceeded(
                f"episode active-time cap: {self.active_seconds:.1f}s > {self.timeout}s")
        est_in = estimate_tokens(messages)
        if self._backend_of(role) == "opencode":
            cap = self._episode_token_cap(role)
            est_cost = float(self.cfg.pipeline.get("opencode", {}).get("estimated_cost_usd_per_call", 0.002))
            if self.ep_tokens + est_in + max_tokens > cap:
                raise BudgetExceeded(f"episode token cap: {self.ep_tokens} + est {est_in + max_tokens} > {cap}")
            if self.ep_cost + est_cost > self.ep_cost_cap:
                raise BudgetExceeded(f"episode cost cap: {self.ep_cost:.4f} + est {est_cost:.4f} > {self.ep_cost_cap}")
            if self.daily_spend() + est_cost > self.daily_cap:
                raise BudgetExceeded(f"daily cap ${self.daily_cap:.2f} would be exceeded")
            return
        pi, po = model_prices(self.cfg, role)
        est_cost = est_in / 1e6 * pi + max_tokens / 1e6 * po
        if self.ep_tokens + est_in + max_tokens > self.ep_token_cap:
            raise BudgetExceeded(f"episode token cap: {self.ep_tokens} + est {est_in + max_tokens} > {self.ep_token_cap}")
        if self.ep_cost + est_cost > self.ep_cost_cap:
            raise BudgetExceeded(f"episode cost cap: {self.ep_cost:.4f} + est {est_cost:.4f} > {self.ep_cost_cap}")
        if self.daily_spend() + est_cost > self.daily_cap:
            raise BudgetExceeded(f"daily cap ${self.daily_cap:.2f} would be exceeded")

    def record(self, role: str, model: str, usage: Usage, prompt_hash: str, latency_s: float,
               chunk_id: str | None = None, stage: str | None = None,
               extras: dict | None = None):
        # R22: the wall span from the pre-call precheck to here is the measured
        # call duration (plus any local tail processing); count it as active.
        self._note_activity()
        self.ep_tokens += usage.input_tokens + usage.output_tokens
        self.ep_cost += usage.cost_usd
        self.save()
        from datetime import date, datetime
        run_dir = self.root / "run" / self.series_id
        run_dir.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now().isoformat(), "role": role, "model": model,
                 "prompt_sha256": prompt_hash, "input_tokens": usage.input_tokens,
                 "output_tokens": usage.output_tokens, "cost_usd": round(usage.cost_usd, 6),
                 "latency_s": round(latency_s, 2), "cached": usage.cached,
                 "chunk_id": chunk_id or self.chunk_id or "", "stage": stage or ""}
        if extras:
            entry.update(extras)
        with open(run_dir / "llm_calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        if not usage.cached and usage.cost_usd > 0:
            with open(self.root / "state" / "spend_ledger.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"date": date.today().isoformat(), "series_id": self.series_id,
                                    "role": role, "model": model, "cost_usd": round(usage.cost_usd, 6)}) + "\n")


def chunk_budget_path(root: Path, sid: str, chunk_id: str) -> Path:
    return root / "run" / sid / "work" / chunk_id / "budget.json"


def load_chunk_budget(cfg, root: Path, sid: str, chunk_id: str) -> LLMBudget:
    return LLMBudget(cfg, root, sid, chunk_id=chunk_id,
                     state_path=chunk_budget_path(root, sid, chunk_id))


class LLMClient:
    def __init__(self, cfg, root: Path, series_id: str = "global", budget: LLMBudget | None = None):
        self.cfg = cfg
        self.root = root
        self.series_id = series_id
        self.budget = budget or LLMBudget(cfg, root, series_id)
        self.cache_dir = root / "state" / "llm_cache"

    def _call_api(self, endpoint: str, api_key: str, model: str, messages: list,
                   temperature: float, max_tokens: int, timeout: int,
                   role: str = "writer") -> tuple[str, int, int]:
        import httpx
        last_err = None
        for attempt in range(3):
            if attempt > 0:
                # re-check budget before each HTTP retry (F2)
                self.budget.precheck(role, messages, max_tokens)
            try:
                r = httpx.post(f"{endpoint.rstrip('/')}/chat/completions",
                               headers={"Authorization": f"Bearer {api_key}"},
                               json={"model": model, "messages": messages,
                                     "temperature": temperature, "max_tokens": max_tokens},
                               timeout=timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                choice = data["choices"][0]["message"]["content"]
                use = data.get("usage", {})
                return choice, int(use.get("prompt_tokens", estimate_tokens(messages))), int(use.get("completion_tokens", 0))
            except BudgetExceeded:
                raise
            except Exception as e:
                last_err = str(e)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"LLM call failed after retries: {last_err}")

    def chat(self, role: str, messages: list, system_prompt_name: str | None = None,
             chunk_id: str | None = None, stage: str | None = None) -> tuple[str, Usage]:
        mcfg = self.cfg.pipeline["models"][role]
        if str(mcfg.get("backend", "http")) == "opencode":
            from .opencode_backend import run_opencode_call
            return run_opencode_call(self.cfg, self.root, self.series_id, role,
                                     messages, self.budget,
                                     chunk_id=chunk_id, stage=stage)
        model = mcfg["model_id"]
        endpoint = mcfg["endpoint"]
        api_key = os.environ.get(mcfg.get("api_key_env", ""), "")
        if not api_key:
            raise RuntimeError(f"Missing API key env {mcfg.get('api_key_env')} for role {role}")
        temperature = float(mcfg.get("temperature", 0.7))
        max_tokens = int(mcfg.get("max_tokens", 1500))
        params = {"temperature": temperature, "max_tokens": max_tokens}
        key = cache_key(model, messages, params)
        hit = cache_get(self.cache_dir, key)
        if hit is not None:
            # R15: every cache hit is logged with cached:true.
            usage = Usage(hit.get("input_tokens", 0), hit.get("output_tokens", 0), 0.0, True)
            self.budget.record(role, model, usage,
                               sha256_text(json.dumps(messages, ensure_ascii=False)),
                               0.0, chunk_id=chunk_id, stage=stage,
                               extras={"backend": "http", "cache_hit": True})
            return hit["content"], usage
        self.budget.precheck(role, messages, max_tokens)
        prompt_hash = sha256_text(json.dumps(messages, ensure_ascii=False))
        t0 = time.time()
        content, inp, outp = self._call_api(endpoint, api_key, model, messages, temperature, max_tokens, self.budget.timeout, role)
        latency = time.time() - t0
        pi, po = model_prices(self.cfg, role)
        cost = inp / 1e6 * pi + outp / 1e6 * po
        usage = Usage(inp, outp, cost, False)
        cache_put(self.cache_dir, key, {"content": content, "input_tokens": inp, "output_tokens": outp, "model": model})
        # raw response archive
        raw_dir = self.root / "run" / self.series_id / "raw_llm"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{key}.txt").write_text(content, encoding="utf-8")
        self.budget.record(role, model, usage, prompt_hash, latency,
                           chunk_id=chunk_id, stage=stage)
        return content, usage

    def _check_canary(self, content: str, usage: Usage | None) -> dict | None:
        """Return the parsed dict (minus prompt_canary) iff it carries the
        expected END-OF-PROMPT token, else None. Callers without a canary
        (HTTP backend, cached hits, test doubles) skip this entirely."""
        expected = (usage.info or {}).get("prompt_canary") if usage is not None else None
        if not expected:
            return None
        from .json_repair import extract_json
        try:
            data = extract_json(content)
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("prompt_canary") != expected:
            return None
        data.pop("prompt_canary", None)
        return data

    def chat_json(self, role: str, messages: list,
                  chunk_id: str | None = None, stage: str | None = None,
                  require_canary: bool = False) -> tuple[dict, Usage]:
        from .json_repair import extract_json
        content, usage = self.chat(role, messages, chunk_id=chunk_id, stage=stage)
        if usage.info.get("tool_used"):
            # opencode backend: tool use is invalid → one repair retry, then refusal
            fix = messages + [{"role": "user", "content": "Your last reply used tools, which is forbidden. Reply with JSON ONLY, no prose and no tool calls."}]
            content, usage = self.chat(role, fix, chunk_id=chunk_id, stage=stage)
            if usage.info.get("tool_used"):
                raise SystemExit(f"REFUSAL stage=llm check=tool_use role={role}")
        checked = self._check_canary(content, usage) if require_canary else None
        if require_canary and checked is None:
            fix = messages + [{"role": "user", "content": "Your last reply was missing the correct prompt_canary. Reply with JSON ONLY, including the prompt_canary value from the END-OF-PROMPT line."}]
            content, usage = self.chat(role, fix, chunk_id=chunk_id, stage=stage)
            if usage.info.get("tool_used"):
                raise SystemExit(f"REFUSAL stage=llm check=tool_use role={role}")
            checked = self._check_canary(content, usage)
            if checked is None:
                raise SystemExit(f"REFUSAL stage=llm check=canary role={role}")
            return checked, usage
        if require_canary and checked is not None:
            return checked, usage
        try:
            return extract_json(content), usage
        except Exception:
            # one repair retry: ask model to fix
            # R16: attribute the retry to the chunk budget/log like every retry.
            fix = messages + [{"role": "user", "content": "Your last reply was not valid JSON. Reply with JSON ONLY, no prose."}]
            content2, usage2 = self.chat(role, fix, chunk_id=chunk_id, stage=stage)
            return extract_json(content2), usage2
