"""E2E (fake LLM): decision-table rows, blacklist, handover, resume, verify."""
from __future__ import annotations
import copy
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from tests.conftest import make_passing_script

from vsp.io import atomic_write_json, read_json
from vsp.llm.fake import FakeLLMClient


def _setup_series(root: Path, sid="novel_test", n_chunks=1, hard_last=False, ep_done=0):
    from vsp.state.manager import blank_state
    sd = root / "run" / sid
    (sd / "chunks").mkdir(parents=True, exist_ok=True)
    src = {"series_id": sid, "title": "t", "normalized_text": "text " * 100,
           "paragraphs": [{"id": i, "text": f"para {i} text here"} for i in range(10)],
           "source_url_or_path": "data/x.txt", "source_sha256": "abc123", "raw_bytes_sha256": "def",
           "rights_status": "public_domain", "language": "en"}
    atomic_write_json(sd / "00_source_raw.json", {**src, "inputs_sha256": {"source": "abc123"}})
    atomic_write_json(sd / "01_topology_meta.json", {"series_id": sid, "topology": "CONTINUOUS_SERIAL",
                                                     "confidence": 1.0, "source_language": "en", "genre": "revenge",
                                                     "genre_tags": ["revenge"], "word_equiv": 6000, "num_chunks": n_chunks,
                                                     "inputs_sha256": {"source": "abc123"}})
    # B4/T1-15 + B6/T1-20/21/22 (stricter): consecutive hand-written drafts
    # must look like real router output — different template/hook/beat1/CTA
    # per chunk, all grounded, first-person, no 5-gram CTA overlap, markers
    # in <=2 of the last 4. Identical consecutive episodes would now correctly
    # fail T1-20 (history comes from work/, whatever the decision).
    _ground = ("smiled docs pulled plug though donor match purse morgue gurney "
               "envelope dinner empty dawn watched cold slab silence kin stayed near vote "
               "trust reveal side take tell past quick town phone rang")
    _beat1 = {
        1: "I smiled as docs pulled his plug, though I'd got donor match in my purse",
        2: "I saw the morgue gurney empty at dawn while docs hid the envelope",
        3: "I watched the cold slab in silence though kin stayed near dinner",
    }
    _cta = {
        1: ("cliffhanger_subscription",
            "Hit follow for more to learn whose kin lay on that cold slab", "Because"),
        2: ("engagement_verdict",
            "Drop your take below and vote on who you trust now", "Because"),
        3: ("loop_back",
            "Tell me whose side you take and follow for the reveal", "Because"),
    }
    _tpl_hook = {
        1: ("Template_1_In_Medias_Res", "Option_2_Out_Of_Context_Confession"),
        2: ("Template_3_Moral_Dilemma", "Option_1_Pink_Teddy_Bear"),
        3: ("Template_2_Breadcrumb_Mystery", "Option_3_Contradiction"),
    }
    from vsp.draft.router import TEMPLATE_BLOCKS
    for n in range(1, n_chunks + 1):
        k = ((n - 1) % 3) + 1
        chunk = {"chunk_id": f"chunk_{n:03d}", "series_id": sid, "chunk_index": n,
                 "topology_type": "CONTINUOUS_SERIAL", "word_count": 6000, "word_equiv": 6000,
                 "is_arc_start": n == 1, "is_hard_boundary_end": hard_last and n == n_chunks,
                 "state_reset_instruction": "PERSIST", "raw_text_content": "text " * 100,
                 "paragraph_ids": list(range(10)), "clean_cut": True,
                 "inputs_sha256": {"source": "abc123", "topology": "CONTINUOUS_SERIAL"}}
        atomic_write_json(sd / "chunks" / f"chunk_{n:03d}.json", chunk)
        wdir = sd / "work" / f"chunk_{n:03d}"
        wdir.mkdir(parents=True, exist_ok=True)
        beats = {"chunk_id": f"chunk_{n:03d}", "series_id": sid,
                 "beats": [{"beat_id": (n - 1) * 6 + i, "summary_en": f"s{i} {_ground} para text here",
                            "characters": [],
                            "source_paragraph_ids": [i % 10], "tension": 3, "is_twist": i == 4,
                            "is_cliffhanger_candidate": i == 5, "bizarre_detail": None} for i in range(1, 7)],
                 "new_entities": [], "glossary_additions": {}, "genre_tags": ["revenge"],
                 "inputs_sha256": {}}
        atomic_write_json(wdir / "beats.json", beats)
        tpl, hook = _tpl_hook[k]
        cta_type, cta_outro, cta_loop = _cta[k]
        draft = make_passing_script(template=tpl, hook=hook, cta=cta_type)
        draft["script_metadata"]["series_id"] = sid
        draft["script_metadata"]["pacing_template"] = tpl
        draft["script_metadata"]["hook_option"] = hook
        # distinct grounded first-person Beat 1 per chunk (T1-20)
        draft["scene_beats"][0]["narration_text"] = _beat1[k]
        for _b, _blk in zip(draft["scene_beats"], TEMPLATE_BLOCKS[tpl]):
            _b["block_type"] = _blk
            _b.setdefault("timestamp_est", "00:00 - 00:10")
        draft["call_to_action"] = {"type": cta_type, "outro_narration": cta_outro,
                                   "loop_connector_word": cta_loop}
        # markers only in chunk 1 (T1-22 <=2 of last 4); later chunks use plain transitions
        if k != 1:
            for _b in draft["scene_beats"][1:]:
                t = str(_b.get("narration_text", ""))
                for _mk in ("So get this,", "Honestly,", "Look,", "The crazy part,"):
                    if t.startswith(_mk):
                        t = "Then," + t[len(_mk):]
                        _b["narration_text"] = t.strip()
        draft["inputs_sha256"] = {}
        draft["_template"] = tpl
        atomic_write_json(wdir / "draft.json", draft)
    st = blank_state(sid)
    st["current_episode"] = ep_done
    atomic_write_json(sd / "series_state.json", st)
    return sd


