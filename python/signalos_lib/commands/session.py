# SignalOS Core v1.1 — `signalos session ...` CLI.
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
#
# Argparse wrapper around signalos_lib.session. This module holds zero
# business logic — all reading, summarising, and archiving lives in the
# parent module.
#
# Subcommands:
#   list                              List sessions in the current repo.
#   show <session-id>                 Summarise one session from its journal.
#   resume <session-id>               Describe what resuming would mean.
#   archive <session-id> [--force]    Move a session to _archive/.
#
# Exit codes:
#   0 ok
#   1 bad args / unknown subcommand
#   2 session missing
#   3 archive-without-end refused

from __future__ import annotations

import argparse
import json
import sys

from signalos_lib import session as session_lib


def _cmd_list(args: argparse.Namespace) -> int:
    from signalos_lib.tenant import resolve_product_id, product_sessions_dir
    product_id = resolve_product_id(getattr(args, "product", None))
    if product_id:
        # Scope session listing to the product namespace
        from signalos_lib.status import _repo_root
        root = _repo_root()
        scoped_dir = product_sessions_dir(root, product_id)
        rows = session_lib.list_sessions_in(scoped_dir)
    else:
        rows = session_lib.list_sessions()
    sys.stdout.write(json.dumps(rows, indent=2) + "\n")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        summary = session_lib.show_session(args.session_id)
    except session_lib.SessionMissingError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    try:
        info = session_lib.resume_session(args.session_id)
    except session_lib.SessionMissingError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    sys.stdout.write(json.dumps(info, indent=2) + "\n")
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    try:
        dst = session_lib.archive_session(args.session_id, force=args.force)
    except session_lib.SessionMissingError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    except session_lib.ArchiveRefusedError as exc:
        sys.stderr.write(f"{exc}\n")
        return 3
    sys.stdout.write(f"archived: {dst}\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos session",
        description="Inspect and manage SignalOS session journals.",
    )
    sub = parser.add_subparsers(dest="sub", metavar="SUBCOMMAND")

    p_list = sub.add_parser("list", help="List known sessions, newest-first.")
    p_list.add_argument(
        "--product",
        default=None,
        metavar="ID",
        help="Scope listing to a product namespace. AMD-CORE-020.",
    )

    p_show = sub.add_parser("show", help="Summarise one session.")
    p_show.add_argument("session_id", help="Session identifier.")

    p_resume = sub.add_parser("resume", help="Describe what resuming a session would mean.")
    p_resume.add_argument("session_id", help="Session identifier.")

    p_arch = sub.add_parser("archive", help="Move a session to the _archive/ folder.")
    p_arch.add_argument("session_id", help="Session identifier.")
    p_arch.add_argument(
        "--force",
        action="store_true",
        help="Archive even if no session.end event is present.",
    )

    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        sys.stderr.write("\nsignalos session: subcommand required (list|show|resume|archive)\n")
        return 1

    # argparse exits with 2 on its own "bad arg" path; remap to 1 to match
    # our documented exit-code contract.
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    sub = args.sub
    if sub == "list":
        return _cmd_list(args)
    if sub == "show":
        return _cmd_show(args)
    if sub == "resume":
        return _cmd_resume(args)
    if sub == "archive":
        return _cmd_archive(args)

    parser.print_help(sys.stderr)
    sys.stderr.write(f"\nsignalos session: unknown subcommand: {sub}\n")
    return 1
