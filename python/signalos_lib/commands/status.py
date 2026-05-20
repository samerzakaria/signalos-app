# SignalOS Core v2.2 — `signalos status` CLI (AMD-CORE-008 + AMD-CORE-010).
#
# Argparse wrapper around signalos_lib.status.get_wave_status() /
# print_status_card(). Renders the Wave status ASCII card or emits JSON.
#
# Usage:
#   signalos status [--repo-root <path>] [--json]
#
# Exit codes (human/card mode):
#   0 -- always (status display is advisory, never blocking)
#
# Exit codes (--json mode, AMD-CORE-010 W13):
#   0 -- Wave open (Gate 5 not yet signed)
#   1 -- Gate 5 signed (wave ready to close / merge)
#   2 -- internal error

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos status",
        description=(
            "Render the Wave status card: gates, active tasks, "
            "belief statement, scale track, and next blocking action. "
            "AMD-CORE-008."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Override repo root (default: walk up from cwd to find .signalos/).",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        default=False,
        help="Refresh the status card on every journal change (W3.2, AMD-CORE-015).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        metavar="SECS",
        help="Polling interval in seconds when inotifywait is unavailable (default 2).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="as_json",
        help=(
            "Emit machine-readable JSON on stdout instead of the ASCII card. "
            "Exit 0 = wave open, 1 = Gate 5 signed, 2 = internal error. "
            "AMD-CORE-010 (W13 fix)."
        ),
    )
    parser.add_argument(
        "--product",
        default=None,
        metavar="ID",
        help=(
            "Scope status to a specific product namespace "
            "(.signalos/products/<id>/). AMD-CORE-020."
        ),
    )
    parser.add_argument(
        "--project-id",
        default="default",
        metavar="ID",
        dest="project_id",
        help=(
            "Multi-project namespace (WAVE-ENGINE-DESIGN §3.2). "
            "Default 'default' preserves today's workspace-root layout. "
            "Future UI exposes a project picker that drives this."
        ),
    )
    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0  # --help exits 0

    from .. import status as status_lib

    repo_root = args.repo_root
    if repo_root is not None:
        repo_root = repo_root.resolve()

    from signalos_lib.tenant import resolve_product_id
    product_id = resolve_product_id(getattr(args, "product", None))

    if args.watch:
        # Watch mode (AMD-CORE-015)
        try:
            status_lib.watch_status(
                repo_root=repo_root,
                interval=args.interval,
                clear=True,
            )
        except Exception as exc:
            sys.stderr.write(f"signalos status --watch: {exc}\n")
        return 0

    if args.as_json:
        # JSON mode (AMD-CORE-010 + M3 gate emissions).
        # Use build_status_json so the payload includes per-gate
        # `activities` and `criteria` arrays the DashboardView reads.
        try:
            root = repo_root if repo_root is not None else status_lib._repo_root()
            data = status_lib.build_status_json(
                root, product_id=product_id, project_id=args.project_id,
            )
            sys.stdout.write(json.dumps(data, default=str) + "\n")
            # Exit 1 if Gate 5 signed, else 0
            gates = data.get("gates", {})
            if gates.get("G5"):
                return 1
            return 0
        except Exception as exc:
            sys.stderr.write(f"signalos status --json: {exc}\n")
            return 2
    else:
        # Human/card mode
        try:
            status_lib.print_status_card(
                repo_root, product_id=product_id, project_id=args.project_id,
            )
        except Exception as exc:
            sys.stderr.write(f"signalos status: {exc}\n")
            # Exit 0 -- status is advisory
        return 0
