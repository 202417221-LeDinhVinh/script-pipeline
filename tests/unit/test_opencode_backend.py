"""Unit (offline): opencode backend — parser, cmd shape, retries, tool-use."""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tests.conftest import REPO
from vsp.llm import opencode_backend as ob

FIX = REPO / "tests" / "fixtures" / "opencode" / "writer_echo_001.jsonl"


def _raw() -> bytes:
    return FIX.read_bytes()


def test_parse_real_stream():
    raw = _raw()
    assert len(raw) > 0
    p = ob.parse_stream(raw)
    assert p["text"] == '{"ping": "pong", "n": 1}'
    assert p["tool_used"] is False
    assert isinstance(p["session_id"], str) and p["session_id"]
    assert p["tokens"]["output"] >= 1 and p["tokens"]["input"] > 1000
    assert p["cost"] > 0
    assert ob.contains_tool_use(raw) is False


def test_tool_use_predicate_marks_synthetic_event():
    raw = _raw()
    assert ob.contains_tool_use(raw) is False
    synth = json.dumps({"type": "tool_use",
                        "part": {"type": "tool_use", "name": "bash"}}).encode()
    assert ob.contains_tool_use(synth) is True
    assert ob.contains_tool_use(b"not json at all") is False


def test_cmd_shape_has_separator_and_no_auto():
    cmd = ob.build_opencode_cmd("/bin/opencode", "m", Path("/s"), Path("/s/p.md"))
    assert "--auto" not in cmd
    assert cmd[:2] == ["/bin/opencode", "run"]
    assert "--model" in cmd and "--pure" in cmd and "--format" in cmd
    assert "-f" in cmd
    assert cmd[-2] == "--" and isinstance(cmd[-1], str) and cmd[-1]


def test_rejects_bad_streams():
    import pytest
    for bad in (b"\xff\xfe{}", b"not json\n",
                b'{"type": "step_start"}\n',
                b'{"type": "text", "part": {"type": "text", "text": ""}}\n'
                b'{"type": "step_finish", "part": {"tokens": {}, "cost": 0}}\n'):
        with pytest.raises(ob.InvalidStream):
            ob.parse_stream(bad)


def test_flatten_and_resolve():
    out = ob.flatten_messages([{"role": "system", "content": "S"}, {"role": "x"}])
    assert out.startswith("### SYSTEM\nS") and "### X\n" in out
    assert ob.resolve_binary("/abs/oc") == "/abs/oc"


def _opencode_cfg(cfg):
    cfg.pipeline["models"]["writer"] = {
        "backend": "opencode", "binary": "opencode",
        "model_id": "opencode-go/muse-spark-1.3-contributor",
        "timeout_seconds": 240, "max_tokens": 500}
    cfg.pipeline.setdefault("opencode", {})
    return cfg


def test_run_offline_success_and_cache(vsp_root, monkeypatch):
    import subprocess
    from vsp.config import load_config
    from vsp.llm.client import LLMBudget
    cfg = _opencode_cfg(load_config(vsp_root))
    raw = _raw()
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        assert "--auto" not in cmd
        assert cmd[cmd.index("--") + 1]
        return subprocess.CompletedProcess(cmd, 0, raw, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    budget = LLMBudget(cfg, vsp_root, "s")
    text, usage = ob.run_opencode_call(cfg, vsp_root, "s", "writer",
                                       [{"role": "user", "content": "hi"}],
                                       budget, chunk_id="c1", stage="t")
    assert text == '{"ping": "pong", "n": 1}'
    assert usage.input_tokens == 8363 and usage.output_tokens == 21 + 156
    assert usage.cost_usd > 0 and usage.info["session_id"].startswith("ses_")
    assert usage.info["tool_used"] is False
    assert calls["n"] == 1
    assert not list((vsp_root / "state" / "llm_sandbox").iterdir())
    lines = (vsp_root / "run" / "s" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[0])
    assert e["backend"] == "opencode" and e["chunk_id"] == "c1" and e["stage"] == "t"
    text2, usage2 = ob.run_opencode_call(cfg, vsp_root, "s", "writer",
                                         [{"role": "user", "content": "hi"}],
                                         budget, chunk_id="c1", stage="t")
    assert text2 == text and usage2.cached is True and calls["n"] == 1
    # R15: the cache hit is logged too, with cached:true and the same attribution.
    lines = (vsp_root / "run" / "s" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    e2 = json.loads(lines[1])
    assert e2.get("cached") is True and e2["chunk_id"] == "c1" and e2["stage"] == "t"


def test_run_retries_then_raises(vsp_root, monkeypatch):
    import subprocess
    from vsp.config import load_config
    from vsp.llm.client import LLMBudget
    cfg = _opencode_cfg(load_config(vsp_root))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b"", b"nope"))
    import vsp.llm.opencode_backend as O
    monkeypatch.setattr(O.time, "sleep", lambda s: None)
    import pytest
    budget = LLMBudget(cfg, vsp_root, "s")
    with pytest.raises(RuntimeError, match="after retries"):
        ob.run_opencode_call(cfg, vsp_root, "s", "writer",
                             [{"role": "user", "content": "hi"}], budget)
    assert not (vsp_root / "run" / "s" / "llm_calls.jsonl").exists()


