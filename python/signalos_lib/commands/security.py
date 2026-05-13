# SignalOS Core — `signalos signal-cso` CLI (AMD-CORE-031, W10).
#
# Subcommands:
#   scan <surface> --wave <W> [--repo-root PATH] [--json]
#   canary plant   [--label L] [--repo-root PATH] [--json]
#   canary check   [--label L] [--repo-root PATH] [--json]
#   threats list   [--wave W] [--category C] [--repo-root PATH] [--json]
#   threats export --out PATH  [--repo-root PATH] [--json]
#   inject-scan <path> [--repo-root PATH] [--json]
#
# Exit codes:
#   0  success
#   1  argument / operation error (canary check not found also exits 1)

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.security import (
    check_canary_token,
    generate_owasp_stride,
    plant_canary_token,
    scan_injection_risks,
    threat_export,
    threat_list,
)

__all__ = ["cmd_signal_cso"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo_root(raw: str | None) -> Path:
    return Path(raw) if raw else Path.cwd()


def _print_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, indent=2) + "\n")


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def _cmd_scan(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos signal-cso scan",
        description="Generate OWASP+STRIDE threat model for a surface (AMD-CORE-031).",
    )
    p.add_argument("surface", help="Attack surface name (e.g. api, web, cli).")
    p.add_argument("--wave", required=True, metavar="W", help="Wave identifier (e.g. 10).")
    p.add_argument("--repo-root", default=None, metavar="PATH")
    p.add_argument("--json", dest="as_json", action="store_true", default=False)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = _repo_root(args.repo_root)
    entries = generate_owasp_stride(args.surface, args.wave, root)

    if args.as_json:
        _print_json([e.as_dict() for e in entries])
        return 0

    sys.stdout.write(
        f"Generated {len(entries)} threat entries for surface '{args.surface}' (wave {args.wave}):\n"
    )
    for e in entries:
        sys.stdout.write(f"  [{e.id}] [{e.severity.upper()}] {e.category}/{e.title}\n")
    return 0


# ---------------------------------------------------------------------------
# canary plant
# ---------------------------------------------------------------------------

def _cmd_canary_plant(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos signal-cso canary plant",
        description="Plant a UUID canary token (AMD-CORE-031).",
    )
    p.add_argument("--label", default="default", metavar="L")
    p.add_argument("--repo-root", default=None, metavar="PATH")
    p.add_argument("--json", dest="as_json", action="store_true", default=False)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = _repo_root(args.repo_root)
    record = plant_canary_token(root, args.label)

    if args.as_json:
        _print_json(record)
        return 0

    sys.stdout.write(
        f"Canary planted: label={record['label']}  token={record['token']}\n"
    )
    return 0


# ---------------------------------------------------------------------------
# canary check
# ---------------------------------------------------------------------------

def _cmd_canary_check(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos signal-cso canary check",
        description="Check if a canary token exists (AMD-CORE-031).",
    )
    p.add_argument("--label", default="default", metavar="L")
    p.add_argument("--repo-root", default=None, metavar="PATH")
    p.add_argument("--json", dest="as_json", action="store_true", default=False)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = _repo_root(args.repo_root)
    result = check_canary_token(root, args.label)

    if args.as_json:
        _print_json(result)
        return 0 if result["found"] else 1

    if result["found"]:
        sys.stdout.write(
            f"Canary OK: label={result['label']}  token={result['token']}\n"
        )
        return 0
    else:
        sys.stderr.write(
            f"Canary MISSING: label={args.label} — canary file not found\n"
        )
        return 1


# ---------------------------------------------------------------------------
# canary dispatcher
# ---------------------------------------------------------------------------

_CANARY_DISPATCH = {
    "plant": _cmd_canary_plant,
    "check": _cmd_canary_check,
}


