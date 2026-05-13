# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.3 — `signalos context ...` CLI (AMD-CORE-005).
#
# Argparse wrapper around signalos_lib.context. Zero business logic.
#
# Subcommands:
#   compress <input.jsonl> [--out <file>]
#   expand   --scope <id>
#
# Exit codes:
#   0 — success
#   1 — usage error (missing arg, unknown subcommand, scope not found)
#   2 — disk-truth rejection (refused to compress a journal/metrics stream)
#   3 — never-compress allowlist violation

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import context as context_lib


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos context",
        description=(
            "Rule-based context compressor (AMD-CORE-005). Compresses a "
            "session transcript JSONL using four layers: verbatim (last 2 "
            "turns), summary (turns 3-10), headline (turns 11+), and "
            "discard (secrets / large tool-output blobs). Never touches "
            "disk-truth files."
        ),
    )
    sub = parser.add_subparsers(dest="sub", metavar="SUBCOMMAND")

    p_compress = sub.add_parser(
        "compress",
        help="Compress a transcript JSONL. Writes a summary dict to stdout.",
    )
    p_compress.add_argument(
        "input", type=Path,
        help="Path to a session transcript JSONL file.",
    )
    p_compress.add_argument(
        "--out", type=Path, default=None,
        help="Optional output path for the compressed JSONL.",
    )

    p_expand = sub.add_parser(
        "expand",
        help="Return the byte-identical on-disk content for a scope id.",
    )
    p_expand.add_argument(
        "--scope", required=True,
        help="Wave id (e.g. W1.3), belief id, or amendment id (AMD-CORE-*).",
    )
    p_expand.add_argument(
        "--root", type=Path, default=None,
        help=argparse.SUPPRESS,
    )

    return parser


def _cmd_compress(args: argparse.Namespace) -> int:
    try:
        if args.out is not None:
            result = context_lib.compress_transcript_to(args.input, args.out)
        else:
            result = context_lib.compress_transcript(args.input)
    except context_lib.DiskTruthRefused as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    except context_lib.NeverCompressViolation as exc:
        sys.stderr.write(f"{exc}\n")
        return 3
    except (ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"signalos context compress: {exc}\n")
        return 1
    except RuntimeError as exc:
        # Disambiguate by message since the base-class catch-all below
        # can match. DiskTruthRefused is already handled above — any
        # remaining RuntimeError is a user-shaped error.
        sys.stderr.write(f"signalos context compress: {exc}\n")
        return 1

    sys.stdout.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return 0


def _cmd_expand(args: argparse.Namespace) -> int:
    try:
        out = context_lib.expand_scope(args.scope, root=args.root)
    except ValueError as exc:
        sys.stderr.write(f"signalos context expand: {exc}\n")
        return 1
    except RuntimeError as exc:
        sys.stderr.write(f"signalos context expand: {exc}\n")
        return 1

    # Byte-identical write to stdout — do not reformat, do not add a
    # trailing newline the on-disk file doesn't already have.
    sys.stdout.write(out)
    return 0


def main(argv: list[str]) -> int:
    parser = _build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        sys.stderr.write("\nsignalos context: subcommand required (compress|expand)\n")
        return 1

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    if args.sub == "compress":
        return _cmd_compress(args)
    if args.sub == "expand":
        return _cmd_expand(args)

    parser.print_help(sys.stderr)
    sys.stderr.write(f"\nsignalos context: unknown subcommand: {args.sub}\n")
    return 1
