"""Metruyenchu chapter extractor (SPEC §18.2 M1).

Chapter text is static HTML inside the first div.chapter_wrap, before the
second div.chapter_wrap.control-last nav. Boilerplate inside it: the
nav and a converter footer starting with a _____ separator line followed
by PS: (begging for votes). Strip from the separator line to the end.
Chapter URLs carry a random token and must be taken from the series chapter
list, never constructed (enforced by the runner: it only fetches listed URLs).
"""
from __future__ import annotations
import html as _html_mod
import re
import unicodedata
from urllib.parse import urlparse

from .. import dom


def is_metruyenchu_url(url: str) -> bool:
    try:
        return "metruyenchu.com.vn" in (urlparse(url).netloc or "").lower()
    except Exception:
        return False


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


def _text_of(node) -> str:
    return _html_mod.unescape(dom.inner_text(node))


def _as_list(value) -> list:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


_HIDDEN_RE = [re.compile(p, re.I) for p in
              (r"display\s*:\s*none", r"visibility\s*:\s*hidden")]


def _walk_all(node):
    for child in node.children:
        yield child
        yield from _walk_all(child)


def _first_wrap(root):
    """First div.chapter_wrap that is NOT .control-last (SPEC §18.1)."""
    for node in dom.select(root, "div.chapter_wrap"):
        classes = [c.lower() for c in node.classes()]
        if "control-last" in classes:
            continue
        return node
    # fallback to configured content selectors
    return None


def _pick_content(root, selectors: list):
    for sel in selectors or ["div.truyen"]:
        if not sel:
            continue
        node = dom.select_one(root, sel)
        if node is not None:
            # div.chapter_wrap alone holds only title/nav; the text lives in
            # the sibling div.truyen, so skip bare wraps when truyen exists.
            if sel == "div.chapter_wrap" and dom.select_one(root, "div.truyen") is not None:
                continue
            return node, sel
    first = _first_wrap(root)
    if first is not None:
        return first, "div.chapter_wrap(first)"
    return None, ""


_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "li", "article",
               "section", "blockquote", "tr", "table", "ul", "ol"}


def _walk_tokens(node, _top=True):
    if not _top and node.tag in _BLOCK_TAGS:
        yield ("block", None)
        yield from _walk_tokens(node, True)
        yield ("block", None)
        return
    for kind, val in node.ordered:
        if kind == "t":
            text = _norm_ws(_html_mod.unescape(val))
            if text:
                yield ("t", text)
        else:
            if val.tag == "br":
                yield ("br", None)
            elif val.tag in ("script", "style"):
                continue
            elif val.tag in _BLOCK_TAGS:
                yield ("block", None)
                yield from _walk_tokens(val, False)
                yield ("block", None)
            else:
                yield from _walk_tokens(val, True)


def _collect_paragraphs(content) -> list:
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
    """Extract one metruyenchu chapter page.

    Returns {title, paragraphs, stripped_lines, page_title, lock_hit}.
    Raises ValueError when no chapter-content element is found.
    """
    ccfg = cfg.get("chapter", {}) or {}
    root = dom.parse(html_text)
    stripped: list = []
    content, _sel = _pick_content(root, _as_list(ccfg.get("content")))
    if content is None:
        raise ValueError("no chapter-content element found")
    # remove configured selectors (scripts, nav, control blocks)
    for sel in _as_list(ccfg.get("remove_selectors")):
        if not sel:
            continue
        for node in dom.select(content, sel):
            _cut(node)
            text = _norm_ws(_text_of(node))
            if text:
                stripped.append(text)
    # drop hidden-style elements
    for node in list(_walk_all(content)):
        style = str(node.attrs.get("style", ""))
        if style and any(rx.search(style) for rx in _HIDDEN_RE):
            text = _norm_ws(_text_of(node))
            _cut(node)
            if text:
                stripped.append(text)
    paras = _collect_paragraphs(content)
    # SPEC §18.1: strip from the _____ separator line to the end (PS footer).
    sep = str(ccfg.get("separator_rule", "_____") or "_____")
    cut_at = None
    for i, p in enumerate(paras):
        if sep in p:
            cut_at = i
            break
    if cut_at is not None:
        stripped.extend(paras[cut_at:])
        paras = paras[:cut_at]
    # title: h2.current-chapter preferred
    title = ""
    for sel in _as_list(ccfg.get("title")) or ["h2.current-chapter a"]:
        node = dom.select_one(root, sel)
        if node is not None:
            t = _norm_ws(_text_of(node))
            if t:
                title = t
                break
    if not title:
        # fall back to content-adjacent h2
        node = dom.select_one(content, "h2")
        if node is not None:
            title = _norm_ws(_text_of(node))
    return {"title": title, "paragraphs": paras, "stripped_lines": stripped,
            "page_title": _page_title(root), "lock_hit": False}
