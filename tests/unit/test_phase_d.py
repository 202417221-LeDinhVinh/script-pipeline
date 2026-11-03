"""Phase D proofs (§17 Q14–Q16). Offline only; no story text; no real data."""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tests.conftest import make_passing_script, REPO


def test_q14_calibrate_writes_receipt_and_metrics(vsp_root):
    """Q14: calibrate 2 synthetic episodes with a stub judge → receipt with metrics."""
    from vsp.calibrate.runner import calibrate_scripts, write_receipt, receipt_ok
    s1 = make_passing_script()
    s2 = make_passing_script(template="Template_2_Breadcrumb_Mystery",
                             hook="Option_1_Pink_Teddy_Bear", payoff=5,
                             cta="cliffhanger_subscription")
    # stub judge: always pass (imperfect → recall 0, receipt red)
    def _jf(_script):
        return {n: True for n in ("H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1")}

    # copy repo config/prompts/rubric into vsp_root for receipt paths
    import shutil
    for sub in ("config", "prompts", "rubric"):
        src = REPO / sub
        if src.exists():
            shutil.copytree(src, vsp_root / sub, dirs_exist_ok=True)
    metrics = calibrate_scripts([s1, s2], _jf, repeats=3)
    assert "overall_recall" in metrics and "test_retest_agreement" in metrics
    assert metrics["n_scripts"] == 2 and metrics["n_mutants"] > 0
    p = write_receipt(vsp_root, "test-model", metrics)
    assert p.exists()
    # imperfect judge → red receipt → production gate closed
    assert receipt_ok(vsp_root, "test-model") is False


def test_q14_green_receipt_passes_gate(vsp_root):
    """Q14: green metrics → receipt_ok True (rubric/prompt bound)."""
    import shutil
    for sub in ("config", "prompts", "rubric"):
        src = REPO / sub
        if src.exists():
            shutil.copytree(src, vsp_root / sub, dirs_exist_ok=True)
    from vsp.calibrate.runner import write_receipt, receipt_ok, _prompt_sha
    # D1 (stricter): green requires per-type recall >= 0.90 and false-fail <= 0.10.
    metrics = {"overall_recall": 0.95, "test_retest_agreement": 0.95,
               "false_fail_rate": 0.0, "recall_per_item": {"L1": 1.0},
               "recall_per_mutant": {"name_swap": 1.0, "pov_flip": 1.0},
               "n_scripts": 5, "n_mutants": 35}
    p = write_receipt(vsp_root, "opencode-go/deepseek-v4.1-flash", metrics)
    assert p.exists()
    assert receipt_ok(vsp_root, "opencode-go/deepseek-v4.1-flash") is True
    # tamper rubric → invalid
    (vsp_root / "rubric" / "rubric_v1.md").write_text("tampered", encoding="utf-8")
    assert receipt_ok(vsp_root, "opencode-go/deepseek-v4.1-flash") is False
    _ = _prompt_sha(vsp_root)


def test_q15_review_sheet_pairwise_agreement(vsp_root):
    """Q15: review-sheet renders offline; structured review + pairwise + agreement."""
    import shutil
    from vsp.io import atomic_write_json
    for sub in ("config", "prompts", "rubric"):
        src = REPO / sub
        if src.exists():
            shutil.copytree(src, vsp_root / sub, dirs_exist_ok=True)
    # one synthetic exported episode + audit
    sid = "rev1"
    ep = make_passing_script()
    ep["script_metadata"]["series_id"] = sid
    (vsp_root / "out" / sid).mkdir(parents=True, exist_ok=True)
    (vsp_root / "out" / sid / "ep_001.json").write_text(json.dumps(ep), encoding="utf-8")
    man = {"series_id": sid, "episodes": [{"episode": 1, "file": "ep_001.json",
            "decision": "RELEASE", "overall": 8.5,
            "scores": {"hook_payoff": 9, "pacing": 8, "logic": 8, "retention": 9}}]}
    (vsp_root / "out" / sid / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (vsp_root / "run" / sid / "work" / "chunk_001").mkdir(parents=True, exist_ok=True)
    atomic_write_json(vsp_root / "run" / sid / "work" / "chunk_001" / "decision.json",
                      {"chunk_id": "chunk_001", "decision": "RELEASE", "episode_number": 1,
                       "overall_score": 8.5, "tier1_passed": True, "rewrite_count": 0,
                       "inputs_sha256": {}})
    atomic_write_json(vsp_root / "run" / sid / "work" / "chunk_001" / "audit_0.json",
                      {"scores": man["episodes"][0]["scores"], "overall": 8.5,
                       "checklist": {n: True for n in ("H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1")},
                       "panel_split": False, "inputs_sha256": {}})
    from vsp.cli import build_parser
    ap = build_parser()
    assert ap.parse_args(["--root", str(vsp_root), "review-sheet", sid]).fn(
        ap.parse_args(["--root", str(vsp_root), "review-sheet", sid])) == 0
    assert (vsp_root / "review" / sid / "index.html").exists()
    # structured review
    a = ap.parse_args(["--root", str(vsp_root), "review", sid, "1",
                       "--dimensions", "hook_payoff:9,pacing:8,logic:8,retention:9",
                       "--release", "yes", "--tags", "hook-miss", "--note", "ok"])
    assert a.fn(a) == 0
    # pairwise needs 2 candidates
    (vsp_root / "run" / sid / "work" / "chunk_001" / "candidates").mkdir(parents=True, exist_ok=True)
    for i, h in enumerate(("Hook_A", "Hook_B")):
        (vsp_root / "run" / sid / "work" / "chunk_001" / "candidates" / f"draft_{i}.json").write_text(
            json.dumps({**ep, "_hook": h}), encoding="utf-8")
    a2 = ap.parse_args(["--root", str(vsp_root), "review", "--pairwise-sid", sid, "--pick", "1"])
    assert a2.fn(a2) == 0
    # judge-agreement (n=1 <30, still reports)
    a3 = ap.parse_args(["--root", str(vsp_root), "judge-agreement"])
    assert a3.fn(a3) == 0


def test_q16_honeypot_miss_forces_all_mode(vsp_root):
    """Q16: missed honeypot reverts sample mode to all (lock untouched)."""
    import shutil, yaml
    for sub in ("config", "prompts"):
        src = REPO / sub
        if src.exists():
            shutil.copytree(src, vsp_root / sub, dirs_exist_ok=True)
    lp = vsp_root / "config" / "quality_lock.yaml"
    lock = yaml.safe_load(lp.read_text())
    lock.setdefault("human_review", {})["mode"] = "sample"
    lp.write_text(yaml.safe_dump(lock))
    from vsp.config import load_config
    from vsp.cli import effective_review_mode
    cfg = load_config(vsp_root)
    assert effective_review_mode(cfg, vsp_root) == "sample"
    # honeypot miss: approved a honeypot mutant
    (vsp_root / "feedback").mkdir(exist_ok=True)
    (vsp_root / "feedback" / "review_ledger.jsonl").write_text(
        json.dumps({"series_id": "s", "ep": 1, "script_sha256": "x",
                    "honeypot": True, "release": True}) + "\n", encoding="utf-8")
    assert effective_review_mode(cfg, vsp_root) == "all"
    # lock file itself unchanged (still says sample)
    assert yaml.safe_load(lp.read_text())["human_review"]["mode"] == "sample"
