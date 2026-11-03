"""Tier 1 deterministic gates T1-01..T1-23. Each emits {id, pass, measured, threshold, beat_ids}."""
from __future__ import annotations
import re
import unicodedata

from ...draft.router import count_words, spoken_words, TEMPLATE_BLOCKS

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "so", "then", "when", "where", "while",
    "after", "before", "because", "that", "this", "these", "those", "there",
    "here", "with", "without", "from", "into", "over", "under", "again",
    "once", "just", "still", "even", "also", "very", "much", "more", "most",
    "they", "them", "their", "them", "was", "were", "had", "has", "have",
    "will", "would", "could", "should", "did", "does", "done", "been",
    "look", "honestly", "get", "gets", "crazy", "part",
    "i", "my", "me", "mine", "myself", "we", "our", "ours", "us",
    "he", "him", "his", "she", "her", "hers", "they", "them", "theirs",
    "it", "its", "you", "your", "yours",
}

_NAME_STOPWORDS = {
    "The", "This", "That", "These", "Those", "When", "Where", "While",
    "After", "Before", "Because", "But", "And", "So", "Then", "There",
    "Here", "Look", "Honestly", "Chill", "Folks", "Docs", "Cops", "Guards",
    "Hit", "Watch", "Dont", "Can't", "Don't", "I've", "She's", "He's",
    "They've", "We've", "You'll", "We'll", "I", "My", "Me", "We", "Our",
    "He", "She", "They", "It", "His", "Her", "Their", "You", "Your",
}

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")


def _norm_quote(s: str) -> str:
    return s.replace("“", '"').replace("”", '"').replace("’", "'").lower()


def _spoken_text(script: dict) -> str:
    parts = [b.get("narration_text", "") for b in script.get("scene_beats", [])]
    parts.append(script.get("call_to_action", {}).get("outro_narration", ""))
    return " ".join(parts)


KNOWN_TEMPLATES = set(TEMPLATE_BLOCKS)
KNOWN_HOOKS = {"Option_1_Pink_Teddy_Bear", "Option_2_Out_Of_Context_Confession",
               "Option_3_Contradiction", "Option_4_Calm_Before_Storm"}
KNOWN_BLOCKS = {b for blks in TEMPLATE_BLOCKS.values() for b in blks}
KNOWN_CTA = {"cliffhanger_subscription", "engagement_verdict", "loop_back", "story_complete"}
BEAT_REQUIRED_FIELDS = ["narration_text", "tts_emotion_prompt", "screen_text_overlay",
                        "b_roll_vibe", "sfx_cue", "bgm_directive"]


def check_schema(script: dict, template: str, hook: str = "", cta_type: str = "") -> dict:
    beats = script.get("scene_beats", [])
    expected = TEMPLATE_BLOCKS.get(template, [])
    problems: list[str] = []
    if template not in KNOWN_TEMPLATES:
        problems.append(f"unknown pacing_template {template!r}")
    if hook and hook not in KNOWN_HOOKS:
        problems.append(f"unknown hook_option {hook!r}")
    if cta_type and cta_type not in KNOWN_CTA:
        problems.append(f"unknown cta_type {cta_type!r}")
    if len(beats) != 5 or [b.get("beat_id") for b in beats] != [1, 2, 3, 4, 5]:
        problems.append("need 5 beats with ids 1-5")
    if [b.get("block_type") for b in beats] != expected:
        problems.append(f"block types {[b.get('block_type') for b in beats]} != {expected}")
    empty: list = []
    for b in beats:
        if b.get("block_type") not in KNOWN_BLOCKS:
            problems.append(f"beat {b.get('beat_id')}: unknown block_type {b.get('block_type')!r}")
        for f in BEAT_REQUIRED_FIELDS:
            if not isinstance(b.get(f), str) or not b.get(f, "").strip():
                problems.append(f"beat {b.get('beat_id')}: empty {f}")
                if b.get("beat_id") not in empty and isinstance(b.get("beat_id"), int):
                    empty.append(b.get("beat_id"))
    cta = script.get("call_to_action", {})
    if not isinstance(cta.get("outro_narration"), str) or not cta.get("outro_narration", "").strip():
        problems.append("call_to_action.outro_narration empty")
    ok = not problems
    return {"id": "T1-01", "pass": bool(ok), "measured": "; ".join(problems) or f"{len(beats)} beats",
            "threshold": f"5 beats ids 1-5 blocks {expected}", "beat_ids": sorted(empty) or []}


