"""Stage 2: beat extraction validation + bible merge (deterministic)."""
from __future__ import annotations
import re

TEMPLATES = ["Template_1_In_Medias_Res", "Template_2_Breadcrumb_Mystery", "Template_3_Moral_Dilemma"]


def validate_beats(beats_data: dict, chunk: dict, registry_names: set[str], next_beat_id: int) -> list[str]:
    errors = []
    beats = beats_data.get("beats", [])
    if len(beats) < 5:
        errors.append(f"need >=5 beats, got {len(beats)}")
    valid_pids = set(chunk.get("paragraph_ids", []))
    known = set(registry_names) | {e.get("original_name") for e in beats_data.get("new_entities", [])}
    for i, b in enumerate(beats):
        exp = next_beat_id + i
        if b.get("beat_id") != exp:
            errors.append(f"beat[{i}].beat_id={b.get('beat_id')} expected {exp}")
        for pid in b.get("source_paragraph_ids", []):
            if pid not in valid_pids:
                errors.append(f"beat {b.get('beat_id')}: unknown paragraph {pid}")
        for ch in b.get("characters", []):
            if ch not in known:
                errors.append(f"beat {b.get('beat_id')}: unknown character {ch!r}")
    return errors


def assign_voice(gender: str, role: str, used: set[str], voices_cfg: dict) -> str:
    pools = voices_cfg.get("pools", {})
    cands: list[str] = []
    g = (gender or "").lower()
    if g.startswith("f"):
        cands = pools.get("female", {}).get("young", []) + pools.get("female", {}).get("mature", [])
    elif g.startswith("m"):
        cands = pools.get("male", {}).get("young", []) + pools.get("male", {}).get("deep", [])
    else:
        cands = pools.get("neutral", {}).get("narrator", [])
    for c in cands:
        if c not in used:
            return c
    # fallback round-robin with suffix
    base = (cands[0] if cands else "en_us_narrator_01")
    i = 2
    while f"{base}#{i}" in used:
        i += 1
    return f"{base}#{i}"


def validate_beats_coverage(beats_data: dict, paragraph_ids: list) -> list[str]:
    """Coverage gate (R11): cited source_paragraph_ids must span >= 70% of the
    chunk's paragraph range (min id <= first + 10% and max id >= last - 10%).
    A model that only saw the start of the chunk (truncated prompt) cites a
    narrow prefix and is refused here."""
    errs: list[str] = []
    try:
        pids = sorted({int(p) for p in (paragraph_ids or [])})
    except (TypeError, ValueError):
        return ["chunk paragraph_ids are not integers"]
    if not pids:
        return ["chunk has no paragraphs to cover"]
    cited: set[int] = set()
    for b in beats_data.get("beats", []) or []:
        if not isinstance(b, dict):
            continue
        for pid in b.get("source_paragraph_ids", []) or []:
            try:
                cited.add(int(pid))
            except (TypeError, ValueError):
                continue
    if not cited:
        return ["beats cite no source paragraphs (coverage 0%)"]
    first, last = pids[0], pids[-1]
    lo = first + 0.1 * (last - first)
    hi = last - 0.1 * (last - first)
    if min(cited) > lo:
        errs.append(f"beats start at paragraph {min(cited)}: min cited above {lo:.0f} (coverage below 70%)")
    if max(cited) < hi:
        errs.append(f"beats cover only up to paragraph {max(cited)}: max cited below {hi:.0f} (coverage below 70%)")
    return errs


def bible_merge(state: dict, beats_data: dict, voices_cfg: dict) -> tuple:
    """Deterministic merge: freeze us_name, assign voices, extend glossary.

    Q7 age-up: compute the frozen age_map on first appearance, store adapted
    fields on the registry, and freeze the setting map into the glossary.
    Returns (merged_state, name_reproposals). R20: re-proposing an existing
    original name is not an error — the frozen us_name is kept and a warning
    is recorded for beats.json. The only refusal is two different original
    names mapping to the same us_name, raised as SystemExit (exit 2),
    never a traceback.
    """
    import copy
    from ..ageup import build_age_map, ensure_setting_map
    st = copy.deepcopy(state)
    reg = st.setdefault("character_registry", [])
    used_voices = {c.get("tts_voice_id") for c in reg if c.get("tts_voice_id")}
    existing = {c["original_name"]: c for c in reg}
    us_to_original: dict[str, str] = {}
    for c in reg:
        if c.get("us_name"):
            us_to_original.setdefault(str(c["us_name"]), c.get("original_name"))
    reproposals: list[dict] = []
    for ent in beats_data.get("new_entities", []) or []:
        oname = ent["original_name"]
        if oname in existing:
            frozen = existing[oname].get("us_name")
            proposed = ent.get("proposed_us_name")
            if proposed and proposed != frozen:
                reproposals.append({"original_name": oname,
                                    "frozen_us_name": frozen,
                                    "proposed_us_name": proposed})
            # Q7(a): keep the frozen source age/stage; fill it if it was unknown.
            try:
                cur_age = str(existing[oname].get("age_or_stage", "unknown"))
                new_age = str(ent.get("age_or_stage", "unknown"))
                if (cur_age in ("", "unknown")) and new_age not in ("", "unknown"):
                    existing[oname]["age_or_stage"] = new_age
            except Exception:
                pass
            continue
        proposed = ent.get("proposed_us_name") or oname
        owner = us_to_original.get(proposed)
        if owner is not None and owner != oname:
            raise SystemExit(
                f"REFUSAL stage=bible_merge check=us_name "
                f"originals={owner!r},{oname!r} share us_name={proposed!r} "
                f"(rename one of them)")
        voice = assign_voice(ent.get("gender", "unknown"), ent.get("role", ""), used_voices, voices_cfg)
        used_voices.add(voice)
        reg.append({"original_name": oname, "us_name": proposed,
                    "role": ent.get("role", ""), "tts_voice_id": voice,
                    "current_status": "", "main_cast": bool(ent.get("main_cast", False)),
                    "age_or_stage": str(ent.get("age_or_stage", "unknown")),
                    "age_citation": list(ent.get("age_citation", []) or [])})
        existing[oname] = reg[-1]
        us_to_original.setdefault(proposed, oname)
    gl = st.setdefault("localized_glossary", {})
    for k, v in (beats_data.get("glossary_additions", {}) or {}).items():
        gl.setdefault(k, v)
    # Q7(b)(c): frozen age map + setting map. Existing frozen entries win.
    try:
        fresh_map = build_age_map(reg, beats_data.get("new_entities", []) or [])
        stored = st.get("age_map") or {}
        for name, entry in fresh_map.items():
            stored.setdefault(name, entry)
        st["age_map"] = stored
        # Freeze adapted fields onto the registry (first appearance wins).
        for c in reg:
            nm = c.get("original_name")
            if nm in stored:
                e = stored[nm]
                c.setdefault("adapted_age", e.get("adapted_age"))
                if e.get("adapted_stage"):
                    c.setdefault("adapted_stage", e.get("adapted_stage"))
                c.setdefault("source_age_or_stage", e.get("source_age_or_stage"))
        ensure_setting_map(gl)
        st["setting_map"] = {k: gl.get(k) for k in
                             ("school", "teacher", "class", "parent-teacher summons",
                              "parent-teacher", "school uniform", "homeroom")}
    except Exception:
        pass
    return st, reproposals
