"""Dynamic boundary chunking with hard-boundary firewall + clean-cut checks."""
from __future__ import annotations
import re
from ..topology.heuristics import word_equiv, SCENE_BREAK_RE

RESET_FOR_TOPOLOGY = {
    "SINGLE_SHOT": "FULL_FLUSH",
    "ANTHOLOGY": "FULL_RESET",
    "EPISODIC_SERIES": "KEEP_MAIN_CAST_FLUSH_PLOT",
    "CONTINUOUS_SERIAL": "PERSIST",
}


def _para_words(paras: list[dict], language: str, factors: dict) -> list[int]:
    return [max(1, word_equiv(p["text"], language, factors)) for p in paras]


def _balanced_quotes(text: str) -> bool:
    for a, b in [('"', '"'), ("“", "”"), ("「", "」"), ("«", "»"), ("'", "'")]:
        if a == b:
            if text.count(a) % 2 == 1:
                return False
        else:
            if text.count(a) != text.count(b):
                return False
    return True


def _starts_with_dialogue(text: str) -> bool:
    t = text.lstrip()
    return t.startswith(('"', '"', '"', "“", "「", "»", "-", "–", "—"))


def is_case_heading(text: str) -> bool:
    return bool(re.match(r"^\s*(case|file|vụ|vụ án)\b", text, re.I) or re.match(r"^\s*第.*?[章节回]", text))


def pick_clean_cut(paras: list[dict], start: int, target_end: int, window: int, hard_stops: set[int]) -> tuple[int, bool]:
    """Pick paragraph-end cut within ±window of target. Returns (exclusive_end_idx, clean_cut)."""
    n = len(paras)
    if target_end >= n:
        return n, True
    lo = max(start + 1, target_end - window if window else target_end)
    hi = min(n, target_end + window if window else target_end)
    # convert word window to paragraph window: search all candidates in [lo, hi]
    candidates = list(range(lo, hi + 1))
    # preference 1: hard heading at boundary
    for c in sorted(candidates, key=lambda c: abs(c - target_end)):
        if c in hard_stops and lo <= c <= hi:
            return c, True
    # preference 2: scene break paragraph just before cut
    for c in sorted(candidates, key=lambda c: abs(c - target_end)):
        if c > start and c <= n and SCENE_BREAK_RE.search(paras[c - 1]["text"]):
            return c, True
    # preference 3: balanced quotes + next para not dialogue
    for c in sorted(candidates, key=lambda c: abs(c - target_end)):
        if c <= start or c > n:
            continue
        prev_text = paras[c - 1]["text"]
        nxt = paras[c]["text"] if c < n else ""
        if _balanced_quotes(prev_text) and not _starts_with_dialogue(nxt):
            return c, True
    # fallback: nearest paragraph end
    best = min(candidates, key=lambda c: abs(c - target_end)) if candidates else target_end
    return best, False


