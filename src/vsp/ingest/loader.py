"""Stage 0 ingestion: file + single Reddit URL, normalize, provenance."""
from __future__ import annotations
import json
import re
import unicodedata
from pathlib import Path
from html.parser import HTMLParser

from ..io import sha256_text, sha256_bytes


class _MLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return "".join(self.parts)


def strip_html(text: str) -> str:
    s = _MLStripper()
    s.feed(text)
    return s.get_text()


def normalize_text(raw: str) -> str:
    t = unicodedata.normalize("NFC", raw)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t\f\v]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def split_paragraphs(normalized: str) -> list[dict]:
    raw_paras = [p.strip() for p in re.split(r"\n\s*\n", normalized) if p.strip()]
    # also split single-newline separated long docs into paragraphs
    paras: list[dict] = []
    pid = 0
    for block in raw_paras:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        # keep block as one paragraph unless very long; split on single newline
        if len(lines) > 1 and all(len(l) < 300 for l in lines):
            for l in lines:
                paras.append({"id": pid, "text": re.sub(r"\s+", " ", l).strip()})
                pid += 1
        else:
            paras.append({"id": pid, "text": re.sub(r"\s+", " ", " ".join(lines)).strip()})
            pid += 1
    return paras


def slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return (s or "untitled")[:40]


def load_source_text(path_or_url: str, http_get=None) -> tuple[str, str, str]:
    """Returns (raw_text, title, source_ref). Supports file paths and single reddit URLs."""
    if path_or_url.startswith("http"):
        if "/comments/" not in path_or_url and "reddit.com" in path_or_url:
            raise ValueError("Only single Reddit post URLs are supported (no crawling)")
        import httpx
        url = path_or_url.rstrip("/")
        if not url.endswith(".json"):
            url = url + ".json"
        getter = http_get or (lambda u: httpx.get(u, headers={"User-Agent": "viral-script-pipeline/0.1.0"}, timeout=30))
        resp = getter(url)
        resp.raise_for_status()
        data = resp.json()
        try:
            post = data[0]["data"]["children"][0]["data"]
        except Exception as e:
            raise ValueError(f"Unexpected Reddit JSON shape: {e}")
        title = post.get("title", "reddit_post")
        selftext = post.get("selftext", "")
        raw = f"{title}\n\n{selftext}".strip()
        return raw, title, path_or_url
    p = Path(path_or_url)
    if not p.exists():
        raise FileNotFoundError(f"Input not found: {path_or_url}")
    suffix = p.suffix.lower()
    raw_bytes = p.read_bytes()
    if suffix == ".json":
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
            if isinstance(data, dict) and "title" in data:
                raw = f"{data.get('title','')}\n\n{data.get('selftext', data.get('text',''))}"
                title = data.get("title", p.stem)
            else:
                raw = raw_bytes.decode("utf-8")
                title = p.stem
        except Exception:
            raw = raw_bytes.decode("utf-8")
            title = p.stem
    elif suffix == ".html":
        raw = strip_html(raw_bytes.decode("utf-8", errors="ignore"))
        title = p.stem
    else:
        raw = raw_bytes.decode("utf-8", errors="ignore")
        title = p.stem
    return raw, title, str(p)


CJK_ANY_RE = None  # lazy init below
_HANGUL_RE = None
_KANA_RE = None
_VI_RE = None


def _init_lang_res():
    import re as _re
    global CJK_ANY_RE, _HANGUL_RE, _KANA_RE, _VI_RE
    if CJK_ANY_RE is None:
        CJK_ANY_RE = _re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
        _HANGUL_RE = _re.compile(r"[\uac00-\ud7af]")
        _KANA_RE = _re.compile(r"[\u3040-\u30ff]")
        _VI_RE = _re.compile(
            r"[ăâêôơưđĂÂÊÔƠƯĐàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễ"
            r"ìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹ"
            r"ÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕ"
            r"ÔỒỐỘỔỖƠỜỚỢỞỠÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸ]")


def detect_language(text: str) -> str:
    """Deterministic language detection (F8): CJK ratio → zh/ja/ko,
    Vietnamese diacritic ratio → vi, else en."""
    _init_lang_res()
    if not text or not text.strip():
        return "en"
    sample = text[:20000]
    letters = [c for c in sample if c.isalpha()]
    n_letters = max(1, len(letters))
    cjk = len(CJK_ANY_RE.findall(sample))
    if cjk / max(1, len(sample)) > 0.03 or cjk > 20:
        if len(_HANGUL_RE.findall(sample)) * 2 >= cjk:
            return "ko"
        if _KANA_RE.search(sample):
            return "ja"
        return "zh"
    vi = len(_VI_RE.findall(sample))
    if vi / n_letters > 0.02:
        return "vi"
    return "en"


def ingest_source(path_or_url: str, series_id: str | None = None, rights: str = "unknown",
                   language: str | None = None, http_get=None) -> dict:
    raw, title, ref = load_source_text(path_or_url, http_get=http_get)
    raw_bytes = raw.encode("utf-8")
    normalized = normalize_text(raw)
    paras = split_paragraphs(normalized)
    sha = sha256_text(normalized)
    raw_sha = sha256_bytes(raw_bytes)
    sid = series_id or f"{slugify(title)}_{sha[:8]}"
    detected = detect_language(normalized)
    effective = language or detected
    return {
        "series_id": sid,
        "title": title,
        "normalized_text": normalized,
        "paragraphs": paras,
        "source_url_or_path": ref,
        "source_sha256": sha,
        "raw_bytes_sha256": raw_sha,
        "rights_status": rights,
        "language": effective,
        "detected_language": detected,
        "language_forced": bool(language),
    }
