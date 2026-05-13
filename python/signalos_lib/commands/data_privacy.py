# SignalOS Core v2.5 — `signalos data` CLI (AMD-CORE-026, W6.2).
#
# Subcommands:
#   export  --subject <name> [--repo-root PATH] [--json]
#   purge   --subject <name> --reason <text> [--repo-root PATH]
#
# Exit codes:
#   0  success
#   1  argument / operation error

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.data_privacy import DataPrivacyError, export_subject, purge_subject

__all__ = ["cmd_export", "cmd_purge", "main"]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _build_export_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos data export",
        description="Export all audit/journal entries referencing a data subject (GDPR Art. 15).",
    )
    p.add_argument(
        "--subject", required=True, metavar="NAME",
        help="Data subject name (case-insensitive search).",
    )
    p.add_argument("--repo-root", default=None, metavar="PATH")
    p.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit a JSON array (default: newline-delimited JSON objects).",
    )
    return p


def _build_purge_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos data purge",
        description="Redact all entries referencing a data subject (GDPR Art. 17).",
    )
    p.add_argument(
        "--subject", required=True, metavar="NAME",
        help="Data subject name to erase.",
    )
    p.add_argument(
        "--reason", required=True, metavar="TEXT",
        help='Stated reason, e.g. "GDPR Article 17".',
    )
    p.add_argument("--repo-root", default=None, metavar="PATH")
    return p


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_export(argv: list[str]) -> int:
    parser = _build_export_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = Path(args.repo_root) if args.repo_root else None
    try:
        matches = export_subject(args.subject, root)
    except DataPrivacyError as exc:
        sys.stderr.write(f"signalos data export: {exc}\n")
        return 1

    if args.as_json:
        sys.stdout.write(json.dumps(matches, indent=2) + "\n")
    else:
        for entry in matches:
            sys.stdout.write(json.dumps(entry, separators=(",", ":")) + "\n")
    return 0


def cmd_purge(argv: list[str]) -> int:
    parser = _build_purge_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = Path(args.repo_root) if args.repo_root else None
    try:
        summary = purge_subject(args.subject, args.reason, root)
    except DataPrivacyError as exc:
        sys.stderr.write(f"signalos data purge: {exc}\n")
        return 1

    sys.stdout.write(
        f"Purge complete: {summary['entries_redacted']} entries redacted "
        f"across {summary['files_modified']} file(s).\n"
    )
    return 0


# ---------------------------------------------------------------------------
# main dispatcher
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(
            "signalos data: subcommand required (export | purge)\n"
            "Usage:\n"
            "  signalos data export --subject <name> [--json]\n"
            "  signalos data purge  --subject <name> --reason <text>\n"
        )
        return 1

    sub = argv[0]
    rest = argv[1:]

    if sub == "export":
        return cmd_export(rest)
    if sub == "purge":
        return cmd_purge(rest)

    sys.stderr.write(
        f"signalos data: unknown subcommand {sub!r}. Use 'export' or 'purge'.\n"
    )
    return 1