def _items(all_true=True, false_items=()):
    names = ["H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1"]
    out = {}
    for n in names:
        out[n] = {"pass": (n not in false_items), "evidence": []}
    return out


def _judge(scores, resolved=True, failed=(), instr=()):
    # C1 (stricter): legacy scores fixtures move to the checklist format.
    # Map old dimensions to checklist items (empty evidence verifies cleanly).
    # Hook>=8 -> H1,H2 True; 5-8 -> H1 True,H2 False; <5 -> both False. Same for
    # pacing (P1,P2), logic (L1,L2,L3, need >=2/3 for >=7), retention (R1).
    # This preserves PASS (>=8), REWRITE ([5,8)), REJECT (<5) buckets.
    hp = float(scores.get("hook_payoff", 0))
    pa = float(scores.get("pacing", 0))
    lo = float(scores.get("logic", 0))
    re_ = float(scores.get("retention", 0))
    false = set()
    if hp < 8:
        false.add("H2")
    if hp < 5:
        false.add("H1")
    if pa < 7:
        false.add("P2")
    if pa < 4:
        false.add("P1")
    if lo < 7:
        false.add("L3")
    if lo < 5:
        false.add("L2")
    if lo < 3:
        false.add("L1")
    if re_ < 5:
        false.add("R1")
    # honor explicit failed/instr (beat-mapped) by also failing the matching item
    _beat_to_item = {1: "H1", 2: "L3", 3: "P1", 4: "H2", 5: "R1"}
    for b in list(failed) + [i.get("beat_id") for i in (instr or []) if isinstance(i, dict)]:
        try:
            if int(b) in _beat_to_item:
                false.add(_beat_to_item[int(b)])
        except (TypeError, ValueError):
            pass
    return {"items": _items(True, false),
            "failed_beat_ids": list(failed),
            "rewrite_instructions": [{"beat_id": b, "issue": "x", "instruction": "fix it"} for b in instr]}


