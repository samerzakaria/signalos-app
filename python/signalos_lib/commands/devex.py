# cli/signalos_lib/commands/devex.py — W13 CLI wrappers (AMD-CORE-034)
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

__all__ = ["cmd_signal_devex_plan", "cmd_signal_devex", "cmd_signal_retro_global"]


def _repo(args_root: Optional[str] = None) -> Path:
    return Path(args_root) if args_root else Path.cwd()


def cmd_signal_devex_plan(args: list[str]) -> int:
    """Plan DevEx work in EXPANSION / POLISH / TRIAGE modes."""
    if not args:
        return 1

    import argparse
    parser = argparse.ArgumentParser(prog="signalos signal-devex-plan")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["EXPANSION", "POLISH", "TRIAGE", "expansion", "polish", "triage"],
    )
    parser.add_argument("--wave", required=True)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", dest="as_json", action="store_true")

    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    from signalos_lib.devex import devex_plan
    root = _repo(ns.repo_root)
    plan = devex_plan(root, ns.mode, ns.wave)

    if ns.as_json:
        print(json.dumps(plan.as_dict(), indent=2))
    else:
        print(f"[{plan.id}] {plan.mode} ({len(plan.items)} items)")

    return 0


def cmd_signal_devex(args: list[str]) -> int:
    """Record a DevEx metric (e.g. TTHW)."""
    if not args:
        return 1

    import argparse
    parser = argparse.ArgumentParser(prog="signalos signal-devex")
    parser.add_argument("metric")
    parser.add_argument("value", type=float)
    parser.add_argument("--wave", required=True)
    parser.add_argument("--note", default="")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", dest="as_json", action="store_true")

    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    from signalos_lib.devex import devex_measure
    root = _repo(ns.repo_root)
    metric = devex_measure(root, ns.metric, ns.value, ns.wave, ns.note)

    if ns.as_json:
        print(json.dumps(metric.as_dict(), indent=2))
    else:
        print(f"{metric.id}")

    return 0


def cmd_signal_retro_global(args: list[str]) -> int:
    """Cross-product retrospective: query brain index for insights."""
    if not args:
        return 1

    import argparse
    parser = argparse.ArgumentParser(prog="signalos signal-retro-global")
    parser.add_argument("query")
    parser.add_argument("--wave", required=True)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", dest="as_json", action="store_true")

    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    from signalos_lib.devex import retro_global
    root = _repo(ns.repo_root)
    entries = retro_global(root, ns.query, ns.wave)

    if ns.as_json:
        print(json.dumps(entries, indent=2))
    else:
        if not entries:
            print("No entries found.")
        else:
            print(f"{len(entries)} entries found.")
            for e in entries:
                print(e.get("content", ""))

    return 0
