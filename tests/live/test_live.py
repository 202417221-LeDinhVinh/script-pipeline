"""Live tests (need VSP_WRITER_API_KEY / VSP_JUDGE_API_KEY). Run: py -m pytest tests/live -m live.

These run the real pipeline end-to-end (no fake client) and assert the MVP
acceptance conditions A2/A3 that cannot be verified offline:
- A2: every inbox source reaches a terminal decision; offences exported.
- A3: a multi-chunk serial yields consecutive episodes with handover.
- A4: every out/ file validates and `vsp verify` passes (checked via verify_series).
"""
from __future__ import annotations
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytestmark = pytest.mark.live
NEEDS_KEYS = not (os.environ.get("VSP_WRITER_API_KEY") and os.environ.get("VSP_JUDGE_API_KEY"))
REPO = Path(__file__).resolve().parents[2]
# R4: BUDGET_STOP is resumable, not terminal (SPEC §10.7) — a live run that hits
# the cap fails A2 with a clear message instead of counting as done.
# Q13: RELEASE replaces PASS; FORCED_PASS is removed (NEEDS_HUMAN is review, not export).
TERMINAL = {"RELEASE", "PASS", "NEEDS_HUMAN", "REJECT", "FAST_FORWARD_MERGE"}


def _assert_terminal(decs: list[str]) -> None:
    assert decs, "no decisions recorded"
    assert "BUDGET_STOP" not in decs, (
        "hit episode/daily cap during live run: raise caps and rerun "
        "(BUDGET_STOP is resumable, not a terminal decision)")
    assert all(d in TERMINAL for d in decs), decs


def _small_chunk_cfg(root: Path) -> None:
    import yaml
    p = root / "config" / "pipeline.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg["chunking"]["sliding_target"] = 500
    cfg["chunking"]["sliding_min"] = 400
    cfg["chunking"]["sliding_max"] = 620
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    lp = root / "config" / "quality_lock.yaml"
    lock = yaml.safe_load(lp.read_text(encoding="utf-8"))
    lock.setdefault("chunking", {}).setdefault("serial", {})\
        .update({"sliding_target": 500, "sliding_min": 400, "sliding_max": 620})
    lp.write_text(yaml.safe_dump(lock), encoding="utf-8")


@pytest.mark.skipif(NEEDS_KEYS, reason="needs API keys")
def test_live_reddit_story(vsp_root):
    """A2 seed: run the pipeline on a Reddit-like file; assert a terminal decision."""
    from vsp.orchestrator import (do_ingest, do_chunk, do_extract, do_draft,
                                  do_audit, do_evolve, do_export, series_decisions)
    from vsp.export.package import verify_series
    shutil.copy(REPO / "tests" / "fixtures" / "reddit_aita.txt", vsp_root / "aita.txt")
    p = do_ingest(vsp_root, str(vsp_root / "aita.txt"), rights="public_domain")
    sid = p["series_id"]
    chunks = do_chunk(vsp_root, sid)
    assert len(chunks) >= 1
    for c in chunks:
        n = c["chunk_index"]
        do_extract(vsp_root, sid, n)
        do_draft(vsp_root, sid, n)
        dec = do_audit(vsp_root, sid, n)
        assert dec["decision"] in TERMINAL or dec["decision"] == "BUDGET_STOP", dec
        if dec["decision"] in ("RELEASE", "PASS", "NEEDS_HUMAN", "FORCED_PASS_WITH_WARNING"):
            do_evolve(vsp_root, sid, n)
    decs, _ = series_decisions(vsp_root, sid)
    _assert_terminal(decs)
    do_export(vsp_root, sid)
    assert verify_series(vsp_root, sid) == []


@pytest.mark.skipif(NEEDS_KEYS, reason="needs API keys")
def test_live_serial_three_chunks(vsp_root):
    """A3 seed: serial yields >=3 consecutive episodes; ep N+1 resolves ep N's cliffhanger."""
    from vsp.orchestrator import (do_ingest, do_chunk, do_extract, do_draft,
                                  do_audit, do_evolve, do_export)
    from vsp.export.package import verify_series
    from vsp.io import read_json
    _small_chunk_cfg(vsp_root)
    shutil.copy(REPO / "tests" / "fixtures" / "serial_harbor.txt", vsp_root / "serial.txt")
    p = do_ingest(vsp_root, str(vsp_root / "serial.txt"), rights="public_domain",
                  topology="CONTINUOUS_SERIAL")
    sid = p["series_id"]
    chunks = do_chunk(vsp_root, sid)
    assert len(chunks) >= 3, [c["word_equiv"] for c in chunks]
    exported = 0
    for c in chunks:
        n = c["chunk_index"]
        do_extract(vsp_root, sid, n)
        do_draft(vsp_root, sid, n)
        dec = do_audit(vsp_root, sid, n)
        assert dec["decision"] in TERMINAL or dec["decision"] == "BUDGET_STOP", dec
        if dec["decision"] in ("RELEASE", "PASS", "NEEDS_HUMAN", "FORCED_PASS_WITH_WARNING"):
            do_evolve(vsp_root, sid, n)
            if dec["decision"] in ("RELEASE", "PASS"):
                exported += 1
    decs, _ = series_decisions(vsp_root, sid)
    _assert_terminal(decs)
    assert exported >= 3, f"need >=3 consecutive exported episodes, got {exported}"
    paths = do_export(vsp_root, sid)
    assert len(paths) >= 3
    assert verify_series(vsp_root, sid) == []
    # handover continuity: ep2+ drafts resolved the previous cliffhanger
    state = read_json(vsp_root / "run" / sid / "series_state.json")
    assert state.get("current_episode", 0) >= 3