def test_chat_json_tool_use_refusal(vsp_root, monkeypatch):
    from vsp.config import load_config
    from vsp.llm.client import LLMClient, Usage
    cfg = _opencode_cfg(load_config(vsp_root))
    client = LLMClient(cfg, vsp_root, "s")
    seen = {"n": 0}

    def always_tool(role, messages, **kw):
        seen["n"] += 1
        u = Usage(10, 5, 0.001, False)
        u.info = {"tool_used": True}
        return "{}", u

    monkeypatch.setattr(client, "chat", always_tool)
    import pytest
    with pytest.raises(SystemExit, match="tool_use"):
        client.chat_json("writer", [{"role": "user", "content": "x"}])
    assert seen["n"] == 2


def test_precheck_opencode_cap(vsp_root):
    from vsp.config import load_config
    from vsp.llm.client import LLMBudget, BudgetExceeded
    import pytest
    cfg = _opencode_cfg(load_config(vsp_root))
    # §17.4/Q13: the locked opencode cap (400k) wins over the legacy 150k default.
    b = LLMBudget(cfg, vsp_root, "s")
    b.precheck("writer", [{"content": "x"}], 100000)
    with pytest.raises(BudgetExceeded):
        b.precheck("writer", [{"content": "x"}], 500000)
    cfg.pipeline["models"]["writer"]["backend"] = "http"
    cfg.pipeline["models"]["writer"].update(price_input_per_1m=0.3,
                                            price_output_per_1m=0.6)
    # HTTP backend keeps the pipeline 35k cap, but the live budget object was
    # created under the 400k locked opencode cap; 500k exceeds either cap.
    with pytest.raises(BudgetExceeded):
        b.precheck("writer", [{"content": "x"}], 500000)


def test_repo_defaults_use_opencode_backend():
    import yaml
    cfg = yaml.safe_load((REPO / "config" / "pipeline.yaml").read_text(encoding="utf-8"))
    for role, model, pi, po in (("writer", "opencode-go/muse-spark-1.3-contributor", 0.10, 0.20),
                               ("judge", "opencode-go/deepseek-v4.1-flash", 0.15, 0.60)):
        m = cfg["models"][role]
        assert m.get("backend") == "opencode", role
        assert m.get("model_id") == model, role
        assert float(m.get("price_input_per_1m")) == pi, role
        assert float(m.get("price_output_per_1m")) == po, role
        assert int(m.get("timeout_seconds", 0)) > 0, role
    assert int(cfg["opencode"]["max_total_tokens"]) == 150000


def test_render_prompt_wraps_long_lines_and_pretty_json():
    from vsp.llm import opencode_backend as ob
    long_line = "x" * 2500
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": long_line}]
    parts = ob.render_prompt_parts(msgs)
    assert len(parts) == 1
    lines = parts[0].splitlines()
    assert all(len(l) <= ob.MAX_PROMPT_LINE for l in lines)
    assert lines[0] == "=== PROMPT PART 1 OF 1 ==="
    assert lines[1] == "### SYSTEM" and any(x == "### USER" for x in lines)
    blob = {"k": [1, 2, 3]}
    parts2 = ob.render_prompt_parts([{"role": "user", "content": '{"a": 1, "b": [1, 2]}'}])
    assert any(l == " \"a\": 1," for l in parts2[0].splitlines())
    big = [{"role": "user", "content": "y\n" * 900}, {"role": "user", "content": "z\n" * 900}, {"role": "user", "content": "w\n" * 900}]
    many = ob.render_prompt_parts(big)
    assert len(many) >= 2 and all(len(x.splitlines()) <= ob.MAX_PROMPT_LINES for x in many)
    # R17: over-long messages split at line boundaries instead of raising.
    split = ob.render_prompt_parts([{"role": "user", "content": "q\n" * 3000}])
    assert len(split) >= 2 and all(len(x.splitlines()) <= ob.MAX_PROMPT_LINES for x in split)


