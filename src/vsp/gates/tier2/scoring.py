"""Tier 2 judge scoring: overall computed by pipeline, payoff gate."""
from __future__ import annotations


def compute_overall(scores: dict, payoff_resolved: bool) -> tuple[float, dict]:
    hp = float(scores.get("hook_payoff", 0))
    if not payoff_resolved:
        hp = 0.0
    pacing = float(scores.get("pacing", 0))
    logic = float(scores.get("logic", 0))
    retention = float(scores.get("retention", 0))
    overall = (2 * hp + pacing + logic + retention) / 5
    adj = {"hook_payoff": hp, "pacing": pacing, "logic": logic, "retention": retention}
    return round(overall, 2), adj


def classify(overall: float, payoff_resolved: bool, pass_th: float = 8.0, rewrite_min: float = 5.0) -> str:
    if not payoff_resolved and overall >= pass_th:
        return "REWRITE"
    if overall >= pass_th:
        return "PASS"
    if overall >= rewrite_min:
        return "REWRITE"
    return "REJECT"


def _nfc(s: str) -> str:
    import unicodedata
    try:
        return unicodedata.normalize("NFC", str(s or ""))
    except Exception:
        return str(s or "")


def verify_checklist_evidence(items: dict, script_text: str, valid_pids: set) -> tuple[dict, list]:
    """Q8: verify each quote is a substring of the script and pids exist.

    Returns (verified_items {name: True/False/None}, errors). None = unverified
    (quote missing or pid unknown) and is not counted as a pass.
    """
    out: dict = {}
    errs: list = []
    st = _nfc(script_text)
    for name, it in (items or {}).items():
        if not isinstance(it, dict):
            out[name] = None
            errs.append(f"{name} not an object")
            continue
        want = it.get("pass")
        ok = True
        for e in it.get("evidence", []) or []:
            if not isinstance(e, dict):
                ok = False
                errs.append(f"{name} evidence not an object")
                break
            q = _nfc(str(e.get("quote", "") or ""))
            if not q or q not in st:
                ok = False
                errs.append(f"{name} quote not in script")
                break
            for pid in e.get("source_paragraph_ids", []) or []:
                try:
                    if int(pid) not in valid_pids:
                        ok = False
                        errs.append(f"{name} unknown paragraph {pid}")
                        break
                except (TypeError, ValueError):
                    ok = False
                    errs.append(f"{name} bad pid {pid!r}")
                    break
            if not ok:
                break
        # unverifiable -> None (not a pass); else the model's pass value
        if not ok:
            out[name] = None
        else:
            out[name] = True if want is True else False
    return out, errs


def checklist_to_scores(verified: dict) -> tuple[dict, float, bool]:
    """Q8: code computes dimensions from the verified checklist (0-10)."""
    def _b(name: str) -> float:
        v = verified.get(name)
        return 1.0 if v is True else 0.0
    hook = (_b("H1") + _b("H2")) / 2 * 10
    pacing = (_b("P1") + _b("P2")) / 2 * 10
    logic = (_b("L1") + _b("L2") + _b("L3")) / 3 * 10
    retention = 10.0 if verified.get("R1") is True else 0.0
    scores = {"hook_payoff": round(hook, 2), "pacing": round(pacing, 2),
              "logic": round(logic, 2), "retention": round(retention, 2)}
    overall = round((2 * hook + pacing + logic + retention) / 5, 2)
    payoff = verified.get("H2") is True
    if not payoff:
        # unresolved payoff caps hook at 0 (same as legacy payoff gate)
        scores["hook_payoff"] = 0.0
        overall = round((pacing + logic + retention) / 5, 2)
    return scores, overall, payoff


def aggregate_panel(samples: list[dict]) -> tuple[dict, list, bool]:
    """Q9/C4: per-item majority over all 4 samples.

    C4: True needs >=3 verified True. Any other outcome is a split (not a
    pass). False needs >=3 verified False to fail; otherwise the item is
    None (split) and, on zero-tolerance items, routes to NEEDS_HUMAN.
    Returns (agg, split_items, panel_split).
    """
    names = ["H1", "H2", "P1", "P2", "L1", "L2", "L3", "R1", "S1", "V1"]
    agg: dict = {}
    split: list = []
    for n in names:
        votes = [s.get(n) for s in samples if isinstance(s, dict)]
        t = sum(1 for v in votes if v is True)
        f = sum(1 for v in votes if v is False)
        if t >= 3:
            agg[n] = True
        elif f >= 3:
            agg[n] = False
        else:
            agg[n] = None
            split.append(n)
    return agg, sorted(split), bool(split)


