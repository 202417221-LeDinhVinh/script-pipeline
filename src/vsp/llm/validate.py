"""Strict validation for every LLM output (F4). No defaults for required fields.

Each validator returns a list of error strings (empty = valid). Orchestrator
does one repair retry, then REFUSAL (exit 2).
"""
from __future__ import annotations

HOOKS = ["Option_1_Pink_Teddy_Bear", "Option_2_Out_Of_Context_Confession",
         "Option_3_Contradiction", "Option_4_Calm_Before_Storm"]
HOOK_LETTERS = ["Hook_A", "Hook_B", "Hook_C", "Hook_D"]
TEMPLATES = ["Template_1_In_Medias_Res", "Template_2_Breadcrumb_Mystery",
             "Template_3_Moral_Dilemma"]
CTA_TYPES = ["cliffhanger_subscription", "engagement_verdict", "loop_back", "story_complete"]
BLOCKS = {
    "Template_1_In_Medias_Res": ["Disruption_Hook", "Fast_Context_And_Handover", "Escalation",
                                 "Micro_Twist_And_Payoff", "Climax_And_Cliffhanger"],
    "Template_2_Breadcrumb_Mystery": ["Anomaly_Hook", "Clue_Stacking", "Investigation_Trap",
                                      "False_Relief", "Chilling_Reveal"],
    "Template_3_Moral_Dilemma": ["Polarizing_Question", "POV_A_Empathy", "POV_B_Grey_Area",
                                 "Breaking_Point", "Engagement_Verdict"],
}
BEAT_FIELDS = ["narration_text", "tts_emotion_prompt", "screen_text_overlay",
               "b_roll_vibe", "sfx_cue", "bgm_directive"]


def validate_extractor(res: dict) -> list[str]:
    errs = []
    if not isinstance(res, dict):
        return ["extractor output must be an object"]
    if not isinstance(res.get("beats"), list) or not res["beats"]:
        errs.append("missing required 'beats[]'")
    for ent in res.get("new_entities", []) or []:
        if not isinstance(ent, dict) or not ent.get("original_name"):
            errs.append(f"new_entities entry missing original_name: {ent!r}")
        if not ent.get("proposed_us_name"):
            errs.append(f"new_entities entry missing proposed_us_name: {ent.get('original_name')!r}")
    if not isinstance(res.get("glossary_additions", {}), dict):
        errs.append("glossary_additions must be an object")
    if not isinstance(res.get("genre_tags", []), list):
        errs.append("genre_tags must be an array")
    return errs


def validate_writer(res: dict, template: str) -> list[str]:
    errs = []
    if not isinstance(res, dict):
        return ["writer output must be an object"]
    beats = res.get("scene_beats")
    if not isinstance(beats, list) or len(beats) != 5:
        return [f"writer must return exactly 5 scene_beats, got {len(beats) if isinstance(beats, list) else type(beats)}"]
    expected = BLOCKS.get(template, [])
    for i, b in enumerate(beats):
        if not isinstance(b, dict):
            errs.append(f"beat[{i}] must be an object")
            continue
        if b.get("beat_id") != i + 1:
            errs.append(f"beat[{i}].beat_id={b.get('beat_id')} expected {i+1}")
        if expected and b.get("block_type") not in expected:
            errs.append(f"beat {b.get('beat_id')}: block_type {b.get('block_type')!r} not in {template}")
        elif b.get("block_type") not in {blk for blks in BLOCKS.values() for blk in blks}:
            errs.append(f"beat {b.get('beat_id')}: unknown block_type {b.get('block_type')!r}")
        for f in BEAT_FIELDS:
            if not isinstance(b.get(f), str) or not b.get(f, "").strip():
                errs.append(f"beat {b.get('beat_id')}: missing required field {f!r}")
    hook = res.get("hook_option")
    if hook not in HOOKS and hook not in HOOK_LETTERS:
        errs.append(f"hook_option {hook!r} must be one of {HOOKS} or {HOOK_LETTERS}")
    payoff = res.get("hook_payoff_beat_id")
    if payoff not in (4, 5):
        errs.append(f"hook_payoff_beat_id {payoff!r} must be 4 or 5")
    cta = res.get("call_to_action")
    if not isinstance(cta, dict):
        errs.append("missing required call_to_action object")
    else:
        if cta.get("type") not in CTA_TYPES:
            errs.append(f"call_to_action.type {cta.get('type')!r} must be one of {CTA_TYPES}")
        if not isinstance(cta.get("outro_narration"), str) or not cta.get("outro_narration", "").strip():
            errs.append("call_to_action.outro_narration is required and non-empty")
    return errs


