# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/commands/validate_cmd.py
# W3.5 — signalos validate subcommand (AMD-CORE-018)

from __future__ import annotations

__all__ = ["main"]

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    import argparse
    from signalos_lib.validate_cmd import run_validators, overall_exit_code

    parser = argparse.ArgumentParser(
        prog="signalos validate",
        description=(
            "Run all governance validators with severity-labelled output (W3.5, AMD-CORE-018).\n\n"
            "Severity levels (from deliver.sh):\n"
            "  HALT        — blocks delivery, requires immediate fix\n"
            "  BLOCK_MERGE — blocks merge, must resolve before ship\n"
            "  WARN        — informational, does not block\n\n"
            "Exit codes:\n"
            "  0 — all validators pass\n"
            "  1 — any HALT failure\n"
            "  2 — any BLOCK_MERGE failure (no HALT)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=None, metavar="PATH",
                        help="Repo root (default: cwd).")
    parser.add_argument("--validator", default=None, metavar="NAME",
                        help="Run only this validator by name.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit JSON instead of table.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else None
    results = run_validators(repo_root=repo_root, validator_name=args.validator)
    code = overall_exit_code(results)

    if args.as_json:
        out = {
            "exit_code": code,
            "results": [
                {
                    "name": r.name,
                    "severity": r.severity,
                    "status": r.status_label,
                    "exit_code": r.exit_code,
                    "duration_ms": r.duration_ms,
                    "skipped": r.skipped,
                    "skip_reason": r.skip_reason,
                    "stderr": r.stderr[:500] if r.stderr else "",
                }
                for r in results
            ],
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        return code

    _render_table(results, code)
    return code


def _render_table(results, exit_code: int) -> None:
    SEV_ORDER = {"HALT": 0, "BLOCK_MERGE": 1, "WARN": 2}
    STATUS_ICON = {"PASS": "✓", "FAIL": "✗", "SKIP": "–"}

    name_w = max((len(r.name) for r in results), default=4)
    name_w = max(name_w, 4)

    sys.stdout.write("\n")
    sys.stdout.write(f"  {'Validator':<{name_w}}  {'Severity':<12}  {'Status':<6}  {'ms':>5}\n")
    sys.stdout.write(f"  {'-'*name_w}  {'-'*12}  {'-'*6}  {'-'*5}\n")

    fails = []
    for r in results:
        icon = STATUS_ICON.get(r.status_label, "?")
        ms = str(r.duration_ms) if not r.skipped else "—"
        sys.stdout.write(
            f"  {r.name:<{name_w}}  {r.severity:<12}  {icon} {r.status_label:<4}  {ms:>5}\n"
        )
        if not r.passed and r.stderr:
            fails.append((r.name, r.stderr[:200]))

    sys.stdout.write("\n")

    if fails:
        sys.stderr.write("Failures:\n")
        for name, msg in fails:
            sys.stderr.write(f"  [{name}] {msg}\n")
        sys.stderr.write("\n")

    label = {0: "all validators pass", 1: "HALT failure", 2: "BLOCK_MERGE failure"}.get(
        exit_code, f"exit {exit_code}"
    )
    icon = "✓" if exit_code == 0 else "✗"
    sys.stdout.write(f"  {icon} {label}\n\n")