def _fid():
    return {"claims": [{"claim": "x", "verdict": "supported", "source_paragraph_ids": [0]}]}


def _rewrite_for(wdir_draft):
    d = json.loads(json.dumps(wdir_draft))
    beats = [{**b, "narration_text": b["narration_text"]} for b in d["scene_beats"]]
    return {"scene_beats": [b for b in beats if b["beat_id"] in (2,)], }


def test_decision_pass(vsp_root):
    from vsp.orchestrator import do_audit
    sd = _setup_series(vsp_root)
    _j = _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})
    fake = FakeLLMClient({("judge", 1): _j, ("judge", 2): _j, ("judge", 3): _j,
                          ("judge", 4): _j, ("judge", 5): _fid()})
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "RELEASE" and dec["episode_number"] == 1


def test_decision_rewrite_then_pass(vsp_root):
    from vsp.orchestrator import do_audit
    sd = _setup_series(vsp_root)
    draft = read_json(sd / "work" / "chunk_001" / "draft.json")
    # C1/C4 (stricter): failing items flag all beats, so the rewrite returns all 5.
    # Round 1 overall 7.0 (rewrite), round 2 overall 10 (release).
    all_beats = [dict(b) for b in draft["scene_beats"]]
    def _r1():
        return {"items": {"H1": {"pass": True, "evidence": []}, "H2": {"pass": True, "evidence": []},
                           "P1": {"pass": True, "evidence": []}, "P2": {"pass": False, "evidence": []},
                           "L1": {"pass": True, "evidence": []}, "L2": {"pass": True, "evidence": []},
                           "L3": {"pass": True, "evidence": []}, "R1": {"pass": False, "evidence": []},
                           "S1": {"pass": True, "evidence": []}, "V1": {"pass": True, "evidence": []}},
                "failed_beat_ids": [1, 2, 3, 4, 5],
                "rewrite_instructions": [{"beat_id": b, "issue": "P2,R1", "instruction": "fix it"} for b in [1, 2, 3, 4, 5]]}
    def _r2():
        names = ["H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1"]
        return {"items": {n: {"pass": True, "evidence": []} for n in names},
                "failed_beat_ids": [], "rewrite_instructions": []}
    _jr, _jp = _r1(), _r2()
    fake = FakeLLMClient({
        ("judge", 1): _jr, ("judge", 2): _jr, ("judge", 3): _jr, ("judge", 4): _jr,
        ("judge", 5): _fid(),
        ("writer", 1): {"scene_beats": all_beats},
        ("judge", 6): _jp, ("judge", 7): _jp, ("judge", 8): _jp, ("judge", 9): _jp,
        ("judge", 10): _fid(),
    })
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "RELEASE" and dec["rewrite_count"] == 1


def test_decision_reject_standalone(vsp_root):
    from vsp.orchestrator import do_audit
    sd = _setup_series(vsp_root)
    _j = _judge({"hook_payoff": 2, "pacing": 2, "logic": 2, "retention": 2}, failed=[1, 2], instr=[1, 2])
    fake = FakeLLMClient({
        ("judge", 1): _j, ("judge", 2): _j, ("judge", 3): _j, ("judge", 4): _j,
        ("judge", 5): _fid(),
        ("writer", 1): {"scene_beats": [dict(read_json(sd / "work/chunk_001/draft.json")["scene_beats"][0]),
                                        dict(read_json(sd / "work/chunk_001/draft.json")["scene_beats"][1])]},
        ("writer", 2): {"scene_beats": [dict(read_json(sd / "work/chunk_001/draft.json")["scene_beats"][0])]},
        ("writer", 3): {"summary": "Mark lost cash and fled town fast."},
    })
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    # standalone (ep 0): score<5 at first audit → hard REJECT, no rewrite consumed
    assert dec["decision"] == "REJECT", dec
    assert dec["rewrite_count"] == 0, dec
    # score < 5 at first audit triggers no rewrite: zero writer calls
    assert [c for c in fake.calls if c["role"] == "writer"] == [], fake.calls