def validate_judge(res: dict) -> list[str]:
    errs = []
    if not isinstance(res, dict):
        return ["judge output must be an object"]
    scores = res.get("scores")
    if not isinstance(scores, dict):
        return ["judge must return scores{} object"]
    for dim in ("hook_payoff", "pacing", "logic", "retention"):
        v = scores.get(dim)
        if v is None:
            errs.append(f"scores.{dim} is required")
        elif not isinstance(v, (int, float)) or isinstance(v, bool):
            errs.append(f"scores.{dim}={v!r} must be a number in [0,10]")
        elif not (0 <= float(v) <= 10):
            errs.append(f"scores.{dim}={v!r} out of range [0,10]")
    if "payoff_resolved" not in res or not isinstance(res.get("payoff_resolved"), bool):
        errs.append("payoff_resolved is required and must be boolean")
    fb = res.get("failed_beat_ids", [])
    if not isinstance(fb, list):
        errs.append("failed_beat_ids must be an array")
    else:
        for b in fb:
            if b not in (1, 2, 3, 4, 5):
                errs.append(f"failed_beat_ids entry {b!r} must be in 1..5")
    instr = res.get("rewrite_instructions", [])
    if not isinstance(instr, list):
        errs.append("rewrite_instructions must be an array")
    else:
        for it in instr:
            if not isinstance(it, dict):
                errs.append(f"rewrite instruction must be object: {it!r}")
                continue
            if it.get("beat_id") not in (1, 2, 3, 4, 5):
                errs.append(f"rewrite instruction beat_id {it.get('beat_id')!r} must be in 1..5")
            if not it.get("issue") or not it.get("instruction"):
                errs.append(f"rewrite instruction for beat {it.get('beat_id')} needs issue+instruction")
    return errs


def validate_arc_selector(res: dict, valid_ids: set[int]) -> list[str]:
    """Q2: arc selection must pick ≤5 existing beats, disjoint dropped with reasons."""
    errs = []
    if not isinstance(res, dict):
        return ["arc selector output must be an object"]
    try:
        setup = list(res.get("setup_beat_ids", []) or [])
        turn = res.get("turn_beat_id")
        payoff = res.get("payoff_beat_id")
        cliff = res.get("cliffhanger_beat_id")
        dropped = res.get("dropped_beat_ids", []) or []
    except Exception:
        return ["arc selector fields unreadable"]
    sel = []
    for b in setup:
        try:
            sel.append(int(b))
        except (TypeError, ValueError):
            errs.append(f"setup_beat_ids entry {b!r} must be an int")
    for name, v in (("turn_beat_id", turn), ("payoff_beat_id", payoff),
                    ("cliffhanger_beat_id", cliff)):
        if v is None:
            errs.append(f"{name} is required")
        else:
            try:
                sel.append(int(v))
            except (TypeError, ValueError):
                errs.append(f"{name} {v!r} must be an int")
    if len(sel) != len(set(sel)):
        errs.append(f"selected beat ids must not duplicate: {sel}")
    if len(sel) > 5:
        errs.append(f"select at most 5 beats, got {len(sel)}")
    for b in sel:
        if b not in valid_ids:
            errs.append(f"selected beat {b} not in chunk beats {sorted(valid_ids)}")
    drop_ids = []
    if not isinstance(dropped, list):
        errs.append("dropped_beat_ids must be an array")
    else:
        for d in dropped:
            if not isinstance(d, dict):
                errs.append(f"dropped entry must be object: {d!r}")
                continue
            try:
                bid = int(d.get("beat_id"))
            except (TypeError, ValueError):
                errs.append(f"dropped beat_id {d.get('beat_id')!r} must be an int")
                continue
            if bid not in valid_ids:
                errs.append(f"dropped beat {bid} not in chunk beats")
            if bid in set(sel):
                errs.append(f"dropped beat {bid} overlaps selected {sel}")
            if not str(d.get("reason", "") or "").strip():
                errs.append(f"dropped beat {bid} needs a one-line reason")
            drop_ids.append(bid)
    return errs


CHECKLIST_ITEMS = ["H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1"]


