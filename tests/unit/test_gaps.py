"""T4 (+F2/F3 proofs): missing-path tests.

Covers: multi-call episode cap + resume spend (F2), active-time cap (F2/R22),
BUDGET_STOP episode/daily incl. run continuation (F3), score<5 no-rewrite
(T4, also asserted in test_pipeline), Tier-1-never-force-passed (T4), reset
modes (T4), blacklist-at-ingest (T4), Reddit loader (T4), invalid LLM outputs
(F4/T4), stale-artifact recomputation (T4/F1), CLI exit codes (T4), F5 reset on
hard-boundary REJECT.
"""
from __future__ import annotations
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tests.conftest import make_passing_script, REPO
from tests.e2e.test_pipeline import _setup_series, _judge, _fid

from vsp.io import atomic_write_json, read_json
from vsp.llm.fake import FakeLLMClient


def _edit_yaml(root: Path, fn) -> None:
    import yaml
    p = root / "config" / "pipeline.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    fn(cfg)
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    # §17.6: locked thresholds win — mirror pipeline edits into the lock.
    try:
        lp = root / "config" / "quality_lock.yaml"
        lock = yaml.safe_load(lp.read_text(encoding="utf-8"))
        if "opencode" in cfg and "max_total_tokens" in cfg["opencode"]:
            lock.setdefault("budget", {})["max_total_tokens_opencode"] = \
                int(cfg["opencode"]["max_total_tokens"])
        lim = (cfg.get("circuit_breakers", {}) or {}).get("episode_limits", {}) or {}
        if "hard_cost_cap_usd" in lim:
            lock.setdefault("budget", {})["hard_cost_cap_usd"] = float(lim["hard_cost_cap_usd"])
        daily = (cfg.get("circuit_breakers", {}) or {}).get("global_daily_safety", {}) or {}
        if "max_daily_api_spend_usd" in daily:
            lock.setdefault("budget", {})["max_daily_api_spend_usd"] = float(daily["max_daily_api_spend_usd"])
        if "chunking" in cfg:
            serial = lock.setdefault("chunking", {}).setdefault("serial", {})
            for k, lk in (("sliding_target", "sliding_target"), ("sliding_min", "sliding_min"),
                          ("sliding_max", "sliding_max"), ("clean_cut_window", "clean_cut_window")):
                if k in cfg["chunking"]:
                    serial[lk] = int(cfg["chunking"][k])
        lp.write_text(yaml.safe_dump(lock), encoding="utf-8")
    except Exception:
        pass


# ---------- F2: budget ----------

def test_budget_multi_call_cap_and_resume(vsp_root):
    from vsp.config import load_config
    from vsp.llm.client import load_chunk_budget, BudgetExceeded, Usage
    cfg = load_config(vsp_root)
    # §17.6 + R12: the locked opencode episode cap wins (Q13/§17.4: 400k default).
    cfg.lock.setdefault("budget", {})["max_total_tokens_opencode"] = 450
    cfg.lock.setdefault("budget", {})["hard_cost_cap_usd"] = 100.0
    b = load_chunk_budget(cfg, vsp_root, "s", "chunk_001")
    msgs = [{"content": "x " * 50}]
    for stage in ("extract", "draft", "judge"):
        b.precheck("writer", msgs, 100)
        b.record("writer", "m", Usage(60, 60, 0.0, False), f"h-{stage}", 0.01,
                 chunk_id="chunk_001", stage=stage)
    # resume keeps prior spend
    b2 = load_chunk_budget(cfg, vsp_root, "s", "chunk_001")
    assert (b2.ep_tokens, b2.ep_cost) == (b.ep_tokens, b.ep_cost)
    assert b2.ep_tokens == 360
    # 4th call refused
    try:
        b2.precheck("writer", msgs, 200)
        raise AssertionError("expected BudgetExceeded")
    except BudgetExceeded:
        pass


def test_r22_active_seconds_ignores_pause_and_sums_calls(vsp_root, monkeypatch):
    """R22 proof: active_seconds persists in budget.json; a 2 h pause between
    save and reload does not count; 400 s + 600 s of calls exceeds the 900 s
    cap. The cap compares active seconds, never timestamps."""
    import json as _json
    from vsp.config import load_config
    from vsp.llm.client import load_chunk_budget, BudgetExceeded, Usage
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    cfg = load_config(vsp_root)
    # Exercise the 900 s R22 shape with the locked 1800 s default in place.
    cfg.lock.setdefault("budget", {})["active_timeout_seconds"] = 900
    b = load_chunk_budget(cfg, vsp_root, "s", "chunk_001")
    b.precheck("writer", [{"content": "x"}], 10)
    now[0] += 400.0
    b.record("writer", "m", Usage(10, 10, 0.0, False), "h", 400.0,
             chunk_id="chunk_001", stage="extract")
    assert b.active_seconds == 400.0
    b.save()
    raw = (vsp_root / "run" / "s" / "work" / "chunk_001" / "budget.json").read_text(
        encoding="utf-8")
    assert "active_seconds" in raw and "start_wall" not in raw
    # two-hour pause, then reload: the pause must not count
    now[0] += 7200.0
    b2 = load_chunk_budget(cfg, vsp_root, "s", "chunk_001")
    assert b2.active_seconds == 400.0
    b2.precheck("writer", [{"content": "x"}], 10)
    now[0] += 600.0
    b2.record("writer", "m", Usage(10, 10, 0.0, False), "h2", 600.0,
              chunk_id="chunk_001", stage="draft")
    assert b2.active_seconds == 1000.0
    try:
        b2.precheck("writer", [{"content": "x"}], 10)
        raise AssertionError("expected active-time BudgetExceeded")
    except BudgetExceeded as e:
        assert "active-time" in str(e)
    assert _json.loads(raw)["active_seconds"] == 400.0