def split_claims(script: dict) -> list[str]:
    """Q10: split narration into atomic claims (sentences) by code."""
    import re as _re
    parts = []
    for b in script.get("scene_beats", []) or []:
        parts.append(str(b.get("narration_text", "") or ""))
    try:
        parts.append(str(script.get("call_to_action", {}).get("outro_narration", "") or ""))
    except Exception:
        pass
    claims: list[str] = []
    for p in parts:
        for s in _re.split(r"(?<=[.!?])\s+", p.strip()):
            s = s.strip()
            if s:
                claims.append(s)
    return claims


def verify_fidelity(claims_res: dict, script_sentences: list, valid_pids: set,
                    max_stylistic: int = 3) -> tuple[dict, list]:
    """Q10: verify ids; release requires contradicted==0 and stylistic ≤ max.

    Returns (summary {contradicted, not_in_source, stylistic, ok, reason}, errors).
    """
    errs: list = []
    claims = (claims_res or {}).get("claims", []) or []
    contradicted = 0
    not_in_src = 0
    stylistic = 0
    for c in claims:
        if not isinstance(c, dict):
            errs.append("claim not an object")
            continue
        verdict = c.get("verdict")
        if verdict == "contradicted":
            contradicted += 1
        elif verdict == "not_in_source":
            not_in_src += 1
            if c.get("stylistic") is True:
                stylistic += 1
        for pid in c.get("source_paragraph_ids", []) or []:
            try:
                if int(pid) not in valid_pids:
                    errs.append(f"fidelity unknown paragraph {pid}")
            except (TypeError, ValueError):
                errs.append(f"fidelity bad pid {pid!r}")
    ok = (contradicted == 0 and stylistic <= max_stylistic
          and (not_in_src - stylistic) == 0)
    reason = f"contradicted={contradicted} not_in_source={not_in_src} stylistic={stylistic}"
    return {"contradicted": contradicted, "not_in_source": not_in_src,
            "stylistic": stylistic, "ok": ok, "reason": reason}, errs


def decide_release(tier1_pass: bool, overall: float | None, scores: dict,
                   checklist: dict | None = None, panel_split: bool = False,
                   overall_min: float = 8.0, dimension_floor: float = 7.0,
                   zero_tolerance: list | None = None) -> tuple[str, str]:
    """Q13/C1 release decision (code decides, models give evidence).

    C1: returns NEEDS_HUMAN when checklist or fidelity is missing (a legacy
    scores-only reply can no longer RELEASE). RELEASE requires: every
    zero-tolerance item passes, each dimension >= floor, overall >= minimum,
    and no panel split.
    """
    zt = zero_tolerance or ["L1", "S1", "V1"]
    if not tier1_pass:
        return "NEEDS_HUMAN", "tier1 must pass for release (routing decides REJECT/merge)"
    if overall is None:
        return "NEEDS_HUMAN", "no overall score"
    if checklist is None:
        return "NEEDS_HUMAN", "no checklist (legacy scores cannot release)"
    for item in zt:
        v = checklist.get(item)
        # checklist values are True/False/None (None = unverified -> not a pass)
        if v is not True:
            return "NEEDS_HUMAN", f"zero-tolerance {item} not passing"
    for dim, val in (scores or {}).items():
        try:
            if float(val) < dimension_floor:
                return "NEEDS_HUMAN", f"dimension {dim}={val} below floor {dimension_floor}"
        except (TypeError, ValueError):
            return "NEEDS_HUMAN", f"dimension {dim} invalid"
    if float(overall) < overall_min:
        return "NEEDS_HUMAN", f"overall {overall} below minimum {overall_min}"
    if panel_split:
        return "NEEDS_HUMAN", "panel split on zero-tolerance item"
    return "RELEASE", f"all release gates pass (overall {overall}>= {overall_min})"
