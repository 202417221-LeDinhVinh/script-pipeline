"""Manifest, inbox file, and verify (SPEC §15.1/15.2.6)."""
from __future__ import annotations
import hashlib
import json
import unicodedata
from pathlib import Path


def series_dir(root: Path, slug: str) -> Path:
    return root / "data" / "scrape" / slug


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def display_chapter_title(series_title: str, number: int, title: str) -> str:
    """Clean chapter title for the inbox heading: strip a leading
    '{series} - ' prefix and a bare 'Chương N' marker (numbered-only chapters
    have no distinct name)."""
    import re as _re
    t = (title or "").strip()
    if series_title and t.startswith(series_title.strip() + " - "):
        t = t[len(series_title.strip()) + 3:].strip()
    if _re.fullmatch(r"[Cc]h.*?ng\s*\d+\s*", t or ""):
        return ""
    return t


def build_inbox_text(title: str, chapters: list) -> str:  # §15.2.6
    """Assemble the inbox file text: series title line, then per chapter a
    heading line plus its paragraphs; blocks separated by blank lines."""
    _nt = unicodedata.normalize("NFC", (title or "").strip())
    blocks = [_nt]
    for ch in chapters:
        head = "Chương %d" % (ch["number"],)
        disp = display_chapter_title(_nt, ch.get("number", 0), ch.get("title", ""))
        if disp and disp.strip():
            head += ": %s" % disp.strip()
        blocks.append(head)
        for para in ch["paragraphs"]:
            blocks.append(para)
    return unicodedata.normalize("NFC", "\n\n".join(blocks) + "\n")


def manifest_path(root: Path, slug: str) -> Path:
    return series_dir(root, slug) / "manifest.json"


def chapter_path(root: Path, slug: str, number: int) -> Path:
    return series_dir(root, slug) / "chapters" / f"chuong-{number:03d}.html"


def read_manifest(root: Path, slug: str) -> dict | None:
    p = manifest_path(root, slug)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_manifest(root: Path, slug: str, manifest: dict) -> Path:
    from ..io import atomic_write_json
    p = manifest_path(root, slug)
    atomic_write_json(p, manifest)
    return p


def _write_text_lf(path: Path, text: str) -> None:
    """Write text with LF newlines (no CRLF translation) for hash stability."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_stored_html(root: Path, slug: str, number: int, html: str) -> Path:
    p = chapter_path(root, slug, number)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    _write_text_lf(tmp, html)
    tmp.replace(p)
    return p


def write_inbox(root: Path, out_dir: str, slug: str, text: str) -> Path:
    out = root / out_dir / f"{slug}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    _write_text_lf(tmp, text)
    tmp.replace(out)
    return out


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def chapter_text(paragraphs: list) -> str:
    return "\n\n".join(paragraphs)


def _paras_of(root: Path, slug: str, ch: dict, cfg: dict, adapter: str) -> list:
    from .adapters import truyenfull as tf
    from .adapters import generic as gen
    from .adapters import metruyenchu as mtc
    html_file = ch.get("html_file") or f"chapters/chuong-{ch['number']:03d}.html"
    html_p = series_dir(root, slug) / html_file
    try:
        html = html_p.read_bytes().decode("utf-8", errors="replace")
        if adapter == "truyenfull":
            ext = tf.extract_chapter(html, cfg)
        elif adapter == "metruyenchu":
            ext = mtc.extract_chapter(html, cfg)
        else:
            ext = gen.extract_chapter(html, cfg)
        return ext["paragraphs"]
    except Exception:
        return []


def verify_slug(root: Path, slug: str, cfg: dict, adapter: str) -> list:
    """Re-extract every stored chapter; check hashes + inbox (SPEC §15.1)."""
    errors = []
    manifest = read_manifest(root, slug)
    if manifest is None:
        return [f"no manifest for {slug}"]
    for ch in manifest.get("chapters", []):
        num = ch["number"]
        html_p = series_dir(root, slug) / ch.get(
            "html_file", f"chapters/chuong-{num:03d}.html")
        if not html_p.exists():
            errors.append(f"chapter {num}: stored HTML missing")
            continue
        raw = html_p.read_bytes()
        if sha_bytes(raw) != ch.get("html_sha256"):
            errors.append(f"chapter {num}: stored HTML hash mismatch")
            continue
        paras = _paras_of(root, slug, ch, cfg, adapter)
        if sha_text(chapter_text(paras)) != ch.get("text_sha256"):
            errors.append(f"chapter {num}: text hash mismatch")
    rebuilt = build_inbox_text(
        manifest.get("title", ""),
        [{"number": c["number"], "title": c["title"],
          "paragraphs": _paras_of(root, slug, c, cfg, adapter)}
         for c in manifest.get("chapters", [])])
    if sha_text(rebuilt) != manifest.get("inbox_sha256"):
        errors.append("inbox text mismatch vs manifest")
    inbox_p = root / manifest.get("inbox_file", f"inbox/{slug}.txt")
    if not inbox_p.exists():
        errors.append(f"inbox file missing: {inbox_p}")
    elif inbox_p.read_bytes() != rebuilt.encode("utf-8"):
        errors.append("inbox file bytes differ from manifest concatenation")
    return errors
