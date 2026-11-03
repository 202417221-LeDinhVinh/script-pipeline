"""Orchestrator: idempotent staged pipeline with rewrite loop + decision table."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

from .config import load_config, VSPConfig
from .io import atomic_write_json, read_json, sha256_text, sha256_file
from .ingest.loader import ingest_source
from .topology.heuristics import word_equiv, find_headings, heuristic_classify
from .topology.classifier import build_classifier_input, parse_classifier_output
from .chunking.boundary import chunk_source
from .extract.beats import validate_beats, bible_merge
from .draft.router import route_template, allowed_hooks, apply_timing, spoken_words, splice_script, count_words
from .gates.tier1.checks import run_tier1
from .gates.tier2.scoring import compute_overall
from .state.manager import blank_state, compile_state, apply_reset, evolve_state, apply_fast_forward
from .breakers.policies import is_blacklisted, add_blacklist, series_should_blacklist, maybe_alert
from .export.package import export_episode, build_manifest, verify_series
from .feedback.review import load_hook_library
from .llm.client import LLMClient, LLMBudget, BudgetExceeded, load_chunk_budget


def _is_test_double(llm) -> bool:
    """True for injected test doubles (FakeLLMClient). Duck-typed so production
    code never imports the fake client (§16.3)."""
    return callable(getattr(llm, "bind", None)) and hasattr(llm, "calls")


def _jchat(llm, budget, cfg, role: str, messages: list, chunk_id: str, stage: str):
    """One budgeted LLM JSON call, shared by real and fake clients (F2).

    The END-OF-PROMPT canary is required for real backend calls: the backend
    appends it to the prompt file and chat_json enforces it. Test doubles do
    not use the prompt renderer, so they take the legacy path.
    """
    if _is_test_double(llm):
        llm.bind(budget=budget, cfg=cfg, chunk_id=chunk_id, stage=stage)
        return llm.chat_json(role, messages, require_canary=False)
    llm.budget = budget
    return llm.chat_json(role, messages, chunk_id=chunk_id, stage=stage,
                         require_canary=True)


def series_dir(root: Path, sid: str) -> Path:
    return root / "run" / sid


def _inputs_match(path: Path, inputs: dict) -> bool:
    if not path.exists():
        return False
    try:
        data = read_json(path)
    except Exception:
        return False
    return data.get("inputs_sha256") == inputs


def load_prompts(root: Path) -> dict:
    out = {}
    for name in ["topology_classifier", "beat_extractor", "arc_selector", "script_writer", "beat_rewriter",
                 "audit_judge", "checklist_judge", "fidelity_checker",
                 "state_updater", "fast_forward_summarizer"]:
        try:
            out[name] = (root / "prompts" / f"{name}.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            out[name] = ""
    return out


def frozen_names(registry) -> dict:
    """R20: the whole registry as {original_name: us_name} (names only) for
    prompts. Outside the compiled-state budget, capped at 150 entries,
    sorted for determinism — so trimmed characters stay visible to the LLM."""
    out = {}
    for c in registry or []:
        if isinstance(c, dict) and c.get("original_name") and c.get("us_name"):
            out[str(c["original_name"])] = str(c["us_name"])
    return dict(sorted(out.items())[:150])


def llm_inputs_extra(cfg, root: Path, role: str, prompt_name: str) -> dict:
    """R13: bind every LLM-derived artifact to the exact prompt template,
    model, backend, and renderer version that produced it. Any change to the
    prompt file or model id changes inputs_sha256, so the stage recomputes
    instead of reusing stale outputs."""
    from .llm.opencode_backend import RENDERER_VERSION
    pipeline = getattr(cfg, "pipeline", cfg)
    if not isinstance(pipeline, dict):
        pipeline = {}
    if "models" not in pipeline and "pipeline" in pipeline and isinstance(pipeline["pipeline"], dict):
        pipeline = pipeline["pipeline"]
    mcfg = (pipeline.get("models", {}) or {}).get(role, {}) or {}
    return {"prompt_template_sha256": sha256_file(root / "prompts" / f"{prompt_name}.md"),
            "model_id": str(mcfg.get("model_id", "")),
            "backend": str(mcfg.get("backend", "http")),
            "renderer_version": RENDERER_VERSION}


def load_styles(root: Path) -> list[str]:
    d = root / "assets" / "style_examples"
    out = []
    if d.exists():
        for f in sorted(d.glob("*.txt")):
            if f.name.startswith("PLACEHOLDER_"):
                continue  # X1: invented samples are not style evidence
            out.append(f.read_text(encoding="utf-8"))
    return out[:3]


_LLM_FACTORY = None
_FACTORY_CACHE: dict = {}


def set_llm_factory(fn) -> None:
    """Injectable LLM factory for offline e2e (T2): fn(cfg, root, sid) -> client | None.
    Called when no explicit fake is passed. Reset with set_llm_factory(None)."""
    global _LLM_FACTORY, _FACTORY_CACHE
    _LLM_FACTORY = fn
    _FACTORY_CACHE = {}


def get_llm(cfg: VSPConfig, root: Path, sid: str, fake=None):
    if fake is not None:
        return fake
    if _LLM_FACTORY is not None:
        if sid not in _FACTORY_CACHE:
            inst = _LLM_FACTORY(cfg, root, sid)
            if inst is not None:
                _FACTORY_CACHE[sid] = inst
        if sid in _FACTORY_CACHE:
            return _FACTORY_CACHE[sid]
    return LLMClient(cfg, root, sid)


# ---------- Stage 0 ----------

def do_ingest(root: Path, path_or_url: str, series_id=None, topology=None, language=None,
              rights="unknown", complete=False, force=False) -> dict:
    cfg = load_config(root)
    data = ingest_source(path_or_url, series_id=series_id, rights=rights, language=language)
    sid = data["series_id"]
    if is_blacklisted(root, data["source_sha256"]):
        raise SystemExit(f"REFUSAL stage=ingest check=blacklist sha={data['source_sha256']}")
    sd = series_dir(root, sid)
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "00_source_raw.json"
    payload = {**data, "inputs_sha256": {"source": data["source_sha256"]},
               "forced_topology": topology, "complete": complete}
    if force or not _inputs_match(out, payload["inputs_sha256"]):
        atomic_write_json(out, payload)
    # init state file if missing
    sfile = sd / "series_state.json"
    if not sfile.exists():
        atomic_write_json(sfile, {**blank_state(sid)})
    return payload


# ---------- Topology + Stage 1 ----------

def do_chunk(root: Path, sid: str, fake=None, force=False) -> list[dict]:
    cfg = load_config(root)
    sd = series_dir(root, sid)
    source = read_json(sd / "00_source_raw.json")
    prompts = load_prompts(root)
    factors = cfg.pipeline.get("word_equiv_factors", {"default": 1.0})
    lang = source.get("language", "en") or "en"
    paras = source["paragraphs"]
    full_text = source["normalized_text"]
    weq = word_equiv(full_text, lang, factors)
    headings = find_headings(paras)
    topo = source.get("forced_topology")
    meta_inputs = {"source": source["source_sha256"]}
    if topo:
        meta = {"series_id": sid, "topology": topo, "confidence": 1.0, "source_language": lang,
                "genre": "", "genre_tags": [], "word_equiv": weq, "num_chunks": 0,
                "notes": "forced via --topology", "inputs_sha256": meta_inputs}
    else:
        heur = heuristic_classify(weq, headings, cfg.pipeline.get("topology", {}).get("single_shot_max_word_equiv", 3500), lang)
        if heur is not None:
            meta = {"series_id": sid, **heur, "word_equiv": weq, "num_chunks": 0, "inputs_sha256": meta_inputs}
        else:
            llm = get_llm(cfg, root, sid, fake)
            q = build_classifier_input(source, headings, weq)
            messages = [{"role": "system", "content": prompts["topology_classifier"]},
                        {"role": "user", "content": q}]
            from .llm.validate import validate_classifier
            # FakeLLMClient accepts the same kwargs; duck-typed, no prod import (§16.3)
            need_canary = not _is_test_double(llm)
            res, _ = llm.chat_json("judge", messages, chunk_id="", stage="classify",
                                   require_canary=need_canary)
            cerrs = validate_classifier(res if isinstance(res, dict) else {})
            if cerrs:
                messages.append({"role": "user", "content": f"Validation failed: {cerrs}. Reply with corrected JSON ONLY."})
                res, _ = llm.chat_json("judge", messages, chunk_id="", stage="classify",
                                       require_canary=need_canary)
                cerrs = validate_classifier(res if isinstance(res, dict) else {})
                if cerrs:
                    raise SystemExit(f"REFUSAL stage=topology check=classifier errors={cerrs} path={sd/'01_topology_meta.json'}")
            try:
                parsed = parse_classifier_output(json.dumps(res))
            except ValueError as e:
                raise SystemExit(f"REFUSAL stage=topology check=classifier errors=[{e}] path={sd/'01_topology_meta.json'}")
            if parsed["confidence"] < float(cfg.pipeline.get("topology", {}).get("confidence_threshold", 0.6)):
                raise SystemExit(f"REFUSAL stage=topology check=confidence measured={parsed['confidence']} threshold=0.6 path={sd/'01_topology_meta.json'} (pass --topology)")
            meta = {"series_id": sid, **parsed, "word_equiv": weq, "num_chunks": 0, "inputs_sha256": meta_inputs}
    from .quality import eff_chunking
    _ch = eff_chunking(cfg)
    chunks = chunk_source(source, meta["topology"], lang, factors, _ch["target"],
                          _ch["smin"], _ch["smax"],
                          _ch["window"], bool(source.get("complete", False)))
    meta["num_chunks"] = len(chunks)
    atomic_write_json(sd / "01_topology_meta.json", meta)
    (sd / "chunks").mkdir(parents=True, exist_ok=True)
    for c in chunks:
        cpath = sd / "chunks" / f"{c['chunk_id']}.json"
        c["inputs_sha256"] = {"source": source["source_sha256"], "topology": meta["topology"]}
        if force or not _inputs_match(cpath, c["inputs_sha256"]):
            atomic_write_json(cpath, c)
    return chunks


def _chunk_path(root, sid, n) -> Path:
    return root / "run" / sid / "chunks" / f"chunk_{n:03d}.json"


def _workdir(root, sid, chunk_id) -> Path:
    d = root / "run" / sid / "work" / chunk_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _script_binding_sha(script: dict) -> str:
    """Binding hash: strip volatile keys, normalize episode_number (export assigns it)."""
    import copy
    clean = {k: v for k, v in script.items() if not k.startswith("_") and k != "inputs_sha256"}
    clean = copy.deepcopy(clean)
    try:
        if isinstance(clean.get("script_metadata"), dict):
            clean["script_metadata"] = dict(clean["script_metadata"])
            clean["script_metadata"]["episode_number"] = 0
    except Exception:
        pass
    return sha256_text(json.dumps(clean, sort_keys=True, ensure_ascii=False))


def _clean_script(script: dict) -> dict:
    return {k: v for k, v in script.items() if not k.startswith("_") and k != "inputs_sha256"}


def is_decision_fresh(wdir: Path, decision: dict) -> tuple[bool, str]:
    """Check decision.json is bound to the exact script it judged."""
    try:
        rw = int(decision.get("rewrite_count", 0))
        script = final_script_for_round(wdir, rw)
        cur_sha = _script_binding_sha(script)
        if decision.get("inputs_sha256", {}).get("script") != cur_sha:
            return False, "script sha changed since decision"
        gpath = wdir / f"gates_{rw}.json"
        apath = wdir / f"audit_{rw}.json"
        if not gpath.exists() or not apath.exists():
            return False, "missing gates/audit receipt"
        gates = read_json(gpath)
        audit = read_json(apath)
        if gates.get("script_sha256") != cur_sha:
            return False, "gates receipt bound to different script"
        if audit.get("inputs_sha256", {}).get("script") != cur_sha:
            return False, "audit receipt bound to different script"
        return True, "fresh"
    except Exception as e:
        return False, f"unreadable artifacts: {e}"


# ---------- Stage 2 ----------

def do_extract(root: Path, sid: str, chunk_n: int | None = None, fake=None, force=False) -> list[dict]:
    cfg = load_config(root)
    sd = series_dir(root, sid)
    source = read_json(sd / "00_source_raw.json")
    prompts = load_prompts(root)
    chunks = sorted((sd / "chunks").glob("chunk_*.json"))
    if chunk_n is not None:
        chunks = [sd / "chunks" / f"chunk_{chunk_n:03d}.json"]
    llm = get_llm(cfg, root, sid, fake)
    state = read_json(sd / "series_state.json")
    acc = state  # accumulator for state_in of not-yet-extracted chunks
    # next beat id = max existing + 1
    next_id = int(state.get("narrative_cursor", {}).get("last_processed_beat_id", 0)) + 1
    # account for beats already extracted in earlier chunks of this run
    out = []
    changed = False
    for cpath in chunks:
        chunk = read_json(cpath)
        wdir = _workdir(root, sid, chunk["chunk_id"])
        bpath = wdir / "beats.json"
        sinpath = wdir / "state_in.json"
        chunk_sha = sha256_text(chunk["raw_text_content"])
        if not force and bpath.exists() and sinpath.exists():
            try:
                bdata = read_json(bpath)
                sindata = read_json(sinpath)
                expect = {"chunk": chunk_sha,
                          "state_in": sha256_text(json.dumps(sindata, sort_keys=True, ensure_ascii=False)),
                          **llm_inputs_extra(cfg, root, "writer", "beat_extractor")}
                if bdata.get("inputs_sha256") == expect:
                    out.append(bdata)
                    next_id = max(next_id, max((b["beat_id"] for b in bdata["beats"]), default=next_id - 1) + 1)
                    # merge reused beats into accumulator for later new chunks (no state file write)
                    try:
                        acc, _reprop = bible_merge(acc, bdata, cfg.voices)
                    except ValueError:
                        pass
                    continue
            except Exception:
                pass
        # fresh extraction: snapshot input state (never re-keyed later)
        state_in = acc
        atomic_write_json(sinpath, state_in)
        inputs = {"chunk": chunk_sha,
                  "state_in": sha256_text(json.dumps(state_in, sort_keys=True, ensure_ascii=False)),
                  **llm_inputs_extra(cfg, root, "writer", "beat_extractor")}
        compiled = compile_state(state_in, cfg.pipeline.get("state", {}).get("compiled_budget_tokens", 600))
        messages = [{"role": "system", "content": prompts["beat_extractor"]},
                    {"role": "user", "content": json.dumps(
                        {"compiled_state": compiled,
                         "frozen_names": frozen_names(state_in.get("character_registry", [])),
                         "chunk": chunk, "next_beat_id": next_id}, ensure_ascii=False)}]
        budget = load_chunk_budget(cfg, root, sid, chunk["chunk_id"])
        try:
            res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "extract")
        except BudgetExceeded as e:
            write_budget_stop(root, sid, chunk, wdir, f"budget stop in extract: {e}")
            raise
        from .llm.validate import validate_extractor
        from .extract.beats import validate_beats_coverage
        errs = validate_beats(res, chunk, {c["original_name"] for c in state_in.get("character_registry", [])}, next_id)
        errs += validate_extractor(res)
        errs += validate_beats_coverage(res, chunk.get("paragraph_ids", []))
        if errs:
            # one repair retry
            messages.append({"role": "user", "content": f"Validation failed: {errs}. Reply with corrected JSON ONLY."})
            try:
                res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "extract")
            except BudgetExceeded as e:
                write_budget_stop(root, sid, chunk, wdir, f"budget stop in extract repair: {e}")
                raise
            errs = validate_beats(res, chunk, {c["original_name"] for c in state_in.get("character_registry", [])}, next_id)
            errs += validate_extractor(res)
            errs += validate_beats_coverage(res, chunk.get("paragraph_ids", []))
            if errs:
                raise SystemExit(f"REFUSAL stage=extract check=beats chunk={chunk['chunk_id']} errors={errs}")
        # merge bible into running accumulator for subsequent chunks
        acc, reproposals = bible_merge(acc, res, cfg.voices)
        payload = {"chunk_id": chunk["chunk_id"], "series_id": sid, "beats": res["beats"],
                   "new_entities": res.get("new_entities", []),
                   "glossary_additions": res.get("glossary_additions", {}),
                   "genre_tags": res.get("genre_tags", []), "inputs_sha256": inputs,
                   "name_reproposals": reproposals}
        atomic_write_json(bpath, payload)
        next_id = max(b["beat_id"] for b in res["beats"]) + 1
        out.append(payload)
        changed = True
    if changed:
        # persist merged registry/glossary/age map (statuses unchanged)
        cur = read_json(sd / "series_state.json")
        cur["character_registry"] = acc.get("character_registry", cur.get("character_registry", []))
        cur["localized_glossary"] = acc.get("localized_glossary", cur.get("localized_glossary", {}))
        if acc.get("age_map"):
            merged_age = cur.get("age_map") or {}
            for k, v in acc["age_map"].items():
                merged_age.setdefault(k, v)
            cur["age_map"] = merged_age
        if acc.get("setting_map"):
            cur["setting_map"] = acc["setting_map"]
        atomic_write_json(sd / "series_state.json", cur)
    return out


# ---------- Stage 3 draft ----------

def build_writer_messages(cfg, root, sid, state, chunk, beats_file, template, hook_choice,
                          is_ep1: bool, selected_beats: list | None = None,
                          minor_present: bool = False) -> list[dict]:
    from .draft.router import TEMPLATE_BLOCKS, LETTER_FOR_HOOK, HOOK_LETTERS
    prompts = load_prompts(root)
    styles = load_styles(root)
    hooklib = load_hook_library(root)
    approved = (hooklib.get(hook_choice, []) or [])[:3]
    beats_for_prompt = selected_beats if selected_beats is not None else (beats_file.get("beats", []) or [])
    # verbatim excerpts for twist/cliffhanger beats (only from selected beats)
    by_id = {p["id"]: p["text"] for p in read_json(series_dir(root, sid) / "00_source_raw.json")["paragraphs"]}
    excerpts = []
    for b in beats_for_prompt:
        if b.get("is_twist") or b.get("is_cliffhanger_candidate"):
            for pid in b.get("source_paragraph_ids", []):
                if pid in by_id:
                    excerpts.append(f"[p{pid}] {by_id[pid]}")
    # Q3: prompts show Hook A-D, never internal Option_* labels.
    _allowed_internal = allowed_hooks(state.get("anti_repetition_tracker", {}).get("prev_hook_option"))
    allowed_letters = [LETTER_FOR_HOOK[h] for h in _allowed_internal if h in LETTER_FOR_HOOK]
    suggested_letter = LETTER_FOR_HOOK.get(hook_choice, hook_choice)
    # Q4 voice lock + Q7 age-up block.
    voice_lock = state.get("narration", {}) or {}
    age_map = state.get("age_map", {}) or {}
    setting_map = state.get("setting_map", {}) or {
        k: (state.get("localized_glossary", {}) or {}).get(k, "")
        for k in ("school", "teacher", "class", "parent-teacher summons",
                  "parent-teacher", "school uniform", "homeroom")}
    safety_block = ""
    if minor_present:
        safety_block = ("AGE-UP (Q7): the following characters were minors in the source "
                        "and are adapted to adults; describe every adapted character ONLY as an adult "
                        f"(age_map={json.dumps(age_map, ensure_ascii=False)}; "
                        f"setting_map={json.dumps(setting_map, ensure_ascii=False)}). "
                        "No child descriptors, no school-level terms, no ages < 18. "
                        "Romance and attraction involve adults only. "
                        "Never write sexual content involving anyone whose source age was < 18.")
    user = {"compiled_state": compile_state(state, beats=beats_file.get("beats", [])),
            "frozen_names": frozen_names(state.get("character_registry", [])),
            "age_map": age_map,
            "setting_map": setting_map,
            "chunk_beats": beats_for_prompt,
            "verbatim_excerpts": excerpts[:12], "pacing_template": template,
            "block_definitions": TEMPLATE_BLOCKS[template], "allowed_hooks": allowed_letters,
            "suggested_hook": suggested_letter, "is_hard_boundary_end": chunk.get("is_hard_boundary_end", False),
            "handover_bridge": state.get("handover_bridge", {}), "is_episode_1": is_ep1,
            "style_examples": styles, "approved_hooks": approved,
            "voice_lock": voice_lock, "safety_block": safety_block,
            "rules": "contractions mandatory; grade<=6; beat1<=20 words; payoff beat 4/5; US only. "
                     "Pick exactly ONE hook from allowed_hooks and report it as hook_option (Hook_A/B/C/D)."}
    sys = prompts["script_writer"] + f"\n\nASSIGNED TEMPLATE: {template}\nSUGGESTED HOOK: {suggested_letter}\n(Pick ONE hook from allowed_hooks; you may use the suggestion or any other allowed hook.)"
    return [{"role": "system", "content": sys}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def finalize_script(cfg, sid: str, source: dict, chunk: dict, template: str, writer_out: dict,
                    episode_no: int = 0) -> dict:
    from .draft.router import TEMPLATE_BLOCKS, normalize_hook
    timed = apply_timing({"scene_beats": writer_out["scene_beats"], "call_to_action": writer_out["call_to_action"]}, cfg.wpm)
    comp = timed["_computed"]
    if "hook_option" not in writer_out or "hook_payoff_beat_id" not in writer_out:
        raise SystemExit(f"REFUSAL stage=draft check=writer chunk={chunk['chunk_id']} errors=['missing hook_option/hook_payoff_beat_id (no defaults)']")
    # Q3: normalize Hook_A-D to the internal Option_* enum for storage/gates.
    _hook_internal = normalize_hook(writer_out.get("hook_option")) or writer_out.get("hook_option")
    script = {"script_metadata": {"series_id": sid, "episode_number": episode_no, "chunk_id": chunk["chunk_id"],
                                  "pacing_template": template, "hook_option": _hook_internal,
                                  "hook_payoff_beat_id": writer_out["hook_payoff_beat_id"],
                                  "target_market": "US", "estimated_duration_sec": comp["estimated_duration_sec"],
                                  "total_word_count": comp["total_word_count"],
                                  "provenance": {"source_url_or_path": source.get("source_url_or_path"),
                                                 "source_sha256": source.get("source_sha256")}},
              "scene_beats": timed["scene_beats"], "call_to_action": timed["call_to_action"]}
    return script


def _routing_state(root: Path, sid: str, chunk: dict, beats_file: dict) -> dict:
    """R1: draft routing context frozen at extraction time.

    Uses the chunk's state_in.json (plus this chunk's own bible merge, which is
    deterministic), never the live series_state.json that later evolves corrupt.
    Falls back to live state only when no state_in snapshot exists (legacy).
    """
    sd = series_dir(root, sid)
    sinpath = sd / "work" / chunk["chunk_id"] / "state_in.json"
    if not sinpath.exists():
        return read_json(sd / "series_state.json")
    import copy as _copy
    state_in = read_json(sinpath)
    try:
        merged, _reprop = bible_merge(_copy.deepcopy(state_in), beats_file,
                                      load_config(root).voices)
        return merged
    except Exception:
        return state_in


def select_arc(root: Path, sid: str, chunk: dict, beats_file: dict, llm, budget,
               prompts: dict, wdir: Path, force: bool = False) -> tuple[dict, list]:
    """Q2: pick exactly 1 central conflict (writer call) before drafting.

    Returns (arc_selection, selected_beats). Freshness-bound to beats.json +
    prompt/model/backend/renderer. One repair retry, then refusal (exit 2).
    """
    from .llm.validate import validate_arc_selector
    apath = wdir / "arc.json"
    valid = {int(b.get("beat_id")) for b in beats_file.get("beats", []) if isinstance(b.get("beat_id"), int)}
    inputs = {"beats": sha256_file(wdir / "beats.json"),
              **llm_inputs_extra(load_config(root), root, "writer", "arc_selector")}
    if not force and apath.exists():
        try:
            existing = read_json(apath)
            if existing.get("inputs_sha256") == inputs:
                sel_ids = set(existing.get("selected_beat_ids", []))
                _sel = [b for b in beats_file.get("beats", []) if int(b.get("beat_id", -1)) in sel_ids]
                if _sel:
                    return existing, _sel
        except Exception:
            pass
    messages = [{"role": "system", "content": prompts.get("arc_selector", "")},
                {"role": "user", "content": json.dumps({"chunk_beats": beats_file.get("beats", [])},
                                                        ensure_ascii=False)}]
    try:
        res, _ = _jchat(llm, budget, load_config(root), "writer", messages, chunk["chunk_id"], "arc")
    except BudgetExceeded:
        raise
    errs = validate_arc_selector(res if isinstance(res, dict) else {}, valid)
    if errs:
        messages.append({"role": "user", "content": f"Validation failed: {errs}. Reply with corrected JSON ONLY."})
        try:
            res, _ = _jchat(llm, budget, load_config(root), "writer", messages, chunk["chunk_id"], "arc")
        except BudgetExceeded:
            raise
        errs = validate_arc_selector(res if isinstance(res, dict) else {}, valid)
        if errs:
            raise SystemExit(f"REFUSAL stage=arc check=selection chunk={chunk['chunk_id']} errors={errs}")
    sel_ids = set(int(x) for x in (res.get("setup_beat_ids", []) or []))
    sel_ids.update([int(res["turn_beat_id"]), int(res["payoff_beat_id"]), int(res["cliffhanger_beat_id"])])
    selected = [b for b in beats_file.get("beats", []) if int(b.get("beat_id", -1)) in sel_ids]
    payload = {**res, "selected_beat_ids": sorted(sel_ids),
               "inputs_sha256": inputs}
    atomic_write_json(apath, payload)
    return payload, selected


def do_draft(root: Path, sid: str, chunk_n: int, fake=None, force=False) -> dict:
    cfg = load_config(root)
    sd = series_dir(root, sid)
    source = read_json(sd / "00_source_raw.json")
    chunk = read_json(_chunk_path(root, sid, chunk_n))
    wdir = _workdir(root, sid, chunk["chunk_id"])
    beats_file = read_json(wdir / "beats.json")
    dpath = wdir / "draft.json"
    cand_dir = wdir / "candidates"
    try:
        cand_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    # R1: a chunk with a fresh terminal decision is skipped (no LLM, no rewrite).
    if not force and (wdir / "decision.json").exists():
        try:
            _existing = read_json(wdir / "decision.json")
        except Exception:
            _existing = None
        if _existing is not None:
            _fresh, _ = is_decision_fresh(wdir, _existing)
            if _fresh and dpath.exists():
                return read_json(dpath)
    routing = _routing_state(root, sid, chunk, beats_file)
    forb = routing.get("anti_repetition_tracker", {}).get("forbidden_for_current_ep", [])
    template = route_template(beats_file.get("genre_tags", []), forb)
    prev_hook = routing.get("anti_repetition_tracker", {}).get("prev_hook_option")
    hooks = allowed_hooks(prev_hook)
    from .draft.router import lru_hook_hint
    hook_choice = lru_hook_hint(hooks, routing.get("anti_repetition_tracker", {}).get("hook_history", []))
    llm = get_llm(cfg, root, sid, fake)
    budget = load_chunk_budget(cfg, root, sid, chunk["chunk_id"])
    # Q2: arc selection (writer call) before drafting; the writer sees only selected beats.
    try:
        _arc, _selected = select_arc(root, sid, chunk, beats_file, llm, budget,
                                     load_prompts(root), wdir, force=force)
    except BudgetExceeded as e:
        write_budget_stop(root, sid, chunk, wdir, f"budget stop in arc: {e}")
        raise
    try:
        from .quality import eff_drafting as _eff_dr
        _best_n = int(_eff_dr(cfg).get("best_of_n", 3))
    except Exception:
        _best_n = 3
    _best_n = max(1, min(3, _best_n))
    inputs = {"beats": sha256_file(wdir / "beats.json"), "template": sha256_text(template),
              "arc": sha256_file(wdir / "arc.json"),
              **llm_inputs_extra(cfg, root, "writer", "script_writer")}
    if not force and _inputs_match(dpath, inputs):
        return read_json(dpath)
    is_ep1 = routing.get("current_episode", 0) == 0
    # Q7: aged-up flag from the SELECTED beats (episode scope).
    try:
        _minor_now = aged_up_present_for_beats(_selected, routing)
    except Exception:
        _minor_now = False
    # Q11: best-of-N drafting — N drafts, each with a different allowed hook.
    from .llm.validate import validate_writer
    from .draft.router import normalize_hook as _norm_hook, LETTER_FOR_HOOK as _L4H
    _allowed_letters = [_L4H[h] for h in hooks if h in _L4H]
    _draft_hooks = hooks[:_best_n] if len(hooks) >= _best_n else hooks
    _candidates: list = []
    for _di, _hook_i in enumerate(_draft_hooks):
        _msgs = build_writer_messages(cfg, root, sid, routing, chunk, beats_file, template, _hook_i, is_ep1,
                                      selected_beats=_selected, minor_present=_minor_now)
        # Q11: each draft must use its assigned hook (different allowed hooks).
        # Override the suggested hook line to force the assigned one.
        try:
            _sys = _msgs[0]["content"].replace(f"SUGGESTED HOOK: {_msgs[0]['content'].split('SUGGESTED HOOK:')[-1].splitlines()[0]}",
                                               f"SUGGESTED HOOK: {_L4H.get(_hook_i, _hook_i)} (you MUST use this hook)")
            # simpler: append a forcing line
            _msgs[0]["content"] = _msgs[0]["content"] + f"\nASSIGNED HOOK (must use): {_L4H.get(_hook_i, _hook_i)}"
        except Exception:
            pass
        try:
            res, _ = _jchat(llm, budget, cfg, "writer", _msgs, chunk["chunk_id"], f"draft_{_di}")
        except BudgetExceeded as e:
            write_budget_stop(root, sid, chunk, wdir, f"budget stop in draft {_di}: {e}")
            raise
        werrs = validate_writer(res, template)
        _pick = _norm_hook(res.get("hook_option")) if isinstance(res, dict) else None
        # Q11: accept the assigned hook or any allowed hook (legacy fixtures use assigned).
        if not werrs and _pick not in hooks:
            werrs = [f"hook_option {res.get('hook_option')!r} not in allowed_hooks {hooks}"]
        if werrs:
            _msgs.append({"role": "user", "content": f"Validation failed: {werrs}. Allowed hooks: {_allowed_letters}. Reply with corrected JSON ONLY."})
            try:
                res, _ = _jchat(llm, budget, cfg, "writer", _msgs, chunk["chunk_id"], f"draft_{_di}")
            except BudgetExceeded as e:
                write_budget_stop(root, sid, chunk, wdir, f"budget stop in draft repair {_di}: {e}")
                raise
            werrs = validate_writer(res, template)
            _pick = _norm_hook(res.get("hook_option")) if isinstance(res, dict) else None
            if not werrs and _pick not in hooks:
                werrs = [f"hook_option {res.get('hook_option')!r} not in allowed_hooks {hooks}"]
            if werrs:
                raise SystemExit(f"REFUSAL stage=draft check=writer chunk={chunk['chunk_id']} errors={werrs}")
        if isinstance(res, dict) and res.get("hook_option") in ("Hook_A", "Hook_B", "Hook_C", "Hook_D"):
            try:
                res = dict(res)
                res["hook_option"] = _norm_hook(res.get("hook_option"))
            except Exception:
                pass
        _script_i = finalize_script(cfg, sid, source, chunk, template, res)
        # Q11: screen with Tier 1 (no LLM) and keep every draft (B2: no swallowing).
        _sel_text_parts = []
        for _b in (_selected or []):
            if isinstance(_b, dict) and _b.get("summary_en"):
                _sel_text_parts.append(str(_b["summary_en"]))
        try:
            _src_all = read_json(series_dir(root, sid) / "00_source_raw.json")
            _by_id_all = {p["id"]: p["text"] for p in _src_all.get("paragraphs", [])}
            for _b in (_selected or []):
                for _pid in (_b.get("source_paragraph_ids", []) or []):
                    if _pid in _by_id_all:
                        _sel_text_parts.append(str(_by_id_all[_pid]))
        except Exception:
            pass
        _sel_text = "\n".join(_sel_text_parts) or None
        _screen_ctx = {"selected_text": _sel_text, "minor_present": _minor_now,
                       "age_map": routing.get("age_map", {}) or {}}
        _g_i = run_gates_for_script(cfg, _script_i, template, routing, chunk, ctx=_screen_ctx)
        _tier1_ok = bool(_g_i.get("passed"))
        _candidates.append((_script_i, _hook_i, _tier1_ok, res))
        try:
            atomic_write_json(cand_dir / f"draft_{_di}.json",
                              {**{k: v for k, v in _script_i.items() if not k.startswith('_')},
                               "_hook": _hook_i, "_tier1_ok": _tier1_ok})
        except Exception:
            pass
    # Q11: pick by code — Tier1 passers first, then first (judge screening happens in audit).
    _passers = [c for c in _candidates if c[2]]
    _best = (_passers[0] if _passers else _candidates[0]) if _candidates else None
    if _best is None:
        raise SystemExit(f"REFUSAL stage=draft check=writer chunk={chunk['chunk_id']} errors=['no drafts']")
    script = _best[0]
    payload = {**script, "inputs_sha256": inputs, "_template": template,
               "_best_of_n": len(_candidates), "_draft_hook": _best[1]}
    atomic_write_json(dpath, payload)
    return payload


def _prior_episode_texts(root: Path, sid: str, limit: int = 3,
                          before_chunk: str | None = None) -> tuple[list, list, list]:
    """Q6/B6 history: prior Beat1 hooks, CTA outros and full spoken texts (oldest→newest).

    B6: use the selected candidate of every earlier episode of the series,
    whatever its decision (RELEASE, NEEDS_HUMAN, REJECT...). With
    human_review.mode=all, episodes land in review/, so out/-only history
    would pass vacuously. The selected candidate is the final script for the
    chunk's decision rewrite_count (draft/rewrite_N). Chunks at or after
    before_chunk (the chunk being gated) are excluded so a resumed chunk does
    not match itself.
    """
    hooks, ctas, texts = [], [], []
    wd = root / "run" / sid / "work"
    if wd.exists():
        for dpath in sorted(wd.glob("*/decision.json")):
            try:
                if before_chunk is not None and dpath.parent.name >= before_chunk:
                    continue
                dec = json.loads(dpath.read_text(encoding="utf-8"))
                wdir = dpath.parent
                rw = int(dec.get("rewrite_count", 0))
                spath = wdir / ("draft.json" if rw == 0 else f"rewrite_{rw}.json")
                if not spath.exists():
                    continue
                s = json.loads(spath.read_text(encoding="utf-8"))
                beats = s.get("scene_beats", []) or []
                if beats:
                    hooks.append(str(beats[0].get("narration_text", "") or ""))
                ctas.append(str(s.get("call_to_action", {}).get("outro_narration", "") or ""))
                parts = [str(b.get("narration_text", "") or "") for b in beats]
                parts.append(str(s.get("call_to_action", {}).get("outro_narration", "") or ""))
                texts.append(" ".join(parts))
            except Exception:
                continue
    hooks, ctas, texts = hooks[-limit:], ctas[-limit:], texts[-limit:]
    try:
        from .feedback.review import load_hook_library
        lib = load_hook_library(root) or {}
        for _opt, items in (lib or {}).items():
            for it in (items or [])[:2]:
                if isinstance(it, dict) and it.get("hook_text"):
                    hooks.append(str(it["hook_text"]))
    except Exception:
        pass
    return hooks, ctas, texts


def detect_narration_lock(script: dict, registry: list | None = None) -> dict:
    """Q4: detect POV/tense from the episode-1 script for the series voice lock."""
    try:
        parts = [str(b.get("narration_text", "") or "") for b in script.get("scene_beats", [])]
        parts.append(str(script.get("call_to_action", {}).get("outro_narration", "") or ""))
        text = " ".join(parts)
        plain = re.sub(r'"[^"]*"', ' ', text)
        plain = re.sub(r"“[^”]*”", ' ', plain)
        first = len(re.findall(r"\b(i|my|me|mine|myself|i'm|i've|i'll|i'd|we|our|us|ourselves)\b", plain, re.I))
        third = len(re.findall(r"\b(he|him|his|she|her|hers|they|them|their|theirs)\b", plain, re.I))
        total = first + third
        ratio = (first / total) if total else 1.0
        if ratio >= 0.60:
            pov = "first_person:narrator"
            try:
                mains = [c.get("us_name", "") for c in (registry or []) if isinstance(c, dict) and c.get("main_cast") and c.get("us_name")]
                if mains:
                    pov = f"first_person:{mains[0]}"
            except Exception:
                pass
        else:
            pov = "third_person" if third > 0 else "third_person"
            if first == 0 and third == 0:
                pov = "third_person"
        past = len(re.findall(r"\b(was|were|had|did|said|went|saw|felt|heard|knew|thought|told|found|left|came)\b", text, re.I))
        present = len(re.findall(r"\b(is|are|have|has|do|does|say|go|see|feel|know|think|tell|find|come|am)\b", text, re.I))
        tense = "past" if past >= present else "present"
        return {"pov": pov, "tense": tense}
    except Exception:
        return {"pov": "third_person", "tense": "past"}


def is_minor_age(age_or_stage: str | None) -> bool:
    """Q7/B5: source age < 18 or school-level stage counts as a minor.

    College/university/graduate students are adults, never minors.
    """
    from .ageup import is_minor_source
    return bool(is_minor_source(age_or_stage))


def aged_up_present_for_beats(beats: list | None, state: dict | None) -> bool:
    """True when the episode involves any aged-up character (Q7 gate scope)."""
    try:
        age_map = (state or {}).get("age_map") or {}
        names: set[str] = set()
        for b in beats or []:
            if isinstance(b, dict):
                for ch in b.get("characters", []) or []:
                    names.add(str(ch))
        for n in names:
            e = age_map.get(n)
            if isinstance(e, dict) and e.get("adapted_age") is not None:
                return True
        # Fallback: source-minor without a frozen map yet (first extraction).
        reg = (state or {}).get("character_registry", []) or []
        by_name = {}
        for c in reg or []:
            if isinstance(c, dict) and c.get("original_name"):
                by_name[str(c["original_name"])] = str(c.get("age_or_stage", "unknown"))
        for n in names:
            if is_minor_age(by_name.get(n, "unknown")):
                return True
        return False
    except Exception:
        return False


def minor_present_for_beats(beats: list | None, registry: list | None) -> bool:
    # Kept for backward compat: source-minor check (extraction scope).
    by_name = {}
    for c in registry or []:
        if isinstance(c, dict) and c.get("original_name"):
            by_name[str(c["original_name"])] = str(c.get("age_or_stage", c.get("source_age_or_stage", "unknown")))
    for b in beats or []:
        if not isinstance(b, dict):
            continue
        for ch in b.get("characters", []) or []:
            if is_minor_age(by_name.get(str(ch), "unknown")):
                return True
    return False


def run_gates_for_script(cfg, script: dict, template: str, state: dict, chunk: dict,
                         ctx: dict | None = None) -> dict:
    from .gates.tier1.checks import run_tier1 as _run
    sm = script["script_metadata"]
    forb = state.get("anti_repetition_tracker", {}).get("forbidden_for_current_ep", [])
    ctx = dict(ctx or {})
    # Q4 voice lock from series state (B3: key must be present; {} = ep1, no lock yet).
    if "narration_lock" not in ctx:
        ctx["narration_lock"] = state.get("narration", {}) or {}
    # Q5 names: frozen us-name tokens + market allowlist.
    if "allowed_names" not in ctx:
        _frozen = [c.get("us_name", "") for c in state.get("character_registry", [])]
        _allow = set()
        for _n in _frozen:
            for _tok in re.findall(r"[A-Za-z']+", str(_n or "")):
                if _tok:
                    _allow.add(_tok)
        for _a in (cfg.market.get("name_allowlist", []) or []):
            _allow.add(str(_a))
        ctx["allowed_names"] = _allow
    # Q5 episode number: CTA cliffhanger must reference episode_number+1
    # (spec §17.3 Q5). Episode_number will be current+1, so expect current+2.
    if "expected_episode" not in ctx:
        ctx["expected_episode"] = int(state.get("current_episode", 0)) + 2
    # Q6 history (B6: work/ selected candidates, whatever the decision;
    # exclude the chunk being gated and later ones so a resume never matches itself).
    if "prior_hooks" not in ctx or "prior_ctas" not in ctx or "episode_texts" not in ctx:
        _h, _c, _t = _prior_episode_texts(cfg.root, state.get("series_id", ""),
                                          before_chunk=chunk.get("chunk_id"))
        ctx.setdefault("prior_hooks", _h)
        ctx.setdefault("prior_ctas", _c)
        ctx.setdefault("episode_texts", _t)
    # Q7 age map (B1/B3: must be present; {} = no aged-up characters).
    if "age_map" not in ctx:
        ctx["age_map"] = state.get("age_map", {}) or {}
    # selected_text and minor_present come from the caller (_eval/do_draft);
    # B3 refuses if they are missing (no silent disabling).
    # T1-08: only foreign originals that differ from the frozen US name leak.
    _origs = []
    for _c in state.get("character_registry", []) or []:
        try:
            _o, _u = str(_c.get("original_name", "") or ""), str(_c.get("us_name", "") or "")
            if _o and _o.lower() != _u.lower():
                _origs.append(_o)
        except Exception:
            continue
    receipt = _run({"scene_beats": script["scene_beats"], "call_to_action": script["call_to_action"]},
                   cfg, template, sm.get("hook_option", ""), sm.get("hook_payoff_beat_id", 4),
                   script["call_to_action"].get("type", ""), chunk.get("is_hard_boundary_end", False),
                   forb, _origs,
                   ctx=ctx)
    receipt["script_sha256"] = _script_binding_sha(script)
    return receipt


# ---------- Tier 2 + rewrite loop + routing ----------

def do_audit(root: Path, sid: str, chunk_n: int, fake=None, force=False) -> dict:
    cfg = load_config(root)
    sd = series_dir(root, sid)
    chunk = read_json(_chunk_path(root, sid, chunk_n))
    wdir = _workdir(root, sid, chunk["chunk_id"])
    prompts = load_prompts(root)
    llm = get_llm(cfg, root, sid, fake)
    budget = load_chunk_budget(cfg, root, sid, chunk["chunk_id"])
    state = read_json(sd / "series_state.json")
    is_mid = state.get("current_episode", 0) >= 1
    is_hard = chunk.get("is_hard_boundary_end", False)
    from .quality import eff_drafting, eff_release
    _dr = eff_drafting(cfg)
    _rel = eff_release(cfg)
    max_rw = int(_dr["max_rewrite_attempts"])
    pass_th = float(_rel["overall_min"])
    rewrite_min = float(_dr["rewrite_min"])
    _dim_floor = float(_rel["dimension_floor"])
    _zero_tol = list(_rel["zero_tolerance"])
    # Q7(g): hard exclusion — sexual content involving source age < 18.
    # Aging up never launders that. Immediate REJECT_SAFETY + blacklist.
    try:
        from .ageup import detect_minor_sexual_content
        _amap = state.get("age_map", {}) or {}
        if not _amap:
            try:
                _bf0 = read_json(wdir / "beats.json")
                from .ageup import build_age_map as _bam
                _amap = _bam(state.get("character_registry", []),
                             _bf0.get("new_entities", []) or [])
            except Exception:
                _amap = {}
        if detect_minor_sexual_content(chunk.get("raw_text_content", ""), _amap):
            try:
                _cur0 = load_script(0)
            except Exception:
                _cur0 = None
            dec = write_decision(root, sid, chunk, wdir, "REJECT_SAFETY", _cur0,
                                 0.0, False, 0,
                                 "minor_sexual_content: source has sexual content involving source age < 18",
                                 audit={"hard_exclusion": "minor_sexual_content"})
            try:
                from .breakers.policies import add_blacklist as _abl
                _abl(root, read_json(sd / "00_source_raw.json").get("source_sha256", ""),
                     "minor_sexual_content", sid)
            except Exception:
                pass
            apply_terminal_hard_reset(root, sid, chunk)
            return dec
    except SystemExit:
        raise
    except Exception:
        pass

    def load_script(k: int) -> dict:
        if k == 0:
            return read_json(wdir / "draft.json")
        return read_json(wdir / f"rewrite_{k}.json")

    # resume: return existing decision only if bound to the exact script it judged
    if not force and (wdir / "decision.json").exists():
        try:
            existing = read_json(wdir / "decision.json")
        except Exception:
            existing = None
        if existing is not None:
            fresh, _why = is_decision_fresh(wdir, existing)
            if fresh:
                return existing
            # stale: wipe derived receipts so the loop below re-judges the current script
            import glob as _glob  # noqa: F401
            for pat in ("gates_*.json", "audit_*.json", "rewrite_*.json", "decision.json"):
                for p in sorted(wdir.glob(pat)):
                    try:
                        p.unlink()
                    except OSError:
                        pass

    current = load_script(0)
    template = current.get("_template") or current.get("script_metadata", {}).get("pacing_template")
    # Q12: every candidate kept in work/<chunk>/candidates/; the export is
    # always the best candidate (monotonic rewrites).
    cand_dir = wdir / "candidates"
    try:
        cand_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        atomic_write_json(cand_dir / "candidate_0.json",
                          {**{k: v for k, v in current.items() if not k.startswith("_")},
                           "_round": 0})
    except Exception:
        pass
    from .gates.tier2.scoring import decide_release

    def _arc_selected_beats() -> list | None:
        try:
            if (wdir / "arc.json").exists():
                _arc = read_json(wdir / "arc.json")
                _sel = set(int(x) for x in (_arc.get("selected_beat_ids", []) or []))
                _bf = read_json(wdir / "beats.json")
                _out = [b for b in _bf.get("beats", []) if int(b.get("beat_id", -1)) in _sel]
                return _out or None
        except Exception:
            pass
        return None

    def _selected_text_for_gates() -> str | None:
        try:
            _bf = read_json(wdir / "beats.json")
            _sel_beats = _arc_selected_beats()
            _beats = _sel_beats if _sel_beats is not None else (_bf.get("beats", []) or [])
            _parts = []
            for _b in _beats:
                if isinstance(_b, dict) and _b.get("summary_en"):
                    _parts.append(str(_b["summary_en"]))
            # source paragraphs for the SELECTED beats give verbatim grounding.
            try:
                _src = read_json(sd / "00_source_raw.json")
                _by_id = {p["id"]: p["text"] for p in _src.get("paragraphs", [])}
                _pids: list = []
                for _b in _beats:
                    if isinstance(_b, dict):
                        _pids.extend(_b.get("source_paragraph_ids", []) or [])
                for _pid in _pids[:20]:
                    if _pid in _by_id:
                        _parts.append(str(_by_id[_pid]))
            except Exception:
                _parts.append(str(chunk.get("raw_text_content", ""))[:4000])
            return "\n".join(_parts) or None
        except Exception:
            return None

    def _minor_present_now() -> bool:
        try:
            _bf = read_json(wdir / "beats.json")
            _sel_beats = _arc_selected_beats()
            _beats = _sel_beats if _sel_beats is not None else (_bf.get("beats", []) or [])
            return aged_up_present_for_beats(_beats, state)
        except Exception:
            return False

    def _select_best_of_n(current: dict, template: str, state: dict, chunk: dict):
        """Q11/C5: Tier1 screen N drafts, return top 2 for panel+fidelity (no LLM).

        C5: on the production path fewer than best_of_n candidates or any
        error is a REFUSAL, never a silent single-draft fallback.
        """
        from .quality import eff_drafting as _eff_dr2
        _need_n = int(_eff_dr2(cfg)["best_of_n"])
        # load candidates (best-of-N drafts from do_draft)
        cands: list = []
        for _fp in sorted((wdir / "candidates").glob("draft_*.json")):
            _s = read_json(_fp)
            _script = {k: v for k, v in _s.items() if not k.startswith("_") and k != "inputs_sha256"}
            _script.setdefault("script_metadata", {})["pacing_template"] = template
            cands.append(_script)
        if len(cands) < _need_n:
            raise SystemExit(f"REFUSAL stage=audit check=best_of_n "
                             f"candidates={len(cands)} need={_need_n} chunk={chunk['chunk_id']}")
        # Tier1 screen (no LLM) — top 2 passers (or first 2 if none pass)
        _ctx0 = {"selected_text": _selected_text_for_gates(), "minor_present": _minor_present_now(),
                 "age_map": state.get("age_map", {}) or {}}
        _passers: list = []
        for _s in cands:
            _g = run_gates_for_script(cfg, _s, template, state, chunk, ctx=dict(_ctx0))
            if _g.get("passed"):
                _passers.append(_s)
        _top2 = (_passers[:2] if len(_passers) >= 2 else cands[:2])
        # Full panel+fidelity happens in the caller (_eval) for each top-2
        # candidate, which picks the final best by release-eligible/overall/
        # not_in_source (Q11).
        return _top2

    def _eval(script: dict, k: int):
        import random as _rnd
        import copy as _cpy
        from .gates.tier2.scoring import (verify_checklist_evidence, checklist_to_scores,
                                          aggregate_panel, split_claims, verify_fidelity)
        from .llm.validate import validate_judge, validate_checklist, validate_fidelity
        _ctx = {"selected_text": _selected_text_for_gates(),
                "minor_present": _minor_present_now()}
        gates = run_gates_for_script(cfg, script, template, state, chunk, ctx=_ctx)
        atomic_write_json(wdir / f"gates_{k}.json", {**gates, "inputs_sha256": {"script": gates["script_sha256"]}})
        tier1_ok = gates["passed"]
        audit = None
        overall = None
        if tier1_ok:
            beats_file = read_json(wdir / "beats.json")
            # Q9: panel — 2 models x 2 samples, beats in fresh random order per sample.
            # C2/C5: locked panel/samples, no pipeline fallback; fewer models is a
            # refusal on the production path, never a silent single-model fallback.
            from .quality import eff_judging
            _panel_models = list(eff_judging(cfg)["panel"])
            _samples = int(eff_judging(cfg)["samples_per_judge"])
            # Q14 production gate: every panel model (and the fidelity judge model,
            # which is the panel family) needs a green calibration receipt.
            # FakeLLMClient (tests, --fake-fixtures) skips it so the offline
            # suite stays green; live runs enforce it. No exception swallowing.
            if not _is_test_double(llm):
                from .calibrate.runner import receipt_ok as _rok
                for _mid in _panel_models[:2]:
                    _mid = str(_mid)
                    if not _rok(root, _mid):
                        raise SystemExit(f"REFUSAL stage=audit check=calibration model={_mid} (no green receipt for current prompt+model)")
            _script_text = " ".join([str(b.get("narration_text", "") or "") for b in script.get("scene_beats", [])]
                                    + [str(script.get("call_to_action", {}).get("outro_narration", "") or "")])
            try:
                _valid_pids = set(int(x) for x in (chunk.get("paragraph_ids", []) or []))
            except Exception:
                _valid_pids = set()
            _verified_samples: list = []
            _panel_seeds: list = []
            for _mi, _model_id in enumerate(_panel_models[:2]):
                for _si in range(_samples):
                    # C3: reproducible seeds from sha256 (Python hash() is salted
                    # per process and breaks rerun=0-calls).
                    import hashlib as _hl
                    _seed_src = f"{chunk['chunk_id']}|{k}|{_model_id}|{_si}".encode("utf-8")
                    _seed = int(_hl.sha256(_seed_src).hexdigest()[:8], 16) % (2 ** 31)
                    _panel_seeds.append({"model": _model_id, "seed": _seed, "sample": _mi * _samples + _si})
                    _beats_ordered = _cpy.deepcopy(beats_file.get("beats", []) or [])
                    _rnd.Random(_seed).shuffle(_beats_ordered)
                    _messages = [{"role": "system", "content": prompts.get("checklist_judge") or prompts["audit_judge"]},
                                 {"role": "user", "content": json.dumps(
                                     {"script": {k2: v2 for k2, v2 in script.items() if not k2.startswith('_')},
                                      "compiled_state": compile_state(state, beats=beats_file.get("beats", [])),
                                      "frozen_names": frozen_names(state.get("character_registry", [])),
                                      "age_map": state.get("age_map", {}),
                                      "setting_map": state.get("setting_map", {}),
                                      "chunk_beats": _beats_ordered,
                                      "order_seed": _seed}, ensure_ascii=False)}]
                    _cfg_m = cfg
                    try:
                        import copy as _cc
                        _cfg_m = _cc.copy(cfg)
                        _cfg_m.pipeline = dict(cfg.pipeline)
                        _cfg_m.pipeline["models"] = dict(cfg.pipeline.get("models", {}))
                        _cfg_m.pipeline["models"]["judge"] = dict(cfg.pipeline["models"]["judge"])
                        _cfg_m.pipeline["models"]["judge"]["model_id"] = _model_id
                    except Exception:
                        _cfg_m = cfg
                    try:
                        res, _ = _jchat(llm, budget, _cfg_m, "judge", _messages, chunk["chunk_id"], "judge")
                    except BudgetExceeded:
                        raise
                    # C1: production path — a non-checklist reply is a validation
                    # failure (repair retry, then REFUSAL). No legacy scores path.
                    if not isinstance(res, dict) or "items" not in res:
                        _cerrs = [f"checklist reply missing items (got keys {sorted(res.keys()) if isinstance(res, dict) else type(res)})"]
                    else:
                        _cerrs = validate_checklist(res)
                    if _cerrs:
                        _messages.append({"role": "user", "content": f"Validation failed: {_cerrs}. Reply with corrected JSON ONLY."})
                        try:
                            res, _ = _jchat(llm, budget, _cfg_m, "judge", _messages, chunk["chunk_id"], "judge")
                        except BudgetExceeded:
                            raise
                        _cerrs = validate_checklist(res if isinstance(res, dict) else {})
                        if _cerrs:
                            raise SystemExit(f"REFUSAL stage=audit check=checklist chunk={chunk['chunk_id']} errors={_cerrs}")
                    _ver, _verrs = verify_checklist_evidence((res.get("items", {}) or {}), _script_text, _valid_pids)
                    _verified_samples.append(_ver)
            agg, split_items, panel_split = aggregate_panel(_verified_samples)
            scores, overall, payoff = checklist_to_scores(agg)
            # Q10: fidelity checker (separate judge-family call)
            _claims = split_claims({k2: v2 for k2, v2 in script.items() if not k2.startswith('_')})
            _src_by_id = {}
            try:
                _src = read_json(sd / "00_source_raw.json")
                _src_by_id = {p["id"]: p["text"] for p in _src.get("paragraphs", [])}
            except Exception:
                pass
            _sel_pids: list = []
            try:
                _arc_sel = _arc_selected_beats()
                _beats_for_fid = _arc_sel if _arc_sel is not None else beats_file.get("beats", [])
                for _b in _beats_for_fid or []:
                    _sel_pids.extend(_b.get("source_paragraph_ids", []) or [])
            except Exception:
                pass
            # C5: pass every selected-beat paragraph; refuse if it exceeds
            # the prompt budget (mirrors the R17 8-part attachment cap).
            _fid_src = [{"id": pid, "text": _src_by_id.get(pid, "")} for pid in sorted(set(_sel_pids))]
            try:
                from .llm.opencode_backend import PROMPT_PART_MAX_BYTES as _PPB
                _fid_bytes = len(json.dumps(_fid_src, ensure_ascii=False).encode("utf-8"))
                if _fid_bytes > 8 * int(_PPB):
                    raise SystemExit(f"REFUSAL stage=fidelity check=prompt_too_large "
                                     f"bytes={_fid_bytes} max={8 * int(_PPB)} chunk={chunk['chunk_id']}")
            except SystemExit:
                raise
            except Exception:
                pass
            _fid_messages = [{"role": "system", "content": prompts.get("fidelity_checker", "")},
                             {"role": "user", "content": json.dumps({"claims": _claims, "source_paragraphs": _fid_src}, ensure_ascii=False)}]
            try:
                _fres, _ = _jchat(llm, budget, cfg, "judge", _fid_messages, chunk["chunk_id"], "fidelity")
            except BudgetExceeded:
                raise
            _ferrs = validate_fidelity(_fres if isinstance(_fres, dict) else {})
            if _ferrs:
                _fid_messages.append({"role": "user", "content": f"Validation failed: {_ferrs}. Reply with corrected JSON ONLY."})
                try:
                    _fres, _ = _jchat(llm, budget, cfg, "judge", _fid_messages, chunk["chunk_id"], "fidelity")
                except BudgetExceeded:
                    raise
                _ferrs = validate_fidelity(_fres if isinstance(_fres, dict) else {})
                if _ferrs:
                    raise SystemExit(f"REFUSAL stage=fidelity check=claims chunk={chunk['chunk_id']} errors={_ferrs}")
            from .quality import eff_release as _eff_rel
            _max_nis = int(_eff_rel(cfg)["max_not_in_source"])
            _fid_sum, _fid_errs = verify_fidelity(_fres, _claims, _valid_pids, _max_nis)
            # knowledge_violations <- contradicted claims (E1)
            _kv = [c.get("claim", "") for c in (_fres.get("claims", []) or []) if isinstance(c, dict) and c.get("verdict") == "contradicted"]
            # derive failed beats from failing items (H1->1, H2->4/5, etc.)
            _item_to_beats = {"H1": [1], "H2": [4, 5], "P1": [1, 2, 3, 4, 5], "P2": [1, 2, 3, 4, 5],
                              "L1": [1, 2, 3, 4, 5], "L2": [1, 2, 3, 4, 5], "L3": [2],
                              "R1": [5], "S1": [1, 2, 3, 4, 5], "V1": [1, 2, 3, 4, 5]}
            _failed_set: set = set()
            for _iname, _val in agg.items():
                if _val is not True:
                    _failed_set.update(_item_to_beats.get(_iname, []))
            audit = {"scores": scores, "overall": overall, "payoff_resolved": payoff,
                     "knowledge_violations": _kv,
                     "failed_beat_ids": sorted(_failed_set) or [1, 2, 3, 4, 5],
                     "rewrite_instructions": [{"beat_id": b, "issue": ",".join(sorted([k for k, v in agg.items() if v is not True])),
                                                "instruction": f"Fix failing checklist items; Tier1 limits apply."} for b in sorted(_failed_set) or [1]],
                     "checklist": agg, "panel_split": panel_split, "panel_seeds": _panel_seeds,
                     "panel_models": _panel_models[:2], "fidelity": _fid_sum}
            try:
                from .calibrate.runner import _rubric_sha as _rsha
                _rsha_val = _rsha(root)
            except Exception:
                _rsha_val = ""
            audit["rubric_sha256"] = _rsha_val
            audit_inputs = {"script": gates["script_sha256"],
                              **llm_inputs_extra(cfg, root, "judge", "checklist_judge")}
            atomic_write_json(wdir / f"audit_{k}.json", {**audit, "inputs_sha256": audit_inputs})
        else:
            instr = tier1_to_instructions(gates)
            bids = sorted({i["beat_id"] for i in instr if isinstance(i.get("beat_id"), int)})
            try:
                from .calibrate.runner import _rubric_sha as _rsha3
                _rsha3v = _rsha3(root)
            except Exception:
                _rsha3v = ""
            atomic_write_json(wdir / f"audit_{k}.json",
                              {"scores": {}, "overall": None, "payoff_resolved": True,
                               "knowledge_violations": [], "failed_beat_ids": bids,
                               "rewrite_instructions": instr, "rubric_sha256": _rsha3v,
                               "tier1_failed": True, "inputs_sha256": {"script": gates["script_sha256"]}})
            audit = read_json(wdir / f"audit_{k}.json")
        return gates, audit, overall, tier1_ok

    def _release_eligible(gates_ok: bool, audit: dict | None, overall) -> tuple[bool, str]:
        if not gates_ok or audit is None or overall is None:
            return False, "tier1/overall missing"
        if not audit.get("payoff_resolved", True):
            return False, "payoff not resolved"
        # E1/Q13 + Q10: invented/contradicted facts block release.
        if audit.get("knowledge_violations"):
            return False, f"knowledge_violations={len(audit['knowledge_violations'])}"
        _fid = audit.get("fidelity") or {}
        if _fid and not _fid.get("ok", True):
            return False, f"fidelity fail: {_fid.get('reason', '')}"
        try:
            _lock_rel2 = (getattr(cfg, "lock", {}) or {}).get("release", {}) or {}
            _cm = int(_lock_rel2.get("contradicted_claims_max", 0))
            if int((_fid or {}).get("contradicted", 0)) > _cm:
                return False, f"contradicted={_fid.get('contradicted')} > max {_cm}"
        except Exception:
            pass
        scores = audit.get("scores", {}) or {}
        for dim, val in scores.items():
            try:
                if float(val) < _dim_floor:
                    return False, f"dimension {dim}={val} below floor {_dim_floor}"
            except (TypeError, ValueError):
                return False, f"dimension {dim} invalid"
        if float(overall) < pass_th:
            return False, f"overall {overall} below {pass_th}"
        verdict, reason = decide_release(True, overall, scores, audit.get("checklist"),
                                          bool(audit.get("panel_split", False)),
                                          pass_th, _dim_floor, _zero_tol)
        return (verdict == "RELEASE"), reason

    best_script = current
    best_k = 0
    # Q11/C5: best-of-N top-2 selection. Fewer than best_of_n candidates or
    # any error is a REFUSAL on the production path, never a silent fallback.
    _cand_files = sorted((wdir / "candidates").glob("draft_*.json")) if (wdir / "candidates").exists() else []
    _use_best_of_n = len(_cand_files) >= 2
    if _use_best_of_n:
        try:
            _top2 = _select_best_of_n(current, template, state, chunk)
            # Q11: panel+fidelity only on top 2, pick by code.
            _best_pick = None
            _best_key = None
            for _s in _top2:
                _g, _a, _o, _t = _eval(_s, 0)
                _elig, _ = _release_eligible(_t, _a, _o)
                _nis = int(((_a.get("fidelity", {}) or {}).get("not_in_source", 0)))
                _key = (1 if _elig else 0, _o if _o is not None else -1, -_nis)
                if _best_key is None or _key > _best_key:
                    _best_key = _key
                    _best_pick = (_s, _g, _a, _o, _t)
            if _best_pick is None:
                raise SystemExit(f"REFUSAL stage=audit check=best_of_n "
                                 f"no best in top2 chunk={chunk['chunk_id']}")
            best_script = _best_pick[0]
            # keep draft.json bound to the picked best for R1/export binding
            if _script_binding_sha(read_json(wdir / "draft.json")) != _script_binding_sha(best_script):
                atomic_write_json(wdir / "draft.json", {**best_script, "inputs_sha256": read_json(wdir / "draft.json").get("inputs_sha256", {}), "_template": template})
            current = best_script
            # stash top2 pick to skip re-eval below (set a flag via best_k=-1 + cached)
            _top2_pick = _best_pick  # (script, gates, audit, overall, tier1)
        except BudgetExceeded as e:
            return write_budget_stop(root, sid, chunk, wdir, f"budget stop in best-of-N: {e}", current, 0)
        except SystemExit:
            raise
    else:
        _top2_pick = None
    if "_top2_pick" in locals() and _top2_pick is not None:
        _best_s, _g0, _a0, _o0, _t0 = _top2_pick
        best_script, current = _best_s, _best_s
        # Q11: persist the picked best's receipts as round 0 so export binding
        # (decision/gates/audit all bound to the same script sha) holds.
        try:
            atomic_write_json(wdir / "gates_0.json", {**_g0, "inputs_sha256": {"script": _g0["script_sha256"]}})
        except Exception:
            pass
        try:
            audit_inputs = {"script": _g0["script_sha256"],
                            **llm_inputs_extra(cfg, root, "judge", "checklist_judge")}
            atomic_write_json(wdir / "audit_0.json", {**_a0, "inputs_sha256": audit_inputs})
        except Exception:
            pass
    else:
        try:
            _g0, _a0, _o0, _t0 = _eval(current, 0)
        except BudgetExceeded as e:
            return write_budget_stop(root, sid, chunk, wdir, f"budget stop: {e}", current, 0)
    best_gates_ok, best_audit, best_overall, best_tier1 = _g0["passed"], _a0, _o0, _t0
    last_audit = _a0
    cur_script = current
    # Q7: minor-safety fail → REJECT_SAFETY immediately, never rewritten or passed.
    if "T1-23" in (_g0.get("failed_ids", []) or []):
        dec = write_decision(root, sid, chunk, wdir, "REJECT_SAFETY", best_script,
                              best_overall if best_overall is not None else 0.0,
                              False, 0, "minor-safety T1-23", audit=best_audit)
        apply_terminal_hard_reset(root, sid, chunk)
        return dec
    # immediate release check (also handles score<5 no-rewrite: no rewrite consumed)
    _eligible, _reason = _release_eligible(best_tier1, best_audit, best_overall)
    if _eligible:
        return write_decision(root, sid, chunk, wdir, "RELEASE", best_script, best_overall,
                              True, 0, f"release: {_reason}", audit=best_audit)
    for attempt in range(1, max_rw + 1):
        # rewrite needed? Tier1 fail always rewrites; scores in [rewrite_min, pass_th) rewrite;
        # score<5 at first audit never rewrites (T4) -> straight to routing below.
        need_rw = (not best_tier1) or (best_overall is not None and rewrite_min <= best_overall < pass_th)
        # Q12: a Tier 1 failure is never release-eligible; scores < rewrite_min
        # (and payoff-unresolved excluded from release) do not consume rewrites
        # on the first pass — they go straight to routing.
        if not need_rw:
            break
        try:
            candidate = do_rewrite_step(cfg, root, sid, chunk, wdir, best_script, last_audit,
                                        template, attempt, llm, prompts, budget)
        except BudgetExceeded as e:
            return write_budget_stop(root, sid, chunk, wdir, f"budget stop in rewrite: {e}",
                                     best_script, best_k)
        try:
            atomic_write_json(cand_dir / f"candidate_{attempt}.json",
                              {**{k: v for k, v in candidate.items() if not k.startswith("_")},
                               "_round": attempt})
        except Exception:
            pass
        try:
            g_new, a_new, o_new, t_new = _eval(candidate, attempt)
        except BudgetExceeded as e:
            return write_budget_stop(root, sid, chunk, wdir, f"budget stop in audit: {e}",
                                     best_script, best_k)
        # Q7: safety fail on any candidate → immediate REJECT_SAFETY.
        if "T1-23" in (g_new.get("failed_ids", []) or []):
            dec = write_decision(root, sid, chunk, wdir, "REJECT_SAFETY", candidate,
                                  o_new if o_new is not None else 0.0,
                                  False, attempt, "minor-safety T1-23", audit=a_new)
            apply_terminal_hard_reset(root, sid, chunk)
            return dec
        # Q12 monotonic accept: Tier1 must pass; no new failing checklist item
        # (Phase A: Tier1 failed_ids must not grow + overall must not regress).
        accept = True
        accept_reason = "accepted"
        if not t_new and best_tier1:
            accept, accept_reason = False, "revert: rewrite introduced Tier1 failure"
        elif t_new and not best_tier1:
            accept = True
        elif not t_new and not best_tier1:
            accept = True  # both fail: keep fixing from the new candidate
        else:
            try:
                if o_new is None or best_overall is None or float(o_new) < float(best_overall):
                    accept, accept_reason = False, f"revert: {o_new} < incumbent {best_overall}"
            except (TypeError, ValueError):
                accept, accept_reason = False, "revert: invalid overall"
        if accept:
            best_script, best_k = candidate, attempt
            best_gates_ok, best_audit, best_overall, best_tier1 = t_new, a_new, o_new, t_new
            last_audit = a_new
            cur_script = candidate
        else:
            # revert: the incumbent stays the export; the rejected candidate
            # stays in candidates/ + rewrite_<attempt>.json as evidence.
            # The decision binds to best_k, so no re-evaluation is needed.
            cur_script = best_script
            last_audit = best_audit
        _eligible, _reason = _release_eligible(best_tier1, best_audit, best_overall)
        if _eligible:
            return write_decision(root, sid, chunk, wdir, "RELEASE", best_script, best_overall,
                                  True, best_k, f"release after {attempt} rewrite(s): {_reason}",
                                  audit=best_audit)
    # exhausted or hard reject zone (uses the BEST candidate, never a worse rewrite)
    overall_val = best_overall if best_overall is not None else 0.0
    standalone = not is_mid
    if standalone or is_hard:
        dec = write_decision(root, sid, chunk, wdir, "REJECT", best_script, overall_val, best_tier1, best_k, "standalone/ep1/hard-boundary -> reject", audit=best_audit)
        apply_terminal_hard_reset(root, sid, chunk)
        return dec
    # Q13: mid-series near-miss (Tier1 pass, overall at/above the dimension
    # floor but not releasable) -> NEEDS_HUMAN (review/), never out/.
    # Scores below the floor (e.g. 5.x) still merge. FORCED_PASS is removed.
    if best_tier1 and best_overall is not None and best_overall >= _dim_floor:
        return write_decision(root, sid, chunk, wdir, "NEEDS_HUMAN", best_script, overall_val, True, best_k, "mid-series near-miss -> human review", audit=best_audit)
    # mid-series, non-hard: Tier 1 failure is never force-passed -> merge.
    try:
        do_fast_forward(cfg, root, sid, chunk, wdir, llm, prompts, state, budget)
    except BudgetExceeded as e:
        return write_budget_stop(root, sid, chunk, wdir, f"budget stop in fast-forward: {e}", best_script, best_k)
    return write_decision(root, sid, chunk, wdir, "FAST_FORWARD_MERGE", best_script, overall_val, best_tier1, best_k, "mid-series fail -> merge", audit=best_audit)


def tier1_to_instructions(gates: dict) -> list[dict]:
    out = []
    for c in gates.get("checks", []):
        if not c.get("pass") and not c.get("warning_only"):
            bids = c.get("beat_ids") or [1, 2, 3, 4, 5]
            for b in bids:
                if isinstance(b, int):
                    out.append({"beat_id": b, "issue": c["id"], "instruction": f"Fix {c['id']}: measured={c.get('measured')} threshold={c.get('threshold')}"})
    return out


def tier1_limits_for_beats(cfg, beat_ids: list[int]) -> dict:
    """Q12 (fixes E9): the rewriter receives the Tier 1 limits for the beats
    it edits, so it does not introduce a new Tier 1 failure."""
    try:
        from .quality import eff_word_count, eff_readability
        wc = eff_word_count(cfg)
        rd = eff_readability(cfg)
    except Exception:
        wc = {"hard_min": 185, "hard_max": 235}
        rd = {"max_grade": 6.5, "max_sentence_words": 20, "max_beat1_words": 20}
    return {"edited_beat_ids": sorted(beat_ids),
            "beat1_max_words": rd.get("max_beat1_words", 20) if 1 in beat_ids else None,
            "every_sentence_max_words": rd.get("max_sentence_words", 20),
            "total_spoken_words_hard_range": [wc.get("hard_min", 185), wc.get("hard_max", 235)],
            "note": "Rewritten beats MUST respect these limits; Beat 1 <= beat1_max_words when edited."}


def do_rewrite_step(cfg, root, sid, chunk, wdir, script, audit, template, k, llm, prompts, budget=None) -> dict:
    flagged = audit.get("failed_beat_ids") or [i.get("beat_id") for i in audit.get("rewrite_instructions", [])]
    if not flagged:
        flagged = [1, 2, 3, 4, 5]
    flagged = sorted({int(x) for x in flagged if isinstance(x, int) and 1 <= int(x) <= 5}) or [1, 2, 3, 4, 5]
    cur_beats = {b["beat_id"]: b for b in script["scene_beats"]}
    messages = [{"role": "system", "content": prompts["beat_rewriter"]},
                {"role": "user", "content": json.dumps({"script": {kk: vv for kk, vv in script.items() if not kk.startswith('_')},
                                                        "failed_beat_ids": flagged,
                                                        "rewrite_instructions": audit.get("rewrite_instructions", []),
                                                        "tier1_limits": tier1_limits_for_beats(cfg, flagged)}, ensure_ascii=False)}]
    if budget is None:
        budget = load_chunk_budget(cfg, root, sid, chunk["chunk_id"])
    from .llm.validate import validate_rewriter
    res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "rewrite")
    rerrs = validate_rewriter(res, [int(x) for x in flagged if isinstance(x, int)])
    if rerrs:
        messages.append({"role": "user", "content": f"Validation failed: {rerrs}. Reply with corrected JSON ONLY."})
        res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "rewrite")
        rerrs = validate_rewriter(res, [int(x) for x in flagged if isinstance(x, int)])
        if rerrs:
            raise SystemExit(f"REFUSAL stage=rewrite check=rewriter chunk={chunk['chunk_id']} errors={rerrs}")
    new_beats = res.get("scene_beats", [])
    got_ids = {b["beat_id"] for b in new_beats}
    if set(flagged) - got_ids:
        raise SystemExit(f"REFUSAL stage=rewrite check=splice chunk={chunk['chunk_id']} missing={set(flagged)-got_ids}")
    # verify unflagged beats byte-identical after splice
    base = {"scene_beats": script["scene_beats"], "call_to_action": script["call_to_action"]}
    spliced = splice_script(base, new_beats, res.get("call_to_action"))
    for b in spliced["scene_beats"]:
        if b["beat_id"] not in flagged:
            before = json.dumps(cur_beats[b["beat_id"]], sort_keys=True)
            after = json.dumps(b, sort_keys=True)
            if before != after:
                raise SystemExit(f"REFUSAL stage=rewrite check=byte-identity beat={b['beat_id']}")
    from .draft.router import apply_timing as _t
    timed = _t(spliced, cfg.wpm)
    comp = timed["_computed"]
    new_script = {"script_metadata": {**script["script_metadata"],
                                        "estimated_duration_sec": comp["estimated_duration_sec"],
                                        "total_word_count": comp["total_word_count"]},
                    "scene_beats": timed["scene_beats"], "call_to_action": timed["call_to_action"],
                    "inputs_sha256": {"prev": sha256_text(json.dumps(script, sort_keys=True)),
                                      **llm_inputs_extra(cfg, root, "writer", "beat_rewriter")},
                    "_template": template}
    atomic_write_json(wdir / f"rewrite_{k}.json", new_script)
    return new_script


def final_script_for_round(wdir: Path, rewrite_count: int) -> dict:
    if rewrite_count == 0:
        return read_json(wdir / "draft.json")
    return read_json(wdir / f"rewrite_{rewrite_count}.json")


def write_review_entry(root, sid, chunk, script, audit, overall, reason) -> Path:
    """Q13: mid-series near-misses land in review/<sid>/, never out/."""
    rdir = root / "review" / sid
    rdir.mkdir(parents=True, exist_ok=True)
    payload = {"chunk_id": chunk.get("chunk_id"), "decision": "NEEDS_HUMAN",
               "overall_score": overall, "reason": reason,
               "script": {k: v for k, v in (script or {}).items() if not k.startswith("_")},
               "audit": audit or {},
               "script_sha256": _script_binding_sha(script) if script is not None else ""}
    p = rdir / f"{chunk.get('chunk_id')}.json"
    atomic_write_json(p, payload)
    return p


def write_decision(root, sid, chunk, wdir, decision, script, overall, tier1_ok, rewrite_count, reason,
                   audit: dict | None = None) -> dict:
    # Episode numbers assigned only on release-eligible decisions (RELEASE,
    # legacy PASS). NEEDS_HUMAN evolves state but never exports, so no episode.
    sd = series_dir(root, sid)
    state = read_json(sd / "series_state.json")
    ep_no = None
    if decision in ("RELEASE", "PASS"):
        ep_no = int(state.get("current_episode", 0)) + 1
    binding = _script_binding_sha(script) if script is not None else sha256_text(chunk.get("chunk_id", ""))
    payload = {"chunk_id": chunk["chunk_id"], "decision": decision, "episode_number": ep_no,
               "overall_score": overall, "tier1_passed": tier1_ok, "rewrite_count": rewrite_count,
               "reason": reason,
               "inputs_sha256": {"script": binding}}
    atomic_write_json(wdir / "decision.json", payload)
    # R21: a fresh decision supersedes any earlier refusal for this chunk.
    try:
        (wdir / "refusal.json").unlink(missing_ok=True)
    except Exception:
        pass
    if decision == "NEEDS_HUMAN":
        try:
            write_review_entry(root, sid, chunk, script, audit, overall, reason)
        except Exception:
            pass
    return payload


def apply_terminal_hard_reset(root, sid, chunk) -> dict | None:
    """F5: after ANY terminal decision on a hard-boundary chunk, purge ephemeral
    entities per the chunk's reset instruction. Preserves episode count,
    repetition tracker, cursor (progress is not rewound) and the Q4 voice lock."""
    if not chunk.get("is_hard_boundary_end"):
        return None
    sd = series_dir(root, sid)
    state = read_json(sd / "series_state.json")
    reset = apply_reset(state, chunk.get("state_reset_instruction", "PERSIST"))
    reset["current_episode"] = state.get("current_episode", 0)
    reset["anti_repetition_tracker"] = state.get("anti_repetition_tracker", reset.get("anti_repetition_tracker", {}))
    reset["narrative_cursor"] = state.get("narrative_cursor", reset.get("narrative_cursor", {}))
    if state.get("narration"):
        reset["narration"] = state.get("narration")
    atomic_write_json(sd / "series_state.json", reset)
    return reset


def write_budget_stop(root, sid, chunk, wdir, reason, script=None, rewrite_count: int = 0) -> dict:
    """Episode-budget stop: non-terminal, resumable, NOT a series failure."""
    binding = _script_binding_sha(script) if script is not None else sha256_text(chunk.get("chunk_id", ""))
    payload = {"chunk_id": chunk["chunk_id"], "decision": "BUDGET_STOP", "episode_number": None,
               "overall_score": None, "tier1_passed": False, "rewrite_count": rewrite_count,
               "reason": reason, "inputs_sha256": {"script": binding}}
    atomic_write_json(wdir / "decision.json", payload)
    # R21: a fresh decision supersedes any earlier refusal for this chunk.
    try:
        (wdir / "refusal.json").unlink(missing_ok=True)
    except Exception:
        pass
    return payload


def do_fast_forward(cfg, root, sid, chunk, wdir, llm, prompts, state, budget=None) -> dict:
    beats_file = read_json(wdir / "beats.json")
    messages = [{"role": "system", "content": prompts["fast_forward_summarizer"]},
                {"role": "user", "content": json.dumps({"beats": beats_file.get("beats", [])}, ensure_ascii=False)}]
    if budget is None:
        budget = load_chunk_budget(cfg, root, sid, chunk["chunk_id"])
    from .llm.validate import validate_fast_forward
    res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "fast_forward")
    ferrs = validate_fast_forward(res)
    if ferrs:
        messages.append({"role": "user", "content": f"Validation failed: {ferrs}. Reply with corrected JSON ONLY."})
        res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "fast_forward")
        ferrs = validate_fast_forward(res)
        if ferrs:
            raise SystemExit(f"REFUSAL stage=fast-forward check=summary chunk={chunk['chunk_id']} errors={ferrs}")
    summary = res.get("summary", "")
    if len(summary.split()) > 30:
        summary = " ".join(summary.split()[:30])
    payload = {"summary": summary, "inputs_sha256": {"chunk": sha256_text(chunk["raw_text_content"]),
                                                              **llm_inputs_extra(cfg, root, "writer", "fast_forward_summarizer")}}
    atomic_write_json(wdir / "fast_forward.json", payload)
    # evolve state cursor immediately
    max_beat = max((b.get("beat_id", 0) for b in beats_file.get("beats", [])), default=0)
    try:
        _n = int(chunk.get("chunk_index", 0))
    except (TypeError, ValueError):
        _n = 0
    _ch, _pr = _cursor_for_chunk(root, sid, chunk, _n)
    new_state = apply_fast_forward(state, summary, max_beat, _ch, _pr)
    atomic_write_json(series_dir(root, sid) / "series_state.json", new_state)
    return payload


# ---------- Stage 5: evolve after PASS ----------

def _state_in_sha(root: Path, sid: str, chunk_id: str) -> str:
    try:
        sin = read_json(series_dir(root, sid) / "work" / chunk_id / "state_in.json")
        return sha256_text(json.dumps(sin, sort_keys=True, ensure_ascii=False))
    except Exception:
        return sha256_text("{}")


def do_evolve(root: Path, sid: str, chunk_n: int, fake=None, force=False) -> dict:
    cfg = load_config(root)
    sd = series_dir(root, sid)
    chunk = read_json(_chunk_path(root, sid, chunk_n))
    wdir = _workdir(root, sid, chunk["chunk_id"])
    decision = read_json(wdir / "decision.json")
    # Q13: state evolves after RELEASE (and NEEDS_HUMAN, so the series continues).
    # Legacy PASS/FORCED_PASS still evolve for old runs.
    if decision["decision"] not in ("RELEASE", "PASS", "NEEDS_HUMAN", "FORCED_PASS_WITH_WARNING"):
        return read_json(sd / "series_state.json")
    if decision["decision"] == "NEEDS_HUMAN":
        # No episode number consumed; evolve the cursor/bridges without ep snapshot.
        # Reuse the release path below but without ep_NNN_state.json.
        state = read_json(sd / "series_state.json")
        prompts = load_prompts(root)
        llm = get_llm(cfg, root, sid, fake)
        budget = load_chunk_budget(cfg, root, sid, chunk["chunk_id"])
        script = final_script_for_round(wdir, decision.get("rewrite_count", 0))
        beats_file = read_json(wdir / "beats.json")
        messages = [{"role": "system", "content": prompts["state_updater"]},
                    {"role": "user", "content": json.dumps({"prev_state": compile_state(state, beats=beats_file.get("beats", [])), "script": {k: v for k, v in script.items() if not k.startswith('_')}}, ensure_ascii=False)}]
        from .llm.validate import validate_state_updater
        res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "evolve")
        serrs = validate_state_updater(res)
        if serrs:
            messages.append({"role": "user", "content": f"Validation failed: {serrs}. Reply with corrected JSON ONLY."})
            res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "evolve")
            serrs = validate_state_updater(res)
            if serrs:
                raise SystemExit(f"REFUSAL stage=evolve check=state chunk={chunk['chunk_id']} errors={serrs}")
        template = script.get("script_metadata", {}).get("pacing_template", "")
        hook = script.get("script_metadata", {}).get("hook_option", "")
        max_beat = max((b.get("beat_id", 0) for b in beats_file.get("beats", [])), default=0)
        # NEEDS_HUMAN keeps current_episode (no new episode) but advances cursor/bridges.
        cur_ep = int(state.get("current_episode", 0))
        active = sorted({ch for b in beats_file.get("beats", []) for ch in b.get("characters", [])})
        try:
            _chn = int(chunk.get("chunk_index", 0))
        except (TypeError, ValueError):
            _chn = chunk_n
        _ch, _pr = _cursor_for_chunk(root, sid, chunk, _chn)
        new_state = evolve_state(state, res, template, hook, max_beat, cur_ep, active, _ch, _pr)
        atomic_write_json(sd / "series_state.json", new_state)
        return new_state
    state = read_json(sd / "series_state.json")
    ep_no = decision["episode_number"]
    # R2: snapshot bound to decision sha + state_in sha; fresh → no LLM, no state
    # touch (this also makes plain reruns — R1 — skip silently instead of refusing).
    snap = sd / "episodes" / f"ep_{ep_no:03d}_state.json"
    snap_fresh = False
    if snap.exists():
        try:
            existing = read_json(snap)
            expect = {"decision": sha256_file(wdir / "decision.json"),
                      "state_in": _state_in_sha(root, sid, chunk["chunk_id"]),
                      **llm_inputs_extra(cfg, root, "writer", "state_updater")}
            snap_fresh = existing.get("inputs_sha256") == expect
        except Exception:
            snap_fresh = False
    if not force and snap_fresh:
        return read_json(sd / "series_state.json")
    # R2: refuse to evolve a chunk older than the series cursor (a recompute here
    # would roll state back) unless --force.
    if not force and ep_no is not None and ep_no < int(state.get("current_episode", 0)):
        raise SystemExit(
            f"REFUSAL stage=evolve check=cursor chunk={chunk['chunk_id']} "
            f"episode={ep_no} cursor={state.get('current_episode', 0)} (use --force)")
    prompts = load_prompts(root)
    llm = get_llm(cfg, root, sid, fake)
    budget = load_chunk_budget(cfg, root, sid, chunk["chunk_id"])
    state = read_json(sd / "series_state.json")
    script = final_script_for_round(wdir, decision.get("rewrite_count", 0))
    beats_file = read_json(wdir / "beats.json")
    messages = [{"role": "system", "content": prompts["state_updater"]},
                {"role": "user", "content": json.dumps({"prev_state": compile_state(state, beats=beats_file.get("beats", [])), "script": {k: v for k, v in script.items() if not k.startswith('_')}}, ensure_ascii=False)}]
    from .llm.validate import validate_state_updater
    res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "evolve")
    serrs = validate_state_updater(res)
    if serrs:
        messages.append({"role": "user", "content": f"Validation failed: {serrs}. Reply with corrected JSON ONLY."})
        res, _ = _jchat(llm, budget, cfg, "writer", messages, chunk["chunk_id"], "evolve")
        serrs = validate_state_updater(res)
        if serrs:
            raise SystemExit(f"REFUSAL stage=evolve check=state chunk={chunk['chunk_id']} errors={serrs}")
    template = script.get("script_metadata", {}).get("pacing_template", "")
    hook = script.get("script_metadata", {}).get("hook_option", "")
    max_beat = max((b.get("beat_id", 0) for b in beats_file.get("beats", [])), default=0)
    ep_no = decision["episode_number"]
    _was_ep0 = int(state.get("current_episode", 0)) == 0
    # reset modes for hard boundaries applied AFTER evolve? spec: resets clear ephemeral at boundary.
    active = sorted({ch for b in beats_file.get("beats", []) for ch in b.get("characters", [])})
    _ch, _pr = _cursor_for_chunk(root, sid, chunk, chunk_n)
    new_state = evolve_state(state, res, template, hook, max_beat, ep_no, active, _ch, _pr)
    # Q4: series voice lock is set at episode 1 and never changes afterwards.
    if _was_ep0 and not (new_state.get("narration") or {}).get("pov"):
        try:
            new_state["narration"] = detect_narration_lock(script, new_state.get("character_registry", []))
        except Exception:
            pass
    if chunk.get("is_hard_boundary_end"):
        # apply reset semantics but keep episode count + repetition tracker
        # + Q4 voice lock.
        reset = apply_reset({**new_state, "character_registry": new_state.get("character_registry", [])},
                            chunk.get("state_reset_instruction", "PERSIST"))
        reset["current_episode"] = new_state["current_episode"]
        reset["anti_repetition_tracker"] = new_state["anti_repetition_tracker"]
        reset["narrative_cursor"] = new_state["narrative_cursor"]
        if new_state.get("narration"):
            reset["narration"] = new_state.get("narration")
        new_state = reset
    atomic_write_json(sd / "series_state.json", new_state)
    atomic_write_json(sd / "episodes" / f"ep_{ep_no:03d}_state.json",
                        {**new_state, "inputs_sha256": {"decision": sha256_file(wdir / "decision.json"),
                                                        "state_in": _state_in_sha(root, sid, chunk["chunk_id"]),
                                                        **llm_inputs_extra(cfg, root, "writer", "state_updater")}})
    return new_state


# ---------- Stage 6 ----------

def do_export(root: Path, sid: str) -> list[Path]:
    cfg = load_config(root)
    sd = series_dir(root, sid)
    source = read_json(sd / "00_source_raw.json")
    out_paths = []
    episodes_meta = []
    for dpath in sorted((sd / "work").glob("*/decision.json")) if (sd / "work").exists() else []:
        dec = read_json(dpath)
        # Q13: only RELEASE (legacy PASS) is exported. FORCED_PASS_WITH_WARNING
        # is removed from export; NEEDS_HUMAN lives in review/, never out/.
        if dec.get("decision") not in ("RELEASE", "PASS"):
            continue
        wdir = dpath.parent
        script = final_script_for_round(wdir, dec.get("rewrite_count", 0))
        cur_sha = _script_binding_sha(script)
        if dec.get("inputs_sha256", {}).get("script") != cur_sha:
            raise SystemExit(
                f"REFUSAL stage=export check=binding chunk={dec.get('chunk_id')} "
                f"decision_script={dec.get('inputs_sha256', {}).get('script')} actual={cur_sha} "
                f"path={wdir / ('draft.json' if not dec.get('rewrite_count') else f'rewrite_{dec.get('rewrite_count')}.json')}")
        rw = dec.get("rewrite_count", 0)
        try:
            gates = read_json(wdir / f"gates_{rw}.json")
        except Exception:
            raise SystemExit(f"REFUSAL stage=export check=binding chunk={dec.get('chunk_id')} missing gates_{rw}.json")
        if gates.get("script_sha256") != cur_sha:
            raise SystemExit(
                f"REFUSAL stage=export check=binding chunk={dec.get('chunk_id')} "
                f"gates_script={gates.get('script_sha256')} actual={cur_sha} path={wdir / f'gates_{rw}.json'}")
        ep_no = dec["episode_number"]
        clean = {k: v for k, v in script.items() if not k.startswith("_") and k != "inputs_sha256"}
        clean["script_metadata"]["episode_number"] = ep_no
        p = export_episode(root, sid, ep_no, clean)
        out_paths.append(p)
        rw = dec.get("rewrite_count", 0)
        gates = read_json(wdir / f"gates_{rw}.json")
        audit = read_json(wdir / f"audit_{rw}.json")
        cost_usd, tokens = _chunk_spend(root, sid, dec.get("chunk_id", ""))
        episodes_meta.append({"episode": ep_no, "file": p.name,
                              "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                              "chunk_id": dec.get("chunk_id", ""),
                              "decision": dec["decision"],
                              "tier1_receipt_sha256": sha256_file(wdir / f"gates_{rw}.json"),
                              "audited_script_sha256": cur_sha,
                              "overall": audit.get("overall"), "scores": audit.get("scores", {}),
                              "warnings": gates.get("warnings", []), "rewrite_count": rw,
                              "cost_usd": round(cost_usd, 6), "tokens": tokens})
    if out_paths:
        build_manifest(root, sid, cfg, source, episodes_meta)
    return out_paths


def _cursor_for_chunk(root: Path, sid: str, chunk: dict, chunk_n: int) -> tuple[int | None, str | None]:
    """F13: chapter + progress derived from the chunk's paragraph range.

    chapter_no = number of chapter headings at-or-before the chunk end;
    progress = processed word_equiv / total word_equiv.
    Returns (None, None) when the source cannot be read (legacy fallback)."""
    try:
        from .topology.heuristics import find_headings
        from .chunking.boundary import SCENE_BREAK_RE
        sd = series_dir(root, sid)
        source = read_json(sd / "00_source_raw.json")
        paras = source.get("paragraphs", [])
        id_to_pos = {p["id"]: i for i, p in enumerate(paras)}
        chapter_positions = sorted(
            id_to_pos[h["para_id"]] for h in find_headings(paras)
            if h["para_id"] in id_to_pos and not SCENE_BREAK_RE.search(h["text"]))
        positions = [id_to_pos[pid] for pid in chunk.get("paragraph_ids", []) if pid in id_to_pos]
        end = max(positions) if positions else 0
        chapter_no = sum(1 for cp in chapter_positions if cp <= end)
        total = 0
        try:
            total = int(read_json(sd / "01_topology_meta.json").get("word_equiv", 0))
        except Exception:
            total = 0
        if not total:
            from .topology.heuristics import word_equiv as _we
            total = _we(source.get("normalized_text", ""), source.get("language", "en") or "en",
                        load_config(root).pipeline.get("word_equiv_factors", {"default": 1.0}))
        cum = 0
        for cpath in sorted((sd / "chunks").glob("chunk_*.json")):
            try:
                c = read_json(cpath)
            except Exception:
                continue
            if c.get("chunk_index", 0) <= chunk_n:
                cum += int(c.get("word_equiv", 0))
        progress = f"{min(100, round(100 * cum / total))}%" if total > 0 else None
        return chapter_no, progress
    except Exception:
        return None, None


def _chunk_spend(root: Path, sid: str, chunk_id: str) -> tuple[float, int]:
    """Numeric per-episode cost/tokens summed from llm_calls.jsonl (F12)."""
    total_cost, total_tokens = 0.0, 0
    calls = root / "run" / sid / "llm_calls.jsonl"
    if chunk_id and calls.exists():
        for line in calls.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("chunk_id") == chunk_id:
                try:
                    total_cost += float(e.get("cost_usd", 0))
                    total_tokens += int(e.get("input_tokens", 0)) + int(e.get("output_tokens", 0))
                except (TypeError, ValueError):
                    continue
    return total_cost, total_tokens


def series_decisions(root: Path, sid: str) -> tuple[list[str], list[float | None]]:
    sd = series_dir(root, sid)
    decs, scores = [], []
    if not (sd / "work").exists():
        return decs, scores
    for wdir in sorted((sd / "work").glob("chunk_*")):
        d = wdir / "decision.json"
        if d.exists():
            j = read_json(d)
            decs.append(j.get("decision"))
            scores.append(j.get("overall_score"))
    return decs, scores
