"""Chapter-list parsing + pagination (SPEC §15.2.2)."""
from __future__ import annotations
import re
from urllib.parse import urljoin

from . import dom


def chapter_number(url: str, text: str) -> int | None:
    m = re.search(r"chuong-(\d+)", url)
    if m:
        return int(m.group(1))
    m = re.search(r"[Cc]h.*?ng\s+(\d+)", text or "")
    return int(m.group(1)) if m else None


def clean_title(text: str, series_title: str = "") -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if series_title:
        prefix = series_title.strip() + " - "
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
    t = re.sub(r"^\d+\s*", "", t)
    m = re.match(r"^[Cc]h.*?ng\s+\d+\s*[:\-–.]\s*(.+)$", t)
    if m:
        return m.group(1).strip()
    m = re.match(r"^[Cc]h.*?ng\s*(\d+)?\s*$", t)
    if m:
        return ""
    return t


def _first_text(root, selectors: list) -> str:
    for sel in selectors:
        node = dom.select_one(root, sel)
        if node is not None:
            t = re.sub(r"\s+", " ", dom.inner_text(node)).strip()
            if t:
                return t
    return ""


def parse_series_page(html: str, page_url: str, cfg: dict) -> dict:
    """Parse one series list page. Returns {title, author, chapters, next_url}."""
    root = dom.parse(html)
    scfg = cfg.get("series", {})
    title = _first_text(root, scfg.get("title", ["h1"]))
    author = _first_text(root, scfg.get("author", []))
    chapters = []
    for cont in ([dom.select_one(root, s) for s in scfg.get("chapter_list", [])]):
        if cont is None:
            continue
        for link_sel in scfg.get("chapter_link", ["li a"]):
            for a in dom.select(cont, link_sel):
                href = (a.attrs.get("href") or "").strip()
                if not href:
                    continue
                url = urljoin(page_url, href)
                raw = (a.attrs.get("title") or "").strip() or re.sub(
                    r"\s+", " ", dom.inner_text(a)).strip()
                num = chapter_number(url, raw)
                if num is None:
                    continue
                chapters.append({"number": num,
                                 "title": clean_title(raw, title),
                                 "url": url})
        if chapters:
            break
    next_url = None
    for sel in scfg.get("next_page", []):
        node = dom.select_one(root, sel)
        if node is not None and node.attrs.get("href"):
            next_url = urljoin(page_url, node.attrs["href"].strip())
            break
    return {"title": title, "author": author, "chapters": chapters,
            "next_url": next_url}


def collect_chapters(session, series_url: str, max_chapters: int | None,
                     cfg: dict) -> dict:
    """Follow list pagination until max chapters (None = all) or list end."""
    seen: dict[int, dict] = {}
    order: list[int] = []
    title, author = "", ""
    url: str | None = series_url
    guard = 0
    while url is not None and guard < 500:
        guard += 1
        resp = session.get(url, purpose="series list")
        page = parse_series_page(resp.text, url, cfg)
        if not title:
            title, author = page["title"], page["author"]
        for ch in page["chapters"]:
            if ch["number"] not in seen:
                seen[ch["number"]] = ch
                order.append(ch["number"])
                if max_chapters is not None and len(order) >= max_chapters:
                    break
        if max_chapters is not None and len(order) >= max_chapters:
            break
        url = page["next_url"]
    chapters = [seen[n] for n in order]
    if max_chapters is not None:
        chapters = chapters[:max_chapters]
    complete = url is None
    return {"title": title, "author": author, "chapters": chapters,
            "complete": complete, "available": len(order)}



