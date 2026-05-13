# SignalOS Core v2.4 — `signalos campaign` CLI (AMD-CORE-023, W5.2).
#
# Subcommands:
#   init        --name <name> --repos <r1,r2,...> [--campaign-root PATH]
#   status      [--campaign-root PATH] [--json]
#   orchestrate --wave <W> --plan <PATH> [--campaign-root PATH]
#               [--max-concurrent N] [--json]
#
# Exit codes:
#   0  success
#   1  argument / campaign error
#   2  orchestration had at least one failed repo

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.campaign import (
    CampaignError,
    campaign_orchestrate,
    campaign_status,
    init_campaign,
    load_campaign,
)

__all__ = ["cmd_init", "cmd_status", "cmd_orchestrate", "main"]


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def _build_init_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos campaign init",
        description="Create a Campaign Constitution (CAMPAIGN.json). AMD-CORE-023.",
    )
    p.add_argument("--name", required=True, help="Campaign name.")
    p.add_argument(
        "--repos", required=True,
        help="Comma-separated list of repo root paths (each must contain .signalos/).",
    )
    p.add_argument("--campaign-root", default=None, metavar="PATH",
                   help="Directory to write CAMPAIGN.json (default: cwd).")
    return p


def cmd_init(argv: list[str]) -> int:
    parser = _build_init_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    root = Path(args.campaign_root) if args.campaign_root else None
    try:
        manifest = init_campaign(args.name, repos, campaign_root=root)
    except CampaignError as exc:
        sys.stderr.write(f"signalos campaign init: {exc}\n")
        return 1

    sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _build_status_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos campaign status",
        description="Aggregate wave status across all repos in a campaign. AMD-CORE-023.",
    )
    p.add_argument("--campaign-root", default=None, metavar="PATH",
                   help="Directory containing CAMPAIGN.json (default: cwd).")
    p.add_argument("--json", dest="as_json", action="store_true", default=False,
                   help="Emit machine-readable JSON.")
    return p


def cmd_status(argv: list[str]) -> int:
    parser = _build_status_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = Path(args.campaign_root) if args.campaign_root else None
    try:
        campaign = load_campaign(root)
    except CampaignError as exc:
        sys.stderr.write(f"signalos campaign status: {exc}\n")
        return 1

    agg = campaign_status(campaign)

    if args.as_json:
        sys.stdout.write(json.dumps(agg, indent=2) + "\n")
        return 0

    sys.stdout.write(f"Campaign: {agg['name']}\n")
    for entry in agg["repos"]:
        path = entry["path"]
        err = entry.get("error")
        st = entry.get("status") or {}
        if err:
            sys.stdout.write(f"  {path}  ERROR: {err}\n")
        else:
            wave_id = st.get("wave_id", "—")
            phase = st.get("phase", "—")
            sys.stdout.write(f"  {path}  wave={wave_id}  phase={phase}\n")
    return 0


# ---------------------------------------------------------------------------
# orchestrate
# ---------------------------------------------------------------------------

def _build_orchestrate_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="signalos campaign orchestrate",
        description="Fan out orchestration to all repos in a campaign. AMD-CORE-023.",
    )
    p.add_argument("--wave", required=True, help="Wave ID (e.g. W5.2).")
    p.add_argument("--plan", required=True, help="Path to the plan file.")
    p.add_argument("--campaign-root", default=None, metavar="PATH",
                   help="Directory containing CAMPAIGN.json (default: cwd).")
    p.add_argument("--max-concurrent", type=int, default=4, metavar="N",
                   help="Maximum parallel orchestrations (default: 4).")
    p.add_argument("--json", dest="as_json", action="store_true", default=False,
                   help="Emit machine-readable JSON.")
    return p


def cmd_orchestrate(argv: list[str]) -> int:
    parser = _build_orchestrate_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    root = Path(args.campaign_root) if args.campaign_root else None
    try:
        campaign = load_campaign(root)
    except CampaignError as exc:
        sys.stderr.write(f"signalos campaign orchestrate: {exc}\n")
        return 1

    result = campaign_orchestrate(
        campaign,
        args.wave,
        args.plan,
        max_concurrent=args.max_concurrent,
        campaign_root=root,
    )

    if args.as_json:
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return 0

    any_failed = False
    for entry in result["repos"]:
        rc = entry["returncode"]
        path = entry["path"]
        err = entry.get("error") or ""
        status = "ok" if rc == 0 else f"FAILED (rc={rc})"
        if err:
            status += f" {err[:80]}"
        sys.stdout.write(f"  {path}  {status}\n")
        if rc != 0:
            any_failed = True

    return 2 if any_failed else 0


# ---------------------------------------------------------------------------
# main dispatcher
# ---------------------------------------------------------------------------

_DISPATCH = {
    "init": cmd_init,
    "status": cmd_status,
    "orchestrate": cmd_orchestrate,
}


def main(argv: list[str]) -> int:
    """Dispatch campaign subcommands: init | status | orchestrate."""
    if not argv:
        sys.stderr.write(
            "signalos campaign: subcommand required (init|status|orchestrate)\n"
        )
        return 1
    sub, rest = argv[0], argv[1:]
    handler = _DISPATCH.get(sub)
    if handler is None:
        sys.stderr.write(f"signalos campaign: unknown subcommand: {sub!r}\n")
        return 1
    return handler(rest)
