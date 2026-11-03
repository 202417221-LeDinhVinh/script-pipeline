"""Calibration runner: mutate episodes, judge, metrics, receipts (Q14)."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

from . import mutators as _m
from .metrics import recall_per_type, false_fail_rate, test_retest_agreement, cohen_kappa


def _prompt_sha(root: Path, name: str = "checklist_judge.md") -> str:
    try:
        return hashlib.sha256((root / "prompts" / name).read_bytes()).hexdigest()
    except Exception:
        return ""


def _rubric_sha(root: Path) -> str:
    try:
        rdir = root / "rubric"
        cands = sorted(rdir.glob("rubric_v*.md"))
        if not cands:
            return ""
        return hashlib.sha256(cands[-1].read_bytes()).hexdigest()
    except Exception:
        return ""


def _sanitize_model(model_id: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in str(model_id or "unknown"))


def receipt_path(root: Path, prompt_sha: str, model_id: str) -> Path:
    return root / "state" / "calibration" / f"{prompt_sha[:16]}_{_sanitize_model(model_id)}.json"


def calibrate_scripts(scripts: list[dict], judge_fn, repeats: int = 3,
                      human_labels: dict | None = None) -> dict:
    """Run calibration on in-memory scripts with a judge_fn(script)->verified items.

    judge_fn returns {H1: True/False/None, ...} (verified checklist).
    Returns metrics dict (no I/O).
    """
    from ..gates.tier2.scoring import checklist_to_scores
    results: list = []
    clean: list = []
    retest: list = []
    for idx, sc in enumerate(scripts):
        # clean (unmutated) — 1 sample for false-fail + repeats for agreement
        base_items = judge_fn(sc)
        clean.append({"checked": list(base_items.keys()),
                      "failed": [k for k, v in base_items.items() if v is not True]})
        rep = []
        for _ in range(max(1, repeats)):
            r = judge_fn(sc)
            # pass/fail per script = all True?
            rep.append(all(v is True for v in r.values()))
        retest.append(rep)
        # mutants
        others = [s for j, s in enumerate(scripts) if j != idx] or [sc]
        for mname, mfn in _m.MUTATORS.items():
            try:
                if mname == "payoff_replace":
                    mut, exp = mfn(sc, others[0])
                else:
                    mut, exp = mfn(sc)
            except Exception:
                continue
            got = judge_fn(mut)
            failed = [k for k, v in got.items() if v is not True]
            # recall counts if every expected item is in failed
            results.append({"mutant": mname, "expected": exp, "failed": failed})
    rec = recall_per_type(results)
    # D1: recall per mutator type (not just the overall average).
    from collections import defaultdict as _dd
    _tot: dict = _dd(int)
    _hit: dict = _dd(int)
    for r in results:
        m = r.get("mutant", "?")
        exp = r.get("expected", []) or []
        if not exp:
            continue
        _tot[m] += 1
        if all(e in (r.get("failed", []) or []) for e in exp):
            _hit[m] += 1
    per_mut = {m: round(_hit[m] / _tot[m], 3) for m in _tot}
    # overall recall on LLM-judged types = mean over expected items (all are LLM checklist)
    all_exp = sum(1 for r in results for _ in r.get("expected", []))
    all_hit = sum(1 for r in results for e in r.get("expected", []) if e in (r.get("failed", []) or []))
    overall_recall = (all_hit / all_exp) if all_exp else 1.0
    ffr = false_fail_rate(clean)
    # test-retest: flatten repeats per script? Use per-script agreement averaged.
    agrs = []
    for rep in retest:
        # rep is list of bools over repeats → agreement 1.0 if unanimous else 0.0
        agrs.append(1.0 if len(set(rep)) == 1 else 0.0)
    agreement = sum(agrs) / len(agrs) if agrs else 1.0
    kappa = None
    if human_labels:
        # human_labels: {idx: True/False release} — compare to judge clean pass/fail
        ja = [all(v is True for v in judge_fn(sc).values()) for sc in scripts]
        ha = [bool(human_labels.get(i, False)) for i in range(len(scripts))]
        try:
            kappa = cohen_kappa([int(x) for x in ja], [int(x) for x in ha])
        except Exception:
            kappa = None
    return {"recall_per_item": rec, "recall_per_mutant": per_mut,
            "overall_recall": round(overall_recall, 3),
            "false_fail_rate": round(ffr, 3), "test_retest_agreement": round(agreement, 3),
            "kappa_vs_human": kappa, "n_scripts": len(scripts), "n_mutants": len(results)}


def write_receipt(root: Path, model_id: str, metrics: dict) -> Path:
    from ..quality import eff_calibration as _eff_cal
    from ..config import load_config as _load_cfg
    psha = _prompt_sha(root)
    rsha = _rubric_sha(root)
    cfg = _load_cfg(root)
    cal = _eff_cal(cfg)
    min_r = float(cal["min_recall_llm_types"])
    min_a = float(cal["min_test_retest_agreement"])
    min_per = float(cal["min_recall_per_type"])
    max_ffr = float(cal["max_false_fail_rate"])
    # D1: green requires per-type recall >= min AND false-fail <= max,
    # not just the overall average (an always-fail judge scores recall 1.0).
    per_type = metrics.get("recall_per_mutant", {}) or {}
    if not per_type and isinstance(metrics.get("recall_per_item"), dict):
        # fallback: item-level recall stands in when mutant-level absent
        per_type = metrics.get("recall_per_item", {})
    per_ok = bool(per_type) and all(float(v) >= min_per for v in per_type.values())
    green = (metrics.get("overall_recall", 0) >= min_r
             and metrics.get("test_retest_agreement", 0) >= min_a
             and metrics.get("false_fail_rate", 1.0) <= max_ffr
             and per_ok)
    payload = {"model_id": model_id, "prompt_sha256": psha, "rubric_sha256": rsha,
               "metrics": metrics, "minimums": {"recall": min_r, "agreement": min_a},
               "green": bool(green)}
    p = receipt_path(root, psha, model_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def receipt_ok(root: Path, model_id: str) -> bool:
    """Production gate: current prompt+model has a green receipt (Q14)."""
    p = receipt_path(root, _prompt_sha(root), model_id)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text())
    except Exception:
        return False
    if not data.get("green"):
        return False
    # changing rubric invalidates receipts
    if data.get("rubric_sha256") != _rubric_sha(root):
        return False
    if data.get("prompt_sha256") != _prompt_sha(root):
        return False
    return True