def test_decision_forced_pass_mid_series(vsp_root):
    from vsp.orchestrator import do_audit
    sd = _setup_series(vsp_root, ep_done=2)
    draft = read_json(sd / "work" / "chunk_001" / "draft.json")
    all_beats = [dict(b) for b in draft["scene_beats"]]
    _jr = _judge({"hook_payoff": 8, "pacing": 7, "logic": 7, "retention": 7}, failed=[3], instr=[3])
    _jp = _judge({"hook_payoff": 8, "pacing": 7, "logic": 7, "retention": 7})
    fake = FakeLLMClient({
        ("judge", 1): _jr, ("judge", 2): _jr, ("judge", 3): _jr, ("judge", 4): _jr,
        ("judge", 5): _fid(),
        ("writer", 1): {"scene_beats": all_beats},
        ("judge", 6): _jr, ("judge", 7): _jr, ("judge", 8): _jr, ("judge", 9): _jr,
        ("judge", 10): _fid(),
        ("writer", 2): {"scene_beats": all_beats},
        ("judge", 11): _jp, ("judge", 12): _jp, ("judge", 13): _jp, ("judge", 14): _jp,
        ("judge", 15): _fid(),
    })
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    # overall = (16+7+7+7)/5 = 7.4 -> mid-series near-miss is NEEDS_HUMAN, never
    # FORCED_PASS (Q13: forced pass removed from export; human reviews instead).
    assert dec["decision"] == "NEEDS_HUMAN", dec
    assert dec["episode_number"] is None
    from pathlib import Path as _P
    assert (_P(vsp_root) / "review" / "novel_test" / "chunk_001.json").exists()


def test_decision_fast_forward_merge(vsp_root):
    from vsp.orchestrator import do_audit
    sd = _setup_series(vsp_root, ep_done=2)
    draft = read_json(sd / "work" / "chunk_001" / "draft.json")
    all_beats = [dict(b) for b in draft["scene_beats"]]
    def _jf():
        return {"items": {"H1": {"pass": True, "evidence": []}, "H2": {"pass": False, "evidence": []},
                           "P1": {"pass": True, "evidence": []}, "P2": {"pass": False, "evidence": []},
                           "L1": {"pass": True, "evidence": []}, "L2": {"pass": True, "evidence": []},
                           "L3": {"pass": False, "evidence": []}, "R1": {"pass": True, "evidence": []},
                           "S1": {"pass": True, "evidence": []}, "V1": {"pass": True, "evidence": []}},
                "failed_beat_ids": [1, 2, 3, 4, 5],
                "rewrite_instructions": [{"beat_id": b, "issue": "x", "instruction": "fix it"} for b in [1, 2, 3, 4, 5]]}
    _j = _jf()
    _ff_summary = "Mark lost cash and fled town fast."
    fake = FakeLLMClient({
        ("judge", 1): _j, ("judge", 2): _j, ("judge", 3): _j, ("judge", 4): _j,
        ("judge", 5): _fid(),
        # C1 panel migration: writer fixtures carry both beats and summary so the
        # test is robust to rewrite-count shifts; assertions (decision + file) unchanged.
        ("writer", 1): {"scene_beats": all_beats, "summary": _ff_summary},
        ("judge", 6): _j, ("judge", 7): _j, ("judge", 8): _j, ("judge", 9): _j,
        ("judge", 10): _fid(),
        ("writer", 2): {"scene_beats": all_beats, "summary": _ff_summary},
        ("judge", 11): _j, ("judge", 12): _j, ("judge", 13): _j, ("judge", 14): _j,
        ("judge", 15): _fid(),
        ("writer", 3): {"scene_beats": all_beats, "summary": _ff_summary},
    })
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "FAST_FORWARD_MERGE"
    assert (sd / "work" / "chunk_001" / "fast_forward.json").exists()


