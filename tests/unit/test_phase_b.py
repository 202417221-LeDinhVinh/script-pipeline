"""Phase B proofs (§17 Q1–Q7). No story text is typed; all narratives come from
existing fixtures or synthetic filler (word counts, headings, placeholders)."""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tests.conftest import make_passing_script, REPO
from tests.e2e.test_pipeline import _setup_series, _judge
from vsp.io import read_json, atomic_write_json
from vsp.llm.fake import FakeLLMClient


def _ctx(**kw):
    base = {"selected_text": None, "narration_lock": {}, "allowed_names": None,
            "expected_episode": None, "prior_hooks": [], "prior_ctas": [],
            "episode_texts": [], "minor_present": False}
    base.update(kw)
    return base


def test_q1_fourteen_chapter_fixture_chunks_in_range():
    """Q1: 14-chapter synthetic fixture → chunks in [2200, 3800]."""
    from vsp.chunking.boundary import chunk_source
    texts = []
    for n in range(1, 15):
        texts.append(f"Chapter {n}")
        # many small paragraphs per chapter so clean cuts land in range
        for _ in range(50):
            texts.append("word " * 50)  # 50 words x50 = 2500 per chapter
    paras = [{"id": i, "text": t} for i, t in enumerate(texts)]
    src = {"series_id": "s", "paragraphs": paras, "normalized_text": "\n\n".join(texts)}
    chunks = chunk_source(src, "CONTINUOUS_SERIAL", "en", {"default": 1.0},
                          target=3000, smin=2200, smax=3800, window_words=300)
    assert len(chunks) >= 3
    for c in chunks:
        assert 2200 <= c["word_equiv"] <= 3800, (c["chunk_id"], c["word_equiv"])


