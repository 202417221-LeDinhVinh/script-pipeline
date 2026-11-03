"""Truyenfull chapter extractor (SPEC §15.2.3)."""
from __future__ import annotations
import html as _html_mod
import re
import unicodedata

from .. import dom


def _norm_ws(text: str) -> str:
    return unicodedata.normalize(
        "NFC", re.sub(r"[\s\u00a0]+", " ", text or "").strip())


def _cut(node) -> None:
    detach = getattr(node, "detach", None)
    if callable(detach):
        node.detach()
        return
    parent = getattr(node, "parent", None)
    if parent is not None:
        try:
            parent.children.remove(node)
        except ValueError:
            pass


def _frag_hit(node, frags: list) -> bool:
    for c in node.classes():
        cl = c.lower()
        for f in frags:
            if f and f.lower() in cl:
                return True
    nid = str(node.attrs.get("id", "")).lower()
    return any(f and f.lower() in nid for f in frags)


def _text_of(node) -> str:
    return _html_mod.unescape(dom.inner_text(node))


def _as_list(value) -> list:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


HIDDEN_STYLE_RES = [
    __import__("re").compile(p, __import__("re").I) for p in
    (r"display\s*:\s*none", r"visibility\s*:\s*hidden")
]


def _walk_all(node):
    for child in node.children:
        yield child
        yield from _walk_all(child)


def _drop_hidden(content, stripped: list) -> None:
    for node in list(_walk_all(content)):
        style = str(node.attrs.get("style", ""))
        if style and any(rx.search(style) for rx in HIDDEN_STYLE_RES):
            text = _norm_ws(_text_of(node))
            _cut(node)
            if text:
                stripped.append(text)


def _pick_content(root, selectors: list):
    for sel in selectors:
        if not sel:
            continue
        node = dom.select_one(root, sel)
        if node is not None:
            return node, sel
    return None, ""


def _drop_matched(scope, selectors: list, stripped: list) -> None:
    for sel in selectors:
        if not sel:
            continue
        for node in dom.select(scope, sel):
            _cut(node)
            text = _norm_ws(_text_of(node))
            if text:
                stripped.append(text)


BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "li", "article",
                "section", "blockquote", "tr", "table", "ul", "ol"}


def _walk_tokens(node, _top=True):
    """Yield ("t", text) / ("br", None) / ("block", None) in document order."""
    if not _top and node.tag in BLOCK_TAGS:
        yield ("block", None)
        yield from _walk_tokens(node, True)
        yield ("block", None)
        return
    for kind, val in node.ordered:
        if kind == "t":
            text = _norm_ws(_text_of_raw(val))
            if text:
                yield ("t", text)
        else:
            if val.tag == "br":
                yield ("br", None)
            elif val.tag in ("script", "style"):
                continue
            elif val.tag in BLOCK_TAGS:
                yield ("block", None)
                yield from _walk_tokens(val, False)
                yield ("block", None)
            else:
                yield from _walk_tokens(val, True)


def _text_of_raw(text: str) -> str:
    import html as _hmod
    return _hmod.unescape(text)


def _collect_paragraphs(content) -> list:
    """Split content into paragraphs: block boundaries and 2+ <br> break;
    single <br> joins with a space."""
    paras = []
    buf: list = []
    breaks = 0

    def flush():
        text = _norm_ws(" ".join(buf))
        if text:
            paras.append(text)
        buf.clear()

    first = True
    for tok, val in _walk_tokens(content):
        if tok == "block":
            if not first:
                flush()
            first = False
            breaks = 0
        elif tok == "br":
            breaks += 1
            if breaks >= 2:
                flush()
                breaks = 0
        else:
            if breaks == 1:
                buf.append(" ")
            buf.append(val)
            breaks = 0
    flush()
    return paras


def _page_title(root) -> str:
    node = dom.select_one(root, "title")
    return _norm_ws(_text_of(node)) if node is not None else ""


def extract_chapter(html_text: str, cfg: dict) -> dict:
    """Extract one chapter page.

    Returns {title, paragraphs, stripped_lines, page_title, lock_hit}.
    Raises ValueError when no chapter-content element is found.
    """
    ccfg = cfg.get("chapter", {}) or {}
    root = dom.parse(html_text)
    stripped: list = []
    content, _sel = _pick_content(
        root, _as_list(ccfg.get("content")) or ["div#chapter-c"])
    if content is None:
        raise ValueError("no chapter-content element found")
    _drop_matched(content, _as_list(ccfg.get("remove_selectors")), stripped)
    _drop_hidden(content, stripped)
    for node in list(_walk_all(content)):
        if node is not content and _frag_hit(node, _as_list(ccfg.get("boilerplate_fragments"))):
            text = _norm_ws(_text_of(node))
            _cut(node)
            if text:
                stripped.append(text)
    paragraphs = _collect_paragraphs(content)
    lock_hit = False
    lock_phrases = [p for p in _as_list(ccfg.get("lock_phrases")) if p]
    kept_lock_check = []
    for para in paragraphs:
        low = para.lower()
        hit = [p for p in lock_phrases if p.lower() in low]
        if hit:
            lock_hit = True
            stripped.append(para)
            continue
        kept_lock_check.append(para)
    drop_pat = _as_list(ccfg.get("drop_line_patterns"))
    cleaned = []
    for para in kept_lock_check:
        if any(re.search(p, para) for p in drop_pat):
            stripped.append(para)
            continue
        cleaned.append(para)
    watermark_exact = {w.strip().lower()
                       for w in _as_list(ccfg.get("watermark_lines"))
                       if str(w).strip()}
    kept = []
    for para in cleaned:
        if para.strip().lower() in watermark_exact:
            stripped.append(para)
            continue
        kept.append(para)
    title = ""
    for sel in _as_list(ccfg.get("title")) or ["h1"]:
        node = dom.select_one(root, sel)
        if node is None:
            continue
        # Prefer the title attribute (real site puts a bare number + spans
        # in the element text, e.g. "1Chương "); fall back to a descendant
        # <a title> (series-list style) and finally the element text.
        title = _norm_ws(str(node.attrs.get("title") or ""))
        if not title:
            link = dom.select_one(node, "a[title]")
            if link is not None:
                title = _norm_ws(str(link.attrs.get("title") or ""))
        if not title:
            title = _norm_ws(_text_of(node))
        if title:
            break
    # Page-level lock scan: the nag box may sit outside the content element.
    page_text = _norm_ws(_text_of(root))
    for phrase in lock_phrases:
        if phrase.lower() in page_text.lower():
            lock_hit = True
            stripped.append(f"[page lock notice] {phrase}")
            break
    return {"title": title, "paragraphs": kept,
            "stripped_lines": stripped,
            "page_title": _page_title(root), "lock_hit": lock_hit}


def is_truyenfull_url(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower().endswith("truyenfull.live")
    except Exception:
        return False
