"""R1/R3 proof tests: rerunSkips everything fresh; BUDGET_STOP stops the series.

R1: 3-chunk serial + single-shot via `vsp run --fake-fixtures`, then rerun with
an EMPTY fixture file: exit 0, zero new `llm_calls.jsonl` lines, byte-identical
`series_state.json` and `out/`.
R3: serial with chunk 1 over cap -> only chunk 1 has a decision; the next inbox
source is still processed; rerun with a raised cap completes chunks 1-3 in order.
"""
from __future__ import annotations
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tests.e2e.test_fullpath import (
    _beats, _arc, _judge_pass, _fid_pass, _evolve_handover, _writer, _small_chunk_cfg)

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures" / "scripts"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _serial_pids(vsp_root) -> tuple[str, list[list[int]]]:
    from vsp.orchestrator import do_ingest, do_chunk
    p = do_ingest(vsp_root, str(vsp_root / "serial.txt"), topology="CONTINUOUS_SERIAL")
    chunks = do_chunk(vsp_root, p["series_id"])
    assert len(chunks) == 3, [c["word_equiv"] for c in chunks]
    return p["series_id"], [c["paragraph_ids"] for c in chunks]


def _three(fname, hooks):
    b = _writer(fname)
    return [{**b, "hook_option": h} for h in hooks]


def _serial_scripts(pids) -> dict:
    ent = [{"original_name": "Mara Halloway", "proposed_us_name": "Dana Cole",
            "role": "lead detective", "gender": "female", "main_cast": True},
           {"original_name": "Jake", "proposed_us_name": "Jake",
            "role": "suspect", "gender": "male", "main_cast": False},
           {"original_name": "Luis", "proposed_us_name": "Luis",
            "role": "helper", "gender": "male", "main_cast": False}]
    w8 = dict(json.loads((FIX / "template1_revenge_writer.json").read_text()))
    w8["hook_option"] = "Option_3_Contradiction"
    d1 = _three("template1_revenge_writer.json", ["Option_1_Pink_Teddy_Bear",
                "Option_2_Out_Of_Context_Confession", "Option_3_Contradiction"])
    d2 = _three("template2_mystery_writer.json", ["Option_2_Out_Of_Context_Confession",
                "Option_3_Contradiction", "Option_4_Calm_Before_Storm"])
    for _d in d2:
        _o = _d.get("call_to_action", {}).get("outro_narration", "")
        _d["call_to_action"] = {**_d.get("call_to_action", {}),
                                "outro_narration": _o.replace("part two", "part three")}
        for _b in _d.get("scene_beats", []) or []:
            _t = str(_b.get("narration_text", ""))
            for _mk in ("So get this,", "Honestly,", "Look,", "The crazy part,",
                        "So get this", "Honestly", "Look", "The crazy part"):
                _t = _t.replace(_mk, "Then")
            _b["narration_text"] = " ".join(_t.split())
    d3 = [{**_writer("template1_revenge_writer.json"), "hook_option": h}
          for h in ("Option_3_Contradiction", "Option_4_Calm_Before_Storm",
                    "Option_1_Pink_Teddy_Bear")]
    for _d in d3:
        _d["call_to_action"] = {**_d.get("call_to_action", {}),
                                "outro_narration": "Tell me whose side you take and follow for part four reveal"}
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
    _j1, _j2, _j3 = _judge_pass(), _judge_pass(), _judge_pass()
    return {
        "writer:1": _beats(1, pids[0], ["Mara Halloway"], ent),
        "writer:2": _arc(1),
        "writer:3": d1[0], "writer:4": d1[1], "writer:5": d1[2],
        "writer:6": _evolve_handover("cliff-one-dock"),
        "judge:1": _j1, "judge:2": _j1, "judge:3": _j1, "judge:4": _j1,
        "judge:5": _fid_pass(), "judge:6": _j1, "judge:7": _j1, "judge:8": _j1,
        "judge:9": _j1, "judge:10": _fid_pass(),
        "writer:7": _beats(7, pids[1], ["Mara Halloway"], [], genre="mystery"),
        "writer:8": _arc(7),
        "writer:9": d2[0], "writer:10": d2[1], "writer:11": d2[2],
        "writer:12": _evolve_handover("cliff-two-cannery"),
        "judge:11": _j2, "judge:12": _j2, "judge:13": _j2, "judge:14": _j2,
        "judge:15": _fid_pass(), "judge:16": _j2, "judge:17": _j2, "judge:18": _j2,
        "judge:19": _j2, "judge:20": _fid_pass(),
        "writer:13": _beats(13, pids[2], ["Mara Halloway"], []),
        "writer:14": _arc(13),
        "writer:15": d3[0], "writer:16": d3[1], "writer:17": d3[2],
        "writer:18": _evolve_handover("cliff-three-trial"),
        "judge:21": _j3, "judge:22": _j3, "judge:23": _j3, "judge:24": _j3,
        "judge:25": _fid_pass(), "judge:26": _j3, "judge:27": _j3, "judge:28": _j3,
        "judge:29": _j3, "judge:30": _fid_pass(),
    }


