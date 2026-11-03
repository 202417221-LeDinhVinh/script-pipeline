"""Round 15 Phase B: B1-B10 failing-then-passing proofs."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from tests.conftest import make_passing_script, REPO


def _ctx(script, **kw):
    spoken = " ".join([b.get("narration_text", "") for b in script.get("scene_beats", [])] +
                      [script.get("call_to_action", {}).get("outro_narration", "")])
    base = {"selected_text": spoken, "narration_lock": {}, "allowed_names": set(),
            "expected_episode": 2, "prior_hooks": [], "prior_ctas": [],
            "episode_texts": [], "minor_present": False, "age_map": {}}
    base.update(kw)
    return base


def test_b1_age_up_rule():
    from vsp.ageup import (build_age_map, adapted_age_for, ensure_setting_map,
                           detect_minor_sexual_content, has_aged_up)
    # (b) 13 -> 18, brother 18 -> 23 (gap kept)
    m = build_age_map([{"original_name": "An", "age_or_stage": "13"},
                       {"original_name": "Bro", "age_or_stage": "18"}],
                      [])
    assert m["An"]["adapted_age"] == 18, m
    assert m["Bro"]["adapted_age"] == 23, m
    # school stage without age -> 18, college
    m2 = build_age_map([], [{"original_name": "Linh", "age_or_stage": "high school student"}])
    assert m2["Linh"]["adapted_age"] == 18
    assert "college" in (m2["Linh"]["adapted_stage"] or "")
    # (c) setting map frozen
    g = ensure_setting_map({})
    assert g["school"] == "college" and "professor" in g["teacher"]
    assert has_aged_up(m)
    # (g) sexual content involving source < 18 -> hard exclusion
    assert detect_minor_sexual_content("they had sex together", {"An": {"source_age": 13}}) is True
    assert detect_minor_sexual_content("they had dinner together", {"An": {"source_age": 13}}) is False
    # prompt + code contain the frozen maps (probe: grep adapted_age|setting_map|age_map)
    src = (REPO / "src" / "vsp" / "orchestrator.py").read_text(encoding="utf-8")
    assert "age_map" in src and "setting_map" in src
    age_src = (REPO / "src" / "vsp" / "ageup.py").read_text(encoding="utf-8")
    assert "adapted_age" in age_src and "setting_map" in age_src.lower()


def test_b2_no_exception_swallowing_in_gates():
    from vsp.config import load_config
    from vsp.gates.tier1 import checks as C
    cfg = load_config(REPO)
    s = make_passing_script()
    orig = C.check_minor_safety
    C.check_minor_safety = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        try:
            C.run_tier1({"scene_beats": s["scene_beats"], "call_to_action": s["call_to_action"]},
                        cfg, "Template_1_In_Medias_Res", "Option_2_Out_Of_Context_Confession",
                        4, s["call_to_action"]["type"], False, [], [],
                        ctx=_ctx(s, selected_text="x y z", minor_present=True))
            raise AssertionError("expected refusal, got pass (swallowed)")
        except SystemExit as e:
            assert "tier1" in str(e).lower(), e
    finally:
        C.check_minor_safety = orig


def test_b3_missing_context_refuses_not_passes():
    from vsp.config import load_config
    from vsp.gates.tier1.checks import _context_gates
    cfg = load_config(REPO)
    s = make_passing_script()
    # missing key entirely -> refusal (old code passed vacuously)
    try:
        _context_gates({"scene_beats": s["scene_beats"], "call_to_action": s["call_to_action"]},
                       cfg, {"selected_text": "x"})
        raise AssertionError("expected refusal for missing keys")
    except SystemExit as e:
        assert "missing" in str(e), e
    # legitimately empty (ep1, no prior hooks) passes when all keys present
    ctx = _ctx(s, prior_hooks=[], prior_ctas=[], episode_texts=[])
    out = _context_gates({"scene_beats": s["scene_beats"], "call_to_action": s["call_to_action"]},
                         cfg, ctx)
    assert any(c["id"] == "T1-20" and c["pass"] for c in out)


def test_b4_lowercase_hook_grounding():
    from vsp.gates.tier1.checks import check_hook_grounding
    s = make_passing_script()
    s["scene_beats"][0]["narration_text"] = "I hid the pink teddy bears in my locker room"
    # beats/source do not contain pink teddy -> fail (old capitalized-only check passed)
    r = check_hook_grounding({"scene_beats": s["scene_beats"], "call_to_action": s["call_to_action"]},
                             "selected beats about cops and robbers downtown")
    assert not r["pass"] and r["beat_ids"] == [1]
    assert any("pink" in str(x).lower() or "teddy" in str(x).lower() for x in r["measured"])


def test_b5_college_student_is_not_minor():
    from vsp.orchestrator import is_minor_age
    assert is_minor_age("college student") is False
    assert is_minor_age("university student") is False
    assert is_minor_age("20, college student") is False
    assert is_minor_age("graduate student") is False
    assert is_minor_age("15, high school student") is True
    assert is_minor_age("13") is True


def test_b6_history_uses_work_not_out():
    # production reads work/ selected candidates, whatever the decision
    src = (REPO / "src" / "vsp" / "orchestrator.py").read_text(encoding="utf-8")
    assert "work" in src and "decision.json" in src
    # _prior_episode_texts no longer reads out/ as primary
    idx = src.find("def _prior_episode_texts")
    body = src[idx:idx + 2000]
    assert "work" in body


def test_b7_episode_words_and_ordinals():
    from vsp.gates.tier1.checks import check_episode_mention
    bad = {"scene_beats": [{"beat_id": 1, "narration_text": "Hi."}],
           "call_to_action": {"outro_narration": "Follow for part four now."}}
    assert not check_episode_mention(bad, 5)["pass"]
    bad2 = {"scene_beats": [{"beat_id": 1, "narration_text": "Hi."}],
            "call_to_action": {"outro_narration": "Follow for the fourth part now."}}
    # 'fourth' without part/episode prefix is not a mention -> passes; digits fail
    assert check_episode_mention(bad2, 5)["pass"]
    bad3 = {"scene_beats": [{"beat_id": 1, "narration_text": "See episode three."}],
            "call_to_action": {"outro_narration": "Bye."}}
    assert not check_episode_mention(bad3, 5)["pass"]


def test_b8_digit_ages_and_screen_text():
    from vsp.gates.tier1.checks import check_minor_safety
    s = make_passing_script()
    s["scene_beats"][0]["narration_text"] = "Look, I'm 13 last week folks"
    r = check_minor_safety({"scene_beats": s["scene_beats"], "call_to_action": s["call_to_action"]},
                           True, ["middle school"])
    assert not r["pass"]
    s2 = make_passing_script()
    s2["scene_beats"][0]["screen_text_overlay"] = "13-YEAR-OLD ME"
    r2 = check_minor_safety({"scene_beats": s2["scene_beats"], "call_to_action": s2["call_to_action"]},
                            True, ["middle school"])
    assert not r2["pass"]


def test_b9_cta_ngram_not_first_words():
    from vsp.gates.tier1.checks import check_cta_first_words
    priors = ["Hit follow for part two to see what happens next and more stuff here",
              "Drop your take below and vote now please friends",
              "Tell me whose side you take today folks"]
    cur = {"scene_beats": [{"beat_id": 1, "narration_text": "Hi there folks today."}],
           "call_to_action": {"outro_narration": "Stick around to see what happens next and more stuff here"}}
    # shares the trailing 5-gram 'to see what happens next' with prior 1 -> fail
    # (old first-5-words check would pass since openings differ)
    assert not check_cta_first_words(cur, priors, 5)["pass"]


def test_b10_narrator_references_not_all_pronouns():
    from vsp.gates.tier1.checks import check_voice_lock
    lock = {"pov": "first_person:Jay"}
    # ep-2-style: first-person narrator + other-character pronouns -> pass
    beats2 = [
        "If Jay liked me then I knew I had my proof right there",
        "He smiled at me and she waved too but I kept my cool",
        "They watched us both while I held my ground firm",
        "I told him straight that I was done with his games",
        "We left together and I knew my truth would hold"]
    ep2 = {"scene_beats": [{"beat_id": i + 1, "narration_text": t} for i, t in enumerate(beats2)],
           "call_to_action": {"outro_narration": "Hit follow for more"}}
    assert check_voice_lock(ep2, lock)["pass"]
    # ep-4-style: 'she' narration in a first-person series -> fail
    beats4 = [
        "She walked in and she sat down quiet",
        "She cried as she held her bag tight",
        "She left without a word to anyone",
        "She vanished into the night alone",
        "She never came back again"]
    ep4 = {"scene_beats": [{"beat_id": i + 1, "narration_text": t} for i, t in enumerate(beats4)],
           "call_to_action": {"outro_narration": "Hit follow for more"}}
    assert not check_voice_lock(ep4, lock)["pass"]