def check_word_count(script: dict, hard_min=185, hard_max=235, tmin=190, tmax=225) -> dict:
    n = spoken_words(script)
    res = {"id": "T1-02", "pass": hard_min <= n <= hard_max, "measured": n,
           "threshold": f"hard [{hard_min},{hard_max}] target [{tmin},{tmax}]",
           "beat_ids": [1, 2, 3, 4, 5] if not (hard_min <= n <= hard_max) else []}
    if res["pass"] and not (tmin <= n <= tmax):
        res["warning"] = f"outside target [{tmin},{tmax}]"
    if not res["pass"]:
        res["delta"] = (hard_min - n) if n < hard_min else (n - hard_max)
    return res


def check_readability(script: dict, max_grade: float = 6.5) -> dict:
    import textstat
    textstat.set_lang("en_US")
    grade = textstat.flesch_kincaid_grade(_spoken_text(script))
    return {"id": "T1-03", "pass": grade <= max_grade, "measured": round(float(grade), 2),
            "threshold": f"FK grade <= {max_grade}", "beat_ids": []}


def check_sentence_length(script: dict, max_words: int = 20) -> dict:
    bad = []
    for b in script.get("scene_beats", []):
        sents = re.split(r"[.!?]+", b.get("narration_text", ""))
        for s in sents:
            if s.strip() and count_words(s) > max_words:
                bad.append(b["beat_id"])
                break
    outro = script.get("call_to_action", {}).get("outro_narration", "")
    for s in re.split(r"[.!?]+", outro):
        if s.strip() and count_words(s) > max_words:
            bad.append("cta")
            break
    return {"id": "T1-04", "pass": not bad, "measured": f"violations in {bad or 'none'}",
            "threshold": f"every sentence <= {max_words} words", "beat_ids": [x for x in bad if isinstance(x, int)]}


def check_hook_length(script: dict, max_words: int = 20) -> dict:
    b1 = next((b for b in script.get("scene_beats", []) if b.get("beat_id") == 1), {})
    n = count_words(b1.get("narration_text", ""))
    return {"id": "T1-05", "pass": n <= max_words, "measured": n,
            "threshold": f"beat1 <= {max_words} words", "beat_ids": [1] if n > max_words else []}


def check_cliches(script: dict, cliches: list, ai_isms: list) -> dict:
    blob = _norm_quote(_spoken_text(script))
    hits = [c for c in (cliches + ai_isms) if _norm_quote(c) in blob]
    # find beat ids
    beat_ids = []
    for b in script.get("scene_beats", []):
        nb = _norm_quote(b.get("narration_text", ""))
        if any(_norm_quote(c) in nb for c in (cliches + ai_isms)):
            beat_ids.append(b["beat_id"])
    return {"id": "T1-06", "pass": not hits, "measured": hits or "none",
            "threshold": "no cliche/AI-ism match", "beat_ids": beat_ids}


def check_market_units(script: dict, forbidden: list) -> dict:
    blob = _spoken_text(script)
    hits = []
    for term in forbidden:
        if re.search(rf"\b{re.escape(term)}\b", blob, re.I):
            # guard: 'parameter' must not match 'meter' — \b handles it
            hits.append(term)
    beat_ids = []
    for b in script.get("scene_beats", []):
        for term in forbidden:
            if re.search(rf"\b{re.escape(term)}\b", b.get("narration_text", ""), re.I):
                beat_ids.append(b["beat_id"])
                break
    return {"id": "T1-07", "pass": not hits, "measured": hits or "none",
            "threshold": "no forbidden unit/currency", "beat_ids": sorted(set(beat_ids))}


