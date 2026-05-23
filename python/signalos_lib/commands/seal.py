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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.artifacts import GATE_ARTIFACTS


def seal_path(repo_root: Path, wave: str) -> Path:
    return repo_root / ".signalos" / "integrity" / f"seal-{wave}.json"


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


def compute_seal(repo_root: Path) -> list[dict[str, Any]]:
    """Compute hash entries for every artifact path in gate_artifacts.json."""

    entries: list[dict[str, Any]] = []
    sealed_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for rel in _enumerate_artifact_paths():
        path = repo_root / rel
        exists = path.is_file()
        entry: dict[str, Any] = {
            "artifact_path": rel,
            "exists": exists,
            "sha256": _sha256_of_file(path) if exists else "",
            "sealed_at": sealed_at,
        }
        entries.append(entry)
    return entries


def create_seal(repo_root: Path, wave: str) -> dict[str, Any]:
    """Create and persist a seal bundle for *wave*. Returns the bundle."""

    entries = compute_seal(repo_root)
    bundle = {
        "schema_version": "signalos.seal.v1",
        "wave": wave,
        "sealed_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": entries,
    }
    out_path = seal_path(repo_root, wave)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return bundle


def verify_seal(repo_root: Path, wave: str) -> dict[str, Any]:
    """Verify the sealed hashes against the current files. Returns result dict."""

    out_path = seal_path(repo_root, wave)
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
    except json.JSONDecodeError as exc:
        return {
            "schema_version": "signalos.seal.verify.v1",
            "wave": wave,
            "status": "error",
            "error": f"seal JSON invalid: {exc}",
            "seal_path": str(out_path),
        }

    mismatches: list[dict[str, Any]] = []
    missing_now: list[str] = []
    appeared_now: list[str] = []
    checked = 0
    for entry in bundle.get("artifacts", []):
        rel = str(entry.get("artifact_path", ""))
        if not rel:
            continue
        checked += 1
        sealed_hash = str(entry.get("sha256", ""))
        sealed_exists = bool(entry.get("exists", False))
        path = repo_root / rel
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
        "status": status,
        "seal_path": str(out_path),
        "checked": checked,
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
