# SignalOS Core v2.3 — `signalos search` + `signalos info` CLI (AMD-CORE-021, W4.3).
#
# Subcommands (dispatched as top-level, not under a `catalog` prefix):
#   search <keyword> [--catalog <url>] [--json]
#   info   <name>   [--catalog <url>] [--json]
#
# Exit codes:
#   0  found / success
#   1  bad args
#   2  fetch / parse error
#   3  not found (info only)

from __future__ import annotations

import argparse
import json
import sys


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dump(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, default=str) + "\n")


def _print_search_table(plugins: list[dict]) -> None:
    """Print a compact search results table."""
    if not plugins:
        sys.stdout.write("No plugins found.\n")
        return
    FMT = "{:<35} {:<10} {:<20} {}"
    sys.stdout.write(
        FMT.format("NAME", "VERSION", "PUBLISHER", "DESCRIPTION") + "\n"
    )
    sys.stdout.write("-" * 90 + "\n")
    for p in plugins:
        desc = str(p.get("description", ""))
        if len(desc) > 40:
            desc = desc[:37] + "..."
        sys.stdout.write(
            FMT.format(
                str(p.get("name", ""))[:34],
                str(p.get("version", ""))[:9],
                str(p.get("publisher", ""))[:19],
                desc,
            )
            + "\n"
        )


def _print_plugin_info(p: dict) -> None:
    """Print full provenance display for one plugin."""
    sys.stdout.write(f"Name:           {p.get('name', '')}\n")
    sys.stdout.write(f"Version:        {p.get('version', '')}\n")
    sys.stdout.write(f"Description:    {p.get('description', '')}\n")
    sys.stdout.write(f"Publisher:      {p.get('publisher', '')}\n")
    sys.stdout.write(f"Provenance:     {p.get('provenance_hash', '')}\n")
    sys.stdout.write(f"Downloads:      {p.get('download_count', 0)}\n")
    sys.stdout.write(f"Last updated:   {p.get('last_updated', '')}\n")
    tags = p.get("tags", [])
    if tags:
        sys.stdout.write(f"Tags:           {', '.join(tags)}\n")
    install_cmd = p.get("install_command", "")
    if install_cmd:
        sys.stdout.write(f"Install:        {install_cmd}\n")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _build_search_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos search",
        description="Search the plugin catalog by keyword. AMD-CORE-021.",
    )
    p.add_argument("keyword", help="Search keyword (name, description, tags, publisher).")
    p.add_argument(
        "--catalog",
        default=None,
        metavar="URL",
        help="Catalog URL or file path (default: SIGNALOS_CATALOG_URL env or remote default).",
    )
    p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON.",
    )
    return p


def cmd_search(argv: list[str]) -> int:
    parser = _build_search_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    from signalos_lib import catalog as cat_lib

    try:
        catalog = cat_lib.fetch_catalog(url=args.catalog)
    except cat_lib.CatalogFetchError as exc:
        sys.stderr.write(f"signalos search: {exc}\n")
        return 2

    results = cat_lib.search_catalog(args.keyword, catalog)

    if args.as_json:
        _dump(results)
        return 0

    _print_search_table(results)
    return 0


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------

def _build_info_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos info",
        description="Show full provenance for a plugin. AMD-CORE-021.",
    )
    p.add_argument("name", help="Exact plugin name (e.g. @signalos/my-plugin).")
    p.add_argument(
        "--catalog",
        default=None,
        metavar="URL",
        help="Catalog URL or file path.",
    )
    p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON.",
    )
    return p


def cmd_info(argv: list[str]) -> int:
    parser = _build_info_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    from signalos_lib import catalog as cat_lib

    try:
        catalog = cat_lib.fetch_catalog(url=args.catalog)
    except cat_lib.CatalogFetchError as exc:
        sys.stderr.write(f"signalos info: {exc}\n")
        return 2

    plugin = cat_lib.plugin_info(args.name, catalog)
    if plugin is None:
        sys.stderr.write(f"signalos info: plugin not found: {args.name}\n")
        return 3

    if args.as_json:
        _dump(plugin)
        return 0

    _print_plugin_info(plugin)
    return 0


# ---------------------------------------------------------------------------
# main dispatcher
# ---------------------------------------------------------------------------

_DISPATCH = {
    "search": cmd_search,
    "info": cmd_info,
}


def main(argv: list[str]) -> int:
    """Dispatch catalog subcommands: search | info."""
    if not argv:
        sys.stderr.write(
            "signalos catalog: subcommand required (search|info)\n"
        )
        return 1
    sub, rest = argv[0], argv[1:]
    handler = _DISPATCH.get(sub)
    if handler is None:
        sys.stderr.write(f"signalos catalog: unknown subcommand: {sub!r}\n")
        return 1
    return handler(rest)
