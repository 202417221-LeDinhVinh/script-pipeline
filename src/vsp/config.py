"""Central config loader: pipeline.yaml + market profile + lexicons + voices."""
from __future__ import annotations
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
import yaml


def _read_text(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_list_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    items = []
    for line in _read_text(path).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(line)
    return items


@dataclass
class VSPConfig:
    root: Path
    pipeline: dict = field(default_factory=dict)
    market: dict = field(default_factory=dict)
    voices: dict = field(default_factory=dict)
    cliches: list = field(default_factory=list)
    ai_isms: list = field(default_factory=list)
    honorifics: list = field(default_factory=list)
    uncontracted: list = field(default_factory=list)
    banned_slang: list = field(default_factory=list)
    slang_whitelist: list = field(default_factory=list)
    minor_unsafe: list = field(default_factory=list)
    prompt_hashes: dict = field(default_factory=dict)
    lock: dict = field(default_factory=dict)
    lock_sha256: str = ""

    @property
    def wpm(self) -> int:
        return int(self.pipeline.get("wpm", 185))


def load_config(root: Path, market: str = "us") -> VSPConfig:
    from .quality import load_lock, lock_sha256, ensure_lock_complete
    cfg_dir = root / "config"
    with open(cfg_dir / "pipeline.yaml", encoding="utf-8") as f:
        pipeline = yaml.safe_load(f) or {}
    _lock = load_lock(root)
    ensure_lock_complete(_lock)
    _lock_sha = lock_sha256(root)
    market_name = market or pipeline.get("default_market", "us")
    with open(cfg_dir / "markets" / f"{market_name}.yaml", encoding="utf-8") as f:
        market_cfg = yaml.safe_load(f) or {}
    with open(cfg_dir / "voices.yaml", encoding="utf-8") as f:
        voices = yaml.safe_load(f) or {}
    lex = cfg_dir / "lexicons"
    cfg = VSPConfig(
        root=root,
        pipeline=pipeline,
        market=market_cfg,
        voices=voices,
        cliches=load_list_file(lex / "cliches.txt"),
        ai_isms=load_list_file(lex / "ai_isms.txt"),
        honorifics=[unicodedata.normalize("NFC", h.lower()) for h in load_list_file(lex / "honorifics.txt")],
        uncontracted=load_list_file(lex / "uncontracted.txt"),
        banned_slang=load_list_file(lex / "banned_slang.txt"),
        slang_whitelist=load_list_file(lex / "slang_whitelist.txt"),
        minor_unsafe=load_list_file(lex / "minor_unsafe.txt"),
        lock=_lock,
        lock_sha256=_lock_sha,
    )
    # prompt hashes for manifest
    for p in (root / "prompts").glob("*.md"):
        import hashlib
        cfg.prompt_hashes[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return cfg
