"""CLI wrapper for app-native Trust Tier surfaces."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from signalos_lib.trust_tiers import (
    TrustTierError,
    demote_trust_surface,
    get_trust_surface_by_surface,
    list_trust_surfaces,
    load_trust_surface,
    promote_trust_surface,
    register_trust_surface,
    validate_trust_tier,
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos trust-tier",
        description="Manage and validate Trust Tier surface declarations.",
    )
    sub = parser.add_subparsers(dest="area")

    surface = sub.add_parser("surface", help="Trust-tier surface lifecycle")
    surface_sub = surface.add_subparsers(dest="action")

    p_register = surface_sub.add_parser("register", help="Register a surface tier")
    _add_repo_root(p_register)
    _add_tenant(p_register)
    p_register.add_argument("--surface-id", required=True)
    p_register.add_argument("--tier", choices=["T1", "T2", "T3"], required=True)
    p_register.add_argument("--justification", required=True)
    p_register.add_argument("--permanent", action="store_true", dest="is_permanently_t3")
    p_register.add_argument("--force", action="store_true")
    _add_json(p_register)

    p_show = surface_sub.add_parser("show", help="Show one surface tier")
    _add_repo_root(p_show)
    _add_tenant(p_show)
    p_show.add_argument("--surface-id", required=True)
    _add_json(p_show)

    p_get = surface_sub.add_parser("get-by-surface", help="Get one surface tier or null")
    _add_repo_root(p_get)
    _add_tenant(p_get)
    p_get.add_argument("--surface-id", required=True)
    _add_json(p_get)

    p_list = surface_sub.add_parser("list", help="List surface tiers")
    _add_repo_root(p_list)
    _add_tenant(p_list)
    p_list.add_argument("--tier", choices=["T1", "T2", "T3"], default=None)
    p_list.add_argument("--all-tenants", action="store_true")
    _add_json(p_list)

    p_promote = surface_sub.add_parser("promote", help="Promote a surface tier")
    _add_repo_root(p_promote)
    _add_tenant(p_promote)
    p_promote.add_argument("--surface-id", required=True)
    p_promote.add_argument("--to", choices=["T2", "T3"], required=True, dest="target_tier")
    p_promote.add_argument("--justification", required=True)
    _add_json(p_promote)

    p_demote = surface_sub.add_parser("demote", help="Demote a surface tier")
    _add_repo_root(p_demote)
    _add_tenant(p_demote)
    p_demote.add_argument("--surface-id", required=True)
    p_demote.add_argument("--to", choices=["T1", "T2"], required=True, dest="target_tier")
    p_demote.add_argument("--justification", required=True)
    _add_json(p_demote)

    p_validate = sub.add_parser("validate", help="Validate touched paths against a declared tier")
    _add_repo_root(p_validate)
    _add_tenant(p_validate)
    p_validate.add_argument("--declared-tier", choices=["T1", "T2", "T3"], required=True)
    p_validate.add_argument("--touched", action="append", default=[])
    p_validate.add_argument("--allow-unclassified", action="store_true")
    p_validate.add_argument("--no-evidence", action="store_true")
    _add_json(p_validate)

    args = parser.parse_args(argv)
    if args.area is None or (args.area == "surface" and args.action is None):
        parser.print_help()
        return 1

    try:
        payload = _run(args)
    except (FileExistsError, FileNotFoundError, OSError, TrustTierError, json.JSONDecodeError) as exc:
        print(f"signalos trust-tier: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(args, payload)
    return 0 if payload.get("ok", True) else 1


def _run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    if args.area == "surface":
        if args.action == "register":
            return register_trust_surface(
                root,
                surface_id=args.surface_id,
                tier=args.tier,
                justification=args.justification,
                is_permanently_t3=args.is_permanently_t3,
                tenant_id=args.tenant_id,
                force=args.force,
            )
        if args.action == "show":
            return load_trust_surface(root, args.surface_id, tenant_id=args.tenant_id)
        if args.action == "get-by-surface":
            surface = get_trust_surface_by_surface(root, args.surface_id, tenant_id=args.tenant_id)
            return {"ok": True, "found": surface is not None, "surface": surface}
        if args.action == "list":
            surfaces = list_trust_surfaces(
                root,
                tier=args.tier,
                tenant_id=args.tenant_id,
                all_tenants=args.all_tenants,
            )
            return {"ok": True, "surfaces": surfaces, "count": len(surfaces)}
        if args.action == "promote":
            return promote_trust_surface(
                root,
                args.surface_id,
                target_tier=args.target_tier,
                justification=args.justification,
                tenant_id=args.tenant_id,
            )
        if args.action == "demote":
            return demote_trust_surface(
                root,
                args.surface_id,
                target_tier=args.target_tier,
                justification=args.justification,
                tenant_id=args.tenant_id,
            )
    if args.area == "validate":
        return validate_trust_tier(
            root,
            declared_tier=args.declared_tier,
            touched_paths=list(args.touched or []),
            tenant_id=args.tenant_id,
            allow_unclassified=args.allow_unclassified,
            write_evidence=not args.no_evidence,
        )
    raise TrustTierError("unknown trust-tier action")


def _add_repo_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=None)


def _add_tenant(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant-id", default=None)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def _print_human(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if args.area == "validate":
        print(f"signalos trust-tier validate: {payload.get('status')}")
        for blocker in payload.get("blockers", []):
            print(f"- {blocker.get('kind')}: {blocker.get('message')}")
        if payload.get("evidence_path"):
            print(f"evidence: {payload['evidence_path']}")
        return
    if args.action == "list":
        print(f"signalos trust-tier surface list: {payload.get('count', 0)}")
        for surface in payload.get("surfaces", []):
            flag = " permanent" if surface.get("is_permanently_t3") else ""
            tenant = surface.get("tenant_id") or "host"
            print(f"- [{tenant}] {surface.get('surface_id')} {surface.get('tier')}{flag}")
        return
    if args.action == "get-by-surface":
        surface = payload.get("surface")
        if surface is None:
            print("signalos trust-tier surface get-by-surface: not found")
        else:
            flag = " permanent" if surface.get("is_permanently_t3") else ""
            tenant = surface.get("tenant_id") or "host"
            print(
                "signalos trust-tier surface get-by-surface: "
                f"[{tenant}] {surface.get('surface_id')} {surface.get('tier')}{flag}"
            )
        return
    flag = " permanent" if payload.get("is_permanently_t3") else ""
    tenant = payload.get("tenant_id") or "host"
    print(
        f"signalos trust-tier surface {args.action}: "
        f"[{tenant}] {payload.get('surface_id')} {payload.get('tier')}{flag}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