def test_blacklist_after_two_consecutive_failures(vsp_root):
    from vsp.orchestrator import do_audit, series_decisions
    from vsp.config import load_config
    from vsp.breakers.policies import series_should_blacklist
    sd = _setup_series(vsp_root, n_chunks=2)
    cfg = load_config(vsp_root)
    for n in (1, 2):
        draft = read_json(sd / f"work/chunk_{n:03d}/draft.json")
        _j = _judge({"hook_payoff": 2, "pacing": 2, "logic": 2, "retention": 2}, failed=[1], instr=[1])
        fake = FakeLLMClient({
            ("judge", 1): _j, ("judge", 2): _j, ("judge", 3): _j, ("judge", 4): _j,
            ("judge", 5): _fid(),
            ("writer", 1): {"scene_beats": [dict(draft["scene_beats"][0])]},
            ("judge", 6): _j, ("judge", 7): _j, ("judge", 8): _j, ("judge", 9): _j,
            ("judge", 10): _fid(),
            ("writer", 2): {"scene_beats": [dict(draft["scene_beats"][0])]},
            ("writer", 3): {"summary": "Mark lost cash and fled town fast."},
        })
        do_audit(vsp_root, "novel_test", n, fake=fake)
    decs, scores = series_decisions(vsp_root, "novel_test")
    trig, _ = series_should_blacklist(decs, scores, cfg)
    assert trig


def test_serial_handover_and_export_verify(vsp_root):
    from vsp.orchestrator import do_audit, do_evolve, do_export
    from vsp.export.package import verify_series
    from vsp.llm.fake import FakeLLMClient
    sd = _setup_series(vsp_root, n_chunks=2)
    # B6 (stricter): _setup_series already rotates chunk_002 to Template_3/
    # Option_1 with a distinct Beat 1/CTA, so the handover chain passes T1-20/21.
    for n in (1, 2):
        _j = _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})
        fake = FakeLLMClient({("judge", 1): _j, ("judge", 2): _j, ("judge", 3): _j,
                              ("judge", 4): _j, ("judge", 5): _fid(),
                              ("writer", 1): {"rolling_summary": f"sum{n}", "secrets_ledger": [],
                                              "character_updates": [],
                                              "handover_bridge": {"pending_cliffhanger": f"cliff{n}",
                                                                  "required_resolution_in_3_to_8s": f"resolve{n}",
                                                                  "ending_emotional_tone": "tense"}}})
        dec = do_audit(vsp_root, "novel_test", n, fake=fake)
        assert dec["decision"] == "RELEASE"
        # evolve uses writer call #2; add it
        fake2 = FakeLLMClient({("writer", 1): {"rolling_summary": f"sum{n}", "secrets_ledger": [],
                                               "character_updates": [],
                                               "handover_bridge": {"pending_cliffhanger": f"cliff{n}",
                                                                   "required_resolution_in_3_to_8s": f"resolve{n}",
                                                                   "ending_emotional_tone": "tense"}}})
        do_evolve(vsp_root, "novel_test", n, fake=fake2)
    paths = do_export(vsp_root, "novel_test")
    assert len(paths) == 2
    assert verify_series(vsp_root, "novel_test") == []
    # tamper detection
    p = paths[0]
    p.write_text(p.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert verify_series(vsp_root, "novel_test") != []


def test_resume_recomputes_nothing_fresh(vsp_root):
    from vsp.orchestrator import do_audit
    sd = _setup_series(vsp_root)
    _j = _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})
    fake = FakeLLMClient({("judge", 1): _j, ("judge", 2): _j, ("judge", 3): _j,
                          ("judge", 4): _j, ("judge", 5): _fid()})
    d1 = do_audit(vsp_root, "novel_test", 1, fake=fake)
    mtime = (sd / "work" / "chunk_001" / "decision.json").stat().st_mtime
    fake2 = FakeLLMClient({})
    d2 = do_audit(vsp_root, "novel_test", 1, fake=fake2)  # resume: no LLM needed
    assert d2 == d1
