"""Product factory command surface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from signalos_lib.product.blueprints.factory import (
    BlueprintWorkflowError,
    draft_blueprint,
    inspect_blueprint,
    register_blueprint,
    review_blueprint,
)
from signalos_lib.product.blueprints.registry import (
    list_blueprints,
    validate_blueprint_registry,
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos product",
        description="Product factory workflows.",
    )
    sub = parser.add_subparsers(dest="area")
    blueprint = sub.add_parser("blueprint", help="List, inspect, validate, draft, review, and register blueprints")
    bp_sub = blueprint.add_subparsers(dest="action")

    p_list = bp_sub.add_parser("list", help="List built-in and custom product blueprints")
    _add_repo_root(p_list)
    _add_json(p_list)

    p_inspect = bp_sub.add_parser("inspect", help="Inspect one product blueprint")
    _add_repo_root(p_inspect)
    p_inspect.add_argument("--id", required=True, dest="blueprint_id")
    _add_json(p_inspect)

    p_validate = bp_sub.add_parser("validate", help="Validate blueprint registry entries")
    _add_repo_root(p_validate)
    p_validate.add_argument("--id", default=None, dest="blueprint_id")
    _add_json(p_validate)

    p_draft = bp_sub.add_parser("draft", help="Create a governed custom-blueprint draft")
    _add_repo_root(p_draft)
    p_draft.add_argument("--id", required=True, dest="blueprint_id")
    p_draft.add_argument("--from-intent", default=None)
    p_draft.add_argument("--force", action="store_true")
    _add_json(p_draft)

    p_review = bp_sub.add_parser("review", help="Record a custom-blueprint review verdict")
    _add_repo_root(p_review)
    p_review.add_argument("--id", required=True, dest="blueprint_id")
    p_review.add_argument("--verdict", choices=["approve", "request-changes", "reject"], required=True)
    p_review.add_argument("--notes", default="")
    _add_json(p_review)

    p_register = bp_sub.add_parser("register", help="Register an approved custom-blueprint draft")
    _add_repo_root(p_register)
    p_register.add_argument("--id", required=True, dest="blueprint_id")
    p_register.add_argument("--force", action="store_true")
    _add_json(p_register)

    args = parser.parse_args(argv)
    if args.area != "blueprint" or args.action is None:
        parser.print_help()
        return 1

    try:
        payload = _run_blueprint(args)
    except BlueprintWorkflowError as exc:
        print(f"signalos product blueprint: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"signalos product blueprint: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(args.action, payload)
    return 0 if payload.get("ok", True) or payload.get("status") in {"drafted", "approved", "registered"} else 1


def _run_blueprint(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    action = args.action
    if action == "list":
        return {
            "schema_version": "signalos.blueprint_list.v1",
            "ok": True,
            "repo_root": str(root),
            "blueprints": list_blueprints(root),
        }
    if action == "inspect":
        return {"ok": True, **inspect_blueprint(root, args.blueprint_id)}
    if action == "validate":
        return validate_blueprint_registry(root, args.blueprint_id)
    if action == "draft":
        return draft_blueprint(
            root,
            args.blueprint_id,
            from_intent=args.from_intent,
            force=args.force,
        )
    if action == "review":
        return review_blueprint(
            root,
            args.blueprint_id,
            verdict=args.verdict,
            notes=args.notes,
        )
    if action == "register":
        return register_blueprint(root, args.blueprint_id, force=args.force)
    raise BlueprintWorkflowError(f"unknown blueprint action: {action}")


def _add_repo_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=None)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def _print_human(action: str, payload: dict[str, Any]) -> None:
    if action == "list":
        print("signalos product blueprint list")
        for item in payload.get("blueprints", []):
            print(f"- {item.get('id')}\t{item.get('origin')}\t{item.get('display_name')}")
        return
    if action == "validate":
        print(f"signalos product blueprint validate: {payload.get('status')}")
        for issue in payload.get("issues", []):
            print(f"- {issue.get('severity')}: {issue.get('scope')}: {issue.get('message')}")
        return
    label = payload.get("blueprint_id") or payload.get("id")
    print(f"signalos product blueprint {action}: {payload.get('status', 'ok')} {label}")
    if payload.get("evidence_path"):
        print(f"evidence: {payload['evidence_path']}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