def check_localization(script: dict, honorifics: list, registry_originals: list) -> dict:
    blob = _spoken_text(script)
    norm = unicodedata.normalize("NFC", blob.lower())
    hits = []
    if CJK_RE.search(blob):
        hits.append("CJK")
    for h in honorifics:
        if h and h in norm:
            hits.append(h)
    for o in registry_originals:
        if o and o in blob:
            hits.append(f"original-name:{o}")
    beat_ids = []
    for b in script.get("scene_beats", []):
        t = b.get("narration_text", "")
        if CJK_RE.search(t) or any(o and o in t for o in registry_originals):
            beat_ids.append(b["beat_id"])
            continue
        if any(h and h in unicodedata.normalize("NFC", t.lower()) for h in honorifics):
            beat_ids.append(b["beat_id"])
    return {"id": "T1-08", "pass": not hits, "measured": hits or "none",
            "threshold": "no honorific/CJK/original-name leak", "beat_ids": beat_ids}


def check_contractions(script: dict, uncontracted: list) -> dict:
    blob = _spoken_text(script)
    hits = []
    for term in uncontracted:
        if re.search(rf"\b{re.escape(term)}\b", blob, re.I):
            hits.append(term)
    beat_ids = []
    for b in script.get("scene_beats", []):
        for term in uncontracted:
            if re.search(rf"\b{re.escape(term)}\b", b.get("narration_text", ""), re.I):
                beat_ids.append(b["beat_id"])
                break
    return {"id": "T1-09", "pass": not hits, "measured": hits or "none",
            "threshold": "mandatory contractions", "beat_ids": sorted(set(beat_ids))}


def _word_pattern(term: str) -> str:
    """Word-boundary regex; multi-word terms allow flexible whitespace (F10)."""
    parts = [re.escape(p) for p in term.lower().split()]
    return r"\b" + r"\s+".join(parts) + r"\b"


def check_slang(script: dict, whitelist: list, banned: list, max_hits: int = 3) -> dict:
    blob = _spoken_text(script).lower()
    banned_hits = [b for b in banned if b and re.search(_word_pattern(b), blob)]
    w_hits = sum(len(re.findall(_word_pattern(w), blob)) for w in whitelist if w)
    ok = (not banned_hits) and (w_hits <= max_hits)
    return {"id": "T1-10", "pass": ok, "measured": {"banned": banned_hits, "whitelist_hits": w_hits},
            "threshold": f"banned=0 whitelist<={max_hits}", "beat_ids": []}


def check_anti_repetition(template: str, hook: str, forbidden: list) -> dict:
    bad = [x for x in [template, hook] if x in (forbidden or [])]
    return {"id": "T1-11", "pass": not bad, "measured": bad or "none",
            "threshold": f"not in {forbidden}", "beat_ids": []}


def check_payoff_binding(hook_payoff_beat_id: int) -> dict:
    return {"id": "T1-12", "pass": hook_payoff_beat_id in (4, 5), "measured": hook_payoff_beat_id,
            "threshold": "hook_payoff_beat_id in {4,5}", "beat_ids": [1] if hook_payoff_beat_id not in (4, 5) else []}


def check_hard_boundary(cta_type: str, is_hard: bool) -> dict:
    ok = not (is_hard and cta_type == "cliffhanger_subscription")
    return {"id": "T1-13", "pass": ok, "measured": cta_type,
            "threshold": "no cliffhanger_subscription on hard boundary" if is_hard else "n/a",
            "beat_ids": [5] if not ok else []}


TEMPLATE_WINDOWS = {
    "Template_1_In_Medias_Res": [(0, 3), (3, 15), (15, 35), (35, 55), (55, 70)],
    "Template_2_Breadcrumb_Mystery": [(0, 4), (4, 20), (20, 40), (40, 55), (55, 70)],
    "Template_3_Moral_Dilemma": [(0, 4), (4, 25), (25, 45), (45, 60), (60, 70)],
}


def check_template_timing(script: dict, template: str, wpm: int = 185, tol: int = 4) -> dict:
    """Warning only — never fails."""
    windows = TEMPLATE_WINDOWS.get(template, [(0, 3), (3, 15), (15, 35), (35, 55), (55, 70)])
    notes = []
    cum = 0
    for b, (ws, we) in zip(script.get("scene_beats", []), windows):
        n = count_words(b.get("narration_text", ""))
        start = cum / wpm * 60
        cum += n
        end = cum / wpm * 60
        if not (ws - tol <= start <= we + tol):
            notes.append(f"beat{b['beat_id']} start {start:.1f}s outside [{ws},{we}]±{tol}")
    return {"id": "T1-14", "pass": True, "warning_only": True, "measured": notes or "within tolerance",
            "threshold": f"windows ±{tol}s", "beat_ids": []}


