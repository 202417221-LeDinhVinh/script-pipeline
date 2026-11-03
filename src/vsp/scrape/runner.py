"""Scrape orchestration (SPEC §15.1–15.3). No LLM imports here."""
from __future__ import annotations
import re
from pathlib import Path
from urllib.parse import urlparse

from . import series as series_mod
from .adapters import truyenfull as tf_adapter
from .adapters import generic as gen_adapter
from .adapters import metruyenchu as mtc_adapter
from . import gates as gates_mod
from . import store as store_mod


def load_scrape_config(root, site: str = "truyenfull") -> dict:
    """Load adapter + gate config (boilerplate lives in YAML, not code)."""
    import yaml
    path = Path(root) / "config" / "scrape" / f"{site}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def slugify_series_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    seg = path.split("/")[-1] if path else ""
    slug = re.sub(r"[^a-z0-9]+", "-", seg.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive series slug from {url!r}")
    return slug


def select_adapter(series_url: str, cfg: dict, allow_generic: bool) -> str:
    if tf_adapter.is_truyenfull_url(series_url):
        return "truyenfull"
    if mtc_adapter.is_metruyenchu_url(series_url):
        return "metruyenchu"
    if allow_generic:
        return "generic"
    raise SystemExit(
        f"REFUSAL stage=scrape check=adapter url={series_url} "
        f"non-truyenfull URL without --allow-generic")


def scrape_series(root: Path, series_url: str, cfg: dict,
                  max_chapters: int | None = 10, start: int = 1,
                  out_dir: str = "inbox", delay: float = 1.5,
                  allow_generic: bool = False, session=None,
                  ingest_fn=None) -> dict:
    """Run a full scrape. Returns the manifest. Raises SystemExit (exit 2)
    on robots/gate/adapter refusals; ScrapeError (exit 1) on transport errors.
    """
    from .http import PoliteSession, build_client, fetch_robots, RobotsRefused
    slug = slugify_series_url(series_url)
    adapter = select_adapter(series_url, cfg, allow_generic)
    if adapter == "truyenfull":
        extract = tf_adapter.extract_chapter
    elif adapter == "metruyenchu":
        extract = mtc_adapter.extract_chapter
    else:
        extract = gen_adapter.extract_chapter

    own_client = session is None
    if session is None:
        session = PoliteSession(build_client(), delay=delay)

    robots = fetch_robots(session.client, series_url)
    robots_sha = robots.sha256
    coll = series_mod.collect_chapters(session, series_url, max_chapters, cfg)
    wanted = coll["chapters"]
    if start > 1:
        wanted = [c for c in wanted if c["number"] >= start]
    truncated = coll["available"] < (max_chapters or 0)
    if max_chapters is None:
        truncated = not coll["complete"]

    import datetime
    manifest = {
        "series_url": series_url,
        "slug": slug,
        "title": coll["title"],
        "author": coll["author"],
        "adapter": adapter,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "robots_txt_sha256": robots_sha,
        "robots_fetch": robots.fetch,
        "robots_url": robots.url,
        "start": start,
        "requested": max_chapters,
        "truncated": truncated,
        "available": coll["available"],
        "chapters": [],
        "gates_passed": False,
        "gate_errors": [],
        "inbox_file": f"{out_dir}/{slug}.txt",
        "inbox_sha256": None,
    }

    chapters = []
    for meta in wanted:
        num = meta["number"]
        html_p = store_mod.chapter_path(root, slug, num)
        html = None
        prev = None
        if html_p.exists():
            prev_manifest = store_mod.read_manifest(root, slug)
            if prev_manifest:
                for pc in prev_manifest.get("chapters", []):
                    if pc.get("number") == num:
                        prev = pc
                        break
        if prev and html_p.exists():
            import hashlib as _hl
            if _hl.sha256(html_p.read_bytes()).hexdigest() == prev.get("html_sha256"):
                html = html_p.read_bytes().decode("utf-8", errors="replace")
        if html is None:
            resp = session.get(meta["url"], purpose=f"chapter {num}")
            html = resp.text
            store_mod.write_stored_html(root, slug, num, html)
            import hashlib as _hl2
            html_sha = _hl2.sha256(html_p.read_bytes()).hexdigest()
        else:
            import hashlib as _hl3
            html_sha = _hl3.sha256(html_p.read_bytes()).hexdigest()
        try:
            ext = extract(html, cfg)
        except ValueError as e:
            raise SystemExit(f"REFUSAL stage=scrape check=extract "
                             f"chunk=chuong-{num} error={e}")
        text = store_mod.chapter_text(ext["paragraphs"])
        manifest["chapters"].append({
            "number": num, "title": meta["title"], "url": meta["url"],
            "http_status": 200, "html_file": f"chapters/chuong-{num:03d}.html",
            "html_sha256": html_sha, "text_sha256": store_mod.sha_text(text),
            "chars": len(text), "paragraphs": len(ext["paragraphs"]),
            "stripped_lines": ext.get("stripped_lines", []),
            "lock_hit": bool(ext.get("lock_hit", False)),
        })
        chapters.append({"number": num, "title": meta["title"],
                         "paragraphs": ext["paragraphs"], "text": text,
                         "stripped_lines": ext.get("stripped_lines", [])})

    errors = gates_mod.check_chapters(
        chapters, start, max_chapters, truncated, cfg)
    title_ok = True
    if chapters:
        try:
            first_html = store_mod.chapter_path(
                root, slug, chapters[0]["number"]).read_text(encoding="utf-8")
            first_ext = extract(first_html, cfg)
            pt = first_ext.get("page_title", "")
            if coll["title"] and coll["title"].lower() not in pt.lower():
                errors.append("page title does not match series title")
                title_ok = False
        except Exception as e:
            errors.append(f"title check failed: {e}")
    manifest["gate_errors"] = errors
    manifest["gates_passed"] = not errors
    store_mod.write_manifest(root, slug, manifest)
    if errors:
        raise SystemExit("REFUSAL stage=scrape check=gates errors="
                         + "; ".join(errors[:5]))
    inbox_text = store_mod.build_inbox_text(coll["title"], chapters)
    out_path = store_mod.write_inbox(root, out_dir, slug, inbox_text)
    manifest["inbox_sha256"] = store_mod.sha_file(out_path)
    store_mod.write_manifest(root, slug, manifest)
    if ingest_fn is not None:
        ingest_fn(str(out_path))
    return manifest
