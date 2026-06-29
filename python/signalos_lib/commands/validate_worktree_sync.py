"""CLI wrapper for app-native WorktreeSync validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.worktree_sync import WorktreeSyncError, validate_worktree_sync


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos validate-worktree-sync",
        description="Validate read-only git worktree snapshot state.",
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--no-evidence", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    try:
        payload = validate_worktree_sync(
            root,
            require_clean=args.require_clean,
            write_evidence=not args.no_evidence,
        )
    except (OSError, WorktreeSyncError) as exc:
        print(f"validate-worktree-sync: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"validate-worktree-sync: {payload['status']}")
        snapshot = payload.get("snapshot") or {}
        if snapshot:
            print(
                f"branch={snapshot.get('branch')} commit={snapshot.get('commit_sha')} "
                f"dirty={snapshot.get('dirty_file_count')}"
            )
        for blocker in payload.get("blockers", []):
            print(f"- {blocker.get('kind')}: {blocker.get('message')}")
        if payload.get("evidence_path"):
            print(f"evidence: {payload['evidence_path']}")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