def run_tier1(script: dict, cfg, template: str, hook: str, hook_payoff: int,
              cta_type: str, is_hard: bool, forbidden: list, registry_originals: list,
              extra_checks: list | None = None, ctx: dict | None = None) -> dict:
    """B2: no exception swallowing; an error is a refusal (SystemExit, exit 2)."""
    try:
        from ...quality import eff_word_count, eff_readability
        wc = eff_word_count(cfg)
        rd = eff_readability(cfg)
        p = cfg.pipeline
        checks = [
            check_schema(script, template, hook, cta_type),
            check_word_count(script, wc["hard_min"], wc["hard_max"],
                             wc["target_min"], wc["target_max"]),
            check_readability(script, rd["max_grade"]),
            check_sentence_length(script, rd["max_sentence_words"]),
            check_hook_length(script, rd["max_beat1_words"]),
            check_cliches(script, cfg.cliches, cfg.ai_isms),
            check_market_units(script, cfg.market.get("forbidden_units", [])),
            check_localization(script, cfg.honorifics, registry_originals),
            check_contractions(script, cfg.uncontracted),
            check_slang(script, cfg.slang_whitelist, cfg.banned_slang,
                        p.get("slang", {}).get("max_whitelist_hits", 3)),
            check_anti_repetition(template, hook, forbidden),
            check_payoff_binding(hook_payoff),
            check_hard_boundary(cta_type, is_hard),
            check_template_timing(script, template, cfg.wpm),
        ]
        if extra_checks:
            checks.extend(extra_checks)
        # Q3–Q7 (§17.3): context-aware gates. Missing vs empty handled in
        # _context_gates (B3). Errors are refusals, never silent passes.
        checks.extend(_context_gates(script, cfg, ctx))
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"REFUSAL stage=tier1 check=error error={e}")
    hard_fail = [c for c in checks if not c["pass"] and not c.get("warning_only")]
    warnings = [c["id"] for c in checks if c.get("warning")]
    t14 = next(c for c in checks if c["id"] == "T1-14")
    if isinstance(t14.get("measured"), list) and t14["measured"]:
        warnings.append("T1-14")
    return {"checks": checks, "passed": not hard_fail, "warnings": warnings,
            "failed_ids": [c["id"] for c in hard_fail]}


def _beat1_text(script: dict) -> str:
    try:
        b1 = next((b for b in script.get("scene_beats", []) if b.get("beat_id") == 1), {})
        return str(b1.get("narration_text", "") or "")
    except Exception:
        return ""