def _reddit_scripts() -> dict:
    _td = _three("template3_aita_writer.json", ["Option_1_Pink_Teddy_Bear",
                "Option_2_Out_Of_Context_Confession", "Option_3_Contradiction"])
    _jp = _judge_pass()
    return {
        "writer:1": _beats(1, list(range(7)), ["Ada"], [
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


def _run(root: Path, *argv: str) -> int:
    from vsp.cli import build_parser
    ap = build_parser()
    args = ap.parse_args(["--root", str(root), *argv])
    return args.fn(args)


def test_r5_refusal_still_exports_prior_passes(vsp_root):
    """R5 proof (with R11 coverage): chunk 1 passes, chunk 2 extractor invalid
    twice → exit 2, ep_001 + manifest exist, verify passes, next inbox source
    processed, refusal recorded in PROGRESS.md."""
    from vsp.io import read_json
    from vsp.orchestrator import do_ingest, do_chunk
    from vsp.export.package import verify_series
    _small_chunk_cfg(vsp_root)
    # R11: the shared writer:1 fixture is consumed by both inbox stories, so use
    # the same serial text twice. Both series chunk identically, keeping the six
    # citations valid while spanning serial chunk_001.
    import yaml
    _pp = vsp_root / "config" / "pipeline.yaml"
    _cfg = yaml.safe_load(_pp.read_text(encoding="utf-8"))
    _cfg["chunking"].update({"sliding_target": 200, "sliding_min": 180, "sliding_max": 230})
    _pp.write_text(yaml.safe_dump(_cfg), encoding="utf-8")
    _lp = vsp_root / "config" / "quality_lock.yaml"
    _lk = yaml.safe_load(_lp.read_text(encoding="utf-8"))
    _lk.setdefault("chunking", {}).setdefault("serial", {})\
        .update({"sliding_target": 200, "sliding_min": 180, "sliding_max": 230})
    _lp.write_text(yaml.safe_dump(_lk), encoding="utf-8")
    inbox = vsp_root / "inbox"
    inbox.mkdir(exist_ok=True)
    shutil.copy(REPO / "tests" / "fixtures" / "serial_harbor.txt", inbox / "a_serial.txt")
    shutil.copy(REPO / "tests" / "fixtures" / "serial_harbor.txt", inbox / "b_serial.txt")

    p = do_ingest(vsp_root, str(inbox / "a_serial.txt"), topology="CONTINUOUS_SERIAL")
    serial_sid = p["series_id"]
    chunks = do_chunk(vsp_root, serial_sid)
    assert len(chunks) >= 2
    cite = list(chunks[0]["paragraph_ids"])
    assert cite == [0, 1, 2, 3, 4, 5], cite

    ent = [{"original_name": "Mara Halloway", "proposed_us_name": "Dana Cole",
            "role": "lead detective", "gender": "female", "main_cast": True},
           {"original_name": "Jake", "proposed_us_name": "Jake",
            "role": "suspect", "gender": "male", "main_cast": False},
           {"original_name": "Luis", "proposed_us_name": "Luis",
            "role": "helper", "gender": "male", "main_cast": False}]
    _t1 = _writer("template1_revenge_writer.json")
    _td = [{**_t1, "hook_option": h} for h in ("Option_1_Pink_Teddy_Bear",
            "Option_2_Out_Of_Context_Confession", "Option_3_Contradiction")]
    _jp = _judge_pass()
    fx = {
        # consumed by both serial copies (shared file, per-series fake
        # instances; identical chunking keeps cited pids valid in both)
        "writer:1": _beats(1, cite, ["Mara Halloway"], ent),
        "writer:2": _arc(1),
        "writer:3": _td[0], "writer:4": _td[1], "writer:5": _td[2],
        "writer:6": _evolve_handover("cliff-one-dock"),
        "judge:1": _jp, "judge:2": _jp, "judge:3": _jp, "judge:4": _jp,
        "judge:5": _fid_pass(), "judge:6": _jp, "judge:7": _jp, "judge:8": _jp,
        "judge:9": _jp, "judge:10": _fid_pass(),
        # serial chunk 2 extraction: invalid twice → terminal REFUSAL
        "writer:7": {"oops": "not beats"},
        "writer:8": {"beats": []},
    }
    fx_path = vsp_root / "r5_fx.json"
    fx_path.write_text(json.dumps(fx), encoding="utf-8")
    assert _run(vsp_root, "run", "--inbox", str(inbox),
                "--topology", "CONTINUOUS_SERIAL",
                "--fake-fixtures", str(fx_path)) == 2

    sd = vsp_root / "run" / serial_sid
    d1 = read_json(sd / "work" / "chunk_001" / "decision.json")
    assert d1["decision"] == "RELEASE", d1
    assert not (sd / "work" / "chunk_002" / "decision.json").exists()
    assert not (sd / "work" / "chunk_003" / "decision.json").exists()
    r2 = read_json(sd / "work" / "chunk_002" / "refusal.json")
    assert r2["stage"] == "extract" and r2["check"] == "beats", r2

    ep1 = vsp_root / "out" / serial_sid / "ep_001.json"
    assert ep1.exists()
    assert (vsp_root / "out" / serial_sid / "manifest.json").exists()
    assert verify_series(vsp_root, serial_sid) == []

    # next inbox source was still processed and exported
    assert len(list((vsp_root / "out").rglob("ep_*.json"))) == 2

    # refusal recorded in PROGRESS.md
    prog = (sd / "PROGRESS.md").read_text(encoding="utf-8")
    assert "chunk_002" in prog and "REFUSED" in prog
    assert "stage=extract" in prog and "check=beats" in prog


def _snapshot(root: Path, sid: str) -> dict:
    sd = root / "run" / sid
    calls = sd / "llm_calls.jsonl"
    out = {}
    out["llm_lines"] = len(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0
    out["state"] = _sha(sd / "series_state.json")
    od = root / "out" / sid
    out["out"] = sorted((f.name, _sha(f)) for f in od.glob("*.json")) if od.exists() else []
    return out


def test_r1_rerun_makes_zero_llm_calls(vsp_root):
    """R1 proof: second `vsp run` with empty fixtures → exit 0, no new LLM lines,
    byte-identical state and out/."""
    from vsp.io import read_json
    _small_chunk_cfg(vsp_root)
    in_serial = vsp_root / "in_serial"
    in_reddit = vsp_root / "in_reddit"
    in_serial.mkdir()
    in_reddit.mkdir()
    shutil.copy(REPO / "tests" / "fixtures" / "serial_harbor.txt", vsp_root / "serial.txt")
    shutil.copy(REPO / "tests" / "fixtures" / "reddit_aita.txt", in_reddit / "story.txt")
    shutil.copy(vsp_root / "serial.txt", in_serial / "serial.txt")

    _, pids = _serial_pids(vsp_root)
    sfx = vsp_root / "serial_fx.json"
    sfx.write_text(json.dumps(_serial_scripts(pids)), encoding="utf-8")
    rfx = vsp_root / "reddit_fx.json"
    rfx.write_text(json.dumps(_reddit_scripts()), encoding="utf-8")

    assert _run(vsp_root, "run", "--inbox", str(in_serial),
                "--topology", "CONTINUOUS_SERIAL", "--fake-fixtures", str(sfx)) == 0
    assert _run(vsp_root, "run", "--inbox", str(in_reddit),
                "--fake-fixtures", str(rfx)) == 0

    serial_sid = [p.name for p in (vsp_root / "run").iterdir()
                  if p.is_dir() and p.name.startswith("serial_")][0]
    reddit_sid = [p.name for p in (vsp_root / "run").iterdir()
                  if p.is_dir() and not p.name.startswith("serial_")][0]
    before = {serial_sid: _snapshot(vsp_root, serial_sid),
              reddit_sid: _snapshot(vsp_root, reddit_sid)}
    assert len(before[serial_sid]["out"]) == 4  # 3 eps + manifest
    assert len(before[reddit_sid]["out"]) == 2  # 1 ep + manifest

    in_all = vsp_root / "in_all"
    in_all.mkdir()
    shutil.copy(vsp_root / "serial.txt", in_all / "serial.txt")
    shutil.copy(in_reddit / "story.txt", in_all / "story.txt")
    empty = vsp_root / "empty_fx.json"
    empty.write_text("{}", encoding="utf-8")
    # any LLM call raises (empty fixtures) → exit 0 proves zero calls
    assert _run(vsp_root, "run", "--inbox", str(in_all),
                "--topology", "CONTINUOUS_SERIAL", "--fake-fixtures", str(empty)) == 0
    after = {serial_sid: _snapshot(vsp_root, serial_sid),
             reddit_sid: _snapshot(vsp_root, reddit_sid)}
    assert after == before


def test_r3_budget_stop_halts_series_then_resumes(vsp_root):
    """R3 proof: chunk-1 cap → only chunk 1 decided; next source processed;
    raised cap rerun completes chunks 1–3 in order."""
    from vsp.io import read_json, atomic_write_json
    from vsp.orchestrator import do_ingest
    _small_chunk_cfg(vsp_root)
    inbox = vsp_root / "inbox"
    inbox.mkdir(exist_ok=True)
    shutil.copy(REPO / "tests" / "fixtures" / "serial_harbor.txt", inbox / "a_serial.txt")
    shutil.copy(REPO / "tests" / "fixtures" / "reddit_aita.txt", inbox / "b_reddit.txt")

    p = do_ingest(vsp_root, str(inbox / "a_serial.txt"), topology="CONTINUOUS_SERIAL")
    serial_sid = p["series_id"]
    wdir = vsp_root / "run" / serial_sid / "work" / "chunk_001"
    wdir.mkdir(parents=True, exist_ok=True)
    # chunk 1 alone exceeds the locked 400k-token opencode episode cap (§17.4)
    atomic_write_json(wdir / "budget.json", {"ep_tokens": 399900, "ep_cost": 0.0})

    fx = vsp_root / "fx1.json"
    fx.write_text(json.dumps(_reddit_scripts()), encoding="utf-8")
    assert _run(vsp_root, "run", "--inbox", str(inbox),
                "--topology", "CONTINUOUS_SERIAL", "--fake-fixtures", str(fx)) == 3

    sd = vsp_root / "run" / serial_sid
    decs = sorted((sd / "work").glob("*/decision.json"))
    assert len(decs) == 1 and decs[0].parent.name == "chunk_001", [str(d) for d in decs]
    assert read_json(decs[0])["decision"] == "BUDGET_STOP"
    # status surfaces the stopped chunk as the next action
    assert _run(vsp_root, "status", serial_sid) == 0
    prog = (sd / "PROGRESS.md").read_text(encoding="utf-8")
    assert "BUDGET_STOP" in prog and "raise caps" in prog
    # next inbox source was still processed
    outs = list((vsp_root / "out").rglob("ep_*.json"))
    assert len(outs) == 1, outs

    # rerun with a raised cap completes chunks 1–3 in order
    import yaml
    pp = vsp_root / "config" / "pipeline.yaml"
    cfg = yaml.safe_load(pp.read_text(encoding="utf-8"))
    cfg["opencode"]["max_total_tokens"] = 500000
    cfg["circuit_breakers"]["episode_limits"]["hard_cost_cap_usd"] = 10.0
    pp.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    _lp = vsp_root / "config" / "quality_lock.yaml"
    _lk = yaml.safe_load(_lp.read_text(encoding="utf-8"))
    _lk.setdefault("budget", {})["max_total_tokens_opencode"] = 500000
    _lk.setdefault("budget", {})["hard_cost_cap_usd"] = 10.0
    _lp.write_text(yaml.safe_dump(_lk), encoding="utf-8")
    pids = [read_json(sd / "chunks" / f"chunk_{n:03d}.json")["paragraph_ids"] for n in (1, 2, 3)]
    sfx = vsp_root / "serial_fx.json"
    sfx.write_text(json.dumps(_serial_scripts(pids)), encoding="utf-8")
    in_serial = vsp_root / "in_serial"
    in_serial.mkdir()
    shutil.copy(inbox / "a_serial.txt", in_serial / "a_serial.txt")
    assert _run(vsp_root, "run", "--inbox", str(in_serial),
                "--topology", "CONTINUOUS_SERIAL", "--fake-fixtures", str(sfx)) == 0
    got = []
    for n in (1, 2, 3):
        d = read_json(sd / "work" / f"chunk_{n:03d}" / "decision.json")
        assert d["decision"] == "RELEASE", d
        got.append(d["episode_number"])
    assert got == [1, 2, 3], got
    assert len(list((vsp_root / "out" / serial_sid).glob("ep_*.json"))) == 3
