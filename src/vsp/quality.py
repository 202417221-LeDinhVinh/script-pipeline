"""Quality lock loader (SPEC §17.6, §17.2 principle 7).

`config/quality_lock.yaml` is auditor-owned. The pipeline reads every locked
threshold from here and nowhere else. `pipeline.yaml` values for locked keys
are ignored and a mismatch is logged (stderr) so owner drift is visible.
"""
from __future__ import annotations
import hashlib
import sys
from pathlib import Path
import yaml


def _refusal(missing: str) -> SystemExit:
    return SystemExit(f"REFUSAL stage=config check=quality-lock missing={missing}")


def _req(section: dict, key: str, section_name: str):
    if not isinstance(section, dict) or key not in section:
        raise _refusal(f"{section_name}.{key}")
    return section[key]


REQUIRED_LOCK_PATHS = [
    ("chunking", "serial", "sliding_target"),
    ("chunking", "serial", "sliding_min"),
    ("chunking", "serial", "sliding_max"),
    ("chunking", "serial", "clean_cut_window"),
    ("word_count", "hard_min"),
    ("word_count", "hard_max"),
    ("word_count", "target_min"),
    ("word_count", "target_max"),
    ("readability", "flesch_kincaid_max"),
    ("readability", "max_sentence_words"),
    ("readability", "max_beat1_words"),
    ("release", "overall_min"),
    ("release", "dimension_floor"),
    ("release", "zero_tolerance_items"),
    ("release", "max_not_in_source_stylistic"),
    ("release", "contradicted_claims_max"),
    ("release", "export_forced_pass"),
    ("drafting", "best_of_n"),
    ("drafting", "max_rewrite_attempts"),
    ("drafting", "rewrite_must_not_regress"),
    ("judging", "panel"),
    ("judging", "samples_per_judge"),
    ("judging", "randomize_order"),
    ("judging", "writer_model_must_differ"),
    ("calibration", "min_recall_llm_types"),
    ("calibration", "min_test_retest_agreement"),
    ("calibration", "repeats"),
    ("calibration", "min_recall_per_type"),
    ("calibration", "max_false_fail_rate"),
    ("anti_template", "hook_trigram_jaccard_max"),
    ("anti_template", "cta_first_words"),
    ("anti_template", "cta_ngram"),
    ("anti_template", "cta_lookback_episodes"),
    ("anti_template", "marker_max_in_last_4"),
    ("continuity", "first_person_min_ratio"),
    ("continuity", "number_tolerance_ratio"),
    ("budget", "hard_cost_cap_usd"),
    ("budget", "max_total_tokens_opencode"),
    ("budget", "active_timeout_seconds"),
    ("budget", "max_daily_api_spend_usd"),
    ("human_review", "mode"),
    ("human_review", "sample_rate"),
    ("human_review", "honeypot_every"),
    ("human_review", "phase2_min_reviews"),
    ("human_review", "phase2_min_kappa"),
    ("human_review", "phase2_max_false_release"),
]


def ensure_lock_complete(lock: dict) -> None:
    if not isinstance(lock, dict) or not lock:
        raise _refusal("empty-lock")
    for path in REQUIRED_LOCK_PATHS:
        cur: object = lock
        for part in path:
            if not isinstance(cur, dict) or part not in cur:
                raise _refusal(".".join(path))
            cur = cur[part]  # type: ignore[index]


def load_lock(root: Path) -> dict:
    p = root / "config" / "quality_lock.yaml"
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise _refusal(f"missing-file {p}")
    except Exception as e:
        raise SystemExit(
            f"REFUSAL stage=config check=quality-lock missing=unparseable ({e})")
    if not isinstance(data, dict) or not data:
        raise _refusal("empty-lock")
    ensure_lock_complete(data)
    return data


def lock_sha256(root: Path) -> str:
    p = root / "config" / "quality_lock.yaml"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mismatch(key: str, pipeline_val, lock_val) -> None:
    try:
        if pipeline_val != lock_val:
            print(f"LOCK-MISMATCH {key}: pipeline={pipeline_val!r} lock={lock_val!r} (lock wins)",
                  file=sys.stderr)
    except Exception:
        pass


