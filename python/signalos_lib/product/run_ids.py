"""Canonical, containment-safe identifiers for persisted agent runs."""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["validate_run_id", "agent_run_dir", "safe_control_path"]

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_run_id(value: str) -> str:
    """Return *value* when it is one safe path segment, otherwise raise.

    Run ids cross an IPC trust boundary and become directory names.  Keeping
    the grammar deliberately small prevents absolute paths, traversal,
    alternate separators, device names, and platform-dependent normalization.
    """
    if not isinstance(value, str):
        raise ValueError("run_id must be a string")
    run_id = value.strip()
    if not _RUN_ID_RE.fullmatch(run_id) or run_id.endswith("."):
        raise ValueError(
            "run_id must be 1-128 characters using only letters, numbers, '.', '_' or '-'"
        )
    if run_id.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise ValueError("run_id is a reserved filesystem name")
    return run_id


def agent_run_dir(repo_root: Path, run_id: str) -> Path:
    """Resolve a run directory and prove it remains below the workspace base."""
    root = Path(repo_root).resolve()
    lexical_base = root / ".signalos" / "agent-runs"
    base = lexical_base.resolve()
    try:
        base.relative_to(root)
    except ValueError as exc:
        raise ValueError("agent-runs storage resolves outside the workspace") from exc
    safe_control_path(root, ".signalos", "agent-runs")
    lexical_candidate = base / validate_run_id(run_id)
    if lexical_candidate.exists() or lexical_candidate.is_symlink():
        try:
            redirected = lexical_candidate.resolve() != lexical_candidate.absolute()
        except OSError as exc:
            raise ValueError("run directory cannot be resolved safely") from exc
        if lexical_candidate.is_symlink() or redirected:
            raise ValueError("run directory must not be a symlink or junction")
    candidate = lexical_candidate.resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:  # defence in depth if the grammar ever widens
        raise ValueError("run_id resolves outside the agent-runs directory") from exc
    return candidate


def safe_control_path(repo_root: Path, *parts: str) -> Path:
    """Resolve an authority-bearing workspace path without redirected parents."""
    root = Path(repo_root).resolve()
    cursor = root
    for raw in parts:
        part = str(raw)
        if not part or Path(part).name != part or part in {".", ".."}:
            raise ValueError(f"unsafe control path segment: {part!r}")
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            is_symlink = cursor.is_symlink()
            try:
                resolved = cursor.resolve()
            except OSError as exc:
                raise ValueError("control path cannot be resolved safely") from exc
            # resolve() also exposes Windows directory junction/reparse aliases
            # that Path.is_symlink() alone may not report.
            if is_symlink or resolved != cursor.absolute():
                raise ValueError("control path must not traverse a symlink or junction")
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError("control path resolves outside the workspace") from exc
    return cursor
