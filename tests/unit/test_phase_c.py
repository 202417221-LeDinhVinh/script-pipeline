"""Phase C proofs (§17 Q8–Q11). No story text; synthetic scripts and quotes only."""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tests.conftest import REPO
from tests.e2e.test_pipeline import _setup_series, _judge
from vsp.io import read_json, atomic_write_json
from vsp.llm.fake import FakeLLMClient


def _script_text(script: dict) -> str:
    parts = [b.get("narration_text", "") for b in script.get("scene_beats", [])]
    parts.append(script.get("call_to_action", {}).get("outro_narration", ""))
    return " ".join(parts)


def _checklist_fixture(script_text: str, valid_pids: list, all_pass: bool = True,
                        fail_items: tuple = ()):
    import re
    # split script into sentences for verbatim quotes
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script_text.strip()) if s.strip()]
    items = {}
    for name in ("H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1"):
        passed = (not all_pass) or (name not in fail_items)
        # H1 evidence must be within 20 words Beat1; use first sentence
        q = sents[0] if sents else "Hello."
        items[name] = {"pass": passed,
                       "evidence": [{"quote": q, "beat_id": 1,
                                     "source_paragraph_ids": valid_pids[:1]}]}
        if name == "L1" and not passed:
            items[name]["unsupported"] = ["invented claim"]
    return {"items": items, "failed_beat_ids": [],
            "rewrite_instructions": []}


def test_q8_checklist_verification_and_scoring():
    """Q8: unverifiable quote → None (not counted); scores computed by code."""
    from vsp.gates.tier2.scoring import (verify_checklist_evidence,
                                         checklist_to_scores)
    script_text = "I saw cops at night. Mom paid the bill."
    valid = {0, 1}
    items = {"H1": {"pass": True, "evidence": [{"quote": "I saw cops at night.", "beat_id": 1, "source_paragraph_ids": [0]}]},
             "H2": {"pass": True, "evidence": [{"quote": "Mom paid the bill.", "beat_id": 4, "source_paragraph_ids": [1]}]},
             "P1": {"pass": True, "evidence": [{"quote": "I saw cops at night.", "beat_id": 1, "source_paragraph_ids": [0]}]},
             "P2": {"pass": True, "evidence": [{"quote": "Mom paid the bill.", "beat_id": 2, "source_paragraph_ids": [1]}]},
             "L1": {"pass": True, "evidence": [{"quote": "I saw cops at night.", "beat_id": 1, "source_paragraph_ids": [0]}]},
             "L2": {"pass": False, "evidence": [{"quote": "Mom paid the bill.", "beat_id": 2, "source_paragraph_ids": [1]}]},
             "L3": {"pass": True, "evidence": [{"quote": "I saw cops at night.", "beat_id": 2, "source_paragraph_ids": [0]}]},
             "R1": {"pass": True, "evidence": [{"quote": "Mom paid the bill.", "beat_id": 5, "source_paragraph_ids": [1]}]},
             "S1": {"pass": True, "evidence": [{"quote": "I saw cops at night.", "beat_id": 1, "source_paragraph_ids": [0]}]},
             "V1": {"pass": True, "evidence": [{"quote": "Mom paid the bill.", "beat_id": 1, "source_paragraph_ids": [0]}]},
             "BAD": {"pass": True, "evidence": [{"quote": "never in script", "beat_id": 1, "source_paragraph_ids": [0]}]}}
    ver, errs = verify_checklist_evidence(items, script_text, valid)
    assert ver["H1"] is True and ver["L2"] is False
    assert ver["BAD"] is None  # unverifiable quote discarded
    assert errs
    scores, overall, payoff = checklist_to_scores(ver)
    assert scores["hook_payoff"] == 10.0  # H1+H2 pass
    assert scores["logic"] == round((1 + 0 + 1) / 3 * 10, 2)  # L2 fails
    assert payoff is True


def _setup_checklist_series(vsp_root, fail_items=(), split_l1=False):
    from vsp.orchestrator import do_extract, do_draft
    sd = _setup_series(vsp_root, ep_done=1)
    # use make_passing_script draft (first-person, Q5-clean) with valid pids
    beats = read_json(sd / "work" / "chunk_001" / "beats.json")
    # arc + 3 drafts (best-of-N) to reach audit
    import tests.e2e.test_fullpath as _fp
    pids = read_json(sd / "chunks" / "chunk_001.json")["paragraph_ids"]
    arc = {"setup_beat_ids": [1, 2], "turn_beat_id": 3, "payoff_beat_id": 4,
           "cliffhanger_beat_id": 5, "dropped_beat_ids": [{"beat_id": 6, "reason": "x"}]}
    # fix beats to span chunk (coverage) and carry grounding nouns
    return sd, pids, arc


