"""VSP CLI — argparse, idempotent resumable commands."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT_DEFAULT = Path(__file__).resolve().parents[2]


def _root(args) -> Path:
    return Path(getattr(args, "root", str(ROOT_DEFAULT))).resolve()


def cmd_ingest(args) -> int:
    from .orchestrator import do_ingest
    try:
        payload = do_ingest(_root(args), args.source, args.series_id, args.topology,
                            args.language, args.rights, args.complete, args.force)
        print(json.dumps({"series_id": payload["series_id"], "sha256": payload["source_sha256"]}))
        return 0
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR ingest: {e}", file=sys.stderr)
        return 1


def cmd_chunk(args) -> int:
    from .orchestrator import do_chunk
    try:
        chunks = do_chunk(_root(args), args.series_id, force=args.force)
        print(json.dumps({"chunks": len(chunks)}))
        return 0
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR chunk: {e}", file=sys.stderr)
        return 1


def cmd_extract(args) -> int:
    from .orchestrator import do_extract
    from .llm.client import BudgetExceeded
    try:
        out = do_extract(_root(args), args.series_id, args.chunk, force=args.force)
        print(json.dumps({"extracted": len(out)}))
        return 0
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except BudgetExceeded as e:
        print(f"BUDGET STOP: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ERROR extract: {e}", file=sys.stderr)
        return 1


def cmd_draft(args) -> int:
    from .orchestrator import do_draft
    from .llm.client import BudgetExceeded
    try:
        d = do_draft(_root(args), args.series_id, args.chunk, force=args.force)
        print(json.dumps({"words": d["script_metadata"]["total_word_count"]}))
        return 0
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except BudgetExceeded as e:
        print(f"BUDGET STOP: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ERROR draft: {e}", file=sys.stderr)
        return 1


def cmd_gate(args) -> int:
    from .config import load_config
    from .orchestrator import run_gates_for_script, series_dir
    from .io import read_json
    try:
        root = _root(args)
        cfg = load_config(root)
        if args.file:
            script = json.loads(Path(args.file).read_text(encoding="utf-8"))
            template = script.get("script_metadata", {}).get("pacing_template", "Template_1_In_Medias_Res")
            state = {"series_id": script.get("script_metadata", {}).get("series_id", "cli"),
                     "current_episode": 0,
                     "anti_repetition_tracker": {"forbidden_for_current_ep": []},
                     "character_registry": [], "narration": {}, "age_map": {}}
            chunk = {"is_hard_boundary_end": False}
            # B3: production supplies every key; for --file, self-ground the
            # selection context so the check targets the script's own gates.
            spoken = " ".join([b.get("narration_text", "") for b in script.get("scene_beats", [])] +
                              [script.get("call_to_action", {}).get("outro_narration", "")])
            from .orchestrator import run_gates_for_script as rg
            ctx = {"selected_text": spoken or "cli gate self-grounded text",
                   "minor_present": False, "age_map": {},
                   "narration_lock": {}, "allowed_names": set(),
                   "expected_episode": 1, "prior_hooks": [], "prior_ctas": [],
                   "episode_texts": []}
            # allowed_names: market allowlist so Mom/Dad pass in --file checks.
            try:
                for _a in (cfg.market.get("name_allowlist", []) or []):
                    ctx["allowed_names"].add(str(_a))
            except Exception:
                pass
            receipt = rg(cfg, script, template, state, chunk, ctx=ctx)
            print(json.dumps(receipt, indent=2))
            return 0 if receipt["passed"] else 2
        from .orchestrator import _workdir, _chunk_path
        chunk = read_json(_chunk_path(root, args.series_id, args.chunk))
        wdir = _workdir(root, args.series_id, chunk["chunk_id"])
        script = read_json(wdir / "draft.json")
        state = read_json(series_dir(root, args.series_id) / "series_state.json")
        # Build the production selection context from beats+arc when available.
        _sel, _minor = None, False
        try:
            from .orchestrator import aged_up_present_for_beats as _aup
            _bf = read_json(wdir / "beats.json")
            _beats = _bf.get("beats", []) or []
            try:
                _arc = read_json(wdir / "arc.json")
                _ids = set(int(x) for x in (_arc.get("selected_beat_ids", []) or []))
                _sel_beats = [b for b in _beats if int(b.get("beat_id", -1)) in _ids] or None
            except Exception:
                _sel_beats = None
            _use = _sel_beats if _sel_beats is not None else _beats
            _parts = [str(b.get("summary_en", "") or "") for b in _use if isinstance(b, dict)]
            _src = read_json(series_dir(root, args.series_id) / "00_source_raw.json")
            _by = {p["id"]: p["text"] for p in _src.get("paragraphs", [])}
            for _b in _use:
                for _pid in (_b.get("source_paragraph_ids", []) or []):
                    if _pid in _by:
                        _parts.append(str(_by[_pid]))
            _sel = "\n".join(_parts) or None
            _minor = bool(_aup(_use, state))
        except Exception:
            pass
        if _sel is None:
            _sp = " ".join([b.get("narration_text", "") for b in script.get("scene_beats", [])] +
                           [script.get("call_to_action", {}).get("outro_narration", "")])
            _sel = _sp
        _ctx = {"selected_text": _sel, "minor_present": _minor}
        receipt = run_gates_for_script(cfg, script, script.get("_template", "Template_1_In_Medias_Res"), state, chunk, ctx=_ctx)
        from .io import atomic_write_json
        atomic_write_json(wdir / "gates_0.json", {**receipt, "inputs_sha256": {"script": receipt["script_sha256"]}})
        print(json.dumps({"passed": receipt["passed"], "failed": receipt["failed_ids"]}))
        return 0 if receipt["passed"] else 2
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR gate: {e}", file=sys.stderr)
        return 1


def cmd_audit(args) -> int:
    from .orchestrator import do_audit, do_evolve, series_decisions
    from .config import load_config
    from .breakers.policies import series_should_blacklist, add_blacklist
    from .io import read_json
    from .llm.client import BudgetExceeded
    try:
        root = _root(args)
        dec = do_audit(root, args.series_id, args.chunk, force=args.force)
        if dec.get("decision") == "BUDGET_STOP":
            print(json.dumps(dec))
            return 3
        # evolve state if release-eligible (RELEASE, legacy PASS/FORCED) or
        # NEEDS_HUMAN (state evolves so the series continues, never out/).
        if dec["decision"] in ("RELEASE", "PASS", "NEEDS_HUMAN", "FORCED_PASS_WITH_WARNING"):
            do_evolve(root, args.series_id, args.chunk, fake=None, force=args.force)
        # blacklist check
        cfg = load_config(root)
        decs, scores = series_decisions(root, args.series_id)
        trig, reason = series_should_blacklist(decs, scores, cfg)
        if trig:
            src = read_json(root / "run" / args.series_id / "00_source_raw.json")
            add_blacklist(root, src["source_sha256"], reason, args.series_id)
            print(json.dumps({**dec, "blacklisted": reason}))
            return 2
        print(json.dumps(dec))
        return 0 if dec["decision"] in ("RELEASE", "PASS", "FORCED_PASS_WITH_WARNING") else 2
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except BudgetExceeded as e:
        print(f"BUDGET STOP: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ERROR audit: {e}", file=sys.stderr)
        return 1


def cmd_export(args) -> int:
    from .orchestrator import do_export
    try:
        paths = do_export(_root(args), args.series_id)
        print(json.dumps({"exported": [str(p) for p in paths]}))
        return 0
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR export: {e}", file=sys.stderr)
        return 1


def cmd_verify(args) -> int:
    from .export.package import verify_series
    errs = verify_series(_root(args), args.series_id)
    if errs:
        for e in errs:
            print(f"VERIFY FAIL: {e}", file=sys.stderr)
        return 2
    print("verify ok")
    return 0


def _record_refusal(root, sid: str, chunk: dict, exc: BaseException) -> None:
    """R5: persist a chunk refusal (stage, check, chunk) for PROGRESS.md.

    R21: `refusal.json` always shows the latest attempt (overwritten), while
    `refusals.jsonl` keeps the append-only history of every attempt.
    """
    import json as _json
    import re
    from datetime import datetime
    from .io import atomic_write_json
    from .orchestrator import series_dir
    msg = str(exc)

    def _grab(pat: str) -> str:
        m = re.search(pat, msg)
        return m.group(1) if m else ""

    payload = {"chunk_id": chunk.get("chunk_id", ""), "chunk_index": chunk.get("chunk_index"),
               "stage": _grab(r"stage=([A-Za-z0-9_]+)"), "check": _grab(r"check=([A-Za-z0-9_]+)"),
               "message": msg, "ts": datetime.now().isoformat()}
    try:
        wdir = series_dir(root, sid) / "work" / (chunk.get("chunk_id") or "chunk_000")
        wdir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(wdir / "refusal.json", payload)
        with open(wdir / "refusals.jsonl", "a", encoding="utf-8") as f:
            f.write(_json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _clear_stale_refusal(root, sid: str, chunk_id: str) -> None:
    """R21: a fresh decision for the chunk supersedes any earlier refusal."""
    try:
        from pathlib import Path as _Path
        from .orchestrator import series_dir
        (series_dir(root, sid) / "work" / chunk_id / "refusal.json").unlink(missing_ok=True)
    except Exception:
        pass


def write_series_progress(root, sid: str) -> str | None:
    """Render one series' PROGRESS.md; returns the console summary line (R5)."""
    from .io import read_json
    from .breakers.policies import is_blacklisted
    sd = root / "run" / sid
    try:
        src = read_json(sd / "00_source_raw.json")
        state = read_json(sd / "series_state.json")
    except Exception:
        return None
    try:
        n_chunks = len(list((sd / "chunks").glob("chunk_*.json")))
    except Exception:
        n_chunks = 0
    # per-chunk spend
    spend_by_chunk: dict[str, float] = {}
    calls_by_chunk: dict[str, int] = {}
    total_spend, total_calls = 0.0, 0
    calls = sd / "llm_calls.jsonl"
    if calls.exists():
        for line in calls.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            try:
                total_spend += float(e.get("cost_usd", 0))
                total_calls += 1
                cid = str(e.get("chunk_id", ""))
                if cid:
                    spend_by_chunk[cid] = spend_by_chunk.get(cid, 0.0) + float(e.get("cost_usd", 0))
                    calls_by_chunk[cid] = calls_by_chunk.get(cid, 0) + 1
            except (TypeError, ValueError):
                continue
    rows = []
    decided = set()
    refused: list[dict] = []
    if (sd / "work").exists():
        for w in sorted((sd / "work").glob("chunk_*")):
            d = w / "decision.json"
            if not d.exists():
                r = w / "refusal.json"
                if r.exists():
                    try:
                        refused.append(read_json(r))
                    except Exception:
                        pass
                    continue
                rows.append(f"| {w.name} | pending | — | — | run extract/draft/audit |")
                continue
            j = read_json(d)
            decided.add(w.name)
            cid = j.get("chunk_id", w.name)
            rows.append(
                f"| {cid} | {j.get('decision')} | {j.get('overall_score')} | "
                f"{j.get('rewrite_count', 0)} | ${spend_by_chunk.get(cid, 0.0):.4f} "
                f"({calls_by_chunk.get(cid, 0)} calls) | {_next_action(j)} |")
    if (sd / "chunks").exists():
        for c in sorted((sd / "chunks").glob("chunk_*.json")):
            if c.stem not in decided and not (sd / "work" / c.stem / "decision.json").exists():
                if not (sd / "work" / c.stem / "refusal.json").exists():
                    rows.append(f"| {c.stem} | pending | — | — | — | run extract/draft/audit |")
    for r in refused:
        rows.append(f"| {r.get('chunk_id')} | REFUSED {r.get('stage')}/{r.get('check')} | — | 0 | — | fix input and rerun |")
    blacklisted = is_blacklisted(root, src.get("source_sha256", ""))
    md = [f"# {sid}", "",
          f"- episodes: {state.get('current_episode', 0)} | chunks: {n_chunks} | "
          f"total spend: ${total_spend:.4f} ({total_calls} calls) | "
          f"blacklist: {'BLACKLISTED' if blacklisted else 'clean'}",
          f"- cursor: {json.dumps(state.get('narrative_cursor', {}))}"]
    if refused:
        md.append(f"- refusals: {len(refused)} (" + "; ".join(
            f"{r.get('chunk_id')} stage={r.get('stage')} check={r.get('check')}" for r in refused) + ")")
    md += ["",
           "| chunk | decision | score | rewrites | cost | next action |",
           "|---|---|---|---|---|---|"] + rows
    (sd / "PROGRESS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return (f"## {sid}: ep={state.get('current_episode',0)} chunks={n_chunks} "
            f"spend=${total_spend:.4f} calls={total_calls} "
            f"blacklist={'yes' if blacklisted else 'no'}")