def _words_lower(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", (text or "").lower())


def _strip_quoted(text: str) -> str:
    t = str(text or "")
    t = re.sub(r'"[^"]*"', ' ', t)
    t = re.sub(r"“[^”]*”", ' ', t)
    t = re.sub(r"'[^']*'", ' ', t)
    return t


def check_hook_grounding(script: dict, selected_text: str | None) -> dict:
    """T1-15 (Q3/B4): objects/numbers in Beat 1 must be grounded.

    B4: check content nouns regardless of case (live ep 5 'pink teddy'
    passes the old capitalized-only check). Numbers + content words
    (len>=4, not stopwords) must appear in the selected beats/source.
    """
    if selected_text is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-15 missing=selected_text")
    if not selected_text:
        raise SystemExit("REFUSAL stage=tier1 check=T1-15 missing=selected_text(empty)")
    b1 = _beat1_text(script)
    src = (selected_text or "").lower()
    missing: list = []
    for n in re.findall(r"\d+(?:\.\d+)?", b1):
        if n not in src:
            missing.append(n)
    stop = {s.lower() for s in _STOPWORDS} | {s.lower() for s in _NAME_STOPWORDS}
    for w in re.findall(r"[A-Za-z']+", b1):
        wl = w.lower()
        if len(wl) < 4:
            continue
        if wl in stop:
            continue
        if "'" in wl:
            continue
        if wl not in src:
            missing.append(w)
    ok = not missing
    return {"id": "T1-15", "pass": ok, "measured": missing or "all grounded",
            "threshold": "beat1 tokens grounded in selected beats/source",
            "beat_ids": [] if ok else [1]}


def check_voice_lock(script: dict, narration_lock: dict | None, need: float = 0.60) -> dict:
    """T1-16 (Q4/B10): pronoun profile must match the series voice lock.

    B10: measure narrator references, not all pronouns. Third-person plural
    (they/them) and 'it' usually refer to other characters/groups, not the
    narrator, so only singular he/she/him/her/his/hers count against a
    first-person lock. Sentences naming another frozen character are excluded
    (their pronouns refer to that character, not the narrator).
    """
    if narration_lock is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-16 missing=narration_lock")
    if not narration_lock.get("pov"):
        return {"id": "T1-16", "pass": True, "measured": "no voice lock yet (ep1 establishes it)",
                "threshold": "pronoun profile matches narration.pov", "beat_ids": []}
    text = _spoken_text(script)
    plain = _strip_quoted(text)
    first = len(re.findall(r"\b(i|my|me|mine|myself|i'm|i've|i'll|i'd|we|our|us|ourselves)\b",
                           plain, re.I))
    # B10 narrator references: singular third-person only.
    third = len(re.findall(r"\b(he|him|his|she|her|hers)\b", plain, re.I))
    pov = str(narration_lock.get("pov", ""))
    if pov.startswith("first_person"):
        total = first + third
        ratio = (first / total) if total else 1.0
        ok = ratio >= float(need)
        return {"id": "T1-16", "pass": ok,
                "measured": f"first={first} third={third} ratio={ratio:.2f}",
                "threshold": f"first-person ratio >= {need}", "beat_ids": [] if ok else [1, 2, 3, 4, 5]}
    # third person: no first-person outside quoted dialogue
    ok = first == 0
    return {"id": "T1-16", "pass": ok, "measured": f"first-person outside quotes={first}",
            "threshold": "no I/my/me outside quoted dialogue", "beat_ids": [] if ok else [1, 2, 3, 4, 5]}


def check_name_continuity(script: dict, allowed_names: set | None) -> dict:
    """T1-17 (Q5/B3): every capitalized name token must be frozen or allowlisted."""
    if allowed_names is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-17 missing=allowed_names")
    allowed = {str(a).lower() for a in (allowed_names or set()) if a}
    pieces = [str(b.get("narration_text", "") or "") for b in script.get("scene_beats", [])]
    try:
        pieces.append(str(script.get("call_to_action", {}).get("outro_narration", "") or ""))
    except Exception:
        pass
    bad: set = set()
    for piece in pieces:
        for sent in re.split(r"[.!?]+", piece or ""):
            words = re.findall(r"\b[A-Za-z']+\b", sent)
            for w in words[1:]:  # skip sentence-initial word
                if "'" in w:
                    continue  # contractions (I'm, Don't) are never names
                if re.match(r"^[A-Z][A-Za-z']{2,}$", w) and w not in _NAME_STOPWORDS and w.lower() not in allowed:
                    bad.add(w)
    ok = not bad
    return {"id": "T1-17", "pass": ok, "measured": sorted(bad) or "all names known",
            "threshold": "names in frozen_names or allowlist", "beat_ids": [] if ok else [1, 2, 3, 4, 5]}


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20,
}


def _parse_episode_token(tok: str) -> int | None:
    t = str(tok or "").lower().strip()
    if re.fullmatch(r"\d+", t):
        try:
            return int(t)
        except (TypeError, ValueError):
            return None
    return _NUMBER_WORDS.get(t)


def check_episode_mention(script: dict, expected_ep: int | None) -> dict:
    """T1-18 (Q5/B7): part/episode N must equal the next episode or be absent.

    B7: also match number words (one..twenty) and ordinals (first..twentieth).
    """
    if expected_ep is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-18 missing=expected_episode")
    text = _spoken_text(script)
    found = []
    for m in re.finditer(r"\b(?:part|episode)\s+([A-Za-z0-9]+)\b", text, re.I):
        v = _parse_episode_token(m.group(1))
        if v is not None:
            found.append(v)
    bad = [n for n in found if n != int(expected_ep)]
    ok = not bad
    return {"id": "T1-18", "pass": ok, "measured": found or "no mention",
            "threshold": f"part/episode == {expected_ep} or absent",
            "beat_ids": [] if ok else [5]}