def test_q9_panel_split_needs_human(vsp_root):
    """Q9: 2-2 split on zero-tolerance L1 → NEEDS_HUMAN (unit, fake judges)."""
    from vsp.gates.tier2.scoring import aggregate_panel, checklist_to_scores, decide_release
    # 4 samples: 2 pass L1, 2 fail L1; all others pass.
    def _s(pass_l1: bool):
        return {"H1": True, "H2": True, "P1": True, "P2": True,
                "L1": pass_l1, "L2": True, "L3": True,
                "R1": True, "S1": True, "V1": True}
    samples = [_s(True), _s(True), _s(False), _s(False)]
    agg, split, panel_split = aggregate_panel(samples)
    assert agg["L1"] is None and "L1" in split and panel_split is True
    scores, overall, payoff = checklist_to_scores(agg)
    # zero-tolerance L1 not passing + panel split → no release
    verdict, reason = decide_release(True, overall, scores, agg, panel_split,
                                     overall_min=8.0, dimension_floor=7.0,
                                     zero_tolerance=["L1", "S1", "V1"])
    assert verdict == "NEEDS_HUMAN", reason


def test_q10_fidelity_contradicted_blocks_release(vsp_root):
    """Q10: chunk_005 'last coin' vs '$20 in coins' → contradicted → no release (unit)."""
    from vsp.gates.tier2.scoring import verify_fidelity
    # selected beats/source contain "$20 in coins"; the script claims "last coin".
    valid = {0, 1}
    fid_bad = {"claims": [{"claim": "I took the last coin.", "verdict": "contradicted",
                           "source_paragraph_ids": [0], "stylistic": False},
                          {"claim": "Mom smiled.", "verdict": "supported",
                           "source_paragraph_ids": [0], "stylistic": True}]}
    summary, errs = verify_fidelity(fid_bad, ["I took the last coin.", "Mom smiled."], valid, max_stylistic=3)
    assert summary["contradicted"] == 1 and not summary["ok"]
    assert not errs
    # release requires contradicted==0 (lock release.contradicted_claims_max=0)
    from vsp.config import load_config
    lock_rel = (load_config(vsp_root).lock.get("release", {}) or {})
    assert int(lock_rel.get("contradicted_claims_max", 0)) == 0


def test_q11_best_of_n_drafts_have_different_hooks(vsp_root):
    """Q11: 3 drafts, each with a different allowed hook; best picked by code."""
    from vsp.orchestrator import do_extract, do_draft
    import shutil
    shutil.copy(REPO / "tests" / "fixtures" / "serial_harbor.txt", vsp_root / "s.txt")
    from vsp.orchestrator import do_ingest, do_chunk
    p = do_ingest(vsp_root, str(vsp_root / "s.txt"), series_id="q11",
                  topology="CONTINUOUS_SERIAL")
    sid = p["series_id"]
    import yaml
    for fn in ("pipeline.yaml", "quality_lock.yaml"):
        fp = vsp_root / "config" / fn
        cfg = yaml.safe_load(fp.read_text())
        if fn == "pipeline.yaml":
            cfg["chunking"].update({"sliding_target": 500, "sliding_min": 400, "sliding_max": 620})
        else:
            cfg.setdefault("chunking", {}).setdefault("serial", {}).update(
                {"sliding_target": 500, "sliding_min": 400, "sliding_max": 620})
        fp.write_text(yaml.safe_dump(cfg))
    chunks = do_chunk(vsp_root, sid)
    pids = chunks[0]["paragraph_ids"]
    beats = {"beats": [{"beat_id": i + 1, "summary_en": f"plot point {i + 1} with cops brother",
                        "characters": [], "source_paragraph_ids": [pids[(i * (len(pids) - 1)) // 5]],
                        "tension": 3, "is_twist": i == 2, "is_cliffhanger_candidate": i == 5,
                        "bizarre_detail": None} for i in range(6)],
             "new_entities": [], "glossary_additions": {}, "genre_tags": ["revenge"]}
    arc = {"setup_beat_ids": [1, 2], "turn_beat_id": 3, "payoff_beat_id": 4,
           "cliffhanger_beat_id": 5, "dropped_beat_ids": [{"beat_id": 6, "reason": "x"}]}
    import json as _j
    base = _j.loads((REPO / "tests/fixtures/scripts/template1_revenge_writer.json").read_text())
    d = [{**base, "hook_option": h} for h in ("Option_1_Pink_Teddy_Bear",
         "Option_2_Out_Of_Context_Confession", "Option_3_Contradiction")]
    fake = FakeLLMClient({("writer", 1): beats, ("writer", 2): arc,
                          ("writer", 3): d[0], ("writer", 4): d[1], ("writer", 5): d[2]})
    do_extract(vsp_root, sid, 1, fake=fake)
    out = do_draft(vsp_root, sid, 1, fake=fake)
    assert out is not None
    import glob as _g
    cands = sorted((vsp_root / "run" / sid / "work" / "chunk_001" / "candidates").glob("draft_*.json"))
    assert len(cands) == 3, [p.name for p in cands]
    hooks = [json.loads(p.read_text()).get("_hook") or json.loads(p.read_text()).get("script_metadata", {}).get("hook_option")
             for p in cands]
    # candidates store _hook (internal) — all three must differ
    assert len(set(hooks)) == 3, hooks
