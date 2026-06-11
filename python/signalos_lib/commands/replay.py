"""`signalos replay` — time-travel over the audit trail.

Read-only. Emits the scrubber-ready timeline (one frame per audit entry, each
with the reconstructed state after that entry) so the UI can travel back to any
past point. With --at N, prints just the reconstructed state at index N.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.audit_replay import build_timeline, load_audit_trail, replay_state


def _resolve_root(repo_root: str | None) -> Path:
    return Path(repo_root).resolve() if repo_root else Path.cwd()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="signalos replay")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--at", type=int, default=None,
                        help="Reconstruct state at this entry index (0-based).")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    root = _resolve_root(args.repo_root)

    if args.at is not None:
        entries = load_audit_trail(root)
        state = replay_state(entries, args.at)
        sys.stdout.write(json.dumps(state, ensure_ascii=False) + "\n")
        return 0

    timeline = build_timeline(root)
    if args.as_json:
        sys.stdout.write(json.dumps({"frames": timeline}, ensure_ascii=False) + "\n")
        return 0

    if not timeline:
        sys.stdout.write("No audit history yet.\n")
        return 0
    for frame in timeline:
        ts = frame.get("ts") or "?"
        sys.stdout.write(f"[{frame['index']:>4}] {ts}  {frame['summary']}\n")
    return 0
