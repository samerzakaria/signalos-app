"""CLI wrapper for the post-retro closure gate."""
from __future__ import annotations

__all__ = ["cmd_signal_post_retro"]

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.retro import PostRetroHookError, run_post_retro_hook


def cmd_signal_post_retro(args: list[str]) -> int:
    """Run the installed post-retro hook for a wave."""
    if not args:
        parser = _build_parser()
        parser.print_help(sys.stderr)
        return 1

    parser = _build_parser()
    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()
    try:
        run_post_retro_hook(root, ns.wave)
    except PostRetroHookError as exc:
        if ns.json:
            sys.stdout.write(json.dumps({"ok": False, "wave": ns.wave, "error": str(exc)}) + "\n")
        sys.stderr.write(f"post-retro blocked: {exc}\n")
        return 1

    result = {"ok": True, "wave": ns.wave}
    if ns.json:
        sys.stdout.write(json.dumps(result) + "\n")
    else:
        sys.stdout.write(f"post-retro passed {ns.wave}\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos signal-post-retro",
        description="Run the post-retro Phase-8 closure gate for a wave.",
    )
    p.add_argument("wave", help="Wave identifier (e.g. 'wave-03-checkout').")
    p.add_argument("--repo-root", default=None, metavar="PATH", help="Repository root (default: cwd).")
    p.add_argument("--json", dest="json", action="store_true", default=False,
                   help="Emit machine-readable JSON.")
    return p
