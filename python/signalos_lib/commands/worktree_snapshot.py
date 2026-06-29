"""CLI wrapper for app-native worktree snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from signalos_lib.worktree_sync import (
    WorktreeSyncError,
    discover_git_root,
    get_worktree_snapshot,
    latest_worktree_snapshot,
    list_worktree_snapshots,
    take_worktree_snapshot,
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos worktree-snapshot",
        description="Take and query read-only git worktree snapshots.",
    )
    sub = parser.add_subparsers(dest="action")

    p_take = sub.add_parser("take", help="Take and persist a read-only snapshot")
    _add_repo_root(p_take)
    p_take.add_argument("--no-persist", action="store_true")
    _add_json(p_take)

    p_latest = sub.add_parser("latest", help="Show the latest persisted snapshot")
    _add_repo_root(p_latest)
    _add_json(p_latest)

    p_get = sub.add_parser("get", help="Show one persisted snapshot by id")
    p_get.add_argument("snapshot_id")
    _add_repo_root(p_get)
    _add_json(p_get)

    p_list = sub.add_parser("list", help="List persisted snapshots")
    _add_repo_root(p_list)
    p_list.add_argument("--branch", default=None)
    p_list.add_argument("--commit", default=None, dest="commit_sha")
    _add_json(p_list)

    args = parser.parse_args(argv)
    if args.action is None:
        parser.print_help()
        return 1

    try:
        payload = _run(args)
    except (OSError, WorktreeSyncError) as exc:
        print(f"signalos worktree-snapshot: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(args.action, payload)
    return 0 if payload.get("ok", True) else 1


def _run(args: argparse.Namespace) -> dict[str, Any]:
    root = _resolve_repo_root(args.repo_root)
    if args.action == "take":
        snapshot = take_worktree_snapshot(root, persist=not args.no_persist)
        return {"ok": True, "snapshot": snapshot}
    if args.action == "latest":
        snapshot = latest_worktree_snapshot(root)
        return {"ok": snapshot is not None, "snapshot": snapshot}
    if args.action == "get":
        snapshot = get_worktree_snapshot(root, args.snapshot_id)
        return {"ok": snapshot is not None, "snapshot": snapshot, "id": args.snapshot_id}
    if args.action == "list":
        snapshots = list_worktree_snapshots(
            root,
            branch=args.branch,
            commit_sha=args.commit_sha,
        )
        return {"ok": True, "snapshots": snapshots, "count": len(snapshots)}
    raise WorktreeSyncError(f"unknown action: {args.action}")


def _resolve_repo_root(raw: str | None) -> Path:
    start = Path(raw).resolve() if raw else Path.cwd().resolve()
    return discover_git_root(start)


def _add_repo_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=None)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def _print_human(action: str, payload: dict[str, Any]) -> None:
    if action in {"take", "latest", "get"}:
        snapshot = payload.get("snapshot")
        if not snapshot:
            print("signalos worktree-snapshot: none")
            return
        print(
            "signalos worktree-snapshot: "
            f"{snapshot.get('branch')} {snapshot.get('commit_sha')} "
            f"dirty={snapshot.get('dirty_file_count')}"
        )
        return
    print(f"signalos worktree-snapshot list: {payload.get('count', 0)}")
    for snapshot in payload.get("snapshots", []):
        print(
            f"- {snapshot.get('taken_at')} {snapshot.get('branch')} "
            f"{snapshot.get('commit_sha')} dirty={snapshot.get('dirty_file_count')}"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
