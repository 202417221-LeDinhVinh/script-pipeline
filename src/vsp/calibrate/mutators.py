"""Code-generated mutations of real episodes (no hand-written story text).

Each mutator takes a script dict (from out/<sid>/ep_*.json) and returns
(mutated_script, expected_failing_items). Labels come from the mutator.
"""
from __future__ import annotations
import copy
import re


def _caps(text: str) -> list[str]:
    return re.findall(r"\b[A-Z][A-Za-z']{2,}\b", text or "")


def mut_name_swap(script: dict) -> tuple[dict, list]:
    """Swap two capitalized names in one beat → L1 (unsupported claim)."""
    s = copy.deepcopy(script)
    for b in s.get("scene_beats", []):
        toks = [w for w in _caps(b.get("narration_text", "")) if w not in ("Look", "Honestly")]
        if len(toks) >= 2:
            t = b["narration_text"]
            t = t.replace(toks[0], "__TMP__").replace(toks[1], toks[0]).replace("__TMP__", toks[1])
            b["narration_text"] = t
            return s, ["L1"]
    # fallback: duplicate a word to force L1 via fidelity? still L1
    return s, ["L1"]


def mut_pov_flip(script: dict) -> tuple[dict, list]:
    """Flip I→she in beats 4-5 → V1 (voice inconsistent)."""
    s = copy.deepcopy(script)
    for b in s.get("scene_beats", []):
        if b.get("beat_id") in (4, 5):
            t = b.get("narration_text", "")
            t = re.sub(r"\bI\b", "she", t)
            t = re.sub(r"\bmy\b", "her", t)
            t = re.sub(r"\bme\b", "her", t)
            b["narration_text"] = t
    return s, ["V1"]


def mut_payoff_replace(script: dict, other: dict) -> tuple[dict, list]:
    """Replace payoff beat (4) with a beat from another episode → H2."""
    s = copy.deepcopy(script)
    try:
        donor = (other.get("scene_beats", []) or [])[3]
        for b in s.get("scene_beats", []):
            if b.get("beat_id") == 4:
                b["narration_text"] = donor.get("narration_text", b["narration_text"])
                break
    except Exception:
        pass
    return s, ["H2"]


def mut_number_change(script: dict) -> tuple[dict, list]:
    """Change a number in narration (e.g. $20→$25) → L1."""
    s = copy.deepcopy(script)
    for b in s.get("scene_beats", []):
        t = b.get("narration_text", "")
        m = re.search(r"\d+(?:\.\d+)?", t)
        if m:
            try:
                v = float(m.group(0))
                b["narration_text"] = t.replace(m.group(0), str(int(v + 5) if v == int(v) else round(v + 5, 2)), 1)
                return s, ["L1"]
            except Exception:
                continue
    return s, ["L1"]


def mut_cta_episode_wrong(script: dict) -> tuple[dict, list]:
    """Set CTA episode number wrong → L1 (factual error)."""
    s = copy.deepcopy(script)
    try:
        cta = s.get("call_to_action", {})
        outro = str(cta.get("outro_narration", "") or "")
        if re.search(r"\bpart\s+\d+", outro, re.I):
            cta["outro_narration"] = re.sub(r"\bpart\s+\d+", "part 99", outro, flags=re.I)
        else:
            cta["outro_narration"] = (outro + " part 99").strip()
    except Exception:
        pass
    return s, ["L1"]


def mut_minor_unsafe_insert(script: dict, term: str = "thirteen") -> tuple[dict, list]:
    """Insert a minor-unsafe lexicon term + an age < 18 (D3, Q7 age-up).

    After B1 the lexicon holds child descriptors (thirteen, middle school,
    ...) — the old "hot" term is not in the lexicon and does not test the
    age-up rule. Insert "thirteen" and a stated age 13 for S1/T1-23.
    """
    s = copy.deepcopy(script)
    try:
        b1 = next(b for b in s.get("scene_beats", []) if b.get("beat_id") == 1)
        b1["narration_text"] = f"{b1.get('narration_text', '').rstrip()} {term} 13 years old"
    except Exception:
        pass
    return s, ["S1"]


def mut_beats_duplicated(script: dict) -> tuple[dict, list]:
    """Duplicate two beats (pacing flat) → P1."""
    s = copy.deepcopy(script)
    try:
        beats = s.get("scene_beats", [])
        if len(beats) >= 3:
            beats[2] = copy.deepcopy(beats[1])
            beats[2]["beat_id"] = 3
    except Exception:
        pass
    return s, ["P1"]


MUTATORS = {
    "name_swap": mut_name_swap,
    "pov_flip": mut_pov_flip,
    "payoff_replace": mut_payoff_replace,
    "number_change": mut_number_change,
    "cta_episode_wrong": mut_cta_episode_wrong,
    "minor_unsafe_insert": mut_minor_unsafe_insert,
    "beats_duplicated": mut_beats_duplicated,
}
