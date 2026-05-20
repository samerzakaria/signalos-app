# cli/signalos_lib/commands/safety.py — W14 CLI wrappers (AMD-CORE-035)
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.safety import (
    careful_disable,
    careful_enable,
    careful_status,
    freeze_dir,
    guard_check,
    unfreeze_dir,
)

__all__ = [
    "cmd_signal_careful",
    "cmd_signal_freeze",
    "cmd_signal_guard",
    "cmd_signal_unfreeze",
]


def cmd_signal_careful(args: list[str]) -> int:
    """CLI wrapper for careful mode enable/disable/status."""
    if not args:
        print("usage: signal-careful <enable|disable|status> [options]")
        return 1

    parser = argparse.ArgumentParser(prog="signal-careful")
    parser.add_argument("--repo-root", default=None)
    subs = parser.add_subparsers(dest="sub")

    p_enable = subs.add_parser("enable")
    p_enable.add_argument("--note", default="")
    p_enable.add_argument("--repo-root", default=None)
    p_enable.add_argument("--json", action="store_true", dest="as_json")

    p_disable = subs.add_parser("disable")
    p_disable.add_argument("--repo-root", default=None)
    p_disable.add_argument("--json", action="store_true", dest="as_json")

    p_status = subs.add_parser("status")
    p_status.add_argument("--repo-root", default=None)
    p_status.add_argument("--json", action="store_true", dest="as_json")

    try:
        ns = parser.parse_args(args)
    except SystemExit:
        return 1

    if ns.sub is None:
        parser.print_help()
        return 1

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()

    if ns.sub == "enable":
        rec = careful_enable(root, getattr(ns, "note", ""))
        if getattr(ns, "as_json", False):
            print(json.dumps(rec.as_dict()))
        else:
            print(f"careful mode ON: {rec.ts}")
        return 0

    if ns.sub == "disable":
        rec = careful_disable(root)
        if getattr(ns, "as_json", False):
            print(json.dumps(rec.as_dict()))
        else:
            print("careful mode OFF")
        return 0

    # sub == "status"
    rec = careful_status(root)
    if getattr(ns, "as_json", False):
        print(json.dumps(rec.as_dict()))
    else:
        print(f"active={rec.active}")
    return 0


def cmd_signal_freeze(args: list[str]) -> int:
    """CLI wrapper for freeze_dir.

    Dual-write note (Milestone 2-b / AMD-CORE-107):
    This handler writes the durable freeze record under
    ``.signalos/safety/freeze/<hash>.json``. That record is the audit-trail
    source of truth. In parallel, the JS chat layer (see
    ``src/js/ui/chat.js``) calls ``ipc.enforcement.freeze()`` immediately
    after this CLI returns, which flips the Rust in-memory mutex
    (``EnforcementStore.wave_frozen``) read by the Toolbar's "Frozen"
    indicator. Both stores must converge — do NOT add freeze-flip logic
    to the Rust mutex here, and do NOT remove the JS-side hook in chat.js
    without also adding a direct Python→Rust bridge.
    """
    if not args:
        print("usage: signal-freeze <target> --wave W [options]")
        return 1

    parser = argparse.ArgumentParser(prog="signal-freeze")
    parser.add_argument("target")
    parser.add_argument("--wave", required=True)
    parser.add_argument("--note", default="")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")

    try:
        ns = parser.parse_args(args)
    except SystemExit:
        return 1

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()
    record = freeze_dir(root, ns.target, ns.wave, ns.note)

    if ns.as_json:
        print(json.dumps(record.as_dict()))
    else:
        print(f"frozen {record.id}: {ns.target}")
    return 0


def cmd_signal_guard(args: list[str]) -> int:
    """CLI wrapper for guard_check. Exit 1 if frozen (guard trips)."""
    if not args:
        print("usage: signal-guard <target> [options]")
        return 1

    parser = argparse.ArgumentParser(prog="signal-guard")
    parser.add_argument("target")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")

    try:
        ns = parser.parse_args(args)
    except SystemExit:
        return 1

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()
    result = guard_check(root, ns.target)

    if ns.as_json:
        print(json.dumps(result))
    else:
        print(f"frozen={result['frozen']}")

    return 1 if result["frozen"] else 0


def cmd_signal_unfreeze(args: list[str]) -> int:
    """CLI wrapper for unfreeze_dir.

    Dual-write note (Milestone 2-b / AMD-CORE-107):
    This handler updates the durable freeze record's ``status`` field to
    ``"unfrozen"`` (the audit-trail truth). The JS chat layer (see
    ``src/js/ui/chat.js``) then calls ``ipc.enforcement.unfreeze()``,
    which clears the Rust in-memory mutex so the Toolbar's indicator
    reflects the new state. See ``cmd_signal_freeze`` above for the
    full convergence contract.
    """
    if not args:
        print("usage: signal-unfreeze <target> [options]")
        return 1

    parser = argparse.ArgumentParser(prog="signal-unfreeze")
    parser.add_argument("target")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")

    try:
        ns = parser.parse_args(args)
    except SystemExit:
        return 1

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()
    found = unfreeze_dir(root, ns.target)

    if ns.as_json:
        print(json.dumps({"unfrozen": found, "target": ns.target}))
    else:
        if found:
            print(f"unfrozen {ns.target}")
        else:
            print(f"not frozen: {ns.target}", file=sys.stderr)

    return 0 if found else 1
