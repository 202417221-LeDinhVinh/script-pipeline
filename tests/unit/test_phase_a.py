"""Phase A proofs (§17): lock loader, Q13 release split, Q12 monotonic rewrites.

No story text is typed here; scripts come from tests.conftest.make_passing_script
and the shared _judge helper (abstract scores only).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tests.conftest import make_passing_script, REPO
from tests.e2e.test_pipeline import _setup_series, _judge, _fid, _fid
from vsp.io import read_json, atomic_write_json
from vsp.llm.fake import FakeLLMClient


def test_lock_loader_effective_and_mismatch_logged(vsp_root, capsys):
    from vsp.config import load_config
    from vsp.quality import eff_budget, eff_chunking, eff_release, lock_sha256
    cfg = load_config(vsp_root)
    assert cfg.lock and cfg.lock_sha256 == lock_sha256(vsp_root)
    assert eff_chunking(cfg)["target"] == 3000
    assert eff_release(cfg)["overall_min"] == 8.0
    assert eff_budget(cfg, "writer")["ep_token_cap"] == 400000
    # mismatch is logged but the lock wins
    cfg.pipeline["chunking"]["sliding_target"] = 6000
    eff_chunking(cfg)
    assert "LOCK-MISMATCH" in capsys.readouterr().err


def test_manifest_records_lock_and_verify_fails_on_mismatch(vsp_root):
    from vsp.orchestrator import do_audit, do_evolve, do_export
    from vsp.export.package import verify_series
    sd = _setup_series(vsp_root)
    _j = _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})
    fake = FakeLLMClient({("judge", 1): _j, ("judge", 2): _j, ("judge", 3): _j,
                          ("judge", 4): _j, ("judge", 5): _fid()})
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "RELEASE"
    fake_e = FakeLLMClient({("writer", 1): {
        "rolling_summary": "s", "secrets_ledger": [], "character_updates": [],
        "handover_bridge": {"pending_cliffhanger": "c",
                            "required_resolution_in_3_to_8s": "r",
                            "ending_emotional_tone": "tense"}}})
    do_evolve(vsp_root, "novel_test", 1, fake=fake_e)
    do_export(vsp_root, "novel_test")
    assert verify_series(vsp_root, "novel_test") == []
    man = json.loads((vsp_root / "out" / "novel_test" / "manifest.json").read_text())
    assert man["quality_lock_sha256"]
    # tamper the lock on disk -> verify must fail
    lp = vsp_root / "config" / "quality_lock.yaml"
    lp.write_text(lp.read_text() + "\n# tamper\n", encoding="utf-8")
    errs = verify_series(vsp_root, "novel_test")
    assert any("quality lock mismatch" in e for e in errs), errs


def test_knowledge_violations_block_release(vsp_root):
    """E1/Q13: invented facts never release, even with high scores."""
    from vsp.orchestrator import do_audit
    sd = _setup_series(vsp_root, ep_done=2)
    _jb = _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})
    def _fid_bad():
        return {"claims": [{"claim": "last coin vs source", "verdict": "contradicted",
                            "source_paragraph_ids": [0]}]}
    fake = FakeLLMClient({("judge", 1): _jb, ("judge", 2): _jb, ("judge", 3): _jb,
                          ("judge", 4): _jb, ("judge", 5): _fid_bad()})
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    # mid-series, Tier1 pass, overall 9 but violations -> NEEDS_HUMAN, never RELEASE
    assert dec["decision"] == "NEEDS_HUMAN", dec


def test_q12_monotonic_exports_best_of_748_782_72(vsp_root):
    """E2/Q12: rewrites keep the best version (NEEDS_HUMAN, monotonic, no regress)."""
    from vsp.orchestrator import do_audit
    from tests.e2e.test_pipeline import _fid
    sd = _setup_series(vsp_root, ep_done=2)  # mid-series so near-miss -> NEEDS_HUMAN
    draft = read_json(sd / "work" / "chunk_001" / "draft.json")
    all_beats = [dict(b) for b in draft["scene_beats"]]

    def _jf(p2=False, r1=False, l3=False):
        # C1/C4 (stricter): checklist items (empty evidence verifies cleanly).
        # initial 6.3 -> rewrite1 7.0 (best, accept) -> rewrite2 6.3 (revert).
        items = {"H1": {"pass": True, "evidence": []}, "H2": {"pass": True, "evidence": []},
                 "P1": {"pass": True, "evidence": []}, "P2": {"pass": not p2, "evidence": []},
                 "L1": {"pass": True, "evidence": []}, "L2": {"pass": True, "evidence": []},
                 "L3": {"pass": not l3, "evidence": []}, "R1": {"pass": not r1, "evidence": []},
                 "S1": {"pass": True, "evidence": []}, "V1": {"pass": True, "evidence": []}}
        return {"items": items, "failed_beat_ids": [1, 2, 3, 4, 5],
                "rewrite_instructions": [{"beat_id": b, "issue": "x", "instruction": "fix it"} for b in [1, 2, 3, 4, 5]]}
    _r0, _r1, _r2 = _jf(p2=True, r1=True, l3=True), _jf(p2=True, r1=True), _jf(p2=True, r1=True, l3=True)
    fake = FakeLLMClient({
        ("judge", 1): _r0, ("judge", 2): _r0, ("judge", 3): _r0, ("judge", 4): _r0,
        ("judge", 5): _fid(),
        ("writer", 1): {"scene_beats": all_beats},
        ("judge", 6): _r1, ("judge", 7): _r1, ("judge", 8): _r1, ("judge", 9): _r1,
        ("judge", 10): _fid(),
        ("writer", 2): {"scene_beats": all_beats},
        ("judge", 11): _r2, ("judge", 12): _r2, ("judge", 13): _r2, ("judge", 14): _r2,
        ("judge", 15): _fid(),
    })
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "NEEDS_HUMAN", dec
    assert dec["rewrite_count"] == 1, dec  # best is round 1 (7.0), round 2 reverted
    assert (sd / "work" / "chunk_001" / "candidates").exists()
    assert (sd / "work" / "chunk_001" / "candidates" / "candidate_2.json").exists()
    rev = json.loads((vsp_root / "review" / "novel_test" / "chunk_001.json").read_text())
    assert rev["overall_score"] == 7.0, rev
    cand1 = json.loads((sd / "work" / "chunk_001" / "candidates" / "candidate_1.json").read_text())
    # review script matches the 7.0 candidate (round 1), not the 6.3 revert
    rev_beats = {b["beat_id"]: b["narration_text"] for b in rev["script"]["scene_beats"]}
    c1_beats = {b["beat_id"]: b.get("narration_text", "") for b in cand1.get("scene_beats", [])}
    for bid, txt in c1_beats.items():
        if txt:
            assert rev_beats[int(bid)] == txt


def test_q12_rewriter_receives_tier1_limits(vsp_root):
    from vsp.orchestrator import do_audit
    sd = _setup_series(vsp_root)
    # force a Tier1 hook-length failure on beat 1 so a rewrite is triggered
    d = read_json(sd / "work" / "chunk_001" / "draft.json")
    d["scene_beats"][0]["narration_text"] = " ".join(["word"] * 25)
    atomic_write_json(sd / "work" / "chunk_001" / "draft.json", d)
    fixed1 = dict(d["scene_beats"][0])
    # B4 (stricter): the rewrite must also ground Beat 1 (lowercase check),
    # so use only words present in the _setup_series selected text.
    fixed1["narration_text"] = "Look, I smiled at the plug now"
    _jp = _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})
    fake = FakeLLMClient({
        ("writer", 1): {"scene_beats": [fixed1]},
        ("judge", 1): _jp, ("judge", 2): _jp, ("judge", 3): _jp, ("judge", 4): _jp,
        ("judge", 5): _fid(),
        ("judge", 6): _jp, ("judge", 7): _jp, ("judge", 8): _jp, ("judge", 9): _jp,
        ("judge", 10): _fid(),
    })
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "RELEASE", dec
    rw_calls = [c for c in fake.calls if c["role"] == "writer"]
    assert rw_calls
    blob = json.dumps(rw_calls[0]["messages"])
    assert "tier1_limits" in blob and "beat1_max_words" in blob


def test_q13_needs_human_review_evolves_but_not_exported(vsp_root):
    from vsp.orchestrator import do_audit, do_evolve, do_export
    from vsp.io import read_json as _r
    sd = _setup_series(vsp_root, ep_done=2)
    # 7.0 mid-series Tier1-pass near-miss -> NEEDS_HUMAN (C1 checklist)
    all_beats = [dict(b) for b in _r(sd / "work/chunk_001/draft.json")["scene_beats"]]
    def _jf2():
        return {"items": {"H1": {"pass": True, "evidence": []}, "H2": {"pass": True, "evidence": []},
                           "P1": {"pass": True, "evidence": []}, "P2": {"pass": False, "evidence": []},
                           "L1": {"pass": True, "evidence": []}, "L2": {"pass": True, "evidence": []},
                           "L3": {"pass": True, "evidence": []}, "R1": {"pass": False, "evidence": []},
                           "S1": {"pass": True, "evidence": []}, "V1": {"pass": True, "evidence": []}},
                "failed_beat_ids": [1, 2, 3, 4, 5],
                "rewrite_instructions": [{"beat_id": b, "issue": "x", "instruction": "fix it"} for b in [1, 2, 3, 4, 5]]}
    _j = _jf2()
    fake = FakeLLMClient({
        ("judge", 1): _j, ("judge", 2): _j, ("judge", 3): _j, ("judge", 4): _j,
        ("judge", 5): _fid(),
        ("writer", 1): {"scene_beats": all_beats},
        ("judge", 6): _j, ("judge", 7): _j, ("judge", 8): _j, ("judge", 9): _j,
        ("judge", 10): _fid(),
        ("writer", 2): {"scene_beats": all_beats},
        ("judge", 11): _j, ("judge", 12): _j, ("judge", 13): _j, ("judge", 14): _j,
        ("judge", 15): _fid(),
    })
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "NEEDS_HUMAN", dec
    assert dec["episode_number"] is None
    rev = vsp_root / "review" / "novel_test" / "chunk_001.json"
    assert rev.exists()
    # state still evolves so the series continues
    fake_e = FakeLLMClient({("writer", 1): {
        "rolling_summary": "s", "secrets_ledger": [], "character_updates": [],
        "handover_bridge": {"pending_cliffhanger": "c",
                            "required_resolution_in_3_to_8s": "r",
                            "ending_emotional_tone": "tense"}}})
    st_before = _r(sd / "series_state.json")["narrative_cursor"]
    do_evolve(vsp_root, "novel_test", 1, fake=fake_e)
    st_after = _r(sd / "series_state.json")["narrative_cursor"]
    assert st_after != st_before
    # never exported
    assert do_export(vsp_root, "novel_test") == []
    assert not (vsp_root / "out" / "novel_test").exists()
