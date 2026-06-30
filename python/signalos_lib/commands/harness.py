# SignalOS Core v1.2 — `signalos harness ...` CLI (AMD-CORE-004).
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
#
# Argparse wrapper around signalos_lib.harness. Zero business logic —
# all step execution, event emission, state I/O, and abort handling
# live in the parent module.
#
# Subcommands:
#   call --step <id> [--prompt <s> | --prompt-file <p>]
#        [--model <id>] [--session-id <sid>]
#        [--parent-step-id <id>] [--intent <text>]
#   status <call-id> [--session-id <sid>]
#   abort  <call-id> [--session-id <sid>]
#
# Exit codes (see cli/signalos entrypoint for the global contract):
#   0 — success (call completed / status found / abort queued)
#   1 — user error (missing arg, bad step-id, prompt empty)
#   2 — execution error (step.failed emitted; anthropic error; call not found)
#   3 — policy refusal

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import harness as harness_lib


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos harness",
        description=(
            "Headless harness — execute a PLAN step without an editor by "
            "calling the configured LLM provider (auto-detected from the "
            "provider key present, or pinned via SIGNALOS_LLM_PROVIDER). "
            "Emits the same journal and metrics events as the editor emitters."
        ),
    )
    sub = parser.add_subparsers(dest="sub", metavar="SUBCOMMAND")

    # call
    p_call = sub.add_parser(
        "call",
        help="Run one step headlessly and emit the four W1.1 step events.",
    )
    p_call.add_argument(
        "--step", dest="step_id", required=True,
        help="Step identifier (matches the PLAN step-spec).",
    )
    prompt_group = p_call.add_mutually_exclusive_group()
    prompt_group.add_argument(
        "--prompt",
        help="Inline prompt text passed to the model.",
    )
    prompt_group.add_argument(
        "--prompt-file", type=Path,
        help="Path to a file whose contents are used as the prompt.",
    )
    p_call.add_argument(
        "--model", default=None,
        help=(
            "Model id for the resolved provider. Default: none — the model "
            "is discovered from the provider's API (override here or via "
            "SIGNALOS_LLM_MODEL)."
        ),
    )
    p_call.add_argument(
        "--session-id",
        help="Attach to an existing session id (default: new harness session).",
    )
    p_call.add_argument(
        "--parent-step-id",
        help="Parent step id when this call was spawned by another step.",
    )
    p_call.add_argument(
        "--intent",
        help="Short human-readable intent for the step (audit record).",
    )
    p_call.add_argument(
        "--provider",
        default=None,
        help=(
            "LLM provider to use. Default: auto-detected from whichever "
            "provider key is set, overridable via SIGNALOS_LLM_PROVIDER. "
            "Valid values: anthropic, openai, gemini, groq, mistral, "
            "deepseek, openrouter, xai, together, cerebras, dashscope, "
            "ollama, test."
        ),
    )
    p_call.add_argument("--cwd", type=Path, default=None, help=argparse.SUPPRESS)

    # status
    p_status = sub.add_parser(
        "status",
        help="Show the state.json for a given harness call.",
    )
    p_status.add_argument("call_id", help="Harness call id.")
    p_status.add_argument("--session-id", default=None)
    p_status.add_argument("--cwd", type=Path, default=None, help=argparse.SUPPRESS)

    # abort
    p_abort = sub.add_parser(
        "abort",
        help="Request abort for a running harness call.",
    )
    p_abort.add_argument("call_id", help="Harness call id.")
    p_abort.add_argument("--session-id", default=None)
    p_abort.add_argument("--cwd", type=Path, default=None, help=argparse.SUPPRESS)

    return parser


def _cmd_call(args: argparse.Namespace) -> int:
    # Resolve provider name from --provider flag (AMD-CORE-007)
    provider = None
    provider_name = getattr(args, "provider", None)
    if provider_name:
        try:
            provider = harness_lib._resolve_provider(provider_name)
        except RuntimeError as exc:
            sys.stderr.write(f"signalos harness call: {exc}\n")
            return 1

    try:
        result = harness_lib.run_step(
            step_id=args.step_id,
            prompt=args.prompt,
            prompt_file=args.prompt_file,
            model=args.model,
            session_id=args.session_id,
            parent_step_id=args.parent_step_id,
            cwd=args.cwd,
            intent=args.intent,
            provider=provider,
            # Pass the name too so model discovery targets the SAME provider
            # the --provider flag selected (provider= is an instance only).
            provider_name=provider_name,
        )
    except ValueError as exc:
        sys.stderr.write(f"signalos harness call: {exc}\n")
        return 1
    except RuntimeError as exc:
        sys.stderr.write(f"signalos harness call: {exc}\n")
        return 2

    sys.stdout.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return int(result.get("exit_code", 0))


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        state = harness_lib.get_status(
            args.call_id, session_id=args.session_id, cwd=args.cwd,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"signalos harness status: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(state, sort_keys=True, indent=2) + "\n")
    return 0


def _cmd_abort(args: argparse.Namespace) -> int:
    try:
        state = harness_lib.abort_call(
            args.call_id, session_id=args.session_id, cwd=args.cwd,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"signalos harness abort: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(state, sort_keys=True, indent=2) + "\n")
    return 0


def main(argv: list[str]) -> int:
    parser = _build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        sys.stderr.write("\nsignalos harness: subcommand required (call|status|abort)\n")
        return 1

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    sub = args.sub
    if sub == "call":
        return _cmd_call(args)
    if sub == "status":
        return _cmd_status(args)
    if sub == "abort":
        return _cmd_abort(args)

    parser.print_help(sys.stderr)
    sys.stderr.write(f"\nsignalos harness: unknown subcommand: {sub}\n")
    return 1
