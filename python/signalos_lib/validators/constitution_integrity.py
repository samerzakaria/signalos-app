"""Layer 1 validator: confirms constitution hash matches the lock file."""

from __future__ import annotations

__all__ = ["check_constitution_integrity"]

import json
from pathlib import Path
from typing import Any

from signalos_lib.commands.constitution import (
    LOCK_REL_PATH,
    compute_constitution_hash,
    constitution_path,
)


def check_constitution_integrity(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    """Return (passed, message, details) for the constitution integrity check.

    Passes when a lock file exists and the recomputed SHA-256 of the
    constitution matches the locked hash. Skips with a pass when the
    constitution file is absent (lock requires explicit `constitution lock`
    invocation; absence means no lock has been established yet).
    """

    repo_root = Path(repo_root)
    lock_path = repo_root / LOCK_REL_PATH
    const_path = constitution_path(repo_root)

    details: dict[str, Any] = {
        "constitution_path": str(const_path.relative_to(repo_root)) if _under(repo_root, const_path) else str(const_path),
        "lock_path": LOCK_REL_PATH,
        "constitution_exists": const_path.is_file(),
        "lock_exists": lock_path.is_file(),
    }

    if not const_path.is_file():
        return (
            True,
            "constitution document is absent; nothing to verify",
            details,
        )

    if not lock_path.is_file():
        return (
            False,
            "constitution lock is missing — run `signalos constitution lock`",
            details,
        )

    try:
        locked = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        details["lock_error"] = str(exc)
        return False, "constitution lock is unreadable or invalid JSON", details

    locked_hash = str(locked.get("sha256", "")).strip().lower()
    current_hash = compute_constitution_hash(const_path)
    details["locked_sha256"] = locked_hash
    details["current_sha256"] = current_hash

    if not locked_hash:
        return False, "constitution lock is missing the sha256 field", details

    if locked_hash != current_hash:
        return (
            False,
            "constitution hash does not match lock — re-run lock to accept changes",
            details,
        )

    return True, "constitution hash matches lock", details


def _under(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False
