"""`signalos detect-bypass` command."""

from __future__ import annotations

__all__ = ["main"]

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.validators.governance_runtime import detect_governance_bypass


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos detect-bypass",
        description="Detect governance-bypass signatures in git diffs and SignalOS agent output.",
    )
    parser.add_argument("--repo-root", default=None, metavar="PATH")
    parser.add_argument("--staged", action="store_true", default=False)
    parser.add_argument("--diff", default=None, metavar="RANGE")
    parser.add_argument("--message-file", default=None, metavar="PATH")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--no-evidence", action="store_true")
    args = parser.parse_args(argv)

    if args.staged and args.diff:
        sys.stderr.write("detect-bypass: --staged and --diff are mutually exclusive.\n")
        return 2

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    staged = args.staged or not args.diff
    passed, message, details = detect_governance_bypass(
        repo_root,
        staged=staged,
        diff_range=args.diff,
        message_file=Path(args.message_file) if args.message_file else None,
        write_evidence=not args.no_evidence,
    )

    if args.as_json:
        sys.stdout.write(json.dumps(details, ensure_ascii=False) + "\n")
    elif passed:
        sys.stdout.write(f"PASS - {message}\n")
    else:
        sys.stderr.write(f"FAIL - {message}\n")
        for violation in details.get("violations", []):
            sys.stderr.write(f"  - {violation}\n")

    return 0 if passed else 1
