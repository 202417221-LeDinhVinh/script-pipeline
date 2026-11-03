"""T2: offline full-path e2e — ingest→chunk→extract→draft→audit→evolve→export.

No hand-written beats.json/draft.json: every stage runs for real against a
shared FakeLLMClient. Covers a 3-chunk serial (handover propagation) and a
Reddit-like single shot, plus `vsp run --fake-fixtures` (injectable factory).
"""
from __future__ import annotations
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from vsp.io import read_json
from vsp.llm.fake import FakeLLMClient
from vsp.export.package import verify_series

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures" / "scripts"


def _writer(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _small_chunk_cfg(root: Path) -> None:
    import yaml
    p = root / "config" / "pipeline.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg["chunking"]["sliding_target"] = 500
    cfg["chunking"]["sliding_min"] = 400
    cfg["chunking"]["sliding_max"] = 620
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    # §17.6: locked thresholds win — mirror the small-chunk sizes into the lock.
    lp = root / "config" / "quality_lock.yaml"
    lock = yaml.safe_load(lp.read_text(encoding="utf-8"))
    lock.setdefault("chunking", {}).setdefault("serial", {})\
        .update({"sliding_target": 500, "sliding_min": 400, "sliding_max": 620})
    lp.write_text(yaml.safe_dump(lock), encoding="utf-8")


def _checklist_all_true() -> dict:
    names = ["H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1"]
    return {"items": {n: {"pass": True, "evidence": []} for n in names},
            "failed_beat_ids": [], "rewrite_instructions": []}


def _judge_pass() -> dict:
    return _checklist_all_true()


def _fid_pass() -> dict:
    return {"claims": [{"claim": "x", "verdict": "supported", "source_paragraph_ids": [0]}]}


def _evolve_handover(cliff: str) -> dict:
    return {"rolling_summary": f"After {cliff}.",
            "secrets_ledger": [{"fact": cliff, "known_by_audience": True,
                                "known_by_protagonist": True, "known_by_antagonist": False}],
            "character_updates": [],
            "handover_bridge": {"pending_cliffhanger": cliff,
                                "required_resolution_in_3_to_8s": f"Cops resolved {cliff}.",
                                "ending_emotional_tone": "tense"}}


def _beats(start: int, pids: list[int], names: list[str], new_entities: list[dict],
           genre: str = "revenge") -> dict:
    # R11 coverage fixtures must span the chunk: cite six evenly spaced valid
    # paragraph IDs from first to last rather than six consecutive IDs.
    # Q3 grounding: summaries carry the template's hook nouns so the fixed
    # draft fixtures stay grounded (revenge: cops/brother/trap; mystery: power
    # bill/yard; aita: mom/wedding).
    _ground = {"revenge": "smiled cops cuffed brother night though trap set",
               "mystery": "power bill jumped twelve bucks cops dug yard",
               "thriller": "power bill jumped twelve bucks cops dug yard",
               "aita": "wrong uninviting mom wedding",
               "family": "wrong uninviting mom wedding"}.get(str(genre).lower(), "cops brother")
    beats = []
    for i in range(6):
        idx = (i * (len(pids) - 1)) // 5 if len(pids) > 1 else 0
        beats.append({"beat_id": start + i,
                      "summary_en": f"event {start + i} with {_ground}",
                      "characters": names,
                      "source_paragraph_ids": [pids[idx]],
                      "tension": 3, "is_twist": i == 3,
                      "is_cliffhanger_candidate": i == 5,
                      "bizarre_detail": None})
    return {"beats": beats, "new_entities": new_entities,
            "glossary_additions": {}, "genre_tags": [genre]}


def _arc(start: int) -> dict:
    """Q2 arc fixture: 6 beats in, 5 selected, last dropped with a reason."""
    return {"setup_beat_ids": [start, start + 1], "turn_beat_id": start + 2,
            "payoff_beat_id": start + 3, "cliffhanger_beat_id": start + 4,
            "dropped_beat_ids": [{"beat_id": start + 5, "reason": "side detail"}]}


def test_full_serial_three_chunks(vsp_root):
    """Ingest→…→export on a 3-chunk serial; ep2 draft prompt carries ep1 cliffhanger."""
    from vsp.orchestrator import (do_ingest, do_chunk, do_extract, do_draft,
                                  do_audit, do_evolve, do_export)
    _small_chunk_cfg(vsp_root)
    shutil.copy(REPO / "tests" / "fixtures" / "serial_harbor.txt", vsp_root / "serial.txt")

    p = do_ingest(vsp_root, str(vsp_root / "serial.txt"), series_id="harbor",
                  topology="CONTINUOUS_SERIAL")
    sid = p["series_id"]
    chunks = do_chunk(vsp_root, sid)
    assert len(chunks) == 3, [c["word_equiv"] for c in chunks]

    def _draft_variants(fname, hooks):
        base = _writer(fname)
        return [{**base, "hook_option": h} for h in hooks]

    pids = [c["paragraph_ids"] for c in chunks]
    ent1 = [{"original_name": "Mara Halloway", "proposed_us_name": "Dana Cole",
             "role": "lead detective", "gender": "female", "main_cast": True},
            {"original_name": "Jake", "proposed_us_name": "Jake",
             "role": "suspect", "gender": "male", "main_cast": False},
            {"original_name": "Luis", "proposed_us_name": "Luis",
             "role": "helper", "gender": "male", "main_cast": False}]
    # Q11 best-of-N: 3 drafts per chunk, different allowed hooks.
    # B7/T1-18 (stricter): CTA part numbers must equal episode+1, so patch
    # chunk 2/3 outros from "part two" to the correct next-episode numbers.
    # The 5-gram T1-21 gate (B9) still passes: "part three/four" differ.
    _d1 = _draft_variants("template1_revenge_writer.json",
                          ["Option_1_Pink_Teddy_Bear", "Option_2_Out_Of_Context_Confession",
                           "Option_3_Contradiction"])
    _d2 = _draft_variants("template2_mystery_writer.json",
                          ["Option_2_Out_Of_Context_Confession", "Option_3_Contradiction",
                           "Option_4_Calm_Before_Storm"])
    for _d in _d2:
        _o = _d.get("call_to_action", {}).get("outro_narration", "")
        _d["call_to_action"] = {**_d.get("call_to_action", {}),
                                "outro_narration": _o.replace("part two", "part three")}
        # B6/T1-22 (stricter): markers in <=2 of last 4; strip them from later
        # chunks so identical fixtures do not fail via work/ history.
        for _b in _d.get("scene_beats", []) or []:
            _t = str(_b.get("narration_text", ""))
            for _mk in ("So get this,", "Honestly,", "Look,", "The crazy part,",
                        "So get this", "Honestly", "Look", "The crazy part"):
                _t = _t.replace(_mk, "Then")
            _b["narration_text"] = " ".join(_t.split())
    _d3 = _draft_variants("template1_revenge_writer.json",
                          ["Option_3_Contradiction", "Option_4_Calm_Before_Storm",
                           "Option_1_Pink_Teddy_Bear"])
    for _d in _d3:
        _d["call_to_action"] = {**_d.get("call_to_action", {}),
                                # distinct ending (no 5-gram overlap with chunk 1)
                                "outro_narration": "Tell me whose side you take and follow for part four reveal"}
        # B6/T1-20 (stricter): chunk 3 reuses the template-1 hook text, which
        # would Jaccard-match chunk 1 via work/ history. Use a distinct
        # grounded variant with the same revenge vocabulary.
        for _b in _d.get("scene_beats", []) or []:
            if _b.get("beat_id") == 1:
                _b["narration_text"] = ("Though cops cuffed my brother at night, "
                                        "I smiled as I set the trap")
            else:
                _t = str(_b.get("narration_text", ""))
                for _mk in ("So get this,", "Honestly,", "Look,", "The crazy part,",
                            "So get this", "Honestly", "Look", "The crazy part"):
                    _t = _t.replace(_mk, "Then")
                _b["narration_text"] = " ".join(_t.split())
    _jp1 = _judge_pass()
    _jp2 = _judge_pass()
    _jp3 = _judge_pass()
    scripts = {
        ("writer", 1): _beats(1, pids[0], ["Mara Halloway"], ent1),
        ("writer", 2): _arc(1),
        ("writer", 3): _d1[0], ("writer", 4): _d1[1], ("writer", 5): _d1[2],
        ("writer", 6): _evolve_handover("cliff-one-dock"),
        ("judge", 1): _jp1, ("judge", 2): _jp1, ("judge", 3): _jp1, ("judge", 4): _jp1,
        ("judge", 5): _fid_pass(),
        ("judge", 6): _jp1, ("judge", 7): _jp1, ("judge", 8): _jp1, ("judge", 9): _jp1,
        ("judge", 10): _fid_pass(),
        ("writer", 7): _beats(7, pids[1], ["Mara Halloway"], [], genre="mystery"),
        ("writer", 8): _arc(7),
        ("writer", 9): _d2[0], ("writer", 10): _d2[1], ("writer", 11): _d2[2],
        ("writer", 12): _evolve_handover("cliff-two-cannery"),
        ("judge", 11): _jp2, ("judge", 12): _jp2, ("judge", 13): _jp2, ("judge", 14): _jp2,
        ("judge", 15): _fid_pass(),
        ("judge", 16): _jp2, ("judge", 17): _jp2, ("judge", 18): _jp2, ("judge", 19): _jp2,
        ("judge", 20): _fid_pass(),
        ("writer", 13): _beats(13, pids[2], ["Mara Halloway"], []),
        ("writer", 14): _arc(13),
        ("writer", 15): _d3[0], ("writer", 16): _d3[1], ("writer", 17): _d3[2],
        ("writer", 18): _evolve_handover("cliff-three-trial"),
        ("judge", 21): _jp3, ("judge", 22): _jp3, ("judge", 23): _jp3, ("judge", 24): _jp3,
        ("judge", 25): _fid_pass(),
        ("judge", 26): _jp3, ("judge", 27): _jp3, ("judge", 28): _jp3, ("judge", 29): _jp3,
        ("judge", 30): _fid_pass(),
    }
    fake = FakeLLMClient(scripts)

    for n in (1, 2, 3):
        do_extract(vsp_root, sid, n, fake=fake)
        do_draft(vsp_root, sid, n, fake=fake)
        dec = do_audit(vsp_root, sid, n, fake=fake)
        assert dec["decision"] == "RELEASE", dec
        do_evolve(vsp_root, sid, n, fake=fake)

    # handover: ep2's writer prompts contain ep1's pending cliffhanger
    # (Q11 best-of-N: 3 drafts per chunk)
    draft_calls = [c for c in fake.calls if c["role"] == "writer"
                   and "block_definitions" in json.dumps(c["messages"])]
    assert len(draft_calls) == 9, len(draft_calls)
    assert "cliff-one-dock" in json.dumps(draft_calls[3]["messages"])
    assert "cliff-two-cannery" in json.dumps(draft_calls[6]["messages"])

    paths = do_export(vsp_root, sid)
    assert len(paths) == 3
    assert verify_series(vsp_root, sid) == []
    # F2/F12: persisted per-chunk budgets + chunk/stage-tagged llm calls
    for c in chunks:
        assert (vsp_root / "run" / sid / "work" / c["chunk_id"] / "budget.json").exists()
    import json as _j
    lines = (vsp_root / "run" / sid / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines, "llm calls logged"
    for line in lines:
        e = _j.loads(line)
        assert e.get("chunk_id") and e.get("stage"), e


def test_full_reddit_single_shot(vsp_root):
    """Single Reddit-like file → exactly 1 exported episode, terminal decision."""
    from vsp.orchestrator import (do_ingest, do_chunk, do_extract, do_draft,
                                  do_audit, do_evolve, do_export)
    shutil.copy(REPO / "tests" / "fixtures" / "reddit_aita.txt", vsp_root / "aita.txt")
    p = do_ingest(vsp_root, str(vsp_root / "aita.txt"), series_id="aita1")
    sid = p["series_id"]
    chunks = do_chunk(vsp_root, sid)
    assert len(chunks) == 1 and chunks[0]["is_hard_boundary_end"]

    def _a3(fname):
        b = _writer(fname)
        return [{**b, "hook_option": h} for h in ("Option_1_Pink_Teddy_Bear",
                "Option_2_Out_Of_Context_Confession", "Option_3_Contradiction")]

    pids = chunks[0]["paragraph_ids"]
    _td = _a3("template3_aita_writer.json")
    _jp = _judge_pass()
    fake = FakeLLMClient({
        ("writer", 1): _beats(1, pids, ["Ada"], [
            {"original_name": "Ada", "proposed_us_name": "Amy Lane",
             "role": "narrator", "gender": "female", "main_cast": True}],
            genre="aita"),
        ("writer", 2): _arc(1),
        ("writer", 3): _td[0], ("writer", 4): _td[1], ("writer", 5): _td[2],
        ("writer", 6): _evolve_handover("cliff-aita"),
        ("judge", 1): _jp, ("judge", 2): _jp, ("judge", 3): _jp, ("judge", 4): _jp,
        ("judge", 5): _fid_pass(),
        ("judge", 6): _jp, ("judge", 7): _jp, ("judge", 8): _jp, ("judge", 9): _jp,
        ("judge", 10): _fid_pass(),
    })
    do_extract(vsp_root, sid, 1, fake=fake)
    # template3 fixture uses engagement_verdict: valid on hard boundary
    do_draft(vsp_root, sid, 1, fake=fake)
    dec = do_audit(vsp_root, sid, 1, fake=fake)
    assert dec["decision"] == "RELEASE"
    do_evolve(vsp_root, sid, 1, fake=fake)
    paths = do_export(vsp_root, sid)
    assert len(paths) == 1
    assert verify_series(vsp_root, sid) == []


def test_cmd_run_with_fake_fixtures(vsp_root):
    """`vsp run --inbox --fake-fixtures`: injectable factory path (T2)."""
    import yaml
    from vsp.cli import build_parser
    inbox = vsp_root / "inbox"
    inbox.mkdir(exist_ok=True)
    shutil.copy(REPO / "tests" / "fixtures" / "reddit_aita.txt", inbox / "story.txt")

    # single-shot beats cite paragraph 0 of the single chunk
    from vsp.ingest.loader import split_paragraphs, normalize_text
    raw = (inbox / "story.txt").read_text(encoding="utf-8")
    npara = len(split_paragraphs(normalize_text(raw)))
    assert npara >= 1
    _tb = _writer("template3_aita_writer.json")
    _td = [{**_tb, "hook_option": h} for h in ("Option_1_Pink_Teddy_Bear",
            "Option_2_Out_Of_Context_Confession", "Option_3_Contradiction")]
    _jp = _judge_pass()
    scripts = {
        "writer:1": _beats(1, list(range(npara)), ["Ada"], [
            {"original_name": "Ada", "proposed_us_name": "Amy Lane",
             "role": "narrator", "gender": "female", "main_cast": True}],
            genre="aita"),
        "writer:2": _arc(1),
        "writer:3": _td[0], "writer:4": _td[1], "writer:5": _td[2],
        "writer:6": _evolve_handover("cliff-x"),
        "judge:1": _jp, "judge:2": _jp, "judge:3": _jp, "judge:4": _jp,
        "judge:5": _fid_pass(), "judge:6": _jp, "judge:7": _jp, "judge:8": _jp,
        "judge:9": _jp, "judge:10": _fid_pass(),
    }
    fx = vsp_root / "scripts.json"
    fx.write_text(json.dumps(scripts), encoding="utf-8")

    ap = build_parser()
    args = ap.parse_args(["--root", str(vsp_root), "run", "--inbox", str(inbox),
                          "--fake-fixtures", str(fx)])
    rc = args.fn(args)
    assert rc == 0, rc
    outs = list((vsp_root / "out").rglob("ep_*.json"))
    assert len(outs) == 1, outs
