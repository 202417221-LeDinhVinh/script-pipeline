"""Response cache keyed by sha256(model+messages+params)."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def cache_key(model: str, messages: list, params: dict) -> str:
    payload = json.dumps({"m": model, "msg": messages, "p": params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_get(cache_dir: Path, key: str) -> dict | None:
    p = cache_dir / f"{key}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def cache_put(cache_dir: Path, key: str, value: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
