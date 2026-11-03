"""Circuit breakers: blacklist, series kill-switch, alerts."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path


def load_blacklist(root: Path) -> dict:
    p = root / "state" / "blacklist.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"entries": []}


def is_blacklisted(root: Path, sha: str) -> bool:
    bl = load_blacklist(root)
    return any(e.get("sha256") == sha for e in bl.get("entries", []))


def add_blacklist(root: Path, sha: str, reason: str, series_id: str = "") -> None:
    from ..io import atomic_write_json
    p = root / "state" / "blacklist.json"
    bl = load_blacklist(root)
    bl["entries"].append({"sha256": sha, "reason": reason, "series_id": series_id,
                          "date": datetime.now().isoformat()})
    atomic_write_json(p, bl)


def series_should_blacklist(decisions: list[str], scores: list[float], cfg) -> tuple[bool, str]:
    ks = cfg.pipeline["circuit_breakers"]["series_kill_switch"]
    n = int(ks.get("max_consecutive_failed_chunks", 2))
    # consecutive failures (REJECT, REJECT_SAFETY or FAST_FORWARD_MERGE)
    tail_fail = 0
    for d in reversed(decisions):
        if d in ("REJECT", "REJECT_SAFETY", "FAST_FORWARD_MERGE"):
            tail_fail += 1
        else:
            break
    tail_safety = 0
    for d in reversed(decisions):
        if d == "REJECT_SAFETY":
            tail_safety += 1
        else:
            break
    # Q7: a series where every candidate fails safety twice is blacklisted.
    if tail_safety >= 2:
        return True, "minor_safety: 2 consecutive REJECT_SAFETY"
    if tail_fail >= n:
        return True, f"{tail_fail} consecutive failed chunks"
    min_avg = float(ks.get("min_acceptable_series_avg_score", 6.5))
    min_n = int(ks.get("min_audited_chunks_for_avg", 3))
    scored = [s for s in scores if s is not None]
    if len(scored) >= min_n and (sum(scored) / len(scored)) < min_avg:
        return True, f"series avg {sum(scored)/len(scored):.2f} < {min_avg}"
    return False, ""


def maybe_alert(root: Path, cfg, message: str) -> None:
    import os
    url = os.environ.get("VSP_ALERT_WEBHOOK") or cfg.pipeline.get("circuit_breakers", {}).get("global_daily_safety", {}).get("alert_webhook", "")
    if not url or url.startswith("https://webhook.site"):
        return
    try:
        import httpx
        httpx.post(url, json={"text": message}, timeout=10)
    except Exception:
        pass
