"""Generic fallback adapter: largest text-dense block (SPEC §15.2.4).

Only used with --allow-generic; the manifest records adapter: generic.
"""
from __future__ import annotations
import html as _html_mod
import re

from .. import dom
from . import truyenfull as tf


def _visible_text(node) -> str:
    return tf._norm_ws(_html_mod.unescape(dom.inner_text(node)))


def _link_chars(node) -> int:
    total = 0
    stack = [node]
    while stack:
        n = stack.pop()
        if n.tag == "a":
            total += len(_visible_text(n))
        stack.extend(n.children)
    return total


def _score(node) -> float:
    text = _visible_text(node)
    links = _link_chars(node)
    return max(0.0, len(text) - 3 * links)


def pick_content(root):
    """Largest text-dense block among article/main/section/div candidates."""
    best, best_score = None, 0.0
    for node in dom.select(root, "article, main, section, div"):
        s = _score(node)
        if s > best_score:
            best, best_score = node, s
    return best


def extract_chapter(html_text: str, cfg: dict) -> dict:
    """Extract via the largest text-dense block. Same return shape as truyenfull."""
    root = dom.parse(html_text)
    content = pick_content(root)
    if content is None:
        raise ValueError("generic adapter: no text-dense block found")
    stripped: list = []
    for node in list(tf._walk_all(content)):
        if node is not content and tf._frag_hit(
                node, ["advertisement", "advert", "watermark", "comment",
                       "sidebar", "footer", "header", "nav", "menu"]):
            text = tf._norm_ws(tf._text_of(node))
            tf._cut(node)
            if text:
                stripped.append(text)
    paragraphs = [p for p in tf._collect_paragraphs(content) if len(p) >= 20]
    title = ""
    for sel in ["h1", "title"]:
        node = dom.select_one(root, sel)
        if node is not None:
            title = tf._norm_ws(tf._text_of(node))
            if title:
                break
    page_node = dom.select_one(root, "title")
    page_title = tf._norm_ws(tf._text_of(page_node)) if page_node is not None else ""
    return {"title": title, "paragraphs": paragraphs,
            "stripped_lines": stripped, "page_title": page_title,
            "lock_hit": False}
