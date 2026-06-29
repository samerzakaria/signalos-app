"""CLI wrapper for app-native product Belief traceability validation."""

from __future__ import annotations

__all__ = ["main"]

import argparse
import json
import sys

from signalos_lib.validators.traceability import validate_product_traceability


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos validate-traceability",
        description="Verify product Belief artifacts are complete and source-traced.",
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--no-evidence", action="store_true")
    args = parser.parse_args(argv)

    payload = validate_product_traceability(
        args.repo_root,
        write_evidence=not args.no_evidence,
    )
    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(payload)
    return 0 if payload["ok"] else 1


def _print_human(payload: dict) -> None:
    print(f"validate-traceability: {payload['status']}")
    details = payload.get("details", {})
    print(f"matrix: {details.get('matrix_path')}")
    print(f"beliefs: {details.get('belief_count', 0)}")
    for issue in payload.get("issues", []):
        print(f"- {issue['code']}: {issue['message']}")
    if payload.get("evidence_path"):
        print(f"evidence: {payload['evidence_path']}")
    if not payload.get("ok"):
        print("validate-traceability: blockers found", file=sys.stderr)
