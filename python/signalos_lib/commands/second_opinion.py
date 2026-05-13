# cli/signalos_lib/commands/second_opinion.py — W15 CLI wrappers (AMD-CORE-036)
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

__all__ = [
    "cmd_signal_second_opinion",
    "cmd_signal_second_opinion_record",
]


def _repo(args_root: Optional[str] = None) -> Path:
    return Path(args_root) if args_root else Path.cwd()


def cmd_signal_second_opinion(args: list[str]) -> int:
    """Request a second-opinion review on a subject (plan/diff/decision)."""
    if not args:
        return 1

    import argparse
    parser = argparse.ArgumentParser(prog="signalos signal-second-opinion")
    parser.add_argument("subject", help="Subject under review (plan title, diff, or decision)")
    parser.add_argument("--wave", required=True)
    parser.add_argument("--note", default="")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", dest="as_json", action="store_true")

    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    from signalos_lib.second_opinion import request_second_opinion
    root = _repo(ns.repo_root)
    record = request_second_opinion(root, ns.subject, ns.wave, ns.note)

    if ns.as_json:
        print(json.dumps(record.as_dict(), indent=2))
    else:
        print(f"[{record.id}] {record.subject} — verdict: {record.verdict}")

    return 0


def cmd_signal_second_opinion_record(args: list[str]) -> int:
    """Record the second model's verdict on an existing opinion request."""
    if not args:
        return 1

    import argparse
    parser = argparse.ArgumentParser(prog="signalos signal-second-opinion-record")
    parser.add_argument("opinion_id", help="ID of the opinion to update (e.g. so-001)")
    parser.add_argument(
        "--verdict",
        required=True,
        choices=["agree", "disagree", "risk-identified", "pending"],
    )
    parser.add_argument("--new-risk", default="", dest="new_risk")
    parser.add_argument("--decision-dna-ref", default="", dest="decision_dna_ref")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", dest="as_json", action="store_true")

    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    from signalos_lib.second_opinion import record_verdict
    root = _repo(ns.repo_root)
    try:
        updated = record_verdict(root, ns.opinion_id, ns.verdict, ns.new_risk, ns.decision_dna_ref)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1

    if updated is None:
        sys.stderr.write(f"error: opinion {ns.opinion_id!r} not found\n")
        return 1

    if ns.as_json:
        print(json.dumps(updated.as_dict(), indent=2))
    else:
        print(f"[{updated.id}] verdict={updated.verdict}")

    return 0
