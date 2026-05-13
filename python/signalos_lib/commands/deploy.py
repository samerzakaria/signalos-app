# cli/signalos_lib/commands/deploy.py — W12 CLI wrappers (AMD-CORE-033)
from __future__ import annotations

__all__ = [
    "cmd_signal_setup_deploy",
    "cmd_signal_land_deploy",
    "cmd_signal_canary_deploy",
    "cmd_signal_benchmark",
]

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.deploy import (
    canary_deploy_check,
    land_deploy,
    record_benchmark,
    setup_deploy,
)


# ---------------------------------------------------------------------------
# cmd_signal_setup_deploy
# ---------------------------------------------------------------------------

def cmd_signal_setup_deploy(args: list[str]) -> int:
    """CLI wrapper for setup_deploy."""
    if not args:
        parser = _build_setup_parser()
        parser.print_help(sys.stderr)
        return 1

    parser = _build_setup_parser()
    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()
    record = setup_deploy(root, ns.wave, ns.stage, ns.note)

    if ns.json:
        sys.stdout.write(json.dumps(record.as_dict()) + "\n")
    else:
        sys.stdout.write(f"{record.id}\n")
    return 0


def _build_setup_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos signal-setup-deploy",
        description="Set up a deployment record for a wave and stage (W12, AMD-CORE-033).",
    )
    p.add_argument("wave", help="Wave identifier (e.g. '12').")
    p.add_argument("stage", help="Stage (e.g. 'staging', 'production').")
    p.add_argument("--note", default="", help="Optional deploy note.")
    p.add_argument("--repo-root", default=None, metavar="PATH",
                   help="Repository root (default: cwd).")
    p.add_argument("--json", dest="json", action="store_true", default=False,
                   help="Emit machine-readable JSON.")
    return p


# ---------------------------------------------------------------------------
# cmd_signal_land_deploy
# ---------------------------------------------------------------------------

def cmd_signal_land_deploy(args: list[str]) -> int:
    """CLI wrapper for land_deploy."""
    if not args:
        parser = _build_land_parser()
        parser.print_help(sys.stderr)
        return 1

    parser = _build_land_parser()
    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()
    record = land_deploy(root, ns.deploy_id)

    if record is None:
        sys.stderr.write(f"not found: {ns.deploy_id}\n")
        return 1

    if ns.json:
        sys.stdout.write(json.dumps(record.as_dict()) + "\n")
    else:
        sys.stdout.write(f"landed {ns.deploy_id}\n")
    return 0


def _build_land_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos signal-land-deploy",
        description="Mark a deployment as landed (W12, AMD-CORE-033).",
    )
    p.add_argument("deploy_id", help="Deploy record ID (e.g. 'deploy-001').")
    p.add_argument("--repo-root", default=None, metavar="PATH",
                   help="Repository root (default: cwd).")
    p.add_argument("--json", dest="json", action="store_true", default=False,
                   help="Emit machine-readable JSON.")
    return p


# ---------------------------------------------------------------------------
# cmd_signal_canary_deploy
# ---------------------------------------------------------------------------

def cmd_signal_canary_deploy(args: list[str]) -> int:
    """CLI wrapper for canary_deploy_check."""
    if not args:
        parser = _build_canary_parser()
        parser.print_help(sys.stderr)
        return 1

    parser = _build_canary_parser()
    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()
    result = canary_deploy_check(root, ns.wave)

    if ns.json:
        sys.stdout.write(json.dumps(result) + "\n")
    else:
        sys.stdout.write(f"wave={ns.wave} count={result['count']}\n")
    return 0


def _build_canary_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos signal-canary-deploy",
        description="Post-deploy canary check (W12, AMD-CORE-033).",
    )
    p.add_argument("--wave", required=True, help="Wave identifier.")
    p.add_argument("--repo-root", default=None, metavar="PATH",
                   help="Repository root (default: cwd).")
    p.add_argument("--json", dest="json", action="store_true", default=False,
                   help="Emit machine-readable JSON.")
    return p


# ---------------------------------------------------------------------------
# cmd_signal_benchmark
# ---------------------------------------------------------------------------

def cmd_signal_benchmark(args: list[str]) -> int:
    """CLI wrapper for record_benchmark."""
    if not args:
        parser = _build_benchmark_parser()
        parser.print_help(sys.stderr)
        return 1

    parser = _build_benchmark_parser()
    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()
    record = record_benchmark(
        root,
        ns.url,
        ns.wave,
        ns.lcp,
        ns.inp,
        ns.cls,
        ns.ttfb,
        ns.weight,
    )

    if ns.json:
        sys.stdout.write(json.dumps(record.as_dict()) + "\n")
    else:
        sys.stdout.write(f"{record.id}\n")
    return 0


def _build_benchmark_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos signal-benchmark",
        description="Record Core Web Vitals benchmark (W12, AMD-CORE-033).",
    )
    p.add_argument("url", help="URL to benchmark.")
    p.add_argument("--wave", required=True, help="Wave identifier.")
    p.add_argument("--lcp", type=float, default=0.0,
                   help="Largest Contentful Paint (ms).")
    p.add_argument("--inp", type=float, default=0.0,
                   help="Interaction to Next Paint (ms).")
    p.add_argument("--cls", type=float, default=0.0,
                   help="Cumulative Layout Shift score.")
    p.add_argument("--ttfb", type=float, default=0.0,
                   help="Time to First Byte (ms).")
    p.add_argument("--weight", type=float, default=0.0,
                   help="Page weight (KB).")
    p.add_argument("--repo-root", default=None, metavar="PATH",
                   help="Repository root (default: cwd).")
    p.add_argument("--json", dest="json", action="store_true", default=False,
                   help="Emit machine-readable JSON.")
    return p