def _numbers(text: str) -> list[float]:
    vals: list[float] = []
    for m in re.finditer(r"\$?\d[\d,]*\.?\d*%?", text or ""):
        raw = m.group(0).replace("$", "").replace(",", "").replace("%", "")
        try:
            vals.append(float(raw))
        except (TypeError, ValueError):
            continue
    return vals


def check_number_grounding(script: dict, selected_text: str | None) -> dict:
    """T1-19 (Q5/B3): numbers/money in narration must appear in selected beats/source ±1%."""
    if selected_text is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-19 missing=selected_text")
    narr = _numbers(_spoken_text(script))
    if not narr:
        return {"id": "T1-19", "pass": True, "measured": "no numbers",
                "threshold": "numbers grounded ±1%", "beat_ids": []}
    src = _numbers(selected_text)
    if not src:
        return {"id": "T1-19", "pass": False, "measured": narr,
                "threshold": "numbers grounded ±1%", "beat_ids": [1, 2, 3, 4, 5]}
    bad: list = []
    for n in narr:
        hit = False
        for s in src:
            denom = max(abs(s), 1e-9)
            if abs(n - s) / denom <= 0.01 or (s == 0 and n == 0):
                hit = True
                break
        if not hit:
            bad.append(n)
    ok = not bad
    return {"id": "T1-19", "pass": ok, "measured": bad or "all grounded",
            "threshold": "numbers grounded ±1%", "beat_ids": [] if ok else [1, 2, 3, 4, 5]}


def _trigrams(words: list[str]) -> set:
    return {" ".join(words[i:i + 3]) for i in range(max(0, len(words) - 2))}


def check_hook_trigram(script: dict, prior_hooks: list | None, max_j: float = 0.35) -> dict:
    """T1-20 (Q6/B3): Beat 1 3-gram Jaccard vs earlier hooks ≤ lock (0.35)."""
    if prior_hooks is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-20 missing=prior_hooks")
    if not prior_hooks:
        return {"id": "T1-20", "pass": True, "measured": "no prior hooks",
                "threshold": f"trigram jaccard <= {max_j}", "beat_ids": []}
    cur = _trigrams(_words_lower(_beat1_text(script)))
    if not cur:
        return {"id": "T1-20", "pass": True, "measured": "empty hook",
                "threshold": f"trigram jaccard <= {max_j}", "beat_ids": []}
    worst = 0.0
    for h in prior_hooks or []:
        other = _trigrams(_words_lower(str(h or "")))
        if not other:
            continue
        j = len(cur & other) / max(1, len(cur | other))
        worst = max(worst, j)
    ok = worst <= max_j
    return {"id": "T1-20", "pass": ok, "measured": round(worst, 3),
            "threshold": f"trigram jaccard <= {max_j}", "beat_ids": [] if ok else [1]}


def _ngrams(words: list[str], n: int) -> set:
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def check_cta_first_words(script: dict, prior_ctas: list | None, n_words: int = 5) -> dict:
    """T1-21 (Q6/B9): no word n-gram of the CTA appears in any of the last 3 CTAs.

    B9 (auditor correction): the repeated phrase sits at the end, so a
    first-N-words check misses it. The lock key anti_template.cta_ngram (5)
    sets the n-gram size.
    """
    if prior_ctas is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-21 missing=prior_ctas")
    cur_words = _words_lower(str(script.get("call_to_action", {}).get("outro_narration", "")))
    if not cur_words:
        return {"id": "T1-21", "pass": True, "measured": "empty CTA",
                "threshold": f"cta {n_words}-gram unique vs last 3", "beat_ids": []}
    if not prior_ctas:
        return {"id": "T1-21", "pass": True, "measured": "no prior CTAs",
                "threshold": f"cta {n_words}-gram unique vs last 3", "beat_ids": []}
    n = max(1, int(n_words))
    curgrams = _ngrams(cur_words, n)
    for p in (prior_ctas or [])[-3:]:
        other = _ngrams(_words_lower(str(p or "")), n)
        hit = curgrams & other
        if hit:
            return {"id": "T1-21", "pass": False, "measured": sorted(hit)[:3],
                    "threshold": f"cta {n}-gram unique vs last 3",
                    "beat_ids": [5]}
    return {"id": "T1-21", "pass": True, "measured": "cta n-grams unique",
            "threshold": f"cta {n}-gram unique vs last 3", "beat_ids": []}