def test_chat_json_canary_required_then_refused(vsp_root, monkeypatch):
    from vsp.config import load_config
    from vsp.llm.client import LLMClient, Usage
    import json as _json
    cfg = _opencode_cfg(load_config(vsp_root))
    client = LLMClient(cfg, vsp_root, "s9")
    seen = {"n": 0}

    def fake_chat(role, messages, **kw):
        seen["n"] += 1
        u = Usage(10, 5, 0.001, False)
        u.info = {"tool_used": False, "prompt_canary": "CANARY-%d" % seen["n"]}
        return _json.dumps({"ok": True, "prompt_canary": "CANARY-%d" % seen["n"]}), u

    monkeypatch.setattr(client, "chat", fake_chat)
    out, usage = client.chat_json("writer", [{"role": "user", "content": "x"}], require_canary=True)
    assert out == {"ok": True} and seen["n"] == 1

    def fake_chat_bad(role, messages, **kw):
        seen["n"] += 1
        u = Usage(10, 5, 0.001, False)
        u.info = {"tool_used": False, "prompt_canary": "REAL-%d" % seen["n"]}
        return _json.dumps({"ok": True}), u

    monkeypatch.setattr(client, "chat", fake_chat_bad)
    seen["n"] = 100
    import pytest
    with pytest.raises(SystemExit, match="check=canary"):
        client.chat_json("writer", [{"role": "user", "content": "x"}], require_canary=True)
    assert seen["n"] == 102


def test_coverage_gate_flags_truncated_prompt():
    from vsp.extract.beats import validate_beats_coverage
    pids = list(range(1, 440))
    narrow = {"beats": [{"beat_id": i + 1, "source_paragraph_ids": list(range(1, 11))} for i in range(5)]}
    errs = validate_beats_coverage(narrow, pids)
    assert any("coverage" in e for e in errs), errs
    wide = {"beats": [{"beat_id": i + 1, "source_paragraph_ids": [1, 100, 200, 300, 439]} for i in range(5)]}
    assert validate_beats_coverage(wide, pids) == []
    assert validate_beats_coverage({"beats": []}, pids) != []


def test_fingerprint_changes_with_prompt_or_model(tmp_path):
    import yaml
    from vsp.orchestrator import llm_inputs_extra
    cfg = yaml.safe_load((REPO / "config" / "pipeline.yaml").read_text(encoding="utf-8"))
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "beat_extractor.md").write_text("v1", encoding="utf-8")
    a = llm_inputs_extra(cfg, tmp_path, "writer", "beat_extractor")
    assert a["model_id"] and a["backend"] == "opencode" and a["renderer_version"]
    (tmp_path / "prompts" / "beat_extractor.md").write_text("v1 changed", encoding="utf-8")
    b = llm_inputs_extra(cfg, tmp_path, "writer", "beat_extractor")
    assert b["prompt_template_sha256"] != a["prompt_template_sha256"]
    cfg["models"]["writer"]["model_id"] = "other-model"
    c = llm_inputs_extra(cfg, tmp_path, "writer", "beat_extractor")
    assert c["model_id"] != a["model_id"]


