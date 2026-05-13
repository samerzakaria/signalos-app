# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/commands/recover.py
# W3.5 — signalos recover subcommand (AMD-CORE-018)
#
# CLI surface for checkpoint-aware deliver.sh resume.
# Reads the last checkpoint and reports what phases remain.

from __future__ import annotations

__all__ = ["main"]

import json
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="signalos recover",
        description=(
            "Checkpoint-aware delivery recovery (W3.5, AMD-CORE-018).\n\n"
            "Reads the last delivery checkpoint from .signalos/ and reports\n"
            "which phases completed and which remain. Use --resume to trigger\n"
            "deliver.sh resume from the last checkpoint.\n\n"
            "Exit codes:\n"
            "  0 — status shown / resume triggered\n"
            "  1 — no checkpoint found\n"
            "  2 — usage error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=None, metavar="PATH",
                        help="Repo root (default: cwd).")
    parser.add_argument("--resume", action="store_true",
                        help="Trigger deliver.sh resume after showing status.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit JSON checkpoint summary.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    checkpoint = _read_checkpoint(repo_root)

    if checkpoint is None:
        sys.stderr.write("signalos recover: no checkpoint found in .signalos/\n")
        return 1

    if args.as_json:
        sys.stdout.write(json.dumps(checkpoint, indent=2, ensure_ascii=False) + "\n")
        if args.resume:
            return _do_resume(repo_root)
        return 0

    _render_checkpoint(checkpoint)
    if args.resume:
        return _do_resume(repo_root)
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_checkpoint(repo_root: Path) -> dict | None:
    """Return the most recent checkpoint dict, or None if absent."""
    cp_dir = repo_root / ".signalos" / "checkpoints"
    if not cp_dir.exists():
        # Fall back to daemon-state.json checkpoint field
        ds = repo_root / ".signalos" / "daemon-state.json"
        if ds.exists():
            try:
                obj = json.loads(ds.read_text(encoding="utf-8"))
                if "checkpoint" in obj:
                    return obj["checkpoint"]
            except Exception:
                pass
        return None

    files = sorted(cp_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _render_checkpoint(cp: dict) -> None:
    sys.stdout.write("\n  Checkpoint summary\n")
    sys.stdout.write("  ──────────────────\n")
    for k, v in cp.items():
        if isinstance(v, (list, dict)):
            v = json.dumps(v)
        sys.stdout.write(f"  {k:<20} {v}\n")
    sys.stdout.write("\n")


def _do_resume(repo_root: Path) -> int:
    deliver = repo_root / "core" / "execution" / "deliver.sh"
    if not deliver.exists():
        sys.stderr.write(f"signalos recover: deliver.sh not found at {deliver}\n")
        return 2
    try:
        result = subprocess.run(
            ["bash", str(deliver), "resume", "--repo-root", str(repo_root)],
            cwd=str(repo_root),
        )
        return result.returncode
    except KeyboardInterrupt:
        return 0
