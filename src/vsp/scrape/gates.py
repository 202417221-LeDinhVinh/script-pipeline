"""Deterministic validation gates (SPEC §15.3). Failures exit 2."""
from __future__ import annotations
import re

TAG_RE = re.compile(r"<[A-Za-z/!][^>]*>")
ENTITY_RE = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]+);")


def check_chapters(chapters: list, start: int, requested: int | None,
                   truncated_ok: bool, cfg: dict) -> list:
    """chapters: [{number, title, paragraphs, text}]. Returns error strings."""
    errors = []
    th = (cfg.get("thresholds", {}) or {})
    min_chars = int(th.get("min_chars", 1500))
    min_paras = int(th.get("min_paragraphs", 10))
    nums = [c["number"] for c in chapters]
    expect = list(range(start, start + len(chapters)))
    if nums != expect:
        errors.append(f"non-contiguous chapter numbers from {start}: {nums}")
    if requested is not None and len(chapters) != requested and not truncated_ok:
        errors.append(f"chapter count {len(chapters)} != requested {requested}")
    ccfg = (cfg.get("chapter", {}) or {})
    lock = [p for p in ccfg.get("lock_phrases", []) or [] if p]
    left = [p for p in cfg.get("leftover_markers", []) or [] if p]
    pats = lock + left
    locked_src = [p for p in cfg.get("locked_at_source_phrases", []) or [] if p]
    seen_texts: dict[str, int] = {}
    for ch in chapters:
        text = ch.get("text", "")
        num = ch.get("number", "?")
        stripped = [s for s in (ch.get("stripped_lines", []) or []) if s and s.strip()]
        for s in stripped:
            if s.strip() and s in text:
                errors.append(f"chapter {num} stripped line leaked into final text: "
                              f"{s[:80]}")
                break
        if len(text) < min_chars:
            locked = [p for p in locked_src if p.lower() in text.lower()]
            if locked:
                errors.append(
                    f"chapter {num} locked at source: check=locked_at_source "
                    f"chapter={num}; retry with --max-chapters {num - 1} "
                    f"(matched: {locked[0][:40]})")
            else:
                errors.append(f"chapter {num} too short: "
                              f"{len(text)} chars < {min_chars}")
        if len(ch.get("paragraphs", [])) < min_paras:
            errors.append(f"chapter {ch['number']} too few paragraphs: "
                          f"{len(ch.get('paragraphs', []))} < {min_paras}")
        m = TAG_RE.search(text)
        if m:
            errors.append(f"chapter {ch['number']} has leftover HTML tag: "
                          f"{m.group(0)[:60]}")
        m = ENTITY_RE.search(text)
        if m:
            errors.append(f"chapter {ch['number']} has leftover entity: "
                          f"{m.group(0)[:30]}")
        for p in pats:
            if p.lower() in text.lower():
                errors.append(f"chapter {ch['number']} has leftover marker: "
                              f"{p[:60]}")
                break
        if text in seen_texts:
            errors.append(f"chapter {ch['number']} duplicates chapter "
                          f"{seen_texts[text]} text")
        else:
            seen_texts[text] = ch["number"]
    return errors
