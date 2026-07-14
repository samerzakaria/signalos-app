"""`signalos seal create|verify` — tamper-evident snapshot of governance artifacts."""

from __future__ import annotations

__all__ = [
    "compute_seal",
    "create_seal",
    "main",
    "seal_path",
    "verify_seal",
]

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.artifacts import GATE_ARTIFACTS, expected_gate_artifacts
from signalos_lib.product.release_tree import ReleaseTreeError, workspace_path
from signalos_lib.product.run_ids import safe_control_path
from signalos_lib.projects import project_governance_dir, validate_project_id


_WAVE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _validate_wave(wave: str) -> str:
    value = str(wave or "").strip()
    if not _WAVE_RE.fullmatch(value) or value.endswith("."):
        raise ValueError(
            "wave must be 1-128 characters using only letters, numbers, '.', '_' or '-'"
        )
    return value


def seal_path(repo_root: Path, wave: str, project_id: str = "default") -> Path:
    project_id = validate_project_id(project_id)
    wave = _validate_wave(wave)
    parts = [".signalos", "integrity"]
    if project_id != "default":
        parts.extend(["projects", project_id])
    parts.append(f"seal-{wave}.json")
    return safe_control_path(Path(repo_root), *parts)


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_root(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    return Path.cwd().resolve()


def _audit_append(root: Path, action: str, payload: dict[str, object]) -> None:
    """Append one row to .signalos/AUDIT_TRAIL.jsonl. Best-effort."""

    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    try:
        trail.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": action,
            **payload,
        }
        with trail.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _enumerate_artifact_paths() -> list[str]:
    """Return every artifact rel_path declared in gate_artifacts.json."""

    paths: list[str] = []
    for entries in GATE_ARTIFACTS.values():
        for artifact in entries:
            paths.append(artifact.rel_path)
    # de-dupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def compute_seal(repo_root: Path, project_id: str = "default") -> list[dict[str, Any]]:
    """Compute hash entries for every artifact path in gate_artifacts.json."""

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    sealed_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    project_id = validate_project_id(project_id)
    root = Path(repo_root).resolve()
    base = Path(project_governance_dir(root, project_id)).absolute()
    try:
        base_rel = base.relative_to(root)
    except ValueError as exc:
        raise ValueError("project governance base escapes workspace") from exc
    for artifact in expected_gate_artifacts():
        rel = (base_rel / Path(artifact.rel_path)).as_posix()
        path = workspace_path(root, rel, allow_leaf_symlink=False)
        if rel in seen:
            continue
        seen.add(rel)
        exists = path.is_file()
        entry: dict[str, Any] = {
            "artifact_path": rel,
            "canonical_artifact_path": artifact.rel_path,
            "exists": exists,
            "sha256": _sha256_of_file(path) if exists else "",
            "sealed_at": sealed_at,
        }
        entries.append(entry)
    return entries