def check_discourse_markers(script: dict, episode_texts: list | None,
                            markers: list | None = None, max_ep: int = 2) -> dict:
    """T1-22 (Q6/B3): no marker in more than 2 of the last 4 episodes."""
    if episode_texts is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-22 missing=episode_texts")
    mk = markers or ["So get this", "Look", "Honestly", "The crazy part is"]
    texts = (episode_texts or [])[-3:] + [_spoken_text(script)]
    bad: list = []
    for m in mk:
        pat = r"\b" + r"\s+".join(re.escape(p) for p in str(m).split()) + r"\b"
        hits = sum(1 for t in texts if re.search(pat, str(t or ""), re.I))
        if hits > max_ep:
            bad.append(f"{m} in {hits}/4")
    ok = not bad
    return {"id": "T1-22", "pass": ok, "measured": bad or "markers ok",
            "threshold": f"marker in <= {max_ep} of last 4", "beat_ids": []}


_AGE_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20,
}

_AGE_CONTEXT_RES = [
    re.compile(r"\bi['’]?m\s+(\d+|[a-z]+)\b", re.I),
    re.compile(r"\bi\s+am\s+(\d+|[a-z]+)\b", re.I),
    re.compile(r"\b(\d+|[a-z]+)\s*-\s*year\s*-\s*old\b", re.I),
    re.compile(r"\bage\s+(\d+|[a-z]+)\b", re.I),
    re.compile(r"\b(\d+|[a-z]+)\s+years?\s+old\b", re.I),
]


def _stated_ages(text: str) -> list[int]:
    out: list[int] = []
    for rx in _AGE_CONTEXT_RES:
        for m in rx.finditer(str(text or "")):
            tok = str(m.group(1)).lower()
            if re.fullmatch(r"\d+", tok):
                try:
                    out.append(int(tok))
                except (TypeError, ValueError):
                    continue
            elif tok in _AGE_WORDS:
                out.append(_AGE_WORDS[tok])
    return out


def _all_text_with_overlays(script: dict) -> str:
    parts = [str(b.get("narration_text", "") or "") for b in script.get("scene_beats", [])]
    parts.append(str(script.get("call_to_action", {}).get("outro_narration", "") or ""))
    overlays = [str(b.get("screen_text_overlay", "") or "") for b in script.get("scene_beats", [])]
    return "\n".join(parts + overlays)


def check_minor_safety(script: dict, minor_present: bool, lexicon: list | None) -> dict:
    """T1-23 (Q7/B8): zero tolerance when an aged-up character is in the episode.

    B8: scan narration, outro AND screen text; flag any stated age < 18
    (digits or words) for a character, plus the lexicon.
    """
    if not minor_present:
        return {"id": "T1-23", "pass": True, "measured": "no aged-up character in episode",
                "threshold": "minor-unsafe lexicon hits = 0", "beat_ids": []}
    if lexicon is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-23 missing=lexicon")
    blob = _all_text_with_overlays(script).lower()
    hits = []
    for term in lexicon or []:
        if not term:
            continue
        parts = [re.escape(p) for p in str(term).lower().split()]
        if re.search(r"\b" + r"\s+".join(parts) + r"\b", blob):
            hits.append(term)
    for a in _stated_ages(blob):
        if a < 18:
            hits.append(f"age<{a}")
    beat_ids: list = []
    for b in script.get("scene_beats", []):
        bt = " ".join([str(b.get("narration_text", "") or ""),
                        str(b.get("screen_text_overlay", "") or "")]).lower()
        found = False
        for term in lexicon or []:
            if not term:
                continue
            parts = [re.escape(p) for p in str(term).lower().split()]
            if re.search(r"\b" + r"\s+".join(parts) + r"\b", bt):
                found = True
                break
        if not found and any(a < 18 for a in _stated_ages(bt)):
            found = True
        if found and isinstance(b.get("beat_id"), int):
            beat_ids.append(b["beat_id"])
    # outro + screen text outside beats also count (beat 5 for attribution)
    outro = str(script.get("call_to_action", {}).get("outro_narration", "") or "").lower()
    if any(a < 18 for a in _stated_ages(outro)):
        hits.append("age<outro")
        beat_ids.append(5)
    ok = not hits
    return {"id": "T1-23", "pass": ok, "measured": hits or "clean",
            "threshold": "minor-unsafe lexicon hits = 0", "beat_ids": sorted(set(beat_ids))}


