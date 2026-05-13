# SignalOS Core v2.1 — `signalos orchestrate ...` CLI (AMD-CORE-008).
#
# Argparse wrapper around signalos_lib.orchestrator.run_wave(). Zero
# business logic — all task dispatch, worktree lifecycle, and event
# emission live in orchestrator.py and harness.py.
#
# Usage:
#   signalos orchestrate --wave <id> --plan <path>
#                        [--provider <name>] [--session-id <sid>]
#                        [--max-concurrent <n>] [--model <id>]
#
# Exit codes:
#   0 — all tasks completed
#   1 — user error (bad args)
#   2 — worktree create failed or all tasks failed
#   4 — some tasks failed (partial success)

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos orchestrate",
        description=(
            "Parallel wave orchestrator — create worktrees for a Wave, "
            "dispatch PLAN tasks concurrently via the headless harness, "
            "then reconcile and retire. AMD-CORE-008."
        ),
    )
    parser.add_argument(
        "--wave",
        dest="wave_id",
        required=True,
        help="Wave identifier (e.g. W2.1).",
    )
    parser.add_argument(
        "--plan",
        dest="plan_path",
        required=True,
        help="Path to the PLAN.md file for the Wave.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "LLM provider (default: anthropic). "
            "Overrides SIGNALOS_LLM_PROVIDER. "
            "Valid: anthropic, openai, gemini, ollama, test."
        ),
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Attach to an existing session (default: new orchestrate session).",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=5,
        help="Maximum number of concurrent worktree tasks (default: 5).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model id to use for each harness call.",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        return 1
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    from .. import orchestrator as orch_lib
    from .. import harness as harness_lib

    model = args.model or harness_lib.DEFAULT_MODEL

    try:
        result = orch_lib.run_wave(
            wave_id=args.wave_id,
            plan_path=args.plan_path,
            session_id=args.session_id,
            max_concurrent=args.max_concurrent,
            provider_name=args.provider,
            cwd=args.cwd,
            model=model,
        )
    except (ValueError, RuntimeError) as exc:
        sys.stderr.write(f"signalos orchestrate: {exc}\n")
        return 2

    sys.stdout.write(json.dumps(result, sort_keys=True, indent=2) + "\n")

    status = result.get("status", "")
    if status == "worktree_create_failed":
        return 2
    if status == "all_completed":
        return 0
    if result.get("failed", 0) > 0:
        return 2
    return 0
