"""Round 15 Phase A: A1-A3 failing-then-passing proofs."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def test_a1_missing_lock_refuses(vsp_root):
    from vsp.config import load_config
    (vsp_root / "config" / "quality_lock.yaml").unlink()
    try:
        load_config(vsp_root)
        raise AssertionError("expected REFUSAL for missing lock")
    except SystemExit as e:
        assert "quality-lock" in str(e), e


def test_a1_unparseable_lock_refuses(vsp_root):
    from vsp.config import load_config
    (vsp_root / "config" / "quality_lock.yaml").write_text("release: [unclosed", encoding="utf-8")
    try:
        load_config(vsp_root)
        raise AssertionError("expected REFUSAL for unparseable lock")
    except SystemExit as e:
        assert "quality-lock" in str(e), e


def test_a1_missing_key_refuses(vsp_root):
    import yaml
    from vsp.config import load_config
    lp = vsp_root / "config" / "quality_lock.yaml"
    lock = yaml.safe_load(lp.read_text(encoding="utf-8"))
    del lock["release"]["overall_min"]
    lp.write_text(yaml.safe_dump(lock), encoding="utf-8")
    try:
        load_config(vsp_root)
        raise AssertionError("expected REFUSAL for lock missing release.overall_min")
    except SystemExit as e:
        assert "quality-lock" in str(e) and "overall_min" in str(e), e


def test_a1_no_defaults_for_locked_keys(vsp_root):
    """eff_* must not fall back to pipeline values when a locked key is absent."""
    from vsp.config import load_config
    from vsp import quality as q
    cfg = load_config(vsp_root)
    cfg.lock["word_count"] = {}
    try:
        q.eff_word_count(cfg)
        raise AssertionError("expected REFUSAL, got pipeline fallback")
    except SystemExit as e:
        assert "quality-lock" in str(e), e


def test_a2_manifest_without_lock_sha_fails_verify(vsp_root):
    import json
    from vsp.export.package import verify_series, build_manifest
    from vsp.config import load_config
    from tests.conftest import make_passing_script
    cfg = load_config(vsp_root)
    (vsp_root / "out" / "s").mkdir(parents=True, exist_ok=True)
    script = make_passing_script()
    script["script_metadata"]["episode_number"] = 1
    (vsp_root / "out" / "s" / "ep_001.json").write_text(json.dumps(script), encoding="utf-8")
    man = build_manifest(vsp_root, "s", cfg,
                         {"source_url_or_path": "x", "source_sha256": "y", "rights_status": "unknown"},
                         [{"episode": 1, "file": "ep_001.json",
                           "sha256": "x", "chunk_id": "c", "decision": "RELEASE",
                           "tier1_receipt_sha256": "r", "audited_script_sha256": "s",
                           "overall": 9.0, "scores": {}, "warnings": [], "rewrite_count": 0,
                           "cost_usd": 0.0, "tokens": 0}])
    man.pop("quality_lock_sha256", None)
    (vsp_root / "out" / "s" / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    errs = verify_series(vsp_root, "s")
    assert any("pre-lock" in e or "quality lock missing" in e for e in errs), errs


def test_a3_no_audit_fallback_in_source():
    src = (Path(__file__).resolve().parents[2] / "src" / "vsp" / "orchestrator.py").read_text(encoding="utf-8")
    # Old code had an except block falling back to pipeline audit/episode_limits + literals.
    assert 'cfg.pipeline["circuit_breakers"]["episode_limits"]["max_rewrite_attempts"]' not in src
    assert 'rewrite_min_threshold' not in src
    # do_audit must not contain a locked-key fallback: check the eff_drafting/eff_release
    # call site has no except.
    idx = src.find("from .quality import eff_drafting, eff_release")
    assert idx != -1
    region = src[idx:idx + 600]
    assert "except" not in region, region