def test_episode_budget_stop_and_resume(vsp_root):
    from vsp.orchestrator import do_audit
    from vsp.llm.client import load_chunk_budget
    from vsp.config import load_config
    sd = _setup_series(vsp_root, sid="novel_test", n_chunks=1)
    cfg = load_config(vsp_root)
    b = load_chunk_budget(cfg, vsp_root, "novel_test", "chunk_001")
    # §17.4 lock cap is 400k for opencode: sit just under it to force the stop.
    from vsp.quality import eff_budget as _eff
    _cap = int(_eff(cfg, "writer")["ep_token_cap"])
    b.ep_tokens = _cap - 100
    b.save()
    fake = FakeLLMClient({("judge", 1): _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})})
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "BUDGET_STOP", dec
    # BUDGET_STOP is not a series failure
    from vsp.breakers.policies import series_should_blacklist
    trig, _ = series_should_blacklist(["BUDGET_STOP"], [None], load_config(vsp_root))
    assert trig is False
    # resume after raising the locked opencode cap completes the chunk
    _edit_yaml(vsp_root, lambda c: c["opencode"].__setitem__("max_total_tokens", 500000))
    fake2 = FakeLLMClient({
        ("judge", 1): _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9}),
        ("writer", 1): {"rolling_summary": "s", "secrets_ledger": [], "character_updates": [],
                        "handover_bridge": {"pending_cliffhanger": "c", "required_resolution_in_3_to_8s": "r",
                                            "ending_emotional_tone": "tense"}},
    })
    from vsp.orchestrator import do_evolve
    dec2 = do_audit(vsp_root, "novel_test", 1, fake=fake2, force=True)
    assert dec2["decision"] == "RELEASE", dec2
    do_evolve(vsp_root, "novel_test", 1, fake=fake2)


def test_daily_cap_stops_run(vsp_root, monkeypatch):
    monkeypatch.setenv("VSP_WRITER_API_KEY", "dummy")
    monkeypatch.setenv("VSP_JUDGE_API_KEY", "dummy")
    from vsp.cli import build_parser
    _edit_yaml(vsp_root, lambda c: c["circuit_breakers"]["global_daily_safety"].__setitem__("max_daily_api_spend_usd", 0.0))
    inbox = vsp_root / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "a.txt").write_text("Short tale of revenge and woe. " * 20, encoding="utf-8")
    (inbox / "b.txt").write_text("Another short tale of loss and grit. " * 20, encoding="utf-8")
    ap = build_parser()
    rc = ap.parse_args(["--root", str(vsp_root), "run", "--inbox", str(inbox)]).fn(
        ap.parse_args(["--root", str(vsp_root), "run", "--inbox", str(inbox)]))
    assert rc == 3, rc
    # run stopped: second source never ingested
    run_dirs = [p for p in (vsp_root / "run").iterdir() if p.is_dir()]
    assert len(run_dirs) == 1, [p.name for p in run_dirs]


def test_episode_cap_continues_next_source(vsp_root, monkeypatch):
    monkeypatch.setenv("VSP_WRITER_API_KEY", "dummy")
    monkeypatch.setenv("VSP_JUDGE_API_KEY", "dummy")
    from vsp.cli import build_parser
    _edit_yaml(vsp_root, lambda c: c["circuit_breakers"]["episode_limits"].__setitem__("hard_cost_cap_usd", 0.0))
    inbox = vsp_root / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "a.txt").write_text("Short tale of revenge and woe. " * 20, encoding="utf-8")
    (inbox / "b.txt").write_text("Another short tale of loss and grit. " * 20, encoding="utf-8")
    ap = build_parser()
    args = ap.parse_args(["--root", str(vsp_root), "run", "--inbox", str(inbox)])
    rc = args.fn(args)
    assert rc == 3, rc
    for sd in (vsp_root / "run").iterdir():
        decs = list(sd.glob("work/*/decision.json"))
        assert decs, sd
        for d in decs:
            assert read_json(d)["decision"] == "BUDGET_STOP", d


# ---------- T4: decision paths ----------

def test_tier1_failure_never_force_passed(vsp_root):
    from vsp.orchestrator import do_audit
    sd = _setup_series(vsp_root, ep_done=2)
    d = read_json(sd / "work" / "chunk_001" / "draft.json")
    d["scene_beats"][1]["narration_text"] = (
        "Have you ever wondered why stars fall at night over town. "
        "I set marked bills in the drawer for weeks on end now.")
    atomic_write_json(sd / "work" / "chunk_001" / "draft.json", d)
    failing_beat = dict(d["scene_beats"][1])
    fake = FakeLLMClient({
        ("writer", 1): {"scene_beats": [failing_beat]},
        ("writer", 2): {"scene_beats": [failing_beat]},
        ("writer", 3): {"summary": "Mark lost cash and fled town fast."},
    })
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "FAST_FORWARD_MERGE", dec
    assert dec["tier1_passed"] is False, dec


