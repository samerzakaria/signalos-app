# SignalOS Core v2.3 — `signalos tenant ...` CLI (AMD-CORE-020).
#
# Subcommands:
#   list                          List all product namespaces.
#   init <id>                     Create a new product namespace.
#   status [<id>] [--json]        Show status (one product, or all).
#
# Exit codes:
#   0  ok
#   1  bad args / invalid product ID / namespace error

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos tenant",
        description=(
            "Manage SignalOS product namespaces (multi-tenant support). "
            "AMD-CORE-020."
        ),
    )
    sub = parser.add_subparsers(dest="sub", metavar="SUBCOMMAND")

    # list
    sub.add_parser("list", help="List all product namespaces in this repo.")

    # init
    p_init = sub.add_parser("init", help="Create a new product namespace.")
    p_init.add_argument("product_id", help="Product ID slug: [a-z0-9][a-z0-9-]*")
    p_init.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        metavar="PATH",
        help="Override repo root (default: walk up from cwd).",
    )

    # status
    p_status = sub.add_parser(
        "status",
        help="Show product namespace status (all products if <id> omitted).",
    )
    p_status.add_argument(
        "product_id",
        nargs="?",
        default=None,
        help="Product ID. Omit to list all products.",
    )
    p_status.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        metavar="PATH",
        help="Override repo root.",
    )
    p_status.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON.",
    )

    return parser


def _resolve_root(args_repo_root: Path | None) -> Path:
    from signalos_lib.status import _repo_root
    if args_repo_root is not None:
        return args_repo_root.resolve()
    return _repo_root()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_list(args: argparse.Namespace) -> int:
    from signalos_lib import tenant as tlib
    root = _resolve_root(getattr(args, "repo_root", None))
    products = tlib.list_products(root)
    if not products:
        sys.stdout.write("no product namespaces found\n")
        return 0
    for pid in products:
        sys.stdout.write(f"{pid}\n")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    from signalos_lib import tenant as tlib
    root = _resolve_root(args.repo_root)
    try:
        proot = tlib.init_product(root, args.product_id)
    except tlib.ProductInitError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    sys.stdout.write(f"initialized: {proot}\n")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from signalos_lib import tenant as tlib
    root = _resolve_root(args.repo_root)
    product_id: str | None = args.product_id

    if product_id:
        records = [tlib.product_status(root, product_id)]
    else:
        records = tlib.multi_product_summary(root)
        if not records:
            sys.stdout.write("no product namespaces found\n")
            return 0

    if args.as_json:
        # Single product (explicit product_id) → dict. All products → list.
        payload = records[0] if product_id else records
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    _print_table(records)
    return 0


def _print_table(records: list[dict]) -> None:
    """Print a compact ASCII status table."""
    FMT = "{:<22} {:<13} {:<10} {:<10} {:<8}"
    sys.stdout.write(
        FMT.format("PRODUCT", "CONSTITUTION", "SOUL-DOC", "SESSIONS", "TASKS") + "\n"
    )
    sys.stdout.write("-" * 70 + "\n")
    for r in records:
        sys.stdout.write(
            FMT.format(
                r["product_id"],
                "✓" if r["constitution"] else "✗",
                "✓" if r["soul_document"] else "✗",
                str(r["session_count"]),
                str(r["active_tasks"]),
            )
            + "\n"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = _build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        sys.stderr.write(
            "\nsignalos tenant: subcommand required (list|init|status)\n"
        )
        return 1

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    sub = args.sub
    if sub == "list":
        return _cmd_list(args)
    if sub == "init":
        return _cmd_init(args)
    if sub == "status":
        return _cmd_status(args)

    parser.print_help(sys.stderr)
    return 1
