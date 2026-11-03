"""Q7 age-up adaptation (owner decision 2026-10-31).

Never reject a story for having minors; adapt them to 18+ consistently.

(a) Extraction records per character source_age_or_stage + citation (unknown allowed).
(b) Age map, deterministic, frozen in the series bible on first appearance.
(c) Setting map, frozen, added to localized_glossary.
(d) Gate T1-23 (zero tolerance) enforced in tier1 checks.
(e) Consistency gate T1-24.
(f) Judge checklist S1 uses the age map.
(g) Hard exclusion: sexual content involving source age < 18 -> REJECT_SAFETY.
"""
from __future__ import annotations
import re

SCHOOL_STAGE_PATTERNS = [
    "middle school", "junior high", "high school", "elementary",
    "grade", "homeroom",
]
COLLEGE_STAGES = ("college freshman", "college sophomore", "college junior",
                  "college senior", "university student", "college student")

SETTING_MAP = {
    "school": "college",
    "teacher": "professor",
    "class": "lecture",
    "parent-teacher summons": "dean's-office warning",
    "parent-teacher": "dean's-office",
    "school uniform": "everyday clothes",
    "homeroom": "year 1 of college",
}

SEXUAL_CONTENT_PATTERNS = [
    "sex", "sexual", "rape", "molest", "nude", "naked",
    "intercourse", "assault", "groom", "porn",
]


def parse_source_age(value: str | None) -> int | None:
    if value is None:
        return None
    m = re.search(r"(\d+)", str(value))
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def is_school_stage(value: str | None) -> bool:
    t = str(value or "").lower()
    if not t or t == "unknown":
        return False
    # College/university stages are adult, not school-level (B5).
    if any(k in t for k in ("college", "university", "grad school", "graduate")):
        return False
    if any(k in t for k in ("middle school", "junior high", "high school",
                            "elementary", "homeroom")):
        return True
    if "student" in t:
        return True
    m = re.search(r"(\d+)\s*(?:st|nd|rd|th)?\s*grade", t)
    if m:
        try:
            return int(m.group(1)) <= 12
        except (TypeError, ValueError):
            return True
    if re.search(r"\bgrade\s*(?:[1-9]|1[0-2])\b", t):
        return True
    return False


def is_minor_source(value: str | None) -> bool:
    age = parse_source_age(value)
    if age is not None and age < 18:
        return True
    return is_school_stage(value)


def adapted_age_for(source_age: int | None, school: bool) -> int | None:
    if source_age is not None and source_age < 18:
        return max(18, source_age + 5)
    if school:
        return 18
    return None


def adapted_stage_for(source_age: int | None, school: bool, source_value: str | None) -> str | None:
    if source_age is not None and source_age < 18:
        # Middle/high-school ages map to college freshman/sophomore.
        return "college freshman" if source_age <= 15 else "college sophomore"
    if school:
        t = str(source_value or "").lower()
        if "middle" in t:
            return "college freshman"
        return "college sophomore"
    return None


def build_age_map(registry: list, new_entities: list) -> dict:
    """Compute the frozen age_map {original_name: entry}.

    Entry: {source_age_or_stage, source_age, adapted_age, adapted_stage, offset}.
    Adults around an aged-up minor shift by the same global offset to keep gaps.
    """
    combined: dict[str, str] = {}
    for c in registry or []:
        if isinstance(c, dict) and c.get("original_name"):
            combined[str(c["original_name"])] = str(c.get("age_or_stage", c.get("source_age_or_stage", "unknown")))
    for e in new_entities or []:
        if isinstance(e, dict) and e.get("original_name"):
            combined.setdefault(str(e["original_name"]),
                                str(e.get("age_or_stage", e.get("source_age_or_stage", "unknown"))))
    minors: dict[str, tuple[int | None, bool]] = {}
    for name, val in combined.items():
        age = parse_source_age(val)
        school = is_school_stage(val)
        if (age is not None and age < 18) or school:
            minors[name] = (age, school)
    offsets: list[int] = []
    age_map: dict[str, dict] = {}
    for name, (age, school) in minors.items():
        adapted = adapted_age_for(age, school)
        stage = adapted_stage_for(age, school, combined[name])
        assert adapted is not None
        off = (adapted - age) if age is not None else 5
        offsets.append(off)
        age_map[name] = {"source_age_or_stage": combined[name], "source_age": age,
                         "adapted_age": adapted, "adapted_stage": stage, "offset": off}
    global_offset = max(offsets) if offsets else 0
    # Shift stated ages around them by the same offset to keep relative gaps.
    for name, val in combined.items():
        if name in age_map:
            continue
        age = parse_source_age(val)
        if age is not None and global_offset:
            age_map[name] = {"source_age_or_stage": val, "source_age": age,
                             "adapted_age": age + global_offset,
                             "adapted_stage": None, "offset": global_offset}
    return age_map


def ensure_setting_map(glossary: dict) -> dict:
    for k, v in SETTING_MAP.items():
        glossary.setdefault(k, v)
    return glossary


def has_aged_up(age_map: dict) -> bool:
    return any(isinstance(v, dict) and v.get("adapted_age") is not None
               and (v.get("source_age") is None or v.get("source_age", 99) < 18
                    or is_school_stage(v.get("source_age_or_stage")))
               for v in (age_map or {}).values())


def detect_minor_sexual_content(text: str, age_map: dict) -> bool:
    """(g) Hard exclusion: sexual content involving a source-age < 18 character."""
    minors = [n for n, e in (age_map or {}).items()
              if isinstance(e, dict) and e.get("source_age") is not None and e["source_age"] < 18]
    if not minors:
        return False
    low = str(text or "").lower()
    return any(re.search(rf"\b{re.escape(p)}\b", low) for p in SEXUAL_CONTENT_PATTERNS)
