"""Word-equivalence, heading detection, heuristic classification."""
from __future__ import annotations
import re

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")

HEADING_PATTERNS = [
    re.compile(r"^\s*(chapter|part|case|book|episode|story)\s+([0-9ivxlcdm]+)", re.I),
    re.compile(r"^\s*(chương|phần|hồi|vụ|quyển)\s+[0-9ivxlcdm]+", re.I),
    re.compile(r"^\s*第\s*[0-9一二三四五六七八九十百千]+\s*[章节回部卷集]", re.I),
    re.compile(r"^\s*(case|file)\s*#?\s*\d+", re.I),
    re.compile(r"^\s*[IVXLCDM]{2,}\s*[\.\-–]"),
    re.compile(r"^\s*(#{1,3}\s+.+)"),
    re.compile(r"^\s*(\*\s*){3,}$"),
    re.compile(r"^\s*(-{3,}|\*{3,}|◇◇◇|◆◆◆|···|•{3,})"),
]

SCENE_BREAK_RE = re.compile(r"^\s*(\*\s*\*\s*\*|---|\*\*\*|◇◇◇|◆◆◆|#\s*$)")


def count_cjk(text: str) -> int:
    return len(CJK_RE.findall(text))


def word_equiv(text: str, language: str = "en", factors: dict | None = None) -> int:
    factors = factors or {}
    cjk = count_cjk(text)
    if language in ("zh", "ja"):
        factor = float(factors.get(language, 0.625))
        non_cjk = re.sub(r"\s+", " ", CJK_RE.sub(" ", text)).strip()
        ws_tokens = len(non_cjk.split()) if non_cjk else 0
        return int(round(cjk / 1.6 * (factor / 0.625) + ws_tokens))
    # space-delimited
    tokens = len(text.split())
    factor = float(factors.get(language, factors.get("default", 1.0)))
    return int(round(tokens * factor))


def find_headings(paragraphs: list[dict]) -> list[dict]:
    out = []
    for p in paragraphs:
        txt = p["text"]
        for pat in HEADING_PATTERNS:
            if pat.search(txt) and len(txt) < 120:
                out.append({"para_id": p["id"], "text": txt})
                break
    return out


def heuristic_classify(word_eq: int, headings: list[dict], max_single: int = 3500,
                       language: str = "en") -> dict | None:
    """Returns classification dict or None if LLM needed."""
    if word_eq < max_single and len(headings) <= 1:
        return {"topology": "SINGLE_SHOT", "confidence": 0.95,
                "source_language": language, "genre": "general", "genre_tags": [],
                "notes": "heuristic: short + <=1 heading"}
    return None