def test_reset_modes():
    from vsp.state.manager import blank_state, apply_reset
    st = blank_state("s")
    st["current_episode"] = 2
    st["character_registry"] = [
        {"original_name": "A", "us_name": "Al", "main_cast": True},
        {"original_name": "B", "us_name": "Bo", "main_cast": False},
    ]
    st["knowledge_and_plot_state"] = {"rolling_summary": "x", "secrets_ledger": [{"fact": "f"}]}
    st["handover_bridge"] = {"pending_cliffhanger": "c"}
    full = apply_reset(copy.deepcopy(st), "FULL_RESET")
    assert full["character_registry"] == [] and full["current_episode"] == 2
    assert full["knowledge_and_plot_state"]["secrets_ledger"] == []
    keep = apply_reset(copy.deepcopy(st), "KEEP_MAIN_CAST_FLUSH_PLOT")
    assert [c["original_name"] for c in keep["character_registry"]] == ["A"]
    assert keep["knowledge_and_plot_state"] == {"rolling_summary": "", "secrets_ledger": []}
    assert keep["handover_bridge"]["pending_cliffhanger"] == ""
    same = apply_reset(copy.deepcopy(st), "PERSIST")
    assert same["character_registry"] == st["character_registry"]


def test_hard_boundary_reset_on_reject(vsp_root):
    from vsp.orchestrator import do_audit
    from vsp.io import read_json as _r
    sd = _setup_series(vsp_root, n_chunks=1, hard_last=True)
    ch = _r(sd / "chunks" / "chunk_001.json")
    ch["state_reset_instruction"] = "FULL_RESET"
    atomic_write_json(sd / "chunks" / "chunk_001.json", ch)
    st = _r(sd / "series_state.json")
    st["character_registry"] = [{"original_name": "Old", "us_name": "Olden",
                                 "tts_voice_id": "v1", "main_cast": False}]
    st["knowledge_and_plot_state"] = {"rolling_summary": "s", "secrets_ledger": [{"fact": "f"}]}
    atomic_write_json(sd / "series_state.json", st)
    draft = _r(sd / "work" / "chunk_001" / "draft.json")
    # hard boundary + cliffhanger CTA fails T1-13 (beat 5); rewriter repeats it
    # so the chunk exhausts rewrites and REJECTs, triggering the F5 reset.
    failing5 = dict(draft["scene_beats"][4])
    fake = FakeLLMClient({
        ("writer", 1): {"scene_beats": [failing5]},
        ("writer", 2): {"scene_beats": [failing5]},
    })
    dec = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert dec["decision"] == "REJECT", dec
    after = _r(sd / "series_state.json")
    assert after["character_registry"] == [], after
    assert after["knowledge_and_plot_state"]["secrets_ledger"] == []
    assert after["current_episode"] == 0


# ---------- T4: ingest / loaders ----------

def test_blacklist_refusal_at_ingest(vsp_root):
    from vsp.orchestrator import do_ingest
    from vsp.breakers.policies import add_blacklist
    f = vsp_root / "story.txt"
    f.write_text("A short tale of revenge. " * 30, encoding="utf-8")
    first = do_ingest(vsp_root, str(f), series_id="blk1")
    add_blacklist(vsp_root, first["source_sha256"], "test reason", "blk1")
    try:
        do_ingest(vsp_root, str(f), series_id="blk1")
        raise AssertionError("expected blacklist refusal")
    except SystemExit as e:
        assert "blacklist" in str(e).lower()


def test_reddit_url_loader_mocked():
    from vsp.ingest.loader import load_source_text

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"data": {"children": [{"data": {"title": "My Title", "selftext": "Body words here"}}]}}]

    raw, title, ref = load_source_text("https://www.reddit.com/r/test/comments/abc123/mypost/",
                                       http_get=lambda u: Resp())
    assert title == "My Title" and "Body words here" in raw
    try:
        load_source_text("https://www.reddit.com/r/test/", http_get=lambda u: Resp())
        raise AssertionError("expected ValueError for non-post URL")
    except ValueError:
        pass


# ---------- T4/F4: invalid LLM outputs refused ----------

def test_invalid_extractor_refused(vsp_root):
    from vsp.orchestrator import do_ingest, do_chunk, do_extract
    f = vsp_root / "story.txt"
    f.write_text("A short tale of revenge. " * 40, encoding="utf-8")
    do_ingest(vsp_root, str(f), series_id="inv1")
    do_chunk(vsp_root, "inv1")
    fake = FakeLLMClient({("writer", 1): {"nope": True}, ("writer", 2): {"beats": []}})
    try:
        do_extract(vsp_root, "inv1", 1, fake=fake)
        raise AssertionError("expected refusal")
    except SystemExit as e:
        assert "REFUSAL" in str(e)


def test_invalid_writer_refused(vsp_root):
    from vsp.orchestrator import do_draft
    _setup_series(vsp_root)
    s = make_passing_script()
    bad = {"scene_beats": s["scene_beats"], "call_to_action": s["call_to_action"]}  # no hook fields
    # Q2 arc + Q11 best-of-N: arc (writer 1), then drafts (writer 2+3, repair consumes 3)
    arc = {"setup_beat_ids": [1, 2], "turn_beat_id": 3, "payoff_beat_id": 4,
           "cliffhanger_beat_id": 5, "dropped_beat_ids": [{"beat_id": 6, "reason": "x"}]}
    fake = FakeLLMClient({("writer", 1): arc, ("writer", 2): bad, ("writer", 3): bad})
    try:
        do_draft(vsp_root, "novel_test", 1, fake=fake)
        raise AssertionError("expected refusal")
    except SystemExit as e:
        assert "REFUSAL" in str(e)