def cmd_status(args) -> int:
    root = _root(args)
    sids = [args.series_id] if args.series_id else ([p.name for p in (root / "run").glob("*") if p.is_dir()] if (root / "run").exists() else [])
    lines = []
    for sid in sids:
        line = write_series_progress(root, sid)
        if line is not None:
            lines.append(line)
    print("\n".join(lines) or "no series")
    return 0


def _next_action(dec: dict) -> str:
    d = dec.get("decision")
    if d == "BUDGET_STOP":
        return "raise caps and rerun this chunk"
    if d in ("REJECT", "REJECT_SAFETY", "FAST_FORWARD_MERGE"):
        return "chunk closed; continue with next chunk"
    if d in ("RELEASE", "PASS"):
        return "exported or ready to export"
    if d == "NEEDS_HUMAN":
        return "human review in review/ then continue"
    if d == "FORCED_PASS_WITH_WARNING":
        return "legacy forced pass (not exported)"
    return "run audit"


def cmd_review(args) -> int:
    from .feedback.review import append_review
    import yaml as _y
    root = _root(args)
    # pairwise: `vsp review --pairwise-sid <sid> --pick N`
    if getattr(args, "pairwise_sid", None):
        return cmd_review_pairwise(args)
    if not getattr(args, "series_id", None) or getattr(args, "ep", None) is None:
        print("REFUSAL stage=review check=args need <series_id> <ep> or --pairwise-sid", file=sys.stderr)
        return 2
    ep_path = root / "out" / args.series_id / f"ep_{args.ep:03d}.json"
    if not ep_path.exists():
        print(f"REFUSAL stage=review check=missing-episode path={ep_path}", file=sys.stderr)
        return 2
    sha = hashlib.sha256(ep_path.read_bytes()).hexdigest()
    # Q15 structured verdict: --dimensions hook:9,pacing:8,... --release yes/no
    dims = None
    if getattr(args, "dimensions", None):
        dims = {}
        for part in str(args.dimensions).split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                try:
                    dims[k.strip()] = max(1, min(10, int(v.strip())))
                except (TypeError, ValueError):
                    print(f"REFUSAL stage=review check=dimensions bad={part!r}", file=sys.stderr)
                    return 2
    rel = None
    if getattr(args, "release", None) is not None:
        rv = str(args.release).lower()
        if rv in ("yes", "true", "1", "y"):
            rel = True
        elif rv in ("no", "false", "0", "n"):
            rel = False
        else:
            print(f"REFUSAL stage=review check=release bad={args.release!r}", file=sys.stderr)
            return 2
    tags = (args.tags.split(",") if getattr(args, "tags", "") else []) or []
    tags = [t.strip() for t in tags if t.strip()]
    # validate tags against fixed taxonomy (warn, don't refuse legacy tags)
    try:
        allowed = set(_y.safe_load((root / "config" / "review_tags.yaml").read_text()) or [])
        bad = [t for t in tags if t not in allowed]
        if bad:
            print(f"WARNING review tags not in taxonomy: {bad}", file=sys.stderr)
    except Exception:
        pass
    verdict = getattr(args, "verdict", "") or ""
    e = append_review(root, args.series_id, args.ep, sha, verdict, tags, args.note or "",
                      dimensions=dims, release=rel)
    print(json.dumps(e))
    return 0


