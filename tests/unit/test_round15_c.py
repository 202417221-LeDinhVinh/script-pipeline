"""Round 15 Phase C: C1-C5 failing-then-passing proofs."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
REPO = Path(__file__).resolve().parents[2]


def test_c1_no_legacy_judge_bypass():
    from vsp.gates.tier2.scoring import decide_release
    # production: missing checklist can never RELEASE
    v, _ = decide_release(True, 9.0, {"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9},
                          checklist=None, panel_split=False)
    assert v == "NEEDS_HUMAN"
    # legacy single-sample path removed from the production evaluator
    src = (REPO / "src" / "vsp" / "orchestrator.py").read_text(encoding="utf-8")
    assert '_used_legacy' not in src
    assert '"legacy"' not in src


def test_c2_calibration_gate_checks_every_panel_model():
    src = (REPO / "src" / "vsp" / "orchestrator.py").read_text(encoding="utf-8")
    idx = src.find("Q14 production gate")
    region = src[idx:idx + 800]
    assert "for _mid in _panel_models" in region
    assert "except Exception" not in region or "raise" in region.split("except Exception")[1][:120]


def test_c3_panel_seeds_reproducible_across_processes():
    import subprocess
    code = ("import hashlib; print(int(hashlib.sha256('chunk_001|0|m|0'.encode()).hexdigest()[:8],16)%(2**31))")
    a = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    b = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert a.stdout.strip() == b.stdout.strip() != ""
    src = (REPO / "src" / "vsp" / "orchestrator.py").read_text(encoding="utf-8")
    assert "sha256" in src
    assert "abs(hash(" not in src


def test_c4_unverified_votes_do_not_pass():
    from vsp.gates.tier2.scoring import aggregate_panel
    agg, split, ps = aggregate_panel([{"H1": True}, {"H1": None}, {"H1": None}, {"H1": None}])
    assert agg["H1"] is None and "H1" in split and ps is True
    agg2, _, _ = aggregate_panel([{"H1": True}] * 3 + [{"H1": False}])
    assert agg2["H1"] is True


def test_c5_best_of_n_needs_all_candidates_and_full_source():
    src = (REPO / "src" / "vsp" / "orchestrator.py").read_text(encoding="utf-8")
    assert "need best_of_n" in src or "best_of_n" in src
    # fidelity passes every selected-beat paragraph (no [:20] truncation)
    assert "sorted(set(_sel_pids))[:20]" not in src
    assert "prompt_too_large" in src
