"""Calibration metrics: recall, false-fail, test-retest agreement, Cohen's kappa."""
from __future__ import annotations


def recall_per_type(results: list[dict]) -> dict:
    """results: [{mutant, expected:[items], failed:[items]}] → recall per type."""
    from collections import defaultdict
    tot: dict = defaultdict(int)
    hit: dict = defaultdict(int)
    for r in results:
        for e in r.get("expected", []) or []:
            tot[r.get("mutant", "?") + ":" + e] += 1
            if e in (r.get("failed", []) or []):
                hit[r.get("mutant", "?") + ":" + e] += 1
    # aggregate by expected item (H1..V1) and overall
    by_item: dict = {}
    for k, v in tot.items():
        _, item = k.split(":", 1)
        by_item.setdefault(item, [0, 0])
        by_item[item][0] += hit.get(k, 0)
        by_item[item][1] += v
    return {k: (h / t if t else 0.0) for k, (h, t) in by_item.items()}


def false_fail_rate(clean_results: list[dict]) -> float:
    """Fraction of unmutated items incorrectly failed."""
    tot = sum(len(r.get("checked", []) or []) for r in clean_results)
    bad = sum(len(r.get("failed", []) or []) for r in clean_results)
    return (bad / tot) if tot else 0.0


def test_retest_agreement(repeats: list[list[bool]]) -> float:
    """Pass/fail agreement over repeats (fraction of items with unanimous verdict)."""
    if not repeats or not repeats[0]:
        return 1.0
    n = len(repeats[0])
    agree = 0
    for i in range(n):
        vals = {r[i] for r in repeats if i < len(r)}
        if len(vals) == 1:
            agree += 1
    return agree / n if n else 1.0


def cohen_kappa(a: list, b: list) -> float:
    """Cohen's kappa for two binary/multiclass label lists."""
    if not a or len(a) != len(b):
        return 0.0
    labels = sorted(set(a) | set(b))
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = 0.0
    for lab in labels:
        pa = sum(1 for x in a if x == lab) / n
        pb = sum(1 for y in b if y == lab) / n
        pe += pa * pb
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1 - pe)