def cmd_review_pairwise(args) -> int:
    """Q15: show 2 candidates blind in random order, record preference."""
    import random as _rnd
    root = _root(args)
    sid = args.pairwise_sid
    # candidates: best-of-N drafts for the latest chunk with candidates
    cands: list = []
    try:
        for wdir in sorted((root / "run" / sid / "work").glob("chunk_*")):
            for fp in sorted(wdir.glob("candidates/draft_*.json")):
                try:
                    cands.append(json.loads(fp.read_text()))
                except Exception:
                    continue
    except Exception:
        pass
    cands = cands[-2:] if len(cands) >= 2 else cands
    if len(cands) < 2:
        print(f"REFUSAL stage=review check=pairwise-need-2-candidates sid={sid}", file=sys.stderr)
        return 2
    seed = int(getattr(args, "seed", 0) or 0)
    order = [0, 1]
    _rnd.Random(seed).shuffle(order)
    # non-interactive: --pick 1|2 selects the shown slot
    pick = getattr(args, "pick", None)
    if pick not in (1, 2, "1", "2"):
        # show blind (no decision info) and require --pick
        for slot, idx in enumerate(order, 1):
            b1 = (cands[idx].get("scene_beats", []) or [{}])[0].get("narration_text", "")[:120]
            print(f"--- Candidate {slot} ---\n{b1}")
        print("pass --pick 1|2 to record a preference", file=sys.stderr)
        return 2
    chosen = order[int(pick) - 1]
    entry = {"series_id": sid, "pair": [cands[0].get("_hook", "?"), cands[1].get("_hook", "?")],
             "order": order, "pick": int(pick), "chosen_index": chosen, "seed": seed}
    (root / "feedback").mkdir(parents=True, exist_ok=True)
    with open(root / "feedback" / "pairwise.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(json.dumps(entry))
    return 0


def cmd_review_sheet(args) -> int:
    """Q15: write review/<sid>/index.html (local file, no server, no network)."""
    root = _root(args)
    sid = args.sheet_sid
    sd = root / "run" / sid
    if not sd.exists():
        print(f"REFUSAL stage=review-sheet check=missing-series sid={sid}", file=sys.stderr)
        return 2
    import html as _h
    rows = []
    try:
        for wdir in sorted((sd / "work").glob("chunk_*")):
            d = wdir / "decision.json"
            if not d.exists():
                continue
            dec = json.loads(d.read_text())
            rw = dec.get("rewrite_count", 0)
            script = {}
            for cand in (wdir / f"draft.json", wdir / f"rewrite_{rw}.json"):
                if cand.exists():
                    try:
                        script = json.loads(cand.read_text())
                        break
                    except Exception:
                        continue
            beats = script.get("scene_beats", []) or []
            audit = {}
            ap = wdir / f"audit_{rw}.json"
            if ap.exists():
                try:
                    audit = json.loads(ap.read_text())
                except Exception:
                    audit = {}
            chk = audit.get("checklist", {}) or {}
            btxt = "<br>".join(f"<b>{b.get('beat_id')}</b> {_h.escape(str(b.get('narration_text', ''))[:200])}" for b in beats)
            grows = "<br>".join(f"{k}: {'PASS' if v is True else ('FAIL' if v is False else 'UNVERIFIED')}" for k, v in chk.items())
            rows.append(f"<h2>{_h.escape(wdir.name)} — {dec.get('decision')}</h2><p>{btxt}</p><p>{grows}</p><p>overall={audit.get('overall')} panel_split={audit.get('panel_split')}</p>")
    except Exception as e:
        print(f"ERROR review-sheet: {e}", file=sys.stderr)
        return 1
    out = root / "review" / sid / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("<html><head><meta charset='utf-8'><title>review " + _h.escape(sid) + "</title></head><body><h1>" + _h.escape(sid) + "</h1>" + "".join(rows) + "</body></html>", encoding="utf-8")
    print(json.dumps({"sheet": str(out)}))
    return 0


def cmd_judge_agreement(args) -> int:
    """Q15/Q16: judge-vs-human kappa and MAE per dimension."""
    from .calibrate.metrics import cohen_kappa
    root = _root(args)
    import statistics as _st
    # human reviews with dimensions+release
    humans: dict = {}
    try:
        lp = root / "feedback" / "review_ledger.jsonl"
        if lp.exists():
            for line in lp.read_text().splitlines():
                try:
                    e = json.loads(line)
                    if isinstance(e.get("dimensions"), dict) and "release" in e:
                        humans[(e.get("series_id"), int(e.get("ep", 0)))] = e
                except Exception:
                    continue
    except Exception:
        pass
    # judge scores from manifests
    pairs_rel: list = []
    mae: dict = {}
    n = 0
    try:
        for mp in (root / "out").rglob("manifest.json"):
            try:
                man = json.loads(mp.read_text())
                sid = man.get("series_id", "")
                for e in man.get("episodes", []) or []:
                    key = (sid, int(e.get("episode", -1)))
                    if key in humans:
                        h = humans[key]
                        j_rel = 1 if str(e.get("decision", "")).upper() == "RELEASE" else 0
                        h_rel = 1 if h.get("release") else 0
                        pairs_rel.append((j_rel, h_rel))
                        for dim in ("hook_payoff", "pacing", "logic", "retention"):
                            jv = float((e.get("scores", {}) or {}).get(dim, 0))
                            hv = float((h.get("dimensions", {}) or {}).get(dim, 0))
                            mae.setdefault(dim, []).append(abs(jv - hv))
                        n += 1
            except Exception:
                continue
    except Exception:
        pass
    kappa = cohen_kappa([a for a, _ in pairs_rel], [b for _, b in pairs_rel]) if pairs_rel else 0.0
    mae_out = {k: round(sum(v) / len(v), 3) if v else 0.0 for k, v in mae.items()}
    # false-release rate: judge RELEASE while human says no-release
    fr = sum(1 for j, h in pairs_rel if j == 1 and h == 0) / max(1, sum(1 for j, _ in pairs_rel if j == 1))
    print(json.dumps({"n": n, "kappa_release": round(kappa, 3), "mae": mae_out,
                      "false_release_rate": round(fr, 3)}))
    return 0


def effective_review_mode(cfg, root: Path) -> str:
    """Q16: lock mode, but a missed honeypot forces 'all' (never edit the lock)."""
    try:
        mode = str((((getattr(cfg, "lock", {}) or {}).get("human_review", {}) or {}).get("mode", "all")))
    except Exception:
        mode = "all"
    # honeypot miss: a ledger entry marked honeypot with release True (approved a mutant)
    try:
        lp = root / "feedback" / "review_ledger.jsonl"
        if lp.exists():
            for line in lp.read_text().splitlines():
                try:
                    e = json.loads(line)
                    if e.get("honeypot") is True and e.get("release") is True:
                        print("HONEYPOT-MISS: forcing human_review mode=all (lock untouched)", file=sys.stderr)
                        return "all"
                except Exception:
                    continue
    except Exception:
        pass
    return mode if mode in ("all", "sample") else "all"


def cmd_calibrate(args) -> int:
    """Q14: calibrate judges on out/<sid> episodes (auditor runs on real data)."""
    from .calibrate.runner import calibrate_scripts, write_receipt
    root = _root(args)
    sid = args.cal_sid
    # fake fixtures for offline tests (like `vsp run --fake-fixtures`)
    fake = None
    if getattr(args, "fake_fixtures", None):
        from .llm.fake import FakeLLMClient, load_scripts_file
        fake = FakeLLMClient(load_scripts_file(args.fake_fixtures))
    od = root / "out" / sid
    if not od.exists():
        print(f"REFUSAL stage=calibrate check=missing-out sid={sid}", file=sys.stderr)
        return 2
    scripts = []
    for f in sorted(od.glob("ep_*.json")):
        try:
            scripts.append(json.loads(f.read_text()))
        except Exception:
            continue
    if not scripts:
        print(f"REFUSAL stage=calibrate check=no-episodes sid={sid}", file=sys.stderr)
        return 2
    # D2: calibrate each panel model separately, with the same message
    # builder _eval uses (state, frozen names, age map, shuffled beats from
    # the episode's run/ chunk) and the chunk's real paragraph ids.
    from .config import load_config as _lc
    from .orchestrator import get_llm as _gl, load_chunk_budget as _lb
    from .llm.validate import validate_checklist as _vc
    from .gates.tier2.scoring import verify_checklist_evidence as _vv
    from .quality import eff_judging as _ej
    cfg = _lc(root)
    panel = list(_ej(cfg)["panel"])
    # map episodes to their chunks via the manifest when available
    import copy as _cpy, random as _rnd
    from .state.manager import compile_state as _compile
    from .orchestrator import frozen_names as _fnames
    try:
        _man = json.loads((root / "out" / sid / "manifest.json").read_text())
        _ep2chunk = {e.get("episode"): e.get("chunk_id", "") for e in _man.get("episodes", [])}
    except Exception:
        _ep2chunk = {}
    try:
        _state = json.loads((root / "run" / sid / "series_state.json").read_text())
    except Exception:
        _state = {"character_registry": [], "age_map": {}, "setting_map": {}}
    # chunk beats + pids per episode
    _ep_data: dict = {}
    for f in sorted(od.glob("ep_*.json")):
        try:
            _ep_no = int(f.stem.split("_")[1])
        except (TypeError, ValueError, IndexError):
            continue
        _chunk_id = _ep2chunk.get(_ep_no, "")
        try:
            _bf = json.loads((root / "run" / sid / "work" / _chunk_id / "beats.json").read_text()) if _chunk_id else {"beats": []}
        except Exception:
            _bf = {"beats": []}
        try:
            _chk = json.loads((root / "run" / sid / "chunks" / f"{_chunk_id}.json").read_text()) if _chunk_id else {"paragraph_ids": []}
            _pids = set(int(x) for x in (_chk.get("paragraph_ids", []) or []))
        except Exception:
            _pids = set()
        _ep_data[_ep_no] = {"beats": _bf.get("beats", []) or [], "pids": _pids}

    def _make_jf(model_id: str):
        llm_m = _gl(cfg, root, sid, fake)
        # override judge model for this calibration run
        try:
            import copy as _cc
            cfg_m = _cc.copy(cfg)
            cfg_m.pipeline = dict(cfg.pipeline)
            cfg_m.pipeline["models"] = dict(cfg.pipeline.get("models", {}))
            cfg_m.pipeline["models"]["judge"] = dict(cfg.pipeline["models"]["judge"])
            cfg_m.pipeline["models"]["judge"]["model_id"] = model_id
        except Exception:
            cfg_m = cfg
        budget_m = _lb(cfg_m, root, sid, "calibration")
        def _jf(script: dict):
            # find episode number by matching script sha? fall back to first chunk
            _beats = []
            _pids = set()
            for _en, _ed in _ep_data.items():
                _beats = _ed["beats"]
                _pids = _ed["pids"]
                break
            _ordered = _cpy.deepcopy(_beats)
            _rnd.Random(0).shuffle(_ordered)
            msgs = [{"role": "system", "content": (root / "prompts" / "checklist_judge.md").read_text()},
                    {"role": "user", "content": json.dumps({"script": script,
                        "compiled_state": _compile(_state, beats=_beats),
                        "frozen_names": _fnames(_state.get("character_registry", [])),
                        "age_map": _state.get("age_map", {}),
                        "setting_map": _state.get("setting_map", {}),
                        "chunk_beats": _ordered, "order_seed": 0})}]
            from .orchestrator import _jchat as _jc
            res, _ = _jc(llm_m, budget_m, cfg_m, "judge", msgs, "calibration", "calibrate")
            errs = _vc(res if isinstance(res, dict) else {})
            if errs:
                raise SystemExit(f"REFUSAL stage=calibrate check=checklist errors={errs}")
            st = " ".join([b.get("narration_text", "") for b in script.get("scene_beats", [])])
            ver, _ = _vv((res.get("items", {}) or {}), st, _pids)
            return ver
        return _jf
    try:
        import copy as _cc2
        _repeats = int(getattr(args, "repeats", 3) or 3)
        for _mid in panel:
            _metrics = calibrate_scripts(scripts, _make_jf(str(_mid)), repeats=_repeats)
            p = write_receipt(root, str(_mid), _metrics)
        print(json.dumps({"receipts": panel}))
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR calibrate: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_hooks(args) -> int:
    from .feedback.review import rebuild_hook_library
    lib = rebuild_hook_library(_root(args))
    print(json.dumps({k: len(v) for k, v in lib.items()}))
    return 0


def cmd_scrape(args) -> int:
    from .scrape import runner as _runner
    root = _root(args)
    try:
        verify_target = getattr(args, "verify_slug", None)
        extra = getattr(args, "extra", None)
        if verify_target is None and getattr(args, "series_url", None) == "verify" and extra:
            verify_target = extra
        if verify_target:
            if verify_target == "verify":
                print("usage: vsp scrape verify <slug>", file=sys.stderr)
                return 2
            from .scrape import store as _store
            # infer adapter from the stored manifest (metruyenchu vs truyenfull)
            _adapter = "truyenfull"
            try:
                import json as _js
                _man = _js.loads((root / "data" / "scrape" / verify_target / "manifest.json").read_text(encoding="utf-8"))
                _adapter = str(_man.get("adapter", "truyenfull"))
            except Exception:
                _adapter = "generic" if getattr(args, "allow_generic", False) else "truyenfull"
            _site = "metruyenchu" if _adapter == "metruyenchu" else ("generic" if _adapter == "generic" else "truyenfull")
            cfg = _runner.load_scrape_config(root, site=_site)
            errs = _store.verify_slug(root, verify_target, cfg, _adapter)
            if errs:
                for e in errs:
                    print(f"VERIFY FAIL: {e}", file=sys.stderr)
                return 2
            print(json.dumps({"slug": verify_target, "verify": "ok"}))
            return 0
        if not getattr(args, "series_url", None) or getattr(args, "extra", None):
            print("ERROR scrape: need a series URL (or vsp scrape verify <slug>)", file=sys.stderr)
            return 2
        cfg = _runner.load_scrape_config(
            root, site=("metruyenchu" if "metruyenchu.com.vn" in str(args.series_url) else "truyenfull"))
        ingest_fn = None
        if getattr(args, "ingest", False):
            from .orchestrator import do_ingest as _do_ingest
            rights = getattr(args, "rights", "unknown") or "unknown"

            def ingest_fn(path, _r=root, _rights=rights):
                _do_ingest(_r, path, rights=_rights)

        manifest = _runner.scrape_series(
            root, args.series_url, cfg,
            max_chapters=args.max_chapters, start=args.start,
            out_dir=args.out_dir, delay=args.delay,
            allow_generic=getattr(args, "allow_generic", False),
            ingest_fn=ingest_fn)
        print(json.dumps({"slug": manifest["slug"],
                          "chapters": len(manifest["chapters"]),
                          "truncated": manifest["truncated"]}))
        return 0
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR scrape: {e}", file=sys.stderr)
        return 1


def cmd_run(args) -> int:
    from .orchestrator import (do_ingest, do_chunk, do_extract, do_draft, do_audit, do_evolve,
                               do_export, series_decisions, series_dir, set_llm_factory, get_llm)
    from .config import load_config
    from .breakers.policies import series_should_blacklist, add_blacklist, maybe_alert
    from .io import read_json
    from .llm.client import BudgetExceeded
    root = _root(args)
    if getattr(args, "fake_fixtures", None):
        from .llm.fake import FakeLLMClient, load_scripts_file
        scripts = load_scripts_file(args.fake_fixtures)
        per_sid: dict = {}

        def _factory(cfg, r, sid, _s=scripts, _c=per_sid):
            if sid not in _c:
                _c[sid] = FakeLLMClient(dict(_s))
            return _c[sid]

        set_llm_factory(_factory)
    sources = []
    if args.inbox:
        for ext in ("*.txt", "*.md", "*.html", "*.json"):
            sources += sorted(str(p) for p in (Path(args.inbox)).glob(ext))
    elif args.source:
        sources = [args.source]
    rc = 0
    for src in sources:
        sid = None
        try:
            p = do_ingest(root, src, complete=args.complete,
                          topology=getattr(args, "topology", None))
            sid = p["series_id"]
            cfg0 = load_config(root)
            series_fake = get_llm(cfg0, root, sid, None)
            chunks = do_chunk(root, sid, fake=series_fake)
            for c in chunks:
                n = c["chunk_index"]
                # R3: episode-cap stop ends this series (its handover is missing),
                # except ANTHOLOGY where the next story is independent.
                stop_series = c.get("topology_type") != "ANTHOLOGY"
                try:
                    do_extract(root, sid, n, fake=series_fake)
                    do_draft(root, sid, n, fake=series_fake)
                    dec = do_audit(root, sid, n, fake=series_fake)
                except BudgetExceeded as e:
                    if "daily cap" in str(e).lower():
                        raise
                    print(f"BUDGET STOP chunk {n}: {e}", file=sys.stderr)
                    rc = 3
                    if stop_series:
                        break
                    continue
                except SystemExit as e:
                    # R5: stop this series but still export its prior passes below.
                    print(str(e), file=sys.stderr)
                    rc = 2
                    _record_refusal(root, sid, c, e)
                    break
                if isinstance(dec, dict) and dec.get("decision") == "BUDGET_STOP":
                    if "daily cap" in str(dec.get("reason", "")).lower():
                        raise BudgetExceeded(dec.get("reason", "daily cap"))
                    rc = 3
                    if stop_series:
                        break
                    continue
                if dec["decision"] in ("RELEASE", "PASS", "NEEDS_HUMAN", "FORCED_PASS_WITH_WARNING"):
                    try:
                        do_evolve(root, sid, n, fake=series_fake)
                    except BudgetExceeded as e:
                        if "daily cap" in str(e).lower():
                            raise
                        print(f"BUDGET STOP evolve chunk {n}: {e}", file=sys.stderr)
                        rc = 3
                        if stop_series:
                            break
                        continue
                    except SystemExit as e:
                        # R5: same as above — export prior passes, keep exit 2.
                        print(str(e), file=sys.stderr)
                        rc = 2
                        _record_refusal(root, sid, c, e)
                        break
                cfg = load_config(root)
                decs, scores = series_decisions(root, sid)
                trig, reason = series_should_blacklist(decs, scores, cfg)
                if trig:
                    add_blacklist(root, p["source_sha256"], reason, sid)
                    break
        except BudgetExceeded as e:
            maybe_alert(root, load_config(root), f"VSP budget stop: {e}")
            print(f"BUDGET STOP: {e}", file=sys.stderr)
            rc = 3
            break
        except SystemExit as e:
            # ingest/chunk-level refusal: nothing new to judge; export below is a no-op guard.
            print(str(e), file=sys.stderr)
            rc = 2
        if sid is not None:
            # R5: always export fresh passes (even after a later-chunk refusal),
            # then record series status incl. any refusal.
            try:
                do_export(root, sid)
            except SystemExit as e:
                print(str(e), file=sys.stderr)
                rc = 2
            write_series_progress(root, sid)
    if getattr(args, "fake_fixtures", None):
        set_llm_factory(None)
    return rc


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vsp")
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ingest"); a.add_argument("source"); a.add_argument("--series-id", default=None)
    a.add_argument("--topology", default=None); a.add_argument("--language", default=None)
    a.add_argument("--rights", default="unknown"); a.add_argument("--complete", action="store_true")
    a.add_argument("--force", action="store_true"); a.set_defaults(fn=cmd_ingest)
    a = sub.add_parser("chunk"); a.add_argument("series_id"); a.add_argument("--force", action="store_true"); a.set_defaults(fn=cmd_chunk)
    a = sub.add_parser("extract"); a.add_argument("series_id"); a.add_argument("--chunk", type=int, default=None)
    a.add_argument("--force", action="store_true"); a.set_defaults(fn=cmd_extract)
    a = sub.add_parser("draft"); a.add_argument("series_id"); a.add_argument("--chunk", type=int, required=True)
    a.add_argument("--force", action="store_true"); a.set_defaults(fn=cmd_draft)
    a = sub.add_parser("gate"); a.add_argument("series_id", nargs="?"); a.add_argument("--chunk", type=int, default=None)
    a.add_argument("--file", default=None); a.set_defaults(fn=cmd_gate)
    a = sub.add_parser("audit"); a.add_argument("series_id"); a.add_argument("--chunk", type=int, required=True)
    a.add_argument("--force", action="store_true"); a.set_defaults(fn=cmd_audit)
    a = sub.add_parser("export"); a.add_argument("series_id"); a.set_defaults(fn=cmd_export)
    a = sub.add_parser("verify"); a.add_argument("series_id"); a.set_defaults(fn=cmd_verify)
    a = sub.add_parser("run"); a.add_argument("source", nargs="?"); a.add_argument("--inbox", default=None)
    a.add_argument("--complete", action="store_true"); a.add_argument("--fake-fixtures", default=None)
    a.add_argument("--topology", default=None); a.set_defaults(fn=cmd_run)
    a = sub.add_parser("scrape")
    a.add_argument("series_url", nargs="?")
    a.add_argument("extra", nargs="?", default=None)
    a.add_argument("--verify", dest="verify_slug", default=None,
                   help="verify a scraped slug instead of scraping")
    a.add_argument("--max-chapters", type=int, default=None)
    a.add_argument("--start", type=int, default=1)
    a.add_argument("--rights", default="unknown")
    a.add_argument("--out-dir", default="inbox")
    a.add_argument("--delay", type=float, default=1.5)
    a.add_argument("--ingest", action="store_true")
    a.add_argument("--allow-generic", action="store_true")
    a.set_defaults(fn=cmd_scrape)
    a = sub.add_parser("status"); a.add_argument("series_id", nargs="?"); a.set_defaults(fn=cmd_status)
    a = sub.add_parser("review"); a.add_argument("series_id", nargs="?"); a.add_argument("ep", type=int, nargs="?")
    a.add_argument("--verdict", default=""); a.add_argument("--tags", default=""); a.add_argument("--note", default="")
    a.add_argument("--dimensions", default=None); a.add_argument("--release", default=None)
    a.add_argument("--pairwise-sid", dest="pairwise_sid", default=None)
    a.add_argument("--pick", type=int, default=None); a.add_argument("--seed", type=int, default=0)
    a.add_argument("--chunk", type=int, default=None)
    a.set_defaults(fn=cmd_review)
    a = sub.add_parser("review-sheet"); a.add_argument("sheet_sid"); a.set_defaults(fn=cmd_review_sheet)
    a = sub.add_parser("judge-agreement"); a.set_defaults(fn=cmd_judge_agreement)
    a = sub.add_parser("calibrate"); a.add_argument("cal_sid"); a.add_argument("--repeats", type=int, default=3)
    a.add_argument("--fake-fixtures", default=None); a.set_defaults(fn=cmd_calibrate)
    a = sub.add_parser("hooks"); a.add_argument("action", nargs="?", default="rebuild"); a.set_defaults(fn=cmd_hooks)
    return ap


def main(argv=None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    if getattr(args, "cmd", None) == "hooks":
        sys.exit(cmd_hooks(args))
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
