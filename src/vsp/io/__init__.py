"""Atomic file writes, hashing, paths, freshness checks."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from pathlib import Path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_fresh(output_path: Path, inputs_sha: dict, output_inputs_sha: dict | None) -> bool:
    """Fresh when output exists, parses, and inputs_sha matches stored."""
    if not output_path.exists():
        return False
    if output_inputs_sha is None:
        return False
    return output_inputs_sha == inputs_sha


def ensure_dirs(root: Path) -> dict:
    paths = {}
    for sub in ["run", "out", "state", "feedback", "data", "inbox"]:
        p = root / sub
        p.mkdir(parents=True, exist_ok=True)
        paths[sub] = p
    (root / "state" / "llm_cache").mkdir(parents=True, exist_ok=True)
    (root / "feedback").mkdir(parents=True, exist_ok=True)
    return paths