def test_r13_extract_reruns_after_prompt_or_model_change(vsp_root, tmp_path):
    import json as _json
    from vsp.orchestrator import do_extract
    from tests.e2e.test_pipeline import _setup_series
    from vsp.llm.fake import FakeLLMClient
    from vsp.io import read_json
    sd = _setup_series(vsp_root)
    sid = sd.name
    beats = {"beats": [{"beat_id": i + 1, "source_paragraph_ids": [(i * 9) // 5],
                        "characters": [], "narration_text": "x",
                        "tts_emotion_prompt": "e", "screen_text_overlay": "",
                        "b_roll_vibe": "", "sfx_cue": "", "bgm_directive": ""} for i in range(6)],
             "new_entities": [], "glossary_additions": {}, "genre_tags": []}
    fake = FakeLLMClient({("writer", 1): dict(beats), ("writer", 2): dict(beats),
                          "beat_extractor": dict(beats)})
    r1 = do_extract(vsp_root, sid, 1, fake=fake)
    assert r1
    n_calls = len(fake.calls)
    assert n_calls > 0
    r2 = do_extract(vsp_root, sid, 1, fake=fake)
    assert len(fake.calls) == n_calls
    import os as _os
    (vsp_root / "prompts" / "beat_extractor.md").write_text(
        (vsp_root / "prompts" / "beat_extractor.md").read_text(encoding="utf-8") + _os.linesep + "# tweak" + _os.linesep)
    fake2 = FakeLLMClient({("writer", 1): dict(beats), ("writer", 2): dict(beats),
                           "beat_extractor": dict(beats)})
    r3 = do_extract(vsp_root, sid, 1, fake=fake2)
    assert len(fake2.calls) > 0
    mpath = vsp_root / "run" / sid / "work" / "chunk_001" / "beats.json"
    man = read_json(mpath)
    assert man["inputs_sha256"]["prompt_template_sha256"]
    assert man["inputs_sha256"]["model_id"]
    assert man["inputs_sha256"]["backend"] == "opencode"
    assert man["inputs_sha256"]["renderer_version"]
    import yaml as _yaml
    _pp = vsp_root / "config" / "pipeline.yaml"
    _pc = _yaml.safe_load(_pp.read_text(encoding="utf-8"))
    _pc["models"]["writer"]["model_id"] = "other-test-model"
    _pp.write_text(_yaml.safe_dump(_pc), encoding="utf-8")
    fake3 = FakeLLMClient({("writer", 1): dict(beats), ("writer", 2): dict(beats),
                           "beat_extractor": dict(beats)})
    r4 = do_extract(vsp_root, sid, 1, fake=fake3)
    assert r4 and len(fake3.calls) > 0
    man2 = read_json(mpath)
    assert man2["inputs_sha256"]["model_id"] == "other-test-model"
    assert man2["inputs_sha256"] != man["inputs_sha256"]


def test_deny_config_in_sandbox_and_subprocess_env(vsp_root, monkeypatch):
    import subprocess
    from vsp.config import load_config
    from vsp.llm.client import LLMBudget
    cfg = _opencode_cfg(load_config(vsp_root))
    raw = _raw()
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = list(cmd)
        seen["env"] = dict(kw.get("env") or {})
        sandbox = Path(cmd[cmd.index("--dir") + 1])
        seen["files"] = sorted(p.name for p in sandbox.iterdir())
        seen["sandbox_config"] = json.loads((sandbox / "opencode.json").read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(cmd, 0, raw, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    budget = LLMBudget(cfg, vsp_root, "s10")
    text, usage = ob.run_opencode_call(cfg, vsp_root, "s10", "writer",
                                       [{"role": "user", "content": "hi"}],
                                       budget, chunk_id="deny", stage="r10")
    assert text == '{"ping": "pong", "n": 1}'
    assert seen["sandbox_config"] == {"$schema": "https://opencode.ai/config.json",
                                      "permission": "deny"}
    assert "prompt.md" in seen["files"] and "opencode.json" in seen["files"]
    assert json.loads(seen["env"]["OPENCODE_CONFIG_CONTENT"]) == seen["sandbox_config"]
    assert usage.info["tool_used"] is False and usage.info["tool_completed"] is False


def test_parse_stream_completed_tool_status():
    denied = "\n".join([
        json.dumps({"type": "text", "part": {"type": "text", "text": "x"}}),
        json.dumps({"type": "tool_use", "part": {"type": "tool_use", "status": "denied"}}),
        json.dumps({"type": "step_finish", "part": {"tokens": {}, "cost": 0}}),
    ]).encode()
    p = ob.parse_stream(denied)
    assert p["tool_used"] is True and p["tool_completed"] is False
    completed = denied.replace(b'"denied"', b'"completed"')
    q = ob.parse_stream(completed)
    assert q["tool_used"] is True and q["tool_completed"] is True
    assert q["tool_statuses"] == ["completed"]


def test_cache_read_logged_separately_and_priced(vsp_root, monkeypatch):
    import subprocess
    from vsp.config import load_config
    from vsp.llm.client import LLMBudget
    cfg = _opencode_cfg(load_config(vsp_root))
    cfg.pipeline["models"]["writer"].update(price_input_per_1m=0.10,
                                            price_output_per_1m=0.20)
    stream = "\n".join([
        json.dumps({"type": "text", "part": {"type": "text", "text": '{"ok": true}'}}),
        json.dumps({"type": "step_finish", "sessionID": "ses_cache",
                    "part": {"tokens": {"input": 1000, "output": 200, "reasoning": 50,
                                        "cache": {"read": 8000, "write": 100}}}}),
    ]).encode()

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stream, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    budget = LLMBudget(cfg, vsp_root, "s12")
    text, usage = ob.run_opencode_call(cfg, vsp_root, "s12", "writer",
                                       [{"role": "user", "content": "hi"}],
                                       budget, chunk_id="cache", stage="r12")
    assert text == '{"ok": true}'
    assert usage.input_tokens == 1100 and usage.output_tokens == 250
    assert usage.info["cache_read"] == 8000 and usage.info["cache_write"] == 100
    expected = ((1000 + 100) * 0.10 + (200 + 50) * 0.20 + 8000 * 0.10) / 1e6
    assert usage.cost_usd == expected
    line = (vsp_root / "run" / "s12" / "llm_calls.jsonl").read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert entry["cache_read_tokens"] == 8000 and entry["cache_write_tokens"] == 100


def test_render_prompt_includes_canary_instruction_once():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    text = ob.render_prompt_parts(msgs)[0]
    assert text.count(ob.CANARY_INSTRUCTION) == 1
    sys_end = text.index("### USER")
    assert text.index(ob.CANARY_INSTRUCTION) < sys_end
    assert text.index("S") < text.index(ob.CANARY_INSTRUCTION)
    # split across files: still exactly once in total
    big = [{"role": "system", "content": "S"},
           {"role": "user", "content": "y\n" * 900},
           {"role": "user", "content": "z\n" * 900},
           {"role": "user", "content": "w\n" * 900}]
    assert sum(p.count(ob.CANARY_INSTRUCTION) for p in ob.render_prompt_parts(big)) == 1
    # no system section: a minimal one carries the instruction
    only_user = ob.render_prompt_parts([{"role": "user", "content": "U"}])[0]
    assert only_user.count(ob.CANARY_INSTRUCTION) == 1


def test_cache_key_binds_renderer_version(vsp_root, monkeypatch):
    import subprocess
    from vsp.config import load_config
    from vsp.llm.client import LLMBudget
    cfg = _opencode_cfg(load_config(vsp_root))
    raw = _raw()
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, raw, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    budget = LLMBudget(cfg, vsp_root, "srv")
    msgs = [{"role": "user", "content": "hi"}]
    monkeypatch.setattr(ob, "RENDERER_VERSION", "rv-a")
    ob.run_opencode_call(cfg, vsp_root, "srv", "writer", msgs, budget,
                         chunk_id="c", stage="s")
    ob.run_opencode_call(cfg, vsp_root, "srv", "writer", msgs, budget,
                         chunk_id="c", stage="s")
    assert calls["n"] == 1
    monkeypatch.setattr(ob, "RENDERER_VERSION", "rv-b")
    ob.run_opencode_call(cfg, vsp_root, "srv", "writer", msgs, budget,
                         chunk_id="c", stage="s")
    assert calls["n"] == 2


def test_cache_hit_returns_stored_canary(vsp_root, monkeypatch):
    import subprocess
    from vsp.config import load_config
    from vsp.llm.client import LLMBudget
    cfg = _opencode_cfg(load_config(vsp_root))
    raw = _raw()
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, raw, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    budget = LLMBudget(cfg, vsp_root, "sc")
    msgs = [{"role": "user", "content": "hi"}]
    _, u1 = ob.run_opencode_call(cfg, vsp_root, "sc", "writer", msgs, budget,
                                 chunk_id="c", stage="s")
    _, u2 = ob.run_opencode_call(cfg, vsp_root, "sc", "writer", msgs, budget,
                                 chunk_id="c", stage="s")
    assert calls["n"] == 1 and u2.cached is True
    assert u2.info.get("prompt_canary") == u1.info.get("prompt_canary")


def test_cache_hit_without_canary_is_not_used_directly(vsp_root, monkeypatch):
    from vsp.config import load_config
    from vsp.llm.client import LLMClient, Usage
    import json as _json
    cfg = _opencode_cfg(load_config(vsp_root))
    client = LLMClient(cfg, vsp_root, "snc")
    seen = {"n": 0}

    def fake_chat(role, messages, **kw):
        seen["n"] += 1
        u = Usage(10, 5, 0.001, False)
        u.info = {"tool_used": False}
        return _json.dumps({"ok": True}), u

    monkeypatch.setattr(client, "chat", fake_chat)
    import pytest
    with pytest.raises(SystemExit, match="check=canary"):
        client.chat_json("writer", [{"role": "user", "content": "x"}], require_canary=True)
    assert seen["n"] == 2


def test_render_prompt_splits_by_bytes_with_headers():
    body = ("x" * 1000 + "\n") * 150  # ~150 KB after wrapping
    parts = ob.render_prompt_parts(
        [{"role": "system", "content": "S"},
         {"role": "user", "content": body}])
    assert len(parts) > 1
    n = len(parts)
    for i, p in enumerate(parts):
        assert len(p.encode("utf-8")) <= ob.PROMPT_PART_MAX_BYTES, (i, len(p))
        assert p.splitlines()[0] == f"=== PROMPT PART {i + 1} OF {n} ==="
        assert len(p.splitlines()) <= ob.MAX_PROMPT_LINES
    assert f"in {n} part(s)" in parts[0]
    assert sum(p.count(ob.CANARY_INSTRUCTION) for p in parts) == 1


def test_prompt_beyond_eight_parts_refuses_without_call(vsp_root, monkeypatch):
    import subprocess
    from vsp.config import load_config
    from vsp.llm.client import LLMBudget
    cfg = _opencode_cfg(load_config(vsp_root))
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    budget = LLMBudget(cfg, vsp_root, "s17")
    big = [{"role": "user", "content": ("y" * 1000 + "\n") * 300}]
    assert len(ob.render_prompt_parts(big)) > ob.MAX_PROMPT_PARTS
    import pytest
    with pytest.raises(SystemExit, match="prompt_too_large"):
        ob.run_opencode_call(cfg, vsp_root, "s17", "writer", big, budget,
                             chunk_id="c", stage="s")
    assert calls["n"] == 0


def test_build_opencode_cmd_repeats_dash_f_per_file():
    cmd = ob.build_opencode_cmd("/bin/opencode", "m", Path("/s"),
                                [Path("/s/a.md"), Path("/s/b.md"), Path("/s/c.md")])
    assert cmd.count("-f") == 3
    assert "--auto" not in cmd and cmd[-2] == "--"


def test_json_repair_retry_carries_chunk_and_stage(vsp_root, monkeypatch):
    from vsp.config import load_config
    from vsp.llm.client import LLMClient
    monkeypatch.setenv("VSP_TEST_WRITER_KEY", "dummy")
    cfg = load_config(vsp_root)
    cfg.pipeline["models"]["writer"] = {
        "backend": "http", "model_id": "m", "endpoint": "http://x",
        "api_key_env": "VSP_TEST_WRITER_KEY", "temperature": 0.0,
        "max_tokens": 50, "price_input_per_1m": 0.1, "price_output_per_1m": 0.2}
    client = LLMClient(cfg, vsp_root, "s16")
    calls = {"n": 0}

    def fake_api(self, endpoint, api_key, model, messages, temperature,
                 max_tokens, timeout, role="writer"):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json", 10, 5
        return '{"ok": 1}', 10, 5

    monkeypatch.setattr(LLMClient, "_call_api", fake_api)
    out, _usage = client.chat_json("writer", [{"role": "user", "content": "hi"}],
                                   chunk_id="c9", stage="s9")
    assert out == {"ok": 1} and calls["n"] == 2
    lines = (vsp_root / "run" / "s16" / "llm_calls.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 2
    entries = [json.loads(line) for line in lines]
    assert all(e["chunk_id"] == "c9" and e["stage"] == "s9" for e in entries)
