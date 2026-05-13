"""
cli/signalos_lib/commands/preamble.py — CLI for /signalos session-preamble (W16, AMD-CORE-037).

Resolves the session-preamble template at integrations/rules/signalos-preamble.mdc
and writes the substituted output to .signalos/session-preamble.md so the
session-start hook can prepend it to the agent's context.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["cmd_session_preamble"]


def _repo(args_root: str | None = None) -> Path:
    return Path(args_root) if args_root else Path.cwd()


def cmd_session_preamble(args: list[str]) -> int:
    """Resolve preamble vars and write .signalos/session-preamble.md.

    Reads the template at integrations/rules/signalos-preamble.mdc, substitutes
    every {{VAR}} from local artifacts (Constitution, Soul Document, BELIEF,
    PLAN.tasks.yaml, .signalos/config.json) plus session-specific overrides
    passed on the command line, writes the resolved markdown.
    """
    parser = argparse.ArgumentParser(
        prog="signalos session-preamble",
        description=(
            "Resolve the SignalOS session preamble template and write the "
            "substituted output to .signalos/session-preamble.md."
        ),
    )
    parser.add_argument("--session-type", default="session",
                        help="onboard / pre-wave / plan / build / review / ship / debrief / wave-review")
    parser.add_argument("--agent", dest="agent_name", default="agent",
                        help="Agent name (e.g. 'PE-Build', 'PO-Onboard', 'QA')")
    parser.add_argument("--agent-owner", default="PE",
                        help="Owner role: PO / PE / QA / DevOps / Eval")
    parser.add_argument("--trust-tier", default="T2", choices=["T1", "T2", "T3"])
    parser.add_argument("--task-surface", default="core/execution/")
    parser.add_argument("--wave", dest="wave_id", default=None,
                        help="Override wave id (defaults to PLAN.tasks.yaml `wave:` field)")
    parser.add_argument("--scope", default=None)
    parser.add_argument("--out-of-scope", default=None)
    parser.add_argument("--inputs", default=None)
    parser.add_argument("--outputs", default=None)
    parser.add_argument("--end-rule", default=None)
    parser.add_argument("--embedded-gates", default=None)
    parser.add_argument("--repo-root", default=None,
                        help="Override repo root (default: cwd)")
    parser.add_argument("--stdout", action="store_true",
                        help="Print resolved preamble to stdout instead of writing the file")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="JSON output: {output, vars_resolved}")

    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    from signalos_lib.preamble import (
        resolve_preamble,
        write_resolved_preamble,
        RESOLVED_OUTPUT_RELATIVE,
    )

    repo_root = _repo(ns.repo_root)
    resolved = resolve_preamble(
        repo_root,
        session_type=ns.session_type,
        agent_name=ns.agent_name,
        agent_owner=ns.agent_owner,
        trust_tier=ns.trust_tier,
        task_surface=ns.task_surface,
        wave_id=ns.wave_id,
        scope=ns.scope,
        out_of_scope=ns.out_of_scope,
        inputs=ns.inputs,
        outputs=ns.outputs,
        end_rule=ns.end_rule,
        embedded_gates=ns.embedded_gates,
    )

    if not resolved:
        sys.stderr.write(
            f"error: preamble template not found at "
            f"{repo_root}/integrations/rules/signalos-preamble.mdc\n"
        )
        return 2

    if ns.stdout:
        sys.stdout.write(resolved)
        return 0

    out_path = write_resolved_preamble(repo_root, resolved)

    if ns.as_json:
        sys.stdout.write(json.dumps({
            "output": str(out_path.relative_to(repo_root)),
            "session_type": ns.session_type,
            "wave_id": ns.wave_id,
            "trust_tier": ns.trust_tier,
        }) + "\n")
    else:
        sys.stdout.write(f"resolved preamble → {out_path.relative_to(repo_root)}\n")

    return 0
