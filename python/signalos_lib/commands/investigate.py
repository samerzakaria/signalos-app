# cli/signalos_lib/commands/investigate.py — W15 CLI wrappers (AMD-CORE-036)
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

__all__ = [
    "cmd_signal_investigate",
]


def _repo(args_root: Optional[str] = None) -> Path:
    return Path(args_root) if args_root else Path.cwd()


def cmd_signal_investigate(args: list[str]) -> int:
    """Open a new investigation. Enforces the 5 iron-law debugging protocol."""
    if not args:
        return 1

    import argparse
    parser = argparse.ArgumentParser(prog="signalos signal-investigate")
    parser.add_argument(
        "action",
        choices=["open", "confirm-reproduction", "confirm-regression", "close", "list"],
    )
    parser.add_argument("inv_id", nargs="?", default=None,
                        help="Investigation ID (required for confirm-* and close)")
    parser.add_argument("--title", default="", help="Investigation title (required for open)")
    parser.add_argument("--wave", required=True)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", dest="as_json", action="store_true")

    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = _repo(ns.repo_root)

    if ns.action == "open":
        if not ns.title:
            sys.stderr.write("error: --title is required for 'open'\n")
            return 1
        from signalos_lib.investigate import open_investigation
        record = open_investigation(root, ns.title, ns.wave)
        if ns.as_json:
            print(json.dumps(record.as_dict(), indent=2))
        else:
            print(f"[{record.id}] opened: {record.title}")
        return 0

    if ns.action in ("confirm-reproduction", "confirm-regression", "close"):
        if not ns.inv_id:
            sys.stderr.write(f"error: inv_id is required for '{ns.action}'\n")
            return 1

        if ns.action == "confirm-reproduction":
            from signalos_lib.investigate import confirm_reproduction
            updated = confirm_reproduction(root, ns.inv_id)
        elif ns.action == "confirm-regression":
            from signalos_lib.investigate import confirm_regression
            updated = confirm_regression(root, ns.inv_id)
        else:  # close
            from signalos_lib.investigate import close_investigation
            updated = close_investigation(root, ns.inv_id)

        if updated is None:
            sys.stderr.write(f"error: investigation {ns.inv_id!r} not found\n")
            return 1
        if ns.as_json:
            print(json.dumps(updated.as_dict(), indent=2))
        else:
            print(f"[{updated.id}] status={updated.status} "
                  f"reproduction={updated.reproduction_confirmed} "
                  f"regression={updated.regression_written}")
        return 0

    if ns.action == "list":
        from signalos_lib.investigate import investigation_list
        records = investigation_list(root, wave=ns.wave if ns.wave != "all" else None)
        if ns.as_json:
            print(json.dumps([r.as_dict() for r in records], indent=2))
        else:
            if not records:
                print("No investigations found.")
            else:
                for r in records:
                    repro = "✓" if r.reproduction_confirmed else "✗"
                    regr = "✓" if r.regression_written else "✗"
                    print(f"[{r.id}] {r.title} ({r.status}) repro={repro} regression={regr}")
        return 0

    return 1
