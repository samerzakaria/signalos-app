"""CLI command for ``signalos deliver`` -- full product delivery pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the deliver subcommand."""
    p = subparsers.add_parser("deliver", help="Run the product delivery bridge")
    p.add_argument("--prompt", required=True, help="Product request prompt")
    p.add_argument("--name", default=None, help="Product/repo name")
    p.add_argument("--repo-root", default=None, help="Existing or target repo root")
    p.add_argument("--target-root", default=None, help="Parent folder for greenfield repos")
    p.add_argument(
        "--mode",
        choices=["greenfield", "adopt", "refresh", "auto"],
        default="auto",
    )
    p.add_argument("--profile", default="auto")
    p.add_argument("--blueprint", default="auto")
    p.add_argument(
        "--deploy",
        choices=["none", "prepare", "live"],
        default="none",
    )
    p.add_argument("--yes", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-repair-cycles", type=int, default=3)
    p.add_argument(
        "--agent",
        choices=["none", "packet-only", "orchestrator", "auto"],
        default="none",
    )
    p.add_argument("--json", action="store_true", dest="as_json")
    return p


def cmd_deliver(args: argparse.Namespace) -> int:
    """Execute the deliver command."""
    from ..product.delivery import run_delivery

    closeout = run_delivery(
        prompt=args.prompt,
        name=args.name,
        repo_root=Path(args.repo_root) if args.repo_root else None,
        target_root=Path(args.target_root) if args.target_root else None,
        mode=args.mode,
        profile=args.profile,
        blueprint=args.blueprint,
        deploy=args.deploy,
        yes=args.yes,
        dry_run=args.dry_run,
        max_repair_cycles=args.max_repair_cycles,
        agent_mode=args.agent,
        json_output=args.as_json,
    )

    level = closeout.get("closure_level", "unknown")
    if not args.as_json:
        print(f"\nDelivery complete: {level}")
        print(f"Repo: {closeout.get('repo_path', 'unknown')}")
        if closeout.get("how_to_run"):
            print("\nHow to run:")
            for step in closeout["how_to_run"]:
                print(f"  {step}")

    return 0 if level in ("ready", "verified") else 1
