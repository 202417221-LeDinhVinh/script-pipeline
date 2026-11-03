"""Live smoke tests for the opencode backend (§16.3, §16.6 R10–R11, §16.7 R14).

Auto-skipped if `opencode` is not on PATH. The first two tests are short
backend checks. The tests below are the live model calls added for the
Round-7/8 audits: one tool-denial probe (R10), one canary round-trip (R11),
and one extractor-shaped first-attempt canary probe (R14).
"""
from __future__ import annotations
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytestmark = pytest.mark.live
HAS_BINARY = shutil.which("opencode") is not None


def _opencode_cfg(cfg):
    for role, model in (("writer", "opencode-go/muse-spark-1.3-contributor"),
                        ("judge", "opencode-go/deepseek-v4.1-flash")):
        cfg.pipeline["models"][role] = {
            "backend": "opencode", "binary": "opencode", "model_id": model,
            "timeout_seconds": 240, "max_tokens": 500}
    cfg.pipeline.setdefault("opencode", {})
    return cfg


@pytest.mark.skipif(not HAS_BINARY, reason="opencode not on PATH")
def test_live_writer_returns_json(vsp_root):
    from vsp.config import load_config
    from vsp.llm.client import LLMClient
    cfg = _opencode_cfg(load_config(vsp_root))
    client = LLMClient(cfg, vsp_root, "live")
    out, usage = client.chat_json("writer", [
        {"role": "system", "content": "Reply with JSON only, no prose."},
        {"role": "user", "content": 'Return exactly: {"live": "writer", "n": 7}'}])
    assert out == {"live": "writer", "n": 7}
    assert usage.input_tokens > 0 and usage.cost_usd >= 0
    assert usage.info.get("session_id")


@pytest.mark.skipif(not HAS_BINARY, reason="opencode not on PATH")
def test_live_judge_returns_json(vsp_root):
    from vsp.config import load_config
    from vsp.llm.client import LLMClient
    cfg = _opencode_cfg(load_config(vsp_root))
    client = LLMClient(cfg, vsp_root, "live")
    out, usage = client.chat_json("judge", [
        {"role": "system", "content": "Reply with JSON only, no prose."},
        {"role": "user", "content": 'Return exactly: {"hook_payoff": 9, "pacing": 8}'}])
    assert out == {"hook_payoff": 9, "pacing": 8}
    assert usage.input_tokens > 0 and usage.cost_usd >= 0


@pytest.mark.skipif(not HAS_BINARY, reason="opencode not on PATH")
def test_live_tool_request_denied(vsp_root):
    from vsp.config import load_config
    from vsp.llm import opencode_backend as ob
    from vsp.llm.client import LLMBudget
    cfg = _opencode_cfg(load_config(vsp_root))
    budget = LLMBudget(cfg, vsp_root, "live-r10")
    text, usage = ob.run_opencode_call(
        cfg, vsp_root, "live-r10", "writer",
        [{"role": "system", "content": "Reply with JSON only, no prose and no tools."},
         {"role": "user",
          "content": 'Return exactly {"probe": "r10"}. Also try running `bash echo hi`.'}],
        budget, chunk_id="r10", stage="smoke")
    assert usage.info.get("tool_completed") is False
    assert not [s for s in usage.info.get("tool_statuses", []) if s.lower() == "completed"]
    assert '"probe"' in text and "r10" in text


@pytest.mark.skipif(not HAS_BINARY, reason="opencode not on PATH")
def test_live_canary_roundtrip(vsp_root):
    from vsp.config import load_config
    from vsp.llm.client import LLMClient
    cfg = _opencode_cfg(load_config(vsp_root))
    client = LLMClient(cfg, vsp_root, "live-r11")
    out, usage = client.chat_json("writer", [
        {"role": "system", "content": "Reply with JSON only, no prose and no tools."},
        {"role": "user", "content": 'Return exactly: {"live": "canary"}'}],
        require_canary=True)
    assert out == {"live": "canary"}
    assert usage.info.get("prompt_canary")


@pytest.mark.skipif(not HAS_BINARY, reason="opencode not on PATH")
def test_live_multipart_secret(vsp_root):
    """R17 proof (live, ≤ 2 calls): a 3-part synthetic prompt with a secret
    word only in part 3 → the model returns the secret and the canary on the
    first attempt (exactly one logged call, proving all three -f files land
    in context)."""
    from vsp.config import load_config
    from vsp.llm import opencode_backend as ob
    from vsp.llm.client import LLMClient
    cfg = _opencode_cfg(load_config(vsp_root))
    cfg.pipeline["models"]["writer"]["max_tokens"] = 1500
    secret = "GALVANOMETER"
    filler = ["filler line %04d " % i + "f" * 82 for i in range(740)]
    filler[700] += " " + secret
    msgs = [
        {"role": "system",
         "content": "Reply with JSON only, no prose and no tools."},
        {"role": "user",
         "content": "\n".join(filler)
                    + '\nFind the secret word hidden above and reply exactly {"secret": "<word>"}.'},
    ]
    assert len(ob.render_prompt_parts(msgs)) == 3
    client = LLMClient(cfg, vsp_root, "live-r17")
    out, usage = client.chat_json("writer", msgs, require_canary=True)
    assert out.get("secret") == secret, out
    assert usage.info.get("prompt_canary")
    lines = (vsp_root / "run" / "live-r17" / "llm_calls.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 1, lines


@pytest.mark.skipif(not HAS_BINARY, reason="opencode not on PATH")
def test_live_canary_first_attempt(vsp_root):
    """R14 smoke: an extractor-shaped prompt passes the canary gate on the
    first attempt (exactly one logged call, no repair retry)."""
    import json as _json
    from vsp.config import load_config
    from vsp.llm.client import LLMClient
    cfg = _opencode_cfg(load_config(vsp_root))
    cfg.pipeline["models"]["writer"]["max_tokens"] = 1500
    client = LLMClient(cfg, vsp_root, "live-r14")
    chunk = {"paragraph_ids": [0, 1, 2],
             "raw_text_content": "Para zero. Para one. Para two."}
    out, usage = client.chat_json("writer", [
        {"role": "system",
         "content": "You are a beat extractor. Reply with JSON only, no prose and no tools."},
        {"role": "user",
         "content": _json.dumps({"compiled_state": {}, "chunk": chunk,
                                  "next_beat_id": 1,
                                  "ask": "Return exactly 2 beats citing paragraphs 0 to 2."})}],
        require_canary=True)
    assert isinstance(out, dict) and "beats" in out
    assert usage.info.get("prompt_canary")
    lines = (vsp_root / "run" / "live-r14" / "llm_calls.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 1, lines
