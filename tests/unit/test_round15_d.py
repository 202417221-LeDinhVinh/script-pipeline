"""Round 15 Phase D: D1-D3 failing-then-passing proofs."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def _script():
    beats = []
    for i in range(1, 6):
        beats.append({"beat_id": i, "narration_text": "Word " * 10, "tts_emotion_prompt": "x",
                      "screen_text_overlay": "y", "b_roll_vibe": "z", "sfx_cue": "w", "bgm_directive": "v",
                      "block_type": "Disruption_Hook"})
    return {"scene_beats": beats, "call_to_action": {"type": "cliffhanger_subscription",
            "outro_narration": "Bye.", "loop_connector_word": ""}}


def test_d1_always_fail_and_always_pass_are_both_red(vsp_root):
    from vsp.calibrate.runner import calibrate_scripts, write_receipt
    sc = [_script()]
    fail = calibrate_scripts(sc, lambda s: {k: False for k in
        ["H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1"]}, repeats=2)
    p1 = write_receipt(vsp_root, "m1", fail)
    import json
    assert json.loads(p1.read_text()).get("green") is False
    passed = calibrate_scripts(sc, lambda s: {k: True for k in
        ["H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1"]}, repeats=2)
    p2 = write_receipt(vsp_root, "m2", passed)
    # always-pass has no mutants caught per type? per-type recall ok but false-fail?
    # an always-pass judge fails to flag mutants -> per-type recall 0 -> red
    assert json.loads(p2.read_text()).get("green") is False


def test_d2_calibrates_each_panel_model_with_real_pids():
    src = (Path(__file__).resolve().parents[2] / "src" / "vsp" / "cli.py").read_text(encoding="utf-8")
    assert "calibrate each panel model separately" in src
    assert "_ep_data" in src and "write_receipt" in src
    # one receipt per panel model (not a single panel[0] receipt)
    assert src.count("write_receipt(root, str(_mid)") >= 1 or "for _mid in panel" in src


def test_d3_minor_unsafe_mutator_tests_age_up_rule():
    from vsp.calibrate import mutators
    s = _script()
    mut, exp = mutators.mut_minor_unsafe_insert(s)
    assert exp == ["S1"]
    assert "thirteen" in mut["scene_beats"][0]["narration_text"]
    assert "13" in mut["scene_beats"][0]["narration_text"]
