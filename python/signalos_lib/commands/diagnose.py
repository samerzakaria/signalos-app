# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/commands/diagnose.py
# W3.5 — signalos diagnose subcommand (AMD-CORE-018)

from __future__ import annotations

__all__ = ["main"]

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    import argparse
    from signalos_lib.diagnose import build_diagnose

    parser = argparse.ArgumentParser(
        prog="signalos diagnose",
        description=(
            "Emit a structured JSON diagnostic bundle (W3.5, AMD-CORE-018).\n\n"
            "Bundle includes: daemon state, last audit entries, worktree list,\n"
            "gate status, and pending T2 pauses. Paste output into bug reports.\n\n"
            "Exit codes:\n"
            "  0 — bundle emitted successfully"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=None, metavar="PATH",
                        help="Repo root (default: cwd).")
    parser.add_argument("--wave", default=None, metavar="WAVE",
                        help="Filter audit trail to this wave ID.")
    parser.add_argument("--output", default=None, metavar="PATH",
                        help="Write bundle to a file instead of stdout.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else None
    bundle = build_diagnose(repo_root=repo_root, wave=args.wave)
    text = json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"

    if args.output:
        out_path = Path(args.output)
        try:
            out_path.write_text(text, encoding="utf-8")
            sys.stdout.write(f"signalos diagnose: bundle written to {out_path}\n")
        except OSError as exc:
            sys.stderr.write(f"signalos diagnose: cannot write {out_path}: {exc}\n")
            return 2
    else:
        sys.stdout.write(text)

    return 0