def validate_checklist(res: dict) -> list[str]:
    """Q8: checklist items are yes/no + verbatim evidence."""
    errs = []
    if not isinstance(res, dict):
        return ["checklist output must be an object"]
    items = res.get("items")
    if not isinstance(items, dict):
        return ["checklist must return items{} object"]
    for name in CHECKLIST_ITEMS:
        it = items.get(name)
        if not isinstance(it, dict):
            errs.append(f"items.{name} is required")
            continue
        if "pass" not in it or not isinstance(it.get("pass"), bool):
            errs.append(f"items.{name}.pass is required and must be boolean")
        ev = it.get("evidence", [])
        if not isinstance(ev, list):
            errs.append(f"items.{name}.evidence must be an array")
        else:
            for e in ev:
                if not isinstance(e, dict):
                    errs.append(f"items.{name}.evidence entry must be object")
                    continue
                if not isinstance(e.get("quote"), str) or not e.get("quote", "").strip():
                    errs.append(f"items.{name}.evidence quote is required")
                bid = e.get("beat_id")
                if bid is not None and bid not in (1, 2, 3, 4, 5):
                    errs.append(f"items.{name}.evidence beat_id {bid!r} must be in 1..5")
                pids = e.get("source_paragraph_ids", [])
                if not isinstance(pids, list):
                    errs.append(f"items.{name}.evidence source_paragraph_ids must be an array")
    fb = res.get("failed_beat_ids", [])
    if not isinstance(fb, list):
        errs.append("failed_beat_ids must be an array")
    else:
        for b in fb:
            if b not in (1, 2, 3, 4, 5):
                errs.append(f"failed_beat_ids entry {b!r} must be in 1..5")
    instr = res.get("rewrite_instructions", [])
    if not isinstance(instr, list):
        errs.append("rewrite_instructions must be an array")
    return errs


def validate_fidelity(res: dict) -> list[str]:
    errs = []
    if not isinstance(res, dict):
        return ["fidelity output must be an object"]
    claims = res.get("claims")
    if not isinstance(claims, list) or not claims:
        return ["fidelity must return non-empty claims[]"]
    for c in claims:
        if not isinstance(c, dict):
            errs.append(f"claim must be object: {c!r}")
            continue
        if not isinstance(c.get("claim"), str) or not c.get("claim", "").strip():
            errs.append("claim.claim is required and non-empty")
        if c.get("verdict") not in ("supported", "contradicted", "not_in_source"):
            errs.append(f"claim verdict {c.get('verdict')!r} must be supported|contradicted|not_in_source")
        if not isinstance(c.get("source_paragraph_ids", []), list):
            errs.append("claim source_paragraph_ids must be an array")
        if "stylistic" in c and not isinstance(c.get("stylistic"), bool):
            errs.append("claim stylistic must be boolean")
    return errs


def validate_rewriter(res: dict, flagged: list[int]) -> list[str]:
    errs = []
    if not isinstance(res, dict):
        return ["rewriter output must be an object"]
    beats = res.get("scene_beats")
    if not isinstance(beats, list) or not beats:
        return ["rewriter must return non-empty scene_beats[]"]
    got = set()
    for b in beats:
        if not isinstance(b, dict):
            errs.append(f"rewritten beat must be object: {b!r}")
            continue
        bid = b.get("beat_id")
        if bid not in (1, 2, 3, 4, 5):
            errs.append(f"rewritten beat_id {bid!r} must be in 1..5")
            continue
        got.add(bid)
        if not isinstance(b.get("narration_text"), str) or not b.get("narration_text", "").strip():
            errs.append(f"rewritten beat {bid}: narration_text is required")
    for f in flagged:
        if f not in got:
            errs.append(f"flagged beat {f} missing from rewrite output")
    return errs


def validate_state_updater(res: dict) -> list[str]:
    errs = []
    if not isinstance(res, dict):
        return ["state_updater output must be an object"]
    for e in res.get("secrets_ledger", []) or []:
        if not isinstance(e, dict) or not e.get("fact"):
            errs.append(f"secrets_ledger entry needs fact: {e!r}")
    for u in res.get("character_updates", []) or []:
        if not isinstance(u, dict) or not u.get("original_name"):
            errs.append(f"character_updates entry needs original_name: {u!r}")
    hb = res.get("handover_bridge")
    if hb is not None and not isinstance(hb, dict):
        errs.append("handover_bridge must be an object")
    return errs


def validate_fast_forward(res: dict) -> list[str]:
    if not isinstance(res, dict):
        return ["fast_forward output must be an object"]
    s = res.get("summary")
    if not isinstance(s, str) or not s.strip():
        return ["fast_forward summary is required and non-empty"]
    return []


def validate_classifier(res: dict) -> list[str]:
    errs = []
    if not isinstance(res, dict):
        return ["classifier output must be an object"]
    if res.get("topology") not in ("SINGLE_SHOT", "ANTHOLOGY", "EPISODIC_SERIES", "CONTINUOUS_SERIAL"):
        errs.append(f"topology {res.get('topology')!r} invalid")
    try:
        c = float(res.get("confidence", -1))
        if not (0 <= c <= 1):
            errs.append(f"confidence {c} must be in [0,1]")
    except (TypeError, ValueError):
        errs.append("confidence must be a number in [0,1]")
    if not isinstance(res.get("source_language", "en"), str) or not res.get("source_language"):
        errs.append("source_language is required")
    if not isinstance(res.get("genre_tags", []), list):
        errs.append("genre_tags must be an array")
    return errs