def test_q2_arc_selection_and_prompt_contains_only_selected(vsp_root):
    """Q2: arc picks 1 conflict (≤5 beats); writer prompt sees only selected."""
    from vsp.orchestrator import do_extract, do_draft, select_arc
    from vsp.io import read_json as _r
    import shutil
    shutil.copy(REPO / "tests" / "fixtures" / "serial_harbor.txt", vsp_root / "s.txt")
    from vsp.orchestrator import do_ingest, do_chunk
    p = do_ingest(vsp_root, str(vsp_root / "s.txt"), series_id="arc1",
                  topology="CONTINUOUS_SERIAL")
    sid = p["series_id"]
    # force a single small chunk for speed
    import yaml
    for fn, val in (("pipeline.yaml", {"chunking": {"sliding_target": 500, "sliding_min": 400, "sliding_max": 620}}),
                    ("quality_lock.yaml", {"chunking": {"serial": {"sliding_target": 500, "sliding_min": 400, "sliding_max": 620}}})):
        fp = vsp_root / "config" / fn
        cfg = yaml.safe_load(fp.read_text())
        if fn == "pipeline.yaml":
            cfg["chunking"].update(val["chunking"])
        else:
            cfg.setdefault("chunking", {}).setdefault("serial", {}).update(val["chunking"]["serial"])
        fp.write_text(yaml.safe_dump(cfg))
    chunks = do_chunk(vsp_root, sid)
    pids = chunks[0]["paragraph_ids"]
    beats = {"beats": [{"beat_id": i + 1, "summary_en": f"plot point {i + 1}",
                        "characters": [],
                        "source_paragraph_ids": [pids[(i * (len(pids) - 1)) // 5] if len(pids) > 1 else pids[0]],
                        "tension": 3, "is_twist": i == 2,
                        "is_cliffhanger_candidate": i == 5,
                        "bizarre_detail": None} for i in range(6)],
             "new_entities": [], "glossary_additions": {}, "genre_tags": ["revenge"]}
    arc_fix = {"setup_beat_ids": [1, 2], "turn_beat_id": 3, "payoff_beat_id": 4,
               "cliffhanger_beat_id": 5,
               "dropped_beat_ids": [{"beat_id": 6, "reason": "side detail"}]}
    from vsp.orchestrator import _workdir
    # hand-write beats via extract fake (writer:1), then arc (writer:2),
    # then Q11 best-of-N 3 drafts (writer:3-5)
    _tpl = json.loads((REPO / "tests/fixtures/scripts/template1_revenge_writer.json").read_text())
    _d = [{**_tpl, "hook_option": h} for h in ("Option_1_Pink_Teddy_Bear",
           "Option_2_Out_Of_Context_Confession", "Option_3_Contradiction")]
    fake = FakeLLMClient({("writer", 1): beats, ("writer", 2): arc_fix,
                          ("writer", 3): _d[0], ("writer", 4): _d[1], ("writer", 5): _d[2],
                          ("judge", 1): {"scores": {"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9},
                                         "payoff_resolved": True, "knowledge_violations": [],
                                         "failed_beat_ids": [], "rewrite_instructions": []}})
    do_extract(vsp_root, sid, 1, fake=fake)
    d = do_draft(vsp_root, sid, 1, fake=fake)
    assert (vsp_root / "run" / sid / "work" / "chunk_001" / "arc.json").exists()
    # writer prompt (draft calls) contains selected beats but not the dropped one
    draft_calls = [c for c in fake.calls if c["role"] == "writer" and "block_definitions" in json.dumps(c["messages"])]
    assert len(draft_calls) == 3, len(draft_calls)
    blob = json.dumps(draft_calls[0]["messages"])
    assert "plot point 1" in blob and "plot point 5" in blob
    # dropped beat 6 must not be in the writer's chunk_beats (only selected 1-5)
    assert "plot point 6" not in blob


def test_q2_invalid_arc_refused(vsp_root):
    from vsp.llm.validate import validate_arc_selector
    assert validate_arc_selector({"setup_beat_ids": [1, 2], "turn_beat_id": 3,
                                  "payoff_beat_id": 4, "cliffhanger_beat_id": 5,
                                  "dropped_beat_ids": []}, {1, 2, 3, 4, 5}) == []
    # too many selected
    assert validate_arc_selector({"setup_beat_ids": [1, 2, 3], "turn_beat_id": 4,
                                  "payoff_beat_id": 5, "cliffhanger_beat_id": 6,
                                  "dropped_beat_ids": []}, {1, 2, 3, 4, 5, 6})
    # unknown id + missing reason
    assert validate_arc_selector({"setup_beat_ids": [99], "turn_beat_id": 1,
                                  "payoff_beat_id": 2, "cliffhanger_beat_id": 3,
                                  "dropped_beat_ids": [{"beat_id": 4}]}, {1, 2, 3, 4})


def test_q3_prompt_has_no_internal_hook_names():
    txt = (REPO / "prompts" / "script_writer.md").read_text()
    for banned in ("Pink_Teddy_Bear", "Out_Of_Context_Confession", "Contradiction", "Calm_Before_Storm",
                   "Option_1_", "Option_2_", "Option_3_", "Option_4_"):
        assert banned not in txt, banned
    assert "<object>" in txt and "<number>" in txt


def test_q3_hook_grounding_gate():
    from vsp.gates.tier1.checks import check_hook_grounding
    sel = "event with cops brother trap night and power bill yard"
    ok_script = {"scene_beats": [{"beat_id": 1, "narration_text": "I saw cops at night."}], "call_to_action": {"outro_narration": ""}}
    # no caps/numbers -> passes (lowercase singletons ignored)
    assert check_hook_grounding(ok_script, sel)["pass"]
    bad_script = {"scene_beats": [{"beat_id": 1, "narration_text": "I saw Pink Teddy at dawn."}], "call_to_action": {"outro_narration": ""}}
    r = check_hook_grounding(bad_script, sel)
    assert not r["pass"] and r["id"] == "T1-15", r


def test_q4_voice_lock_gate():
    from vsp.gates.tier1.checks import check_voice_lock
    first = {"scene_beats": [{"beat_id": i, "narration_text": "I saw my brother and I took my bag."} for i in range(1, 6)],
             "call_to_action": {"outro_narration": "I will follow my plan."}}
    lock = {"pov": "first_person:Amy", "tense": "past"}
    assert check_voice_lock(first, lock)["pass"]
    # ep-4-style flip to third person in a first-person series -> fail
    third = {"scene_beats": [{"beat_id": i, "narration_text": "She walked home as he watched her bag."} for i in range(1, 6)],
             "call_to_action": {"outro_narration": "She will follow her plan."}}
    r = check_voice_lock(third, lock)
    assert not r["pass"] and r["id"] == "T1-16", r
    # third-person lock forbids I/my outside quotes
    tlock = {"pov": "third_person", "tense": "past"}
    assert check_voice_lock(third, tlock)["pass"]
    assert not check_voice_lock(first, tlock)["pass"]


def test_q5_name_episode_number_gates():
    from vsp.gates.tier1.checks import check_name_continuity, check_episode_mention, check_number_grounding
    allowed = {"amy", "lane", "mom", "dad"}
    good = {"scene_beats": [{"beat_id": 1, "narration_text": "Amy saw Mom at noon."}], "call_to_action": {"outro_narration": "Bye."}}
    assert check_name_continuity(good, allowed)["pass"]
    bad = {"scene_beats": [{"beat_id": 1, "narration_text": "Jake met Luis at noon."}], "call_to_action": {"outro_narration": "Bye."}}
    assert not check_name_continuity(bad, allowed)["pass"]
    # episode mention (B7 stricter: number words and ordinals also match)
    ep_ok = {"scene_beats": [{"beat_id": 1, "narration_text": "Hello."}], "call_to_action": {"outro_narration": "Follow for part 5 votes."}}
    assert check_episode_mention(ep_ok, 5)["pass"]
    ep_bad = {"scene_beats": [{"beat_id": 1, "narration_text": "Hello part four fans."}], "call_to_action": {"outro_narration": "Bye."}}
    # B7: 'part four' (words) now fails when it names the wrong episode
    assert not check_episode_mention(ep_bad, 5)["pass"]
    ep_bad2 = {"scene_beats": [{"beat_id": 1, "narration_text": "Hello part 4 fans."}], "call_to_action": {"outro_narration": "Bye."}}
    assert not check_episode_mention(ep_bad2, 5)["pass"]
    # numbers
    sel = "the bill was $20 and 3 items"
    num_ok = {"scene_beats": [{"beat_id": 1, "narration_text": "I paid $20 today."}], "call_to_action": {"outro_narration": ""}}
    assert check_number_grounding(num_ok, sel)["pass"]
    num_bad = {"scene_beats": [{"beat_id": 1, "narration_text": "I paid $25 today."}], "call_to_action": {"outro_narration": ""}}
    assert not check_number_grounding(num_bad, sel)["pass"]


def test_q6_anti_template_gates():
    from vsp.gates.tier1.checks import check_hook_trigram, check_cta_first_words, check_discourse_markers
    # T1-20: identical hooks -> Jaccard 1.0 > 0.35
    cur = {"scene_beats": [{"beat_id": 1, "narration_text": "I saw cops cuff my brother at night."}], "call_to_action": {"outro_narration": ""}}
    assert not check_hook_trigram(cur, ["I saw cops cuff my brother at night."])["pass"]
    assert check_hook_trigram(cur, ["Completely different words about oceans here."])["pass"]
    # T1-21: CTA first-5 repeat (5 live episodes all open the same way)
    priors = ["Follow so you don't miss part two.", "Follow so you don't miss part three.", "Follow so you don't miss part four."]
    cur_cta = {"scene_beats": [{"beat_id": 1, "narration_text": "Hi."}], "call_to_action": {"outro_narration": "Follow so you don't miss part five."}}
    assert not check_cta_first_words(cur_cta, priors)["pass"]
    cur_cta2 = {"scene_beats": [{"beat_id": 1, "narration_text": "Hi."}], "call_to_action": {"outro_narration": "Drop your take below now."}}
    assert check_cta_first_words(cur_cta2, priors)["pass"]
    # T1-22: marker in >2 of last 4
    hist = ["So get this, I ran.", "So get this, I hid.", "So get this, I fled."]
    cur_m = {"scene_beats": [{"beat_id": 1, "narration_text": "So get this, I stayed."}], "call_to_action": {"outro_narration": ""}}
    assert not check_discourse_markers(cur_m, hist)["pass"]
    assert check_discourse_markers(cur_m, ["I ran.", "I hid."])["pass"]


def test_q7_minor_safety_gate_and_reject(vsp_root):
    from vsp.gates.tier1.checks import check_minor_safety
    from vsp.config import load_config
    cfg = load_config(REPO)
    lex = cfg.minor_unsafe
    assert lex
    # Auditor-owned lexicon (do not edit): use its own terms for the proof.
    _term = next((t for t in lex if " " not in t and len(t) > 3), lex[0])
    script_bad = {"scene_beats": [{"beat_id": 1, "narration_text": f"The {_term} kid smiled at me."}], "call_to_action": {"outro_narration": ""}}
    assert not check_minor_safety(script_bad, True, lex)["pass"], _term
    assert check_minor_safety(script_bad, False, lex)["pass"]
    script_ok = {"scene_beats": [{"beat_id": 1, "narration_text": "The old ledger sat on the desk."}], "call_to_action": {"outro_narration": ""}}
    assert check_minor_safety(script_ok, True, lex)["pass"]
    # REJECT_SAFETY via the pipeline: minor (13) + unsafe lexicon in draft
    sd = _setup_series(vsp_root, ep_done=1)
    st = read_json(sd / "series_state.json")
    st["character_registry"] = [{"original_name": "Kid", "us_name": "Kyle", "main_cast": True,
                                 "current_status": "x", "tts_voice_id": "v1", "age_or_stage": "13"}]
    atomic_write_json(sd / "series_state.json", st)
    d = read_json(sd / "work" / "chunk_001" / "draft.json")
    d["scene_beats"][0]["narration_text"] = f"The {_term} kid smiled at me today folks"
    atomic_write_json(sd / "work" / "chunk_001" / "draft.json", d)
    # beats cite the minor so minor_present is true
    b = read_json(sd / "work" / "chunk_001" / "beats.json")
    for beat in b["beats"]:
        beat["characters"] = ["Kid"]
    atomic_write_json(sd / "work" / "chunk_001" / "beats.json", b)
    fake = FakeLLMClient({})
    from vsp.orchestrator import do_audit
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "REJECT_SAFETY", dec


def test_q7_safety_blacklist_after_two(vsp_root):
    from vsp.breakers.policies import series_should_blacklist
    from vsp.config import load_config
    cfg = load_config(vsp_root)
    trig, reason = series_should_blacklist(["REJECT_SAFETY", "REJECT_SAFETY"], [0.0, 0.0], cfg)
    assert trig and "minor_safety" in reason
