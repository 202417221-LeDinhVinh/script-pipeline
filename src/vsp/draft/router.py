"""Deterministic router + timing + splice."""
from __future__ import annotations
import re

TEMPLATE_BLOCKS = {
    "Template_1_In_Medias_Res": ["Disruption_Hook", "Fast_Context_And_Handover", "Escalation",
                                 "Micro_Twist_And_Payoff", "Climax_And_Cliffhanger"],
    "Template_2_Breadcrumb_Mystery": ["Anomaly_Hook", "Clue_Stacking", "Investigation_Trap",
                                      "False_Relief", "Chilling_Reveal"],
    "Template_3_Moral_Dilemma": ["Polarizing_Question", "POV_A_Empathy", "POV_B_Grey_Area",
                                 "Breaking_Point", "Engagement_Verdict"],
}
HOOKS = ["Option_1_Pink_Teddy_Bear", "Option_2_Out_Of_Context_Confession",
         "Option_3_Contradiction", "Option_4_Calm_Before_Storm"]

# Q3: prompts show Hook A-D (definitions + abstract <object>/<number> examples),
# never the internal Option_* labels. The writer reports Hook_A-D; the pipeline
# normalizes to the internal enum for storage and gates.
HOOK_LETTERS = {"Hook_A": "Option_1_Pink_Teddy_Bear",
                "Hook_B": "Option_2_Out_Of_Context_Confession",
                "Hook_C": "Option_3_Contradiction",
                "Hook_D": "Option_4_Calm_Before_Storm"}
LETTER_FOR_HOOK = {v: k for k, v in HOOK_LETTERS.items()}


def normalize_hook(hook: str | None) -> str | None:
    if hook in HOOK_LETTERS:
        return HOOK_LETTERS[hook]
    return hook


def letter_for_hook(hook: str | None) -> str | None:
    if hook in LETTER_FOR_HOOK:
        return LETTER_FOR_HOOK[hook]
    if hook in HOOK_LETTERS:
        return hook
    return None

T1_TAGS = {"revenge", "betrayal", "action", "thriller-action"}
T2_TAGS = {"mystery", "thriller", "paranormal", "horror"}
T3_TAGS = {"family", "workplace", "aita", "drama", "moral"}


def route_template(genre_tags: list[str], forbidden: list[str]) -> str:
    tags = {t.lower() for t in genre_tags}
    if tags & T1_TAGS:
        priority = ["Template_1_In_Medias_Res", "Template_2_Breadcrumb_Mystery", "Template_3_Moral_Dilemma"]
    elif tags & T2_TAGS:
        priority = ["Template_2_Breadcrumb_Mystery", "Template_1_In_Medias_Res", "Template_3_Moral_Dilemma"]
    elif tags & T3_TAGS:
        priority = ["Template_3_Moral_Dilemma", "Template_1_In_Medias_Res", "Template_2_Breadcrumb_Mystery"]
    else:
        priority = ["Template_1_In_Medias_Res", "Template_2_Breadcrumb_Mystery", "Template_3_Moral_Dilemma"]
    forb = set(forbidden or [])
    for t in priority:
        if t not in forb:
            return t
    return priority[0]


def allowed_hooks(prev_hook: str | None) -> list[str]:
    return [h for h in HOOKS if h != prev_hook]


def lru_hook_hint(allowed: list[str], history: list[str] | None) -> str:
    """Deterministic hint: least-recently-used hook (never-used first, ties → order)."""
    hist = history or []
    last_use = {}
    for i, h in enumerate(hist):
        last_use[h] = i
    return min(allowed, key=lambda h: (last_use.get(h, -1), allowed.index(h)))


def count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


def spoken_words(script: dict) -> int:
    total = sum(count_words(b.get("narration_text", "")) for b in script.get("scene_beats", []))
    total += count_words(script.get("call_to_action", {}).get("outro_narration", ""))
    return total


def estimate_duration(spoken: int, wpm: int = 185) -> int:
    return int(round(spoken / wpm * 60))


def fmt_ts(sec: float) -> str:
    m = int(sec // 60)
    s = int(round(sec % 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m:02d}:{s:02d}"


def apply_timing(script: dict, wpm: int = 185) -> dict:
    import copy
    s = copy.deepcopy(script)
    beats = s["scene_beats"]
    counts = [count_words(b.get("narration_text", "")) for b in beats]
    total = sum(counts) + count_words(s.get("call_to_action", {}).get("outro_narration", ""))
    cum = 0.0
    for b, c in zip(beats, counts):
        start = cum / wpm * 60
        cum += c
        end = cum / wpm * 60
        b["timestamp_est"] = f"{fmt_ts(start)} - {fmt_ts(end)}"
    s["_computed"] = {"total_word_count": total, "estimated_duration_sec": estimate_duration(total, wpm)}
    return s


def splice_script(base: dict, rewritten_beats: list[dict], rewritten_cta: dict | None = None) -> dict:
    """Replace only flagged beats; assert unflagged beats byte-identical."""
    import copy
    out = copy.deepcopy(base)
    flagged = {b["beat_id"] for b in rewritten_beats}
    by_id = {b["beat_id"]: b for b in out["scene_beats"]}
    for b in rewritten_beats:
        if b["beat_id"] not in by_id:
            raise ValueError(f"rewrite references unknown beat {b['beat_id']}")
        by_id[b["beat_id"]].update(b)
    if rewritten_cta is not None:
        out["call_to_action"] = rewritten_cta
    return out