def eff_chunking(cfg) -> dict:
    """Effective serial chunking from the lock (Q1). No pipeline fallback."""
    lock = getattr(cfg, "lock", {}) or {}
    serial = _req(lock.get("chunking"), "serial", "chunking")
    if not isinstance(serial, dict):
        raise _refusal("chunking.serial")
    pipe = (cfg.pipeline.get("chunking", {}) or {})
    for k in ("sliding_target", "sliding_min", "sliding_max", "clean_cut_window"):
        _mismatch(f"chunking.{k}", pipe.get(k), _req(serial, k, "chunking.serial"))
    return {"target": int(_req(serial, "sliding_target", "chunking.serial")),
            "smin": int(_req(serial, "sliding_min", "chunking.serial")),
            "smax": int(_req(serial, "sliding_max", "chunking.serial")),
            "window": int(_req(serial, "clean_cut_window", "chunking.serial"))}


def eff_word_count(cfg) -> dict:
    lock = _req(getattr(cfg, "lock", {}), "word_count", "lock")
    pipe = cfg.pipeline.get("word_count", {}) or {}
    out = {}
    for k in ("hard_min", "hard_max", "target_min", "target_max"):
        _mismatch(f"word_count.{k}", pipe.get(k), _req(lock, k, "word_count"))
        out[k] = _req(lock, k, "word_count")
    return out


def eff_readability(cfg) -> dict:
    lock = _req(getattr(cfg, "lock", {}), "readability", "lock")
    pipe = cfg.pipeline.get("readability", {}) or {}
    hook_pipe = cfg.pipeline.get("hook", {}) or {}
    _mismatch("readability.flesch_kincaid_max", pipe.get("flesch_kincaid_max"),
              _req(lock, "flesch_kincaid_max", "readability"))
    _mismatch("readability.max_sentence_words", pipe.get("max_sentence_words"),
              _req(lock, "max_sentence_words", "readability"))
    _mismatch("hook.max_beat1_words", hook_pipe.get("max_beat1_words"),
              _req(lock, "max_beat1_words", "readability"))
    return {"max_grade": _req(lock, "flesch_kincaid_max", "readability"),
            "max_sentence_words": _req(lock, "max_sentence_words", "readability"),
            "max_beat1_words": _req(lock, "max_beat1_words", "readability")}


def eff_release(cfg) -> dict:
    lock = _req(getattr(cfg, "lock", {}), "release", "lock")
    return {"overall_min": float(_req(lock, "overall_min", "release")),
            "dimension_floor": float(_req(lock, "dimension_floor", "release")),
            "zero_tolerance": list(_req(lock, "zero_tolerance_items", "release")),
            "max_not_in_source": int(_req(lock, "max_not_in_source_stylistic", "release")),
            "contradicted_max": int(_req(lock, "contradicted_claims_max", "release")),
            "export_forced_pass": bool(_req(lock, "export_forced_pass", "release"))}


def eff_drafting(cfg) -> dict:
    lock = _req(getattr(cfg, "lock", {}), "drafting", "lock")
    pipe = cfg.pipeline.get("circuit_breakers", {}).get("episode_limits", {}) or {}
    audit = cfg.pipeline.get("audit", {}) or {}
    _mismatch("episode_limits.max_rewrite_attempts", pipe.get("max_rewrite_attempts"),
              _req(lock, "max_rewrite_attempts", "drafting"))
    return {"best_of_n": int(_req(lock, "best_of_n", "drafting")),
            "max_rewrite_attempts": int(_req(lock, "max_rewrite_attempts", "drafting")),
            "rewrite_must_not_regress": bool(_req(lock, "rewrite_must_not_regress", "drafting")),
            "pass_threshold": float(audit.get("pass_threshold", 8.0)),
            "rewrite_min": float(audit.get("rewrite_min_threshold", 5.0)),
            "forced_pass_min": float(audit.get("forced_pass_min", 7.0))}