def create_seal(
    repo_root: Path, wave: str, project_id: str = "default"
) -> dict[str, Any]:
    """Create and persist a seal bundle for *wave*. Returns the bundle."""

    project_id = validate_project_id(project_id)
    wave = _validate_wave(wave)
    entries = compute_seal(repo_root, project_id=project_id)
    bundle = {
        "schema_version": "signalos.seal.v1",
        "wave": wave,
        "project_id": project_id,
        "sealed_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": entries,
    }
    out_path = seal_path(repo_root, wave, project_id=project_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    tmp = out_path.parent / f".{out_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, out_path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return bundle


def verify_seal(
    repo_root: Path, wave: str, project_id: str = "default"
) -> dict[str, Any]:
    """Verify the sealed hashes against the current files. Returns result dict."""

    project_id = validate_project_id(project_id)
    wave = _validate_wave(wave)
    out_path = seal_path(repo_root, wave, project_id=project_id)
    if not out_path.is_file():
        return {
            "schema_version": "signalos.seal.verify.v1",
            "wave": wave,
            "status": "error",
            "error": "seal file not found",
            "seal_path": str(out_path),
        }
    try:
        bundle = json.loads(out_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "schema_version": "signalos.seal.verify.v1",
            "wave": wave,
            "status": "error",
            "error": f"seal JSON invalid: {exc}",
            "seal_path": str(out_path),
        }

    if not isinstance(bundle, dict):
        return {
            "schema_version": "signalos.seal.verify.v1",
            "wave": wave,
            "project_id": project_id,
            "status": "error",
            "error": "seal bundle must be a JSON object",
            "seal_path": str(out_path),
            "checked": 0,
        }

    structural_errors: list[str] = []
    if bundle.get("schema_version") != "signalos.seal.v1":
        structural_errors.append("seal schema_version is missing or invalid")
    if str(bundle.get("wave") or "") != wave:
        structural_errors.append("seal wave does not match the requested wave")
    if str(bundle.get("project_id") or "") != project_id:
        structural_errors.append("seal project_id does not match the requested project")

    raw_entries = bundle.get("artifacts")
    if not isinstance(raw_entries, list):
        structural_errors.append("seal artifacts must be a non-empty list")
        raw_entries = []
    elif not raw_entries:
        structural_errors.append("seal artifacts list is empty")

    try:
        current_entries = compute_seal(repo_root, project_id=project_id)
    except (OSError, ValueError, ReleaseTreeError) as exc:
        return {
            "schema_version": "signalos.seal.verify.v1",
            "wave": wave,
            "project_id": project_id,
            "status": "error",
            "error": f"current canonical artifact set could not be computed: {exc}",
            "seal_path": str(out_path),
            "checked": 0,
        }
    expected_by_path = {
        str(entry["artifact_path"]): entry for entry in current_entries
    }
    sealed_by_path: dict[str, dict[str, Any]] = {}
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            structural_errors.append(f"seal artifact row {index} is not an object")
            continue
        rel = raw_entry.get("artifact_path")
        if not isinstance(rel, str) or not rel.strip():
            structural_errors.append(f"seal artifact row {index} has no artifact_path")
            continue
        if rel != rel.strip():
            structural_errors.append(f"seal artifact row {index} has a non-canonical artifact_path")
            continue
        if rel in sealed_by_path:
            structural_errors.append(f"duplicate seal artifact row: {rel}")
            continue
        sealed_by_path[rel] = raw_entry

    expected_paths = set(expected_by_path)
    sealed_paths = set(sealed_by_path)
    for rel in sorted(expected_paths - sealed_paths):
        structural_errors.append(f"seal artifact row omitted: {rel}")
    for rel in sorted(sealed_paths - expected_paths):
        structural_errors.append(f"unexpected seal artifact row: {rel}")

    for rel in sorted(expected_paths & sealed_paths):
        row = sealed_by_path[rel]
        expected = expected_by_path[rel]
        if row.get("canonical_artifact_path") != expected.get("canonical_artifact_path"):
            structural_errors.append(
                f"seal artifact canonical path mismatch: {rel}"
            )
        if type(row.get("exists")) is not bool:
            structural_errors.append(f"seal artifact exists flag is invalid: {rel}")
        digest = row.get("sha256")
        if not isinstance(digest, str):
            structural_errors.append(f"seal artifact sha256 is invalid: {rel}")
        elif row.get("exists") is True and not re.fullmatch(r"[0-9a-f]{64}", digest):
            structural_errors.append(f"seal artifact sha256 is invalid: {rel}")
        elif row.get("exists") is False and digest != "":
            structural_errors.append(
                f"absent seal artifact must have an empty sha256: {rel}"
            )

    if structural_errors:
        return {
            "schema_version": "signalos.seal.verify.v1",
            "wave": wave,
            "project_id": project_id,
            "status": "error",
            "error": "seal bundle structure is invalid",
            "errors": structural_errors,
            "seal_path": str(out_path),
            "checked": 0,
            "expected": len(current_entries),
        }

    mismatches: list[dict[str, Any]] = []
    missing_now: list[str] = []
    appeared_now: list[str] = []
    checked = 0
    for rel in sorted(expected_by_path):
        entry = sealed_by_path[rel]
        checked += 1
        sealed_hash = str(entry.get("sha256", ""))
        sealed_exists = bool(entry.get("exists", False))
        try:
            path = workspace_path(
                Path(repo_root).resolve(), rel, allow_leaf_symlink=False,
            )
        except ReleaseTreeError as exc:
            mismatches.append({
                "artifact_path": rel,
                "reason": "unsafe-path",
                "detail": str(exc),
                "sealed_sha256": sealed_hash,
                "current_sha256": "",
            })
            continue
        current_exists = path.is_file()
        current_hash = _sha256_of_file(path) if current_exists else ""

        if sealed_exists and not current_exists:
            missing_now.append(rel)
            mismatches.append({
                "artifact_path": rel,
                "reason": "missing-now",
                "sealed_sha256": sealed_hash,
                "current_sha256": "",
            })
        elif not sealed_exists and current_exists:
            appeared_now.append(rel)
            mismatches.append({
                "artifact_path": rel,
                "reason": "appeared-now",
                "sealed_sha256": "",
                "current_sha256": current_hash,
            })
        elif sealed_exists and current_exists and sealed_hash != current_hash:
            mismatches.append({
                "artifact_path": rel,
                "reason": "hash-changed",
                "sealed_sha256": sealed_hash,
                "current_sha256": current_hash,
            })

    status = "ok" if not mismatches else "mismatch"
    return {
        "schema_version": "signalos.seal.verify.v1",
        "wave": wave,
        "project_id": project_id,
        "status": status,
        "seal_path": str(out_path),
        "checked": checked,
        "expected": len(current_entries),
        "mismatches": mismatches,
        "missing_now": missing_now,
        "appeared_now": appeared_now,
    }


def _emit(payload: dict[str, object], as_json: bool, summary: str) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(summary + "\n")


def _cmd_create(args: argparse.Namespace) -> int:
    root = _resolve_root(args.repo_root)
    wave = str(args.wave).strip()
    if not wave:
        sys.stderr.write("signalos seal create: --wave is required.\n")
        return 2

    bundle = create_seal(root, wave)
    sealed_count = sum(1 for e in bundle["artifacts"] if e["exists"])
    total = len(bundle["artifacts"])

    _audit_append(root, "seal-create", {
        "wave": wave,
        "sealed": sealed_count,
        "total": total,
        "path": str(seal_path(root, wave)),
    })

    payload = {
        "schema_version": "signalos.seal.create.v1",
        "wave": wave,
        "status": "ok",
        "sealed": sealed_count,
        "total": total,
        "seal_path": str(seal_path(root, wave)),
    }
    _emit(payload, args.as_json, f"Sealed {sealed_count}/{total} artifacts for wave {wave}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    root = _resolve_root(args.repo_root)
    wave = str(args.wave).strip()
    if not wave:
        sys.stderr.write("signalos seal verify: --wave is required.\n")
        return 2

    result = verify_seal(root, wave)
    _audit_append(root, "seal-verify", {
        "wave": wave,
        "status": result.get("status"),
        "mismatches": len(result.get("mismatches", [])),
    })

    if result.get("status") == "error":
        _emit(result, args.as_json, f"seal verify error: {result.get('error')}")
        return 1
    if result.get("status") == "ok":
        _emit(result, args.as_json,
              f"Seal ok for wave {wave} ({result['checked']} artifacts verified)")
        return 0
    _emit(result, args.as_json,
          f"Seal MISMATCH for wave {wave}: {len(result['mismatches'])} differing")
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos seal",
        description="Create and verify tamper-evident snapshots of governance artifacts.",
    )
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    p_create = sub.add_parser("create", help="Create a new seal for the given wave.")
    p_create.add_argument("--wave", required=True, metavar="N")
    p_create.add_argument("--repo-root", default=None, metavar="PATH")
    p_create.add_argument("--json", action="store_true", dest="as_json")

    p_verify = sub.add_parser("verify", help="Verify a seal against current files.")
    p_verify.add_argument("--wave", required=True, metavar="N")
    p_verify.add_argument("--repo-root", default=None, metavar="PATH")
    p_verify.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)

    if args.action == "create":
        return _cmd_create(args)
    if args.action == "verify":
        return _cmd_verify(args)

    parser.print_help(sys.stderr)
    return 2
