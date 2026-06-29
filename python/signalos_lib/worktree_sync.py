"""App-native WorktreeSync snapshots.

SignalOS.NET models WorktreeSync as a read-only aggregate over git state. This
module preserves that behavior without ABP, EF Core, or libgit2sharp: it shells
out to git in read-only mode, records immutable JSONL snapshots, and exposes
query/validation helpers for CLI, hooks, and tests.
"""

from __future__ import annotations

__all__ = [
    "SCHEMA_VERSION",
    "VALIDATION_SCHEMA_VERSION",
    "WorktreeSyncError",
    "discover_git_root",
    "take_worktree_snapshot",
    "load_worktree_snapshots",
    "get_worktree_snapshot",
    "latest_worktree_snapshot",
    "list_worktree_snapshots",
    "validate_worktree_sync",
]

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "signalos.worktree_snapshot.v1"
VALIDATION_SCHEMA_VERSION = "signalos.validate_worktree_sync.v1"

_SNAPSHOTS_REL = Path(".signalos") / "worktree-sync" / "snapshots.jsonl"
_EVIDENCE_REL = Path(".signalos") / "evidence" / "worktree-sync" / "validate-worktree-sync.json"


class WorktreeSyncError(RuntimeError):
    """Raised when git state cannot be read safely."""


def discover_git_root(repo_path: Path | str) -> Path:
    """Return the git root at or above ``repo_path`` without mutating it."""
    candidate = Path(repo_path).expanduser()
    if not str(candidate).strip():
        raise WorktreeSyncError("repository path is required")
    if candidate.is_file():
        candidate = candidate.parent
    if not candidate.exists():
        raise WorktreeSyncError(f"repository path does not exist: {candidate}")
    out = _git(candidate, "rev-parse", "--show-toplevel")
    root = Path(out.strip()).resolve()
    if not root.exists():
        raise WorktreeSyncError(f"git root does not exist: {root}")
    return root


def take_worktree_snapshot(
    repo_path: Path | str,
    *,
    store_root: Path | str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Take and optionally persist a read-only git worktree snapshot."""
    git_root = discover_git_root(repo_path)
    commit_sha = _git(git_root, "rev-parse", "HEAD").strip()
    branch = _git(git_root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    head_message = _git(git_root, "log", "-1", "--pretty=%s").strip()
    dirty_file_count = _dirty_file_count(git_root)
    target_root = Path(store_root).resolve() if store_root else git_root
    # Monotonic per-repo sequence makes "latest" deterministic when two
    # snapshots land in the same wall-clock second (taken_at granularity).
    seq = len(load_worktree_snapshots(target_root)) + 1
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "id": str(uuid.uuid4()),
        "repo_path": str(git_root),
        "commit_sha": commit_sha,
        "branch": branch,
        "head_message": head_message,
        "dirty_file_count": dirty_file_count,
        "seq": seq,
        "taken_at": _now_iso(),
    }
    _validate_snapshot_shape(snapshot)
    if persist:
        _append_snapshot(target_root, snapshot)
    return snapshot


def load_worktree_snapshots(repo_root: Path | str) -> list[dict[str, Any]]:
    """Load persisted snapshots, skipping malformed rows."""
    path = Path(repo_root) / _SNAPSHOTS_REL
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            try:
                _validate_snapshot_shape(obj)
            except WorktreeSyncError:
                continue
            rows.append(obj)
    except OSError:
        return []
    return rows


def get_worktree_snapshot(repo_root: Path | str, snapshot_id: str) -> dict[str, Any] | None:
    """Return one persisted snapshot by id, or ``None`` when absent."""
    wanted = str(snapshot_id or "").strip()
    if not wanted:
        return None
    for snapshot in load_worktree_snapshots(repo_root):
        if str(snapshot.get("id", "")).strip() == wanted:
            return snapshot
    return None


def latest_worktree_snapshot(repo_root: Path | str) -> dict[str, Any] | None:
    """Return the latest persisted snapshot.

    Sorts by ``(taken_at, seq)`` so the most-recently-taken snapshot always
    wins even when two snapshots share the same wall-clock timestamp. ``seq``
    is the monotonic per-repo sequence assigned at write time; legacy rows
    without it sort as 0.
    """
    snapshots = load_worktree_snapshots(repo_root)
    if not snapshots:
        return None
    return sorted(snapshots, key=_snapshot_sort_key)[-1]


def _snapshot_sort_key(row: dict[str, Any]) -> tuple[str, int]:
    try:
        seq = int(row.get("seq", 0))
    except (TypeError, ValueError):
        seq = 0
    return (str(row.get("taken_at", "")), seq)


def list_worktree_snapshots(
    repo_root: Path | str,
    *,
    branch: str | None = None,
    commit_sha: str | None = None,
) -> list[dict[str, Any]]:
    """Return persisted snapshots, optionally filtered by branch or commit."""
    snapshots = load_worktree_snapshots(repo_root)
    if branch:
        snapshots = [row for row in snapshots if row.get("branch") == branch]
    if commit_sha:
        snapshots = [row for row in snapshots if row.get("commit_sha") == commit_sha]
    return snapshots


def validate_worktree_sync(
    repo_path: Path | str,
    *,
    require_clean: bool = False,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Take a snapshot and validate that worktree state is readable and sane."""
    root = discover_git_root(repo_path)
    blockers: list[dict[str, Any]] = []
    try:
        snapshot = take_worktree_snapshot(root, persist=True)
    except WorktreeSyncError as exc:
        snapshot = None
        blockers.append(
            {
                "kind": "worktree-snapshot-unreadable",
                "message": str(exc),
                "fix_command": "git status",
            }
        )
    if snapshot is not None and require_clean and snapshot["dirty_file_count"] > 0:
        blockers.append(
            {
                "kind": "dirty-worktree",
                "message": (
                    f"worktree has {snapshot['dirty_file_count']} dirty file(s); "
                    "clean or intentionally record the change before merge"
                ),
                "fix_command": "git status --short",
            }
        )

    payload: dict[str, Any] = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "repo_root": str(root),
        "ok": not blockers,
        "status": "PASS" if not blockers else "FAIL",
        "source": "git-read-only",
        "snapshot": snapshot,
        "blockers": blockers,
        "generated_at": _now_iso(),
    }
    if write_evidence:
        payload["evidence_path"] = _write_validation(root, payload)
    else:
        payload["evidence_path"] = None
    return payload