def test_invalid_judge_refused(vsp_root):
    from vsp.orchestrator import do_audit
    _setup_series(vsp_root)
    bad = {"scores": {"hook_payoff": 99, "pacing": 9, "logic": 9, "retention": 9},
           "payoff_resolved": True, "knowledge_violations": [],
           "failed_beat_ids": [7], "rewrite_instructions": []}
    fake = FakeLLMClient({("judge", 1): bad, ("judge", 2): bad})
    try:
        do_audit(vsp_root, "novel_test", 1, fake=fake)
        raise AssertionError("expected refusal")
    except SystemExit as e:
        assert "REFUSAL" in str(e)


# ---------- T4/F1: stale artifacts ----------

def test_stale_draft_triggers_reaudit_and_export_refusal(vsp_root):
    from vsp.orchestrator import do_audit, do_export
    sd = _setup_series(vsp_root)
    fake = FakeLLMClient({("judge", 1): _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})})
    d1 = do_audit(vsp_root, "novel_test", 1, fake=fake)
    assert d1["decision"] == "RELEASE"
    # tamper the judged script (still Tier-1 valid)
    d = read_json(sd / "work" / "chunk_001" / "draft.json")
    d["scene_beats"][2]["narration_text"] += " Folks gasped loud."
    atomic_write_json(sd / "work" / "chunk_001" / "draft.json", d)
    try:
        do_export(vsp_root, "novel_test")
        raise AssertionError("export must refuse a tampered script")
    except SystemExit as e:
        assert "binding" in str(e)
    # re-audit recomputes on the new script instead of returning the stale decision
    fake2 = FakeLLMClient({("judge", 1): _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})})
    d2 = do_audit(vsp_root, "novel_test", 1, fake=fake2)
    assert d2["decision"] == "RELEASE"
    assert d2["inputs_sha256"] != d1["inputs_sha256"]
    assert do_export(vsp_root, "novel_test")


# ---------- T5: realistic fixture scripts ----------

def test_realistic_fixture_scripts():
    """T5: human-readable scripts (one per template) pass Tier 1; failing variants fail right."""
    import json as _json
    from vsp.config import load_config
    from vsp.gates.tier1.checks import run_tier1
    cfg = load_config(REPO)
    state = {"anti_repetition_tracker": {"forbidden_for_current_ep": []}, "character_registry": []}
    good = {
        "template1_revenge_writer.json": ("Template_1_In_Medias_Res", "Option_2_Out_Of_Context_Confession", 4, False),
        "template2_mystery_writer.json": ("Template_2_Breadcrumb_Mystery", "Option_1_Pink_Teddy_Bear", 5, False),
        "template3_aita_writer.json": ("Template_3_Moral_Dilemma", "Option_3_Contradiction", 4, False),
    }
    for fname, (template, hook, payoff, hard) in good.items():
        w = _json.loads((REPO / "tests" / "fixtures" / "scripts" / fname).read_text(encoding="utf-8"))
        assert w["hook_option"] == hook and w["hook_payoff_beat_id"] == payoff
        spoken = " ".join([b.get("narration_text", "") for b in w["scene_beats"]] +
                          [w["call_to_action"].get("outro_narration", "")])
        ctx = {"selected_text": spoken, "narration_lock": {},
               "allowed_names": {"Mom", "Dad", "Jake", "Luis", "Marcus", "Vance", "Chloe",
                                 "Mommy", "Daddy", "Grandma", "Grandpa", "Doctor", "Officer",
                                 "Judge", "Sir", "Maam", "Coach", "Boss"},
               "expected_episode": 2, "prior_hooks": [], "prior_ctas": [],
               "episode_texts": [], "minor_present": False, "age_map": {}}
        r = run_tier1({"scene_beats": w["scene_beats"], "call_to_action": w["call_to_action"]},
                      cfg, template, hook, payoff, w["call_to_action"]["type"], hard, [], [],
                      ctx=ctx)
        assert r["passed"], (fname, [c for c in r["checks"] if not c["pass"]])
    bad = {
        "fail_long_writer.json": "T1-02",
        "fail_cliche_writer.json": "T1-06",
        "fail_units_writer.json": "T1-07",
        "fail_slang_writer.json": "T1-10",
    }
    for fname, check in bad.items():
        w = _json.loads((REPO / "tests" / "fixtures" / "scripts" / fname).read_text(encoding="utf-8"))
        spoken = " ".join([b.get("narration_text", "") for b in w["scene_beats"]] +
                          [w["call_to_action"].get("outro_narration", "")])
        ctx = {"selected_text": spoken, "narration_lock": {},
               "allowed_names": {"Mom", "Dad", "Jake", "Luis",
                                 "Mommy", "Daddy", "Grandma", "Grandpa", "Doctor", "Officer",
                                 "Judge", "Sir", "Maam", "Coach", "Boss"},
               "expected_episode": 2, "prior_hooks": [], "prior_ctas": [],
               "episode_texts": [], "minor_present": False, "age_map": {}}
        r = run_tier1({"scene_beats": w["scene_beats"], "call_to_action": w["call_to_action"]},
                      cfg, "Template_1_In_Medias_Res", "Option_2_Out_Of_Context_Confession",
                      4, "cliffhanger_subscription", False, [], [], ctx=ctx)
        assert not r["passed"] and check in r["failed_ids"], (fname, r["failed_ids"])


