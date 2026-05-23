"""`signalos constitution` — hash-lock and verify the governance constitution."""

from __future__ import annotations

__all__ = [
    "CONSTITUTION_REL_PATH",
    "LOCK_REL_PATH",
    "compute_constitution_hash",
    "constitution_path",
    "main",
]

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Mirrors gate_artifacts.json G0 entry for the constitution. We don't import
# the artifact map here because the constitution may legitimately exist
# without G0 being fully populated.
CONSTITUTION_REL_PATH = "core/governance/Governance/CONSTITUTION.md"
LOCK_REL_PATH = ".signalos/integrity/constitution.lock.json"


def constitution_path(repo_root: Path) -> Path:
    return repo_root / CONSTITUTION_REL_PATH


def compute_constitution_hash(path: Path) -> str:
    """Return SHA-256 hex digest of *path* contents (bytes)."""

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


def _emit(payload: dict[str, object], as_json: bool, summary: str) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(summary + "\n")


def _cmd_lock(args: argparse.Namespace) -> int:
    root = _resolve_root(args.repo_root)
    const_path = constitution_path(root)
    if not const_path.is_file():
        payload = {
            "schema_version": "signalos.constitution.lock.v1",
            "status": "error",
            "error": "constitution file not found",
            "path": str(const_path),
        }
        _emit(payload, args.as_json, f"constitution not found: {const_path}")
        return 1

    sha256 = compute_constitution_hash(const_path)
    locked_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock_path = root / LOCK_REL_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_data = {
        "schema_version": "signalos.constitution.lock.v1",
        "path": CONSTITUTION_REL_PATH,
        "sha256": sha256,
        "locked_at": locked_at,
    }
    lock_path.write_text(json.dumps(lock_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _audit_append(root, "constitution-lock", {
        "path": CONSTITUTION_REL_PATH,
        "sha256": sha256,
    })

    payload = {
        "schema_version": "signalos.constitution.lock.v1",
        "status": "ok",
        "path": CONSTITUTION_REL_PATH,
        "sha256": sha256,
        "locked_at": locked_at,
        "lock_path": str(lock_path),
    }
    _emit(payload, args.as_json, f"Locked {CONSTITUTION_REL_PATH} → {sha256[:16]}…")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    root = _resolve_root(args.repo_root)
    const_path = constitution_path(root)
    lock_path = root / LOCK_REL_PATH

    if not const_path.is_file():
        payload = {
            "schema_version": "signalos.constitution.verify.v1",
            "status": "error",
            "error": "constitution file not found",
            "path": str(const_path),
        }
        _emit(payload, args.as_json, f"constitution not found: {const_path}")
        return 1

    if not lock_path.is_file():
        payload = {
            "schema_version": "signalos.constitution.verify.v1",
            "status": "error",
            "error": "lock file not found",
            "lock_path": str(lock_path),
        }
        _emit(payload, args.as_json, f"lock not found: {lock_path}")
        return 1

    try:
        locked = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        payload = {
            "schema_version": "signalos.constitution.verify.v1",
            "status": "error",
            "error": f"lock JSON invalid: {exc}",
        }
        _emit(payload, args.as_json, f"lock invalid JSON: {exc}")
        return 1

    locked_hash = str(locked.get("sha256", "")).strip().lower()
    current_hash = compute_constitution_hash(const_path)
    matches = locked_hash == current_hash and bool(locked_hash)

    _audit_append(root, "constitution-verify", {
        "path": CONSTITUTION_REL_PATH,
        "locked_sha256": locked_hash,
        "current_sha256": current_hash,
        "matches": matches,
    })

    payload = {
        "schema_version": "signalos.constitution.verify.v1",
        "status": "ok" if matches else "mismatch",
        "path": CONSTITUTION_REL_PATH,
        "locked_sha256": locked_hash,
        "current_sha256": current_hash,
        "matches": matches,
    }
    summary = (
        f"Constitution matches lock ({current_hash[:16]}…)"
        if matches
        else f"Constitution MISMATCH — locked={locked_hash[:16]}… current={current_hash[:16]}…"
    )
    _emit(payload, args.as_json, summary)
    return 0 if matches else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos constitution",
        description="Hash-lock and verify the governance constitution.",
    )
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    p_lock = sub.add_parser("lock", help="Compute SHA-256 and write the lock file.")
    p_lock.add_argument("--repo-root", default=None, metavar="PATH")
    p_lock.add_argument("--json", action="store_true", dest="as_json")

    p_verify = sub.add_parser("verify", help="Verify the current hash matches the lock.")
    p_verify.add_argument("--repo-root", default=None, metavar="PATH")
    p_verify.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)

    if args.action == "lock":
        return _cmd_lock(args)
    if args.action == "verify":
        return _cmd_verify(args)

    parser.print_help(sys.stderr)
    return 2