def eff_budget(cfg, role: str = "writer") -> dict:
    """Effective budget caps from the lock (§17.4, §16.2, R12). No pipeline fallback for locked keys."""
    lock_b = _req(getattr(cfg, "lock", {}), "budget", "lock")
    pipe_lim = (cfg.pipeline.get("circuit_breakers", {}).get("episode_limits", {}) or {})
    pipe_oc = (cfg.pipeline.get("opencode", {}) or {})
    pipe_daily = (cfg.pipeline.get("circuit_breakers", {}).get("global_daily_safety", {}) or {})
    backend = ""
    try:
        backend = str((cfg.pipeline.get("models", {}).get(role, {}) or {}).get("backend", "http"))
    except Exception:
        backend = "http"
    if backend == "opencode":
        tok = int(_req(lock_b, "max_total_tokens_opencode", "budget"))
        _mismatch("opencode.max_total_tokens", pipe_oc.get("max_total_tokens"), tok)
    else:
        tok = int(pipe_lim.get("max_total_tokens", 35000))
    cost = float(_req(lock_b, "hard_cost_cap_usd", "budget"))
    _mismatch("episode_limits.hard_cost_cap_usd", pipe_lim.get("hard_cost_cap_usd"), cost)
    timeout = int(_req(lock_b, "active_timeout_seconds", "budget"))
    _mismatch("episode_limits.timeout_seconds", pipe_lim.get("timeout_seconds"), timeout)
    daily = float(_req(lock_b, "max_daily_api_spend_usd", "budget"))
    _mismatch("global_daily_safety.max_daily_api_spend_usd",
              pipe_daily.get("max_daily_api_spend_usd"), daily)
    return {"ep_token_cap": tok, "ep_cost_cap": cost, "timeout": timeout, "daily_cap": daily}


def eff_judging(cfg) -> dict:
    lock = _req(getattr(cfg, "lock", {}), "judging", "lock")
    return {"panel": list(_req(lock, "panel", "judging")),
            "samples_per_judge": int(_req(lock, "samples_per_judge", "judging")),
            "randomize_order": bool(_req(lock, "randomize_order", "judging")),
            "writer_must_differ": bool(_req(lock, "writer_model_must_differ", "judging"))}


def eff_human_review(cfg) -> dict:
    lock = _req(getattr(cfg, "lock", {}), "human_review", "lock")
    return {"mode": str(_req(lock, "mode", "human_review")),
            "sample_rate": float(_req(lock, "sample_rate", "human_review")),
            "honeypot_every": int(_req(lock, "honeypot_every", "human_review")),
            "phase2_min_reviews": int(_req(lock, "phase2_min_reviews", "human_review")),
            "phase2_min_kappa": float(_req(lock, "phase2_min_kappa", "human_review")),
            "phase2_max_false_release": float(_req(lock, "phase2_max_false_release", "human_review"))}


def eff_anti_template(cfg) -> dict:
    lock = _req(getattr(cfg, "lock", {}), "anti_template", "lock")
    return {"hook_trigram_jaccard_max": float(_req(lock, "hook_trigram_jaccard_max", "anti_template")),
            "cta_first_words": int(_req(lock, "cta_first_words", "anti_template")),
            "cta_ngram": int(_req(lock, "cta_ngram", "anti_template")),
            "cta_lookback_episodes": int(_req(lock, "cta_lookback_episodes", "anti_template")),
            "marker_max_in_last_4": int(_req(lock, "marker_max_in_last_4", "anti_template"))}


def eff_continuity(cfg) -> dict:
    lock = _req(getattr(cfg, "lock", {}), "continuity", "lock")
    return {"first_person_min_ratio": float(_req(lock, "first_person_min_ratio", "continuity")),
            "number_tolerance_ratio": float(_req(lock, "number_tolerance_ratio", "continuity"))}


def eff_calibration(cfg) -> dict:
    lock = _req(getattr(cfg, "lock", {}), "calibration", "lock")
    return {"min_recall_llm_types": float(_req(lock, "min_recall_llm_types", "calibration")),
            "min_test_retest_agreement": float(_req(lock, "min_test_retest_agreement", "calibration")),
            "repeats": int(_req(lock, "repeats", "calibration")),
            "min_recall_per_type": float(_req(lock, "min_recall_per_type", "calibration")),
            "max_false_fail_rate": float(_req(lock, "max_false_fail_rate", "calibration"))}