def test_status_progress_content(vsp_root):
    """F15: PROGRESS.md carries per-chunk decisions, costs and next actions."""
    from vsp.orchestrator import do_audit
    from vsp.cli import build_parser
    sd = _setup_series(vsp_root, n_chunks=1)
    fake = FakeLLMClient({("judge", 1): _judge({"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})})
    do_audit(vsp_root, "novel_test", 1, fake=fake)
    ap = build_parser()
    args = ap.parse_args(["--root", str(vsp_root), "status", "novel_test"])
    assert args.fn(args) == 0
    md = (sd / "PROGRESS.md").read_text(encoding="utf-8")
    assert "chunk_001" in md and "RELEASE" in md
    assert "next action" in md and "cost" in md.lower()


# ---------- F8/F10/F11/F13 unit proofs ----------

def test_language_detection_and_ingest_override(vsp_root):
    from vsp.ingest.loader import detect_language, ingest_source
    assert detect_language("xin chào các bạn hôm nay mình kể chuyện") == "vi"
    assert detect_language("第1章 开始了。" + "汉" * 100) == "zh"
    assert detect_language("hello world this is a test story") == "en"
    f = vsp_root / "vi.txt"
    f.write_text("xin chào các bạn hôm nay mình kể chuyện ma ở quê nhà", encoding="utf-8")
    auto = ingest_source(str(f))
    assert auto["language"] == "vi" and auto["detected_language"] == "vi"
    forced = ingest_source(str(f), language="en")
    assert forced["language"] == "en" and forced["detected_language"] == "vi"


def test_slang_word_boundary():
    from vsp.gates.tier1.checks import check_slang
    fp = {"scene_beats": [{"beat_id": 1, "narration_text": "He felt intoxicated and grizzled by dawn today folks"}],
          "call_to_action": {"outro_narration": "Bye now folks"}}
    r = check_slang(fp, ["toxic"], ["rizz"], 3)
    assert r["pass"], r["measured"]  # substrings must not count
    hit = {"scene_beats": [{"beat_id": 1, "narration_text": "That move was toxic, bro, straight up folks"}],
           "call_to_action": {"outro_narration": "Bye now folks"}}
    r = check_slang(hit, ["toxic"], ["rizz"], 3)
    assert r["measured"]["whitelist_hits"] == 1


def test_compiled_state_active_cast_and_budget():
    from vsp.state.manager import blank_state, compile_state, evolve_state, estimate_tokens
    st = blank_state("s")
    st["character_registry"] = [
        {"original_name": "A", "us_name": "Al", "main_cast": True},
        {"original_name": "B", "us_name": "Bo", "main_cast": False},
        {"original_name": "C", "us_name": "Co", "main_cast": False},
    ]
    st = evolve_state(st, {}, "T1", "H1", 6, 1, ["B"])
    st = evolve_state(st, {}, "T2", "H2", 12, 2, ["C"])
    assert {c["original_name"] for c in compile_state(st)["character_registry"]} == {"A", "B", "C"}
    st = evolve_state(st, {}, "T1", "H3", 18, 3, ["C"])
    assert {c["original_name"] for c in compile_state(st)["character_registry"]} == {"A", "C"}
    assert estimate_tokens(compile_state(st)) <= 600


def test_r18_small_overshoot_trims_instead_of_refusing():
    """R18 proof: a state at 611 tokens (2% over) trims to fit instead of
    refusing, with the handover bridge and main cast intact."""
    from vsp.state.manager import blank_state, compile_state, estimate_tokens
    st = blank_state("s")
    st["character_registry"] = [
        {"original_name": "Ada", "us_name": "Amy", "main_cast": True,
         "current_status": "Amy is hunting the harbor ledger every single night now"},
        {"original_name": "Old", "us_name": "Olden", "main_cast": False,
         "current_status": "Old left town years ago and never came back at all"},
    ]
    st["handover_bridge"] = {"pending_cliffhanger": "The ledger is gone.",
                             "required_resolution_in_3_to_8s": "Find it fast.",
                             "ending_emotional_tone": "tense"}
    st["knowledge_and_plot_state"] = {
        "rolling_summary": "x",
        "secrets_ledger": [{"fact": "Ada hid the key.", "known_by_audience": False,
                            "known_by_protagonist": True, "known_by_antagonist": False}]}
    # pad deterministically to exactly 611 tokens (len // 4)
    pad = ""
    while estimate_tokens({**st, "knowledge_and_plot_state":
                           {**st["knowledge_and_plot_state"],
                            "rolling_summary": "First. Second. " + pad}}) < 611:
        pad += "y"
    st["knowledge_and_plot_state"]["rolling_summary"] = "First. Second. " + pad
    assert estimate_tokens(st) == 611
    out = compile_state(dict(st))
    assert estimate_tokens(out) <= 600
    assert out["handover_bridge"]["pending_cliffhanger"] == "The ledger is gone."
    assert {c["original_name"] for c in out["character_registry"]} == {"Ada"}
    assert out["knowledge_and_plot_state"]["secrets_ledger"] == [
        {"fact": "Ada hid the key.", "known_by_audience": False,
         "known_by_protagonist": True, "known_by_antagonist": False}]


def test_r18_full_trim_order_and_irreducible_core_refusal():
    """R18 proof: the deterministic trim order engages every step, and a
    state whose core alone exceeds the budget refuses naming the core size."""
    from vsp.state.manager import blank_state, compile_state, estimate_tokens
    st = blank_state("s")
    st["character_registry"] = [
        {"original_name": "Ada", "us_name": "Amy", "main_cast": True,
         "current_status": " ".join(f"word{i}" for i in range(30))},
        {"original_name": "Old", "us_name": "Olden", "main_cast": False,
         "current_status": "Old left town years ago and never came back at all"},
    ]
    st["localized_glossary"] = {"harbor": "the docks where Ada works",
                                "quux": "an irrelevant leftover term"}
    st["knowledge_and_plot_state"] = {
        "rolling_summary": " ".join(
            f"Sentence number {n} about the harbor ledger and everyone involved."
            for n in range(1, 21)),
        "secrets_ledger": [
            {"fact": "Ada hid the key.", "known_by_audience": False,
             "known_by_protagonist": True, "known_by_antagonist": False}] + [
            {"fact": f"Resolved matter {n} " + "about the harbor crew " * 20,
             "known_by_audience": True, "known_by_protagonist": True,
             "known_by_antagonist": True} for n in range(3)]}
    st["handover_bridge"] = {"pending_cliffhanger": "The ledger is gone.",
                             "required_resolution_in_3_to_8s": "Find it fast.",
                             "ending_emotional_tone": "tense"}
    beats = [{"beat_id": 1, "summary_en": "Ada works the harbor docks at dawn"}]
    assert estimate_tokens(st) > 600
    out = compile_state(dict(st), beats=beats)
    assert estimate_tokens(out) <= 600
    assert {c["original_name"] for c in out["character_registry"]} == {"Ada"}
    assert out["localized_glossary"] == {"harbor": "the docks where Ada works"}
    assert len(out["character_registry"][0]["current_status"].split()) <= 12
    assert out["knowledge_and_plot_state"]["rolling_summary"] == \
        "Sentence number 19 about the harbor ledger and everyone involved. " \
        "Sentence number 20 about the harbor ledger and everyone involved."
    assert [e["fact"] for e in out["knowledge_and_plot_state"]["secrets_ledger"]] == \
        ["Ada hid the key."]
    assert out["handover_bridge"]["pending_cliffhanger"] == "The ledger is gone."
    assert out["anti_repetition_tracker"] == st["anti_repetition_tracker"]
    # irreducible core: handover alone already exceeds the budget
    big = blank_state("core")
    big["handover_bridge"] = {"pending_cliffhanger": "z" * 2800,
                              "required_resolution_in_3_to_8s": "",
                              "ending_emotional_tone": ""}
    try:
        compile_state(big)
        raise AssertionError("expected compiled-budget refusal")
    except SystemExit as e:
        assert "check=compiled-budget" in str(e) and "core_tokens=" in str(e)
        assert int(str(e).split("core_tokens=")[1].split()[0]) > 600


def test_r19_live_ep1_state_compiles_with_core_intact():
    """R19 proof: replay the exact live series_state.json shape
    (tests/fixtures/state/live_ep1_state.json) → compiled ≤ 600 with the
    main cast, handover bridge and tracker intact and no `_` keys sent."""
    import json
    from tests.conftest import REPO
    from vsp.state.manager import compile_state, estimate_tokens
    fx = REPO / "tests" / "fixtures" / "state" / "live_ep1_state.json"
    prov = json.loads((fx.parent / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert prov["sha256"] == (
        __import__("hashlib").sha256(fx.read_bytes()).hexdigest())
    st = json.loads(fx.read_text(encoding="utf-8"))
    assert estimate_tokens(st) > 600
    out = compile_state(dict(st))
    assert estimate_tokens(out) <= 600
    assert {c["original_name"] for c in out["character_registry"]
            if c.get("main_cast")} == {"Tang Trĩ", "Tang Diên", "Đoàn Gia Hứa"}
    assert out["handover_bridge"]["pending_cliffhanger"]
    assert out["anti_repetition_tracker"]["hook_history"]
    assert not [k for k in out if k.startswith("_")]


def test_r19_random_states_with_fitting_core_never_refuse():
    """R19 proof: 200 random states whose core fits the budget never refuse
    (and always fit after the trim). Fixed seed: fully deterministic."""
    import random
    from vsp.state.manager import (blank_state, compile_state,
                                   estimate_tokens)
    rng = random.Random(20261030)
    names = ["Ada", "Bo", "Cy", "Dan", "Eli", "Fay", "Gus", "Hal"]
    done = 0
    attempts = 0
    while done < 200:
        attempts += 1
        assert attempts < 20000, "generator cannot reach 200 fitting cores"
        st = blank_state("prop")
        crew = rng.sample(names, rng.randint(1, 6))
        mains = set(rng.sample(crew, rng.randint(1, min(2, len(crew)))))
        st["character_registry"] = [
            {"original_name": n, "us_name": n + "en",
             "main_cast": n in mains,
             "current_status": " ".join(f"s{i}" for i in range(rng.randint(0, 50)))}
            for n in crew]
        cast = {}
        for ep in ("1", "2", "3"):
            if rng.random() < 0.8:
                cast[ep] = rng.sample(crew, rng.randint(0, len(crew)))
        st["_episode_cast"] = cast
        glossary = {}
        for i in range(rng.randint(0, 12)):
            glossary[f"term{i}"] = " ".join(f"d{j}" for j in range(rng.randint(1, 16)))
        st["localized_glossary"] = glossary
        if rng.random() < 0.3:
            beats = None
        else:
            mentioned = rng.sample(sorted(glossary), min(len(glossary), rng.randint(0, 4)))
            cast_names = rng.sample(crew, min(len(crew), rng.randint(0, 3)))
            beats = [{"beat_id": 1,
                      "summary_en": " ".join(mentioned + cast_names + ["plot"]),
                      "characters": cast_names}]
        st["knowledge_and_plot_state"] = {
            "rolling_summary": " ".join(
                " ".join(f"w{n}" for n in range(rng.randint(5, 24))) + "."
                for _ in range(rng.randint(0, 12))),
            "secrets_ledger": [
                {"fact": " ".join(f"f{n}" for n in range(rng.randint(3, 30))),
                 "known_by_audience": rng.random() < 0.5,
                 "known_by_protagonist": True, "known_by_antagonist": False}
                for _ in range(rng.randint(0, 12))]}
        st["handover_bridge"] = {
            "pending_cliffhanger": " ".join(f"c{n}" for n in range(rng.randint(0, 40))),
            "required_resolution_in_3_to_8s": " ".join(f"r{n}" for n in range(rng.randint(0, 24))),
            "ending_emotional_tone": "tense"}
        core = (estimate_tokens([c for c in st["character_registry"] if c.get("main_cast")])
                + estimate_tokens(st["handover_bridge"])
                + estimate_tokens(st["anti_repetition_tracker"]))
        if core > 600:
            continue
        out = compile_state(st, 600, beats)
        assert estimate_tokens(out) <= 600
        done += 1


def test_r20_prompt_carries_frozen_names_for_trimmed_characters(vsp_root):
    """R20 proof: the extractor prompt carries frozen_names for the whole
    registry, including a character the compiled state trimmed away."""
    import json as _json
    from vsp.io import read_json, atomic_write_json
    from vsp.llm.fake import FakeLLMClient
    from vsp.orchestrator import do_extract
    sd = _setup_series(vsp_root)
    sid = sd.name
    st = read_json(sd / "series_state.json")
    st["character_registry"] = [
        {"original_name": "Ada", "us_name": "Amy", "main_cast": True,
         "current_status": "x"},
        {"original_name": "Old", "us_name": "Olden", "main_cast": False,
         "current_status": "y"}]
    atomic_write_json(sd / "series_state.json", st)
    beats = {"beats": [{"beat_id": i + 1, "source_paragraph_ids": [(i * 9) // 5],
                        "characters": ["Ada"], "narration_text": "x",
                        "tts_emotion_prompt": "e", "screen_text_overlay": "",
                        "b_roll_vibe": "", "sfx_cue": "", "bgm_directive": ""} for i in range(6)],
             "new_entities": [], "glossary_additions": {}, "genre_tags": []}
    fake = FakeLLMClient({("writer", 1): dict(beats), ("writer", 2): dict(beats),
                          "beat_extractor": dict(beats)})
    do_extract(vsp_root, sid, 1, fake=fake)
    user_msg = next(m for m in fake.calls[0]["messages"] if m.get("role") == "user")
    body = _json.loads(user_msg["content"])
    assert body["frozen_names"] == {"Ada": "Amy", "Old": "Olden"}
    compiled_names = {c["original_name"]
                      for c in body["compiled_state"]["character_registry"]}
    assert "Old" not in compiled_names and "Ada" in compiled_names


def test_r21_refusal_overwritten_and_history_appended(vsp_root):
    """R21 proof: two failing attempts → refusal.json shows the latest while
    refusals.jsonl keeps both."""
    import json as _json
    from vsp.cli import _record_refusal
    from vsp.io import read_json
    sid = "s21"
    chunk = {"chunk_id": "chunk_001", "chunk_index": 1}
    (vsp_root / "run" / sid / "work" / "chunk_001").mkdir(parents=True, exist_ok=True)
    _record_refusal(vsp_root, sid, chunk,
                    SystemExit("REFUSAL stage=state check=compiled_budget measured=776"))
    _record_refusal(vsp_root, sid, chunk,
                    SystemExit("REFUSAL stage=extract check=beats errors=['x']"))
    cur = read_json(vsp_root / "run" / sid / "work" / "chunk_001" / "refusal.json")
    assert (cur["stage"], cur["check"]) == ("extract", "beats")
    hist = (vsp_root / "run" / sid / "work" / "chunk_001" / "refusals.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(hist) == 2
    assert [_json.loads(line)["check"] for line in hist] == ["compiled_budget", "beats"]


def test_cursor_chapter_and_progress():
    from vsp.state.manager import blank_state, evolve_state, apply_fast_forward
    st = evolve_state(blank_state("s"), {}, "T1", "H1", 6, 1, [], chapter_no=3, progress="35%")
    assert st["narrative_cursor"] == {"last_processed_chapter": 3, "last_processed_beat_id": 6,
                                      "story_arc_progress": "35%"}
    ff = apply_fast_forward(blank_state("s"), "summary words here", 9, chapter_no=4, progress="50%")
    assert ff["narrative_cursor"]["last_processed_chapter"] == 4
    assert ff["narrative_cursor"]["story_arc_progress"] == "50%"


# ---------- R2: evolve idempotency ----------

def _evolve_pass_setup(vsp_root, n_chunks=3):
    from vsp.orchestrator import do_audit, do_evolve
    sd = _setup_series(vsp_root, n_chunks=n_chunks)
    # B6/T1-20/21/22 (stricter): _setup_series already rotates template/hook/
    # Beat 1/CTA per chunk, so consecutive episodes pass the anti-template gates.
    for n in range(1, n_chunks + 1):
        fake_j = FakeLLMClient({("judge", 1): _judge(
            {"hook_payoff": 9, "pacing": 9, "logic": 9, "retention": 9})})
        dec = do_audit(vsp_root, "novel_test", n, fake=fake_j)
        assert dec["decision"] == "RELEASE", dec
        fake_e = FakeLLMClient({("writer", 1): {
            "rolling_summary": f"sum{n}", "secrets_ledger": [],
            "character_updates": [],
            "handover_bridge": {"pending_cliffhanger": f"cliff{n}",
                                "required_resolution_in_3_to_8s": f"resolve{n}",
                                "ending_emotional_tone": "tense"}}})
        do_evolve(vsp_root, "novel_test", n, fake=fake_e)
    return sd


def test_evolve_twice_no_second_llm_call(vsp_root):
    from vsp.orchestrator import do_evolve
    sd = _evolve_pass_setup(vsp_root)
    before_state = (sd / "series_state.json").read_bytes()
    before_ep3 = (sd / "episodes" / "ep_003_state.json").read_bytes()
    # current chunk, snapshot fresh → no LLM (empty fake would raise), no state touch
    out = do_evolve(vsp_root, "novel_test", 3, fake=FakeLLMClient({}))
    assert (sd / "series_state.json").read_bytes() == before_state
    assert (sd / "episodes" / "ep_003_state.json").read_bytes() == before_ep3
    assert out["current_episode"] == 3


def test_evolve_older_chunk_refused(vsp_root):
    from vsp.orchestrator import do_evolve
    sd = _evolve_pass_setup(vsp_root)
    before_state = (sd / "series_state.json").read_bytes()
    # fresh snapshot → silent skip (no LLM, no state touch), even for an old chunk
    out = do_evolve(vsp_root, "novel_test", 1, fake=FakeLLMClient({}))
    assert (sd / "series_state.json").read_bytes() == before_state
    assert out["current_episode"] == 3
    # missing snapshot + older than cursor → refusal (a recompute would roll back)
    (sd / "episodes" / "ep_001_state.json").unlink()
    try:
        do_evolve(vsp_root, "novel_test", 1, fake=FakeLLMClient({}))
        raise AssertionError("expected cursor refusal")
    except SystemExit as e:
        assert "cursor" in str(e)
    assert (sd / "series_state.json").read_bytes() == before_state
    # --force overrides the refusal and recomputes
    fake_e = FakeLLMClient({("writer", 1): {
        "rolling_summary": "forced", "secrets_ledger": [], "character_updates": [],
        "handover_bridge": {"pending_cliffhanger": "c", "required_resolution_in_3_to_8s": "r",
                            "ending_emotional_tone": "tense"}}})
    out = do_evolve(vsp_root, "novel_test", 1, fake=fake_e, force=True)
    assert out["knowledge_and_plot_state"]["rolling_summary"] == "forced"


# ---------- T4: CLI exit codes ----------

def test_cli_exit_codes(vsp_root):
    from vsp.cli import build_parser
    ap = build_parser()
    ok_path = vsp_root / "ok.json"
    ok_path.write_text(json.dumps(make_passing_script()), encoding="utf-8")
    assert ap.parse_args(["--root", str(vsp_root), "gate", "--file", str(ok_path)]).fn(
        ap.parse_args(["--root", str(vsp_root), "gate", "--file", str(ok_path)])) == 0
    bad = make_passing_script(total_words=200)
    bad["scene_beats"][2]["narration_text"] = "Short bit."
    bad["call_to_action"]["outro_narration"] = "Bye."
    bad_path = vsp_root / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    assert ap.parse_args(["--root", str(vsp_root), "gate", "--file", str(bad_path)]).fn(
        ap.parse_args(["--root", str(vsp_root), "gate", "--file", str(bad_path)])) == 2
    assert ap.parse_args(["--root", str(vsp_root), "gate", "--file", str(vsp_root / "nope.json")]).fn(
        ap.parse_args(["--root", str(vsp_root), "gate", "--file", str(vsp_root / "nope.json")])) == 1


def test_cli_audit_budget_stop_exit_3(vsp_root, monkeypatch):
    monkeypatch.setenv("VSP_WRITER_API_KEY", "dummy")
    monkeypatch.setenv("VSP_JUDGE_API_KEY", "dummy")
    from vsp.cli import build_parser
    from vsp.llm.client import load_chunk_budget
    from vsp.config import load_config
    _setup_series(vsp_root)
    # Q14/C2 gate needs a green receipt for every panel model; write them so the
    # test reaches the budget stop (exit 3) instead of a calibration refusal.
    # D1: green requires per-type recall + false-fail.
    from vsp.calibrate.runner import write_receipt
    _cfg0 = load_config(vsp_root)
    try:
        _panel = list(_cfg0.lock.get("judging", {}).get("panel", []) or ["judge"])
    except Exception:
        _panel = ["judge"]
    _mets = {"overall_recall": 0.95, "test_retest_agreement": 0.95,
             "false_fail_rate": 0.0, "recall_per_item": {"L1": 1.0},
             "recall_per_mutant": {"name_swap": 1.0, "pov_flip": 1.0},
             "n_scripts": 1, "n_mutants": 7}
    for _mm in _panel[:2]:
        write_receipt(vsp_root, str(_mm), _mets)
    b = load_chunk_budget(load_config(vsp_root), vsp_root, "novel_test", "chunk_001")
    b.ep_tokens = 999999
    b.save()
    ap = build_parser()
    args = ap.parse_args(["--root", str(vsp_root), "audit", "novel_test", "--chunk", "1"])
    assert args.fn(args) == 3
