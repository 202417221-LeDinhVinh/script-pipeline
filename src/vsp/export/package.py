"""Stage 6: package export + manifest + verify."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

from ..io import atomic_write_json, sha256_text
from ..schemas.models import ScriptOutput


def validate_script_output(obj: dict) -> dict:
    return ScriptOutput.model_validate(obj).model_dump()


def export_episode(root: Path, series_id: str, episode_no: int, script_obj: dict) -> Path:
    validated = validate_script_output(script_obj)
    out_path = root / "out" / series_id / f"ep_{episode_no:03d}.json"
    atomic_write_json(out_path, validated)
    return out_path


def build_manifest(root: Path, series_id: str, cfg, source: dict, episodes: list[dict]) -> dict:
    import yaml
    try:
        _lock_sha = getattr(cfg, "lock_sha256", "") or hashlib.sha256(
            (root / "config" / "quality_lock.yaml").read_bytes()).hexdigest()
    except Exception:
        _lock_sha = getattr(cfg, "lock_sha256", "")
    manifest = {
        "series_id": series_id,
        "pipeline_version": cfg.pipeline.get("pipeline_version", "0.1.0"),
        "source": {"url_or_path": source.get("source_url_or_path"), "sha256": source.get("source_sha256"),
                   "rights_status": source.get("rights_status", "unknown")},
        "config_sha256": hashlib.sha256((root / "config" / "pipeline.yaml").read_bytes()).hexdigest(),
        "quality_lock_sha256": _lock_sha,
        "prompt_hashes": cfg.prompt_hashes,
        "models": cfg.pipeline.get("models", {}),
        "episodes": episodes,
    }
    atomic_write_json(root / "out" / series_id / "manifest.json", manifest)
    return manifest


def _normalized_binding_sha(script: dict) -> str:
    """Binding hash with episode_number normalized (export assigns it) — F12."""
    import copy as _copy
    clean = {k: v for k, v in script.items() if not k.startswith("_") and k != "inputs_sha256"}
    clean = _copy.deepcopy(clean)
    if isinstance(clean.get("script_metadata"), dict):
        clean["script_metadata"] = dict(clean["script_metadata"])
        clean["script_metadata"]["episode_number"] = 0
    return hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def verify_series(root: Path, series_id: str) -> list[str]:
    errors = []
    mpath = root / "out" / series_id / "manifest.json"
    if not mpath.exists():
        return [f"missing manifest {mpath}"]
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    # §17.6: the manifest records the lock sha; verify fails if it differs.
    try:
        _lock_sha = hashlib.sha256((root / "config" / "quality_lock.yaml").read_bytes()).hexdigest()
        _manifest_lock = manifest.get("quality_lock_sha256", "")
        if not _manifest_lock:
            errors.append("quality lock missing: manifest has no quality_lock_sha256 (pre-lock manifest)")
        elif _manifest_lock != _lock_sha:
            errors.append(f"quality lock mismatch: manifest {_manifest_lock} vs disk {_lock_sha}")
    except Exception as e:
        errors.append(f"quality lock unreadable: {e}")
    for ep in manifest.get("episodes", []):
        f = root / "out" / series_id / ep["file"]
        if not f.exists():
            errors.append(f"missing {ep['file']}")
            continue
        actual = hashlib.sha256(f.read_bytes()).hexdigest()
        if actual != ep.get("sha256"):
            errors.append(f"hash mismatch {ep['file']}: manifest {ep.get('sha256')} actual {actual}")
        try:
            script = json.loads(f.read_text(encoding="utf-8"))
            validate_script_output(script)
        except Exception as e:
            errors.append(f"schema invalid {ep['file']}: {e}")
            continue
        # F12: re-check gates + decision binding to the audited script
        binding = _normalized_binding_sha(script)
        if ep.get("audited_script_sha256") and binding != ep["audited_script_sha256"]:
            errors.append(f"binding mismatch {ep['file']}: manifest {ep.get('audited_script_sha256')} actual {binding}")
        chunk_id = ep.get("chunk_id", "")
        rw = ep.get("rewrite_count", 0)
        wdir = root / "run" / series_id / "work" / chunk_id if chunk_id else None
        if wdir is None or not (wdir / "decision.json").exists():
            errors.append(f"missing decision receipt for {ep['file']} (chunk {chunk_id or '?'})")
            continue
        try:
            decision = json.loads((wdir / "decision.json").read_text(encoding="utf-8"))
            if decision.get("inputs_sha256", {}).get("script") != binding:
                errors.append(f"decision binding mismatch {ep['file']}")
            gates_p = wdir / f"gates_{rw}.json"
            if not gates_p.exists():
                errors.append(f"missing gates receipt for {ep['file']}")
            else:
                gates = json.loads(gates_p.read_text(encoding="utf-8"))
                if gates.get("script_sha256") != binding:
                    errors.append(f"gates binding mismatch {ep['file']}")
                if ep.get("tier1_receipt_sha256"):
                    actual_receipt = hashlib.sha256(gates_p.read_bytes()).hexdigest()
                    if actual_receipt != ep["tier1_receipt_sha256"]:
                        errors.append(f"receipt hash mismatch {ep['file']}")
            audit_p = wdir / f"audit_{rw}.json"
            if audit_p.exists():
                audit = json.loads(audit_p.read_text(encoding="utf-8"))
                if audit.get("inputs_sha256", {}).get("script") != binding:
                    errors.append(f"audit binding mismatch {ep['file']}")
        except Exception as e:
            errors.append(f"receipt unreadable {ep['file']}: {e}")
    return errors
