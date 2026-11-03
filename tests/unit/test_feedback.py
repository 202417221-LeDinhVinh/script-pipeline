"""Unit: feedback ledger + hook library rebuild."""
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from tests.conftest import make_passing_script


def test_review_and_hooks_rebuild(vsp_root):
    from vsp.feedback.review import append_review, rebuild_hook_library
    s = make_passing_script(hook="Option_2_Out_Of_Context_Confession")
    out = vsp_root / "out" / "s1"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ep_001.json").write_text(json.dumps(s), encoding="utf-8")
    e = append_review(vsp_root, "s1", 1, "sha", "approve", ["hook"], "good")
    assert e["verdict"] == "approve"
    lib = rebuild_hook_library(vsp_root)
    assert "Option_2_Out_Of_Context_Confession" in lib
    assert lib["Option_2_Out_Of_Context_Confession"][0]["hook_text"] == s["scene_beats"][0]["narration_text"]


def test_hooks_newest_first_and_dedupe(vsp_root):
    from vsp.feedback.review import append_review, rebuild_hook_library
    out = vsp_root / "out" / "s1"
    out.mkdir(parents=True, exist_ok=True)
    s1 = make_passing_script(hook="Option_2_Out_Of_Context_Confession")
    s1["scene_beats"][0]["narration_text"] = "First hook text that grabs fast attention now folks"
    (out / "ep_001.json").write_text(json.dumps(s1), encoding="utf-8")
    s2 = make_passing_script(hook="Option_2_Out_Of_Context_Confession")
    s2["scene_beats"][0]["narration_text"] = "Second hook text with even faster pull now folks"
    (out / "ep_002.json").write_text(json.dumps(s2), encoding="utf-8")
    append_review(vsp_root, "s1", 1, "sha-1", "approve", [], "")
    append_review(vsp_root, "s1", 2, "sha-2", "approve", [], "")
    append_review(vsp_root, "s1", 2, "sha-2", "approve", [], "")  # duplicate review deduped
    lib = rebuild_hook_library(vsp_root)
    hooks = lib["Option_2_Out_Of_Context_Confession"]
    assert [h["hook_text"] for h in hooks] == [
        "Second hook text with even faster pull now folks",
        "First hook text that grabs fast attention now folks"]


def test_review_missing_episode_refuses(vsp_root):
    from vsp.cli import build_parser
    ap = build_parser()
    args = ap.parse_args(["--root", str(vsp_root), "review", "nope", "1", "--verdict", "approve"])
    assert args.fn(args) == 2