def _git(cwd: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeSyncError(f"unable to run git {' '.join(args)}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise WorktreeSyncError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout or ""


def _dirty_file_count(git_root: Path) -> int:
    out = _git(git_root, "status", "--porcelain=v1", "--untracked-files=all")
    return len(
        [
            line
            for line in out.splitlines()
            if line.strip() and not _is_signalos_bookkeeping_status(line)
        ]
    )


def _is_signalos_bookkeeping_status(line: str) -> bool:
    # SignalOS.NET persists snapshots in a database, outside git status. The
    # app uses .signalos JSONL files instead, so ignore that bookkeeping when
    # answering "is the product worktree dirty?"
    path = line[3:].strip().strip('"') if len(line) > 3 else line.strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip().strip('"')
    normalized = path.replace("\\", "/")
    return normalized == ".signalos" or normalized.startswith(".signalos/")


def _validate_snapshot_shape(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise WorktreeSyncError("unsupported worktree snapshot schema")
    commit_sha = str(snapshot.get("commit_sha", "")).strip()
    branch = str(snapshot.get("branch", "")).strip()
    if not commit_sha:
        raise WorktreeSyncError("commit_sha is required")
    if not branch:
        raise WorktreeSyncError("branch is required")
    try:
        dirty_count = int(snapshot.get("dirty_file_count"))
    except (TypeError, ValueError) as exc:
        raise WorktreeSyncError("dirty_file_count must be a non-negative integer") from exc
    if dirty_count < 0:
        raise WorktreeSyncError("dirty_file_count must be a non-negative integer")
    if not str(snapshot.get("taken_at", "")).strip():
        raise WorktreeSyncError("taken_at is required")


def _append_snapshot(repo_root: Path, snapshot: dict[str, Any]) -> None:
    path = repo_root / _SNAPSHOTS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")


def _write_validation(repo_root: Path, payload: dict[str, Any]) -> str:
    path = repo_root / _EVIDENCE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def _now_iso() -> str:
    # Microsecond precision narrows (but does not eliminate) same-second
    # collisions; the persisted ``seq`` is the deterministic tiebreaker.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
