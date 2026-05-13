# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/commands/health.py
# W3.5 — signalos health subcommand (AMD-CORE-018)

from __future__ import annotations

__all__ = ["main"]

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    import argparse
    from signalos_lib.health import HealthStatus, run_health

    parser = argparse.ArgumentParser(
        prog="signalos health",
        description=(
            "Aggregate subsystem health check (W3.5, AMD-CORE-018).\n\n"
            "Checks: git · python ≥3.11 · jq · wiring-guard · daemon heartbeat.\n\n"
            "Exit codes:\n"
            "  0 — healthy (all checks OK)\n"
            "  1 — degraded (≥1 check DEGRADED, none DOWN)\n"
            "  2 — down (≥1 check DOWN)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=None, metavar="PATH",
                        help="Repo root (default: cwd).")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit JSON instead of table.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else None
    report = run_health(repo_root)

    if args.as_json:
        out = {
            "overall": report.overall,
            "exit_code": report.exit_code,
            "items": [
                {"name": i.name, "status": i.status, "detail": i.detail}
                for i in report.items
            ],
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        return report.exit_code

    # Human-readable table
    _render_table(report)
    return report.exit_code


def _render_table(report) -> None:
    from signalos_lib.health import HealthStatus

    ICONS = {HealthStatus.OK: "✓", HealthStatus.DEGRADED: "!", HealthStatus.DOWN: "✗"}
    LABELS = {HealthStatus.OK: "ok", HealthStatus.DEGRADED: "degraded", HealthStatus.DOWN: "down"}

    name_w = max((len(i.name) for i in report.items), default=4)
    name_w = max(name_w, 4)

    print()
    print(f"  {'Check':<{name_w}}  Status    Detail")
    print(f"  {'-' * name_w}  --------  ------")
    for item in report.items:
        icon = ICONS.get(item.status, "?")
        label = LABELS.get(item.status, item.status)
        detail = item.detail[:80] if item.detail else ""
        print(f"  {item.name:<{name_w}}  {icon} {label:<7}  {detail}")
    print()

    overall = report.overall
    icon = ICONS.get(overall, "?")
    label = LABELS.get(overall, overall)
    print(f"  Overall: {icon} {label}")
    print()