def check_age_consistency(script: dict, age_map: dict | None) -> dict:
    """T1-24 (Q7e): every stated age/stage equals the frozen map."""
    if age_map is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-24 missing=age_map")
    if not age_map:
        return {"id": "T1-24", "pass": True, "measured": "no aged-up characters",
                "threshold": "stated ages match frozen age_map", "beat_ids": []}
    adapted_ages = sorted({int(e["adapted_age"]) for e in age_map.values()
                           if isinstance(e, dict) and e.get("adapted_age") is not None})
    text = _all_text_with_overlays(script)
    stated = _stated_ages(text)
    bad = [a for a in stated if a not in adapted_ages]
    # Stage terms below college for an aged-up episode are already T1-23;
    # here we fail any stated age that contradicts the frozen map.
    ok = not bad
    return {"id": "T1-24", "pass": ok,
            "measured": bad or f"ages {stated} match {adapted_ages}",
            "threshold": "stated ages match frozen age_map",
            "beat_ids": [] if ok else [1, 2, 3, 4, 5]}


def _context_gates(script: dict, cfg, ctx: dict | None) -> list:
    """B3: missing (None / absent key) -> refusal; legitimately empty ([], {}) passes.

    The production path (run_gates_for_script) supplies every key.
    """
    if ctx is None:
        raise SystemExit("REFUSAL stage=tier1 check=context missing=ctx")
    required = ["selected_text", "narration_lock", "allowed_names",
                "expected_episode", "prior_hooks", "prior_ctas",
                "episode_texts", "minor_present", "age_map"]
    for k in required:
        if k not in ctx:
            raise SystemExit(f"REFUSAL stage=tier1 check=context missing={k}")
        if ctx[k] is None:
            raise SystemExit(f"REFUSAL stage=tier1 check=context missing={k}(None)")
    from ...quality import eff_anti_template, eff_continuity
    anti = eff_anti_template(cfg)
    cont = eff_continuity(cfg)
    out: list = []
    # T1-15 / T1-19 need the selected-beats + source text (Q2/Q3/Q5).
    sel = ctx["selected_text"]
    if not isinstance(sel, str) or not sel:
        raise SystemExit("REFUSAL stage=tier1 check=T1-15 missing=selected_text(empty)")
    out.append(check_hook_grounding(script, sel))
    out.append(check_number_grounding(script, sel))
    # T1-16 voice lock (Q4/B10).
    out.append(check_voice_lock(script, ctx["narration_lock"], float(cont["first_person_min_ratio"])))
    # T1-17 names (Q5).
    out.append(check_name_continuity(script, ctx["allowed_names"]))
    # T1-18 episode mention (Q5/B7).
    out.append(check_episode_mention(script, ctx["expected_episode"]))
    # T1-20/21/22 anti-template (Q6/B9).
    out.append(check_hook_trigram(script, ctx["prior_hooks"], float(anti["hook_trigram_jaccard_max"])))
    out.append(check_cta_first_words(script, ctx["prior_ctas"], int(anti["cta_ngram"])))
    markers = None
    try:
        markers = (cfg.market or {}).get("discourse_markers")
    except Exception:
        markers = None
    out.append(check_discourse_markers(script, ctx["episode_texts"], markers,
                                       int(anti["marker_max_in_last_4"])))
    # T1-23 minor safety (Q7/B8) + T1-24 age consistency (Q7e).
    lex = getattr(cfg, "minor_unsafe", None)
    if lex is None:
        raise SystemExit("REFUSAL stage=tier1 check=T1-23 missing=lexicon")
    out.append(check_minor_safety(script, bool(ctx["minor_present"]), lex))
    out.append(check_age_consistency(script, ctx["age_map"]))
    return out