def chunk_source(source: dict, topology: str, language: str = "en", factors: dict | None = None,
                 target: int = 6000, smin: int = 5000, smax: int = 7000,
                 window_words: int = 300, complete: bool = False) -> list[dict]:
    factors = factors or {"default": 1.0}
    paras = source["paragraphs"]
    sid = source["series_id"]
    if not paras:
        raise ValueError("No paragraphs to chunk")
    pw = _para_words(paras, language, factors)
    total = sum(pw)

    # Boundary classes (F6): story/case boundaries are HARD (state flush);
    # chapter headings and scene breaks are only PREFERRED cut points.
    from ..topology.heuristics import find_headings
    headings = find_headings(paras)
    id_to_pos = {p["id"]: i for i, p in enumerate(paras)}
    heading_by_pos: dict[int, str] = {}  # paragraph position -> heading text
    for h in headings:
        if h["para_id"] in id_to_pos:
            heading_by_pos[id_to_pos[h["para_id"]]] = h["text"]
    case_starts = sorted(p for p, t in heading_by_pos.items() if is_case_heading(t))
    # preferred cuts: every heading that is not a pure scene break (chapters,
    # markdown titles, case headings inside sliding arcs)
    preferred_cuts = sorted(p for p, t in heading_by_pos.items()
                            if not SCENE_BREAK_RE.search(t))

    def para_window_to_words(a: int, b: int) -> int:
        return sum(pw[a:b])

    chunks: list[dict] = []

    def emit(a: int, b: int, is_last_of_story: bool, arc_start: bool):
        text = "\n\n".join(paras[i]["text"] for i in range(a, b))
        we = para_window_to_words(a, b)
        chunks.append({
            "a": a, "b": b, "word_equiv": we,
            "is_hard_boundary_end": is_last_of_story,
            "is_arc_start": arc_start,
        })

    def _slack(cur: int, tgt: int, end: int) -> int:
        w = 0
        k = tgt
        while k < end and w < window_words:
            w += pw[k]
            k += 1
        return max(1, k - tgt + 2)

    def _cut(cur: int, tgt: int, end: int) -> tuple[int, bool]:
        """Clean cut near tgt within ±window; chapter headings are top preference (F7)."""
        stops = {p - cur for p in preferred_cuts if cur < p <= end}
        rel, clean = pick_clean_cut(paras[cur:end], 0, tgt - cur, _slack(cur, tgt, end), stops)
        cut = cur + rel
        if cut <= cur:
            cut = min(end, tgt)
        return cut, clean

    def _advance(cur: int, end: int) -> int:
        acc = 0
        tgt = cur
        while tgt < end and acc < target:
            acc += pw[tgt]
            tgt += 1
        return tgt

    def _sliding(a: int, b: int, arc_start: bool, hard_end: bool):
        """Sliding arc over [a, b); only the final part is a hard boundary.
        Rebalances a sub-minimum trailing remainder (F7)."""
        cur = a
        first = arc_start
        while cur < b:
            tgt = _advance(cur, b)
            if tgt >= b or para_window_to_words(cur, b) <= smax:
                rest = para_window_to_words(cur, b)
                if rest < smin and chunks and chunks[-1]["b"] == cur and chunks[-1]["a"] >= a:
                    # rebalance last two: merge if combined fits, else even split
                    prev = chunks.pop()
                    combined = para_window_to_words(prev["a"], b)
                    if combined <= smax:
                        emit(prev["a"], b, hard_end, prev["is_arc_start"])
                        chunks[-1]["clean_cut"] = prev.get("clean_cut", True)
                    else:
                        mid_words = combined // 2
                        acc2 = 0
                        mid = prev["a"]
                        while mid < b and acc2 < mid_words:
                            acc2 += pw[mid]
                            mid += 1
                        cut2, clean2 = _cut(prev["a"], mid, b)
                        emit(prev["a"], cut2, False, prev["is_arc_start"])
                        chunks[-1]["clean_cut"] = clean2
                        emit(cut2, b, hard_end, False)
                        chunks[-1]["clean_cut"] = True
                    return
                emit(cur, b, hard_end, first)
                if "clean_cut" not in chunks[-1]:
                    chunks[-1]["clean_cut"] = True
                return
            cut, clean = _cut(cur, tgt, b)
            # avoid leaving a sub-minimum remainder: rebalance instead
            if para_window_to_words(cut, b) < smin:
                combined = para_window_to_words(cur, b)
                if combined <= smax:
                    emit(cur, b, hard_end, first)
                    chunks[-1]["clean_cut"] = clean
                    return
                mid_words = combined // 2
                acc2 = 0
                mid = cur
                while mid < b and acc2 < mid_words:
                    acc2 += pw[mid]
                    mid += 1
                cut2, clean2 = _cut(cur, mid, b)
                if cut2 <= cur or cut2 >= b:
                    emit(cur, b, hard_end, first)
                    chunks[-1]["clean_cut"] = clean
                    return
                emit(cur, cut2, False, first)
                chunks[-1]["clean_cut"] = clean2
                emit(cut2, b, hard_end, False)
                chunks[-1]["clean_cut"] = True
                return
            emit(cur, cut, False, first)
            chunks[-1]["clean_cut"] = clean
            cur = cut
            first = False

    if topology == "SINGLE_SHOT":
        emit(0, len(paras), True, True)
    elif topology == "EPISODIC_SERIES":
        # hard boundaries ONLY at case headings (F6); chapters are preferred cuts
        bounds = [0] + case_starts + [len(paras)]
        bounds = sorted(set(bounds))
        if len(bounds) <= 2:
            bounds = [0, len(paras)]
        for s in range(len(bounds) - 1):
            a, b = bounds[s], bounds[s + 1]
            if a >= b:
                continue
            if para_window_to_words(a, b) <= smax:
                emit(a, b, True, s == 0)
            else:
                _sliding(a, b, s == 0, True)
    elif topology == "ANTHOLOGY":
        # hard boundaries at story headings (any non-scene-break heading);
        # fallback: whole input is one story when no headings found
        bounds = [0] + preferred_cuts + [len(paras)]
        bounds = sorted(set(bounds))
        if len(bounds) <= 2:
            bounds = [0, len(paras)]
        for s in range(len(bounds) - 1):
            a, b = bounds[s], bounds[s + 1]
            if a >= b:
                continue
            if para_window_to_words(a, b) <= smax:
                emit(a, b, True, s == 0)
            else:
                _sliding(a, b, s == 0, True)
    else:  # CONTINUOUS_SERIAL sliding arc
        _sliding(0, len(paras), True, complete)

    # build chunk metadata dicts
    out = []
    for i, c in enumerate(chunks):
        a, b = c["a"], c["b"]
        text = "\n\n".join(paras[k]["text"] for k in range(a, b))
        pids = [paras[k]["id"] for k in range(a, b)]
        out.append({
            "chunk_id": f"chunk_{(i+1):03d}",
            "series_id": sid,
            "chunk_index": i + 1,
            "topology_type": topology,
            "word_count": sum(len(paras[k]["text"].split()) for k in range(a, b)),
            "word_equiv": c["word_equiv"],
            "is_arc_start": c["is_arc_start"],
            "is_hard_boundary_end": c["is_hard_boundary_end"],
            "state_reset_instruction": RESET_FOR_TOPOLOGY[topology],
            "raw_text_content": text,
            "paragraph_ids": pids,
            "clean_cut": c.get("clean_cut", True),
        })
    return out