def _cmd_canary(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(
            "signalos signal-cso canary: sub-action required (plant|check)\n"
        )
        return 1
    sub, rest = argv[0], argv[1:]
    handler = _CANARY_DISPATCH.get(sub)
    if handler is None:
        sys.stderr.write(
            f"signalos signal-cso canary: unknown sub-action {sub!r} (plant|check)\n"
        )
        return 1
    return handler(rest)


# ---------------------------------------------------------------------------
# threats list
# ---------------------------------------------------------------------------

def _cmd_threats_list(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos signal-cso threats list",
        description="List threat index entries (AMD-CORE-031).",
    )
    p.add_argument("--wave", default=None, metavar="W")
    p.add_argument("--category", default=None, metavar="C")
    p.add_argument("--repo-root", default=None, metavar="PATH")
    p.add_argument("--json", dest="as_json", action="store_true", default=False)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = _repo_root(args.repo_root)
    entries = threat_list(root, wave=args.wave, category=args.category)

    if args.as_json:
        _print_json([e.as_dict() for e in entries])
        return 0

    if not entries:
        sys.stdout.write("No threat entries found.\n")
        return 0
    for e in entries:
        sys.stdout.write(
            f"  [{e.id}] [{e.severity.upper()}] {e.category}/{e.title}  wave={e.wave}\n"
        )
    return 0


# ---------------------------------------------------------------------------
# threats export
# ---------------------------------------------------------------------------

def _cmd_threats_export(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos signal-cso threats export",
        description="Export threat index to JSONL (AMD-CORE-031).",
    )
    p.add_argument("--out", required=True, metavar="PATH", help="Output JSONL file path.")
    p.add_argument("--repo-root", default=None, metavar="PATH")
    p.add_argument("--json", dest="as_json", action="store_true", default=False)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = _repo_root(args.repo_root)
    out_path = Path(args.out)
    count = threat_export(root, out_path)

    if args.as_json:
        _print_json({"exported": count, "out": str(out_path)})
        return 0

    sys.stdout.write(f"Exported {count} threat entries to {out_path}\n")
    return 0


# ---------------------------------------------------------------------------
# threats dispatcher
# ---------------------------------------------------------------------------

_THREATS_DISPATCH = {
    "list": _cmd_threats_list,
    "export": _cmd_threats_export,
}


def _cmd_threats(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(
            "signalos signal-cso threats: sub-action required (list|export)\n"
        )
        return 1
    sub, rest = argv[0], argv[1:]
    handler = _THREATS_DISPATCH.get(sub)
    if handler is None:
        sys.stderr.write(
            f"signalos signal-cso threats: unknown sub-action {sub!r} (list|export)\n"
        )
        return 1
    return handler(rest)


# ---------------------------------------------------------------------------
# inject-scan
# ---------------------------------------------------------------------------

def _cmd_inject_scan(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos signal-cso inject-scan",
        description="Scan a file for injection risk patterns (AMD-CORE-031).",
    )
    p.add_argument("path", help="File path to scan (relative to repo-root or absolute).")
    p.add_argument("--repo-root", default=None, metavar="PATH")
    p.add_argument("--json", dest="as_json", action="store_true", default=False)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = _repo_root(args.repo_root)
    findings = scan_injection_risks(root, args.path)

    if args.as_json:
        _print_json(findings)
        return 0

    if not findings:
        sys.stdout.write(f"No injection risks found in {args.path}\n")
        return 0
    for f in findings:
        sys.stdout.write(
            f"  [{f['file']}:{f['line']}] {f['risk']}\n"
        )
    return 0


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

_DISPATCH = {
    "scan": _cmd_scan,
    "canary": _cmd_canary,
    "threats": _cmd_threats,
    "inject-scan": _cmd_inject_scan,
}

_HELP = (
    "signalos signal-cso: subcommand required\n"
    "Usage:\n"
    "  signalos signal-cso scan <surface> --wave <W> [--json]\n"
    "  signalos signal-cso canary plant [--label L] [--json]\n"
    "  signalos signal-cso canary check [--label L] [--json]\n"
    "  signalos signal-cso threats list [--wave W] [--category C] [--json]\n"
    "  signalos signal-cso threats export --out <path> [--json]\n"
    "  signalos signal-cso inject-scan <path> [--json]\n"
)


def cmd_signal_cso(args: list[str]) -> int:
    """Dispatch signal-cso subcommands (AMD-CORE-031)."""
    if not args:
        sys.stdout.write(_HELP)
        return 1
    action, rest = args[0], args[1:]
    handler = _DISPATCH.get(action)
    if handler is None:
        sys.stderr.write(
            f"signalos signal-cso: unknown action {action!r}\n"
        )
        sys.stdout.write(_HELP)
        return 1
    return handler(rest)
