"""Unit: every Tier 1 check pass/fail."""
from __future__ import annotations
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tests.conftest import make_passing_script, REPO
from vsp.config import load_config

CFG = load_config(REPO)
T = "Template_1_In_Medias_Res"


def run(script, **kw):
    from vsp.gates.tier1.checks import run_tier1
    from vsp.draft.router import count_words  # noqa
    state = {"anti_repetition_tracker": {"forbidden_for_current_ep": []}, "character_registry": []}
    # B3 (stricter): the production path supplies every context key.
    # selected_text carries the script's own words so T1-15/T1-19 ground;
    # other keys are legitimately empty for these single-script unit checks.
    spoken = " ".join([b.get("narration_text", "") for b in script.get("scene_beats", [])] +
                      [script.get("call_to_action", {}).get("outro_narration", "")])
    ctx = kw.get("ctx") or {
        "selected_text": spoken or "smiled docs pulled plug though donor match purse",
        "narration_lock": {},
        "allowed_names": {"Mom", "Dad", "Mommy", "Daddy", "Grandma", "Grandpa",
                          "Doctor", "Officer", "Judge", "Sir", "Maam", "Coach", "Boss",
                          "Marcus", "Vance", "Chloe", "Jake", "Luis"},
        "expected_episode": 1,
        "prior_hooks": [],
        "prior_ctas": [],
        "episode_texts": [],
        "minor_present": False,
        "age_map": {},
    }
    return run_tier1({"scene_beats": script["scene_beats"], "call_to_action": script["call_to_action"]},
                     CFG, kw.get("template", T), kw.get("hook", "Option_2_Out_Of_Context_Confession"),
                     kw.get("payoff", 4), script["call_to_action"]["type"], kw.get("hard", False),
                     kw.get("forbidden", []), kw.get("originals", []), ctx=ctx)


def test_all_pass():
    s = make_passing_script()
    r = run(s)
    assert r["passed"], [c for c in r["checks"] if not c["pass"] and not c.get("warning_only")]


def test_too_short_and_too_long():
    s = make_passing_script(total_words=200)
    s2 = copy.deepcopy(s)
    s2["scene_beats"][2]["narration_text"] = "Short bit here folks."
    s2["call_to_action"]["outro_narration"] = "Bye now."
    r = run(s2)
    assert "T1-02" in r["failed_ids"]
    s3 = make_passing_script(total_words=200)
    s3["scene_beats"][2]["narration_text"] += " " + "word " * 120
    r3 = run(s3)
    assert "T1-02" in r3["failed_ids"]


def test_cliche_and_aism():
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "Have you ever wondered why stars fall at night over town"
    assert "T1-06" in run(s)["failed_ids"]
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "Little did they know the vault held cash and gold bars"
    assert "T1-06" in run(s)["failed_ids"]


def test_market_units_parameter_passes():
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "The key parameter held firm as guards paced halls all night long today"
    assert "T1-07" not in run(s)["failed_ids"]
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "He ran ten kilometer past town to reach farm house by dawn light"
    assert "T1-07" in run(s)["failed_ids"]


def test_honorific_cjk_and_original_name():
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "Look, oppa took charge as guards paced halls all night long today"
    assert "T1-08" in run(s)["failed_ids"]
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "Look, boss took charge as guards paced halls all night \u4ed6"
    assert "T1-08" in run(s)["failed_ids"]
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "Look, L\u1ee5c C\u1ea9n Niên took charge as guards paced halls today"
    r = run(s, originals=["L\u1ee5c C\u1ea9n Niên"])
    assert "T1-08" in r["failed_ids"]


def test_uncontracted_and_hook_length():
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "Look, I am boss here and guards paced halls all night today"
    assert "T1-09" in run(s)["failed_ids"]
    s = make_passing_script()
    s["scene_beats"][0]["narration_text"] = "word " * 25
    assert "T1-05" in run(s)["failed_ids"]


def test_slang_banned_and_whitelist_cap():
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "Look, bro had rizz and guards paced halls all night today"
    assert "T1-10" in run(s)["failed_ids"]
    s = make_passing_script()
    s["scene_beats"][1]["narration_text"] = "gaslight toxic ghosted unhinged red flag drama here now folks"
    assert "T1-10" in run(s)["failed_ids"]


def test_repetition_payoff_hardboundary():
    s = make_passing_script(template="Template_1_In_Medias_Res", hook="Option_1_Pink_Teddy_Bear")
    r = run(s, template="Template_1_In_Medias_Res", hook="Option_1_Pink_Teddy_Bear",
            forbidden=["Template_1_In_Medias_Res", "Option_1_Pink_Teddy_Bear"])
    assert "T1-11" in r["failed_ids"]
    s = make_passing_script(payoff=3)
    assert "T1-12" in run(s, payoff=3)["failed_ids"]
    s = make_passing_script(cta="cliffhanger_subscription")
    assert "T1-13" in run(s, hard=True)["failed_ids"]
    s = make_passing_script(cta="story_complete")
    assert "T1-13" not in run(s, hard=True)["failed_ids"]
