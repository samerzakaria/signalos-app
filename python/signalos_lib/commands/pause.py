# SignalOS Core v1.1 — `signalos pause ...` CLI.
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
#
# Subcommands:
#   pause list                         — print JSON lines, one per pending pause
#   pause resume <step-id> --rationale <text> [--session-id <sid>]
#   pause abort  <step-id> --rationale <text> [--session-id <sid>]
#
# Exit codes:
#   0 — success
#   1 — user error (bad args, unknown subcommand, empty rationale)
#   2 — step not paused (no pending pause found for the given id)
#   3 — policy refusal (e.g. resuming a T3-aborted step)

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import pause as pause_lib


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos pause",
        description="List, resume, or abort paused steps. Pause is opt-in per PLAN step.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_list = sub.add_parser("list", help="List currently paused steps (JSON-per-line).")
    p_list.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)

    for name, helptext in (
        ("resume", "Unblock a paused step; writes .resume marker and step.resumed event."),
        ("abort", "Terminate a paused step; writes .abort marker and step.aborted event."),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("step_id", help="Step identifier (matches the PLAN step-spec).")
        sp.add_argument("--rationale", required=True, help="Non-empty rationale (audit record).")
        sp.add_argument("--session-id", default=None, help="Session id (default: scan all).")
        sp.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)

    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on parse error; normalise to 1 per CLI contract.
        return 1 if exc.code not in (0,) else 0

    if args.subcommand == "list":
        rows = pause_lib.list_paused(root=args.root)
        for rec in rows:
            sys.stdout.write(json.dumps(rec, sort_keys=True) + "\n")
        return 0

    # resume / abort share the same arg shape.
    rationale = (args.rationale or "").strip()
    if not rationale:
        sys.stderr.write("signalos pause: --rationale must not be empty\n")
        return 1

    try:
        if args.subcommand == "resume":
            result = pause_lib.resume(
                step_id=args.step_id,
                rationale=rationale,
                session_id=args.session_id,
                root=args.root,
            )
        else:
            result = pause_lib.abort(
                step_id=args.step_id,
                rationale=rationale,
                session_id=args.session_id,
                root=args.root,
            )
    except ValueError as exc:
        sys.stderr.write(f"signalos pause {args.subcommand}: {exc}\n")
        return 1
    except PermissionError as exc:
        sys.stderr.write(f"signalos pause {args.subcommand}: {exc}\n")
        return 3
    except FileNotFoundError as exc:
        sys.stderr.write(f"signalos pause {args.subcommand}: {exc}\n")
        return 2

    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0
