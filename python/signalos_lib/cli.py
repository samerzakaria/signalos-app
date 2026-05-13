"""SignalOS CLI entry point — argparse subparser dispatcher.

AMD-CORE-013 I3/I4: argparse subparser dispatch (replaces manual if-chain).
AMD-CORE-013 I10: daemon and worktree thin-dispatch subcommands.
AMD-CORE-016: intent subcommand (W3.3 — natural language routing).
AMD-CORE-017: plan subcommand (W3.4 — machine-readable task schema).
AMD-CORE-018: health/diagnose/validate/hooks/recover (W3.5 — operator tooling).
AMD-CORE-038 (W17): refactored from cli/signalos script into importable module.
"""
# AMD-CORE-013 I3/I4: argparse subparser dispatch (replaces manual if-chain).
# AMD-CORE-013 I10: daemon and worktree thin-dispatch subcommands.
# AMD-CORE-016: intent subcommand (W3.3 — natural language routing).
# AMD-CORE-017: plan subcommand (W3.4 — machine-readable task schema).
# AMD-CORE-018: health/diagnose/validate/hooks/recover (W3.5 — operator tooling).

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

def _repo_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL
        )
        return Path(out.strip())
    except Exception:
        return Path.cwd()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos",
        description="SignalOS Core CLI — Wave delivery, gate signing, and observability.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Role-based reading paths:\n"
            "  PO:      status, session list\n"
            "  PE:      orchestrate, pause, status, harness\n"
            "  QA:      status, session show\n"
            "  DevOps:  daemon, worktree, install\n"
            "  Eval:    status --json\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_session = sub.add_parser("session", help="Session journal commands (W1.1)")
    p_session.add_argument("action", choices=["list", "show", "resume", "archive"])
    p_session.add_argument("session_id", nargs="?", default=None)
    p_session.add_argument("--product", default=None, metavar="ID", help="Product namespace (W4.2)")

    p_pause = sub.add_parser("pause", help="Step-pause controller (W1.1)")
    p_pause.add_argument("action", choices=["list", "resume", "abort"])
    p_pause.add_argument("step_id", nargs="?", default=None)

    p_harness = sub.add_parser("harness", help="Headless harness execution (W1.2)")
    p_harness.add_argument("action", choices=["call", "status", "abort"])
    p_harness.add_argument("--step", dest="step_id", default=None)
    p_harness.add_argument("--model", default=None)
    p_harness.add_argument("--provider", default=None)
    p_harness.add_argument("--cwd", default=None)

    for verb in ("install", "verify", "list", "uninstall", "publish"):
        sp = sub.add_parser(verb, help=f"Plugin registry: {verb} (W1.3)")
        if verb in ("install", "verify", "uninstall"):
            sp.add_argument("target", nargs="?", default=None)
        if verb == "publish":
            sp.add_argument("dir", nargs="?", default=None)
            sp.add_argument("--key", default=None)
            sp.add_argument("--out", default=None)
            sp.add_argument("--update-catalog", dest="catalog_path", default=None, metavar="PATH")
        if verb == "install":
            sp.add_argument("--allow-unsigned", action="store_true")

    p_ctx = sub.add_parser("context", help="Context compress/expand (W1.3)")
    p_ctx.add_argument("action", choices=["compress", "expand"])
    p_ctx.add_argument("target", nargs="?", default=None)
    p_ctx.add_argument("--scope", default=None)
    p_ctx.add_argument("--turn", type=int, default=None)

    p_orch = sub.add_parser("orchestrate", help="Parallel wave orchestration (W2.1)")
    p_orch.add_argument("--wave", required=True)
    p_orch.add_argument("--plan", required=True)
    p_orch.add_argument("--session-id", default=None)
    p_orch.add_argument("--repo-root", default=None)
    p_orch.add_argument("--max-concurrent", type=int, default=5)
    p_orch.add_argument("--provider", default=None)

    p_status = sub.add_parser("status", help="Wave status card (W2.1)")
    p_status.add_argument("--repo-root", type=Path, default=None)
    p_status.add_argument("--watch", action="store_true", default=False)
    p_status.add_argument("--interval", type=float, default=2.0, metavar="SECS")
    p_status.add_argument("--json", action="store_true", dest="as_json")
    p_status.add_argument("--product", default=None, metavar="ID", help="Product namespace (W4.2)")

    p_sign = sub.add_parser("sign", help="Guided gate signing wizard (W3.1)")
    p_sign.add_argument("gate", choices=["G0","G1","G2","G3","G4","G5"], metavar="GATE")
    p_sign.add_argument("--check", action="store_true")
    p_sign.add_argument("--signer", default=None)
    p_sign.add_argument("--role", choices=["PO","PE","QA","DevOps"], default=None)
    p_sign.add_argument("--verdict", choices=["APPROVED","APPROVED-WITH-CONDITIONS","WAIVED"], default=None)
    p_sign.add_argument("--conditions", default="")
    p_sign.add_argument("--repo-root", default=None)
    p_sign.add_argument("--oidc", action="store_true", help="OIDC browser auth before signing (W6.3)")

    p_plan = sub.add_parser("plan", help="Machine-readable task schema (W3.4)")
    p_plan.add_argument("action", choices=["render", "validate", "list"])
    p_plan.add_argument("--input", default=None, metavar="PATH")
    p_plan.add_argument("--output", default=None, metavar="PATH")
    p_plan.add_argument("--status", default=None, metavar="STATUS")
    p_plan.add_argument("--json", action="store_true", dest="as_json")

    p_intent = sub.add_parser("intent", help="Natural language intent routing (W3.3)")
    p_intent.add_argument("phrase", nargs="?", default=None,
        help='Free-form intent phrase, e.g. "I want to add a payment feature"')
    p_intent.add_argument("--all", action="store_true", help="Show all intent scores.")
    p_intent.add_argument("--json", action="store_true", dest="as_json", help="JSON output.")
    p_intent.add_argument("--threshold", type=float, default=None, metavar="FLOAT",
        help="Confidence threshold override (default 0.70).")

    p_daemon = sub.add_parser("daemon", help="Delivery daemon control (I10)")
    p_daemon.add_argument("action", choices=["start", "status", "stop", "resume"])
    p_daemon.add_argument("--mode", choices=["fresh-wave", "daemon"], default="daemon")
    p_daemon.add_argument("--wave", default=None)
    p_daemon.add_argument("--repo-root", default=None)
    p_daemon.add_argument("--poll-interval", type=int, default=60)

    p_wt = sub.add_parser("worktree", help="Worktree lifecycle management (I10)")
    p_wt.add_argument("action", choices=["create", "status", "reconcile", "retire", "list"])
    p_wt.add_argument("--wave", default=None)
    p_wt.add_argument("--plan", default=None)
    p_wt.add_argument("--repo-root", default=None)
    p_wt.add_argument("--max-concurrent", type=int, default=5)

    p_health = sub.add_parser("health", help="System health check (W3.5)")
    p_health.add_argument("--repo-root", default=None)
    p_health.add_argument("--json", action="store_true", dest="as_json")

    p_diag = sub.add_parser("diagnose", help="Runtime diagnostic snapshot (W3.5)")
    p_diag.add_argument("--repo-root", default=None)
    p_diag.add_argument("--wave", default=None)
    p_diag.add_argument("--output", default=None, metavar="PATH")
    p_diag.add_argument("--json", action="store_true", dest="as_json")

    p_val = sub.add_parser("validate", help="Run validator suite (W3.5)")
    p_val.add_argument("--repo-root", default=None)
    p_val.add_argument("--validator", default=None)
    p_val.add_argument("--json", action="store_true", dest="as_json")

    p_hooks = sub.add_parser("hooks", help="Hook lifecycle management (W3.5)")
    p_hooks.add_argument("action", choices=["test"])
    p_hooks.add_argument("--hook", default=None)
    p_hooks.add_argument("--repo-root", default=None)

    p_recover = sub.add_parser("recover", help="Checkpoint recovery (W3.5)")
    p_recover.add_argument("--repo-root", default=None)
    p_recover.add_argument("--resume", action="store_true")
    p_recover.add_argument("--json", action="store_true", dest="as_json")

    p_comp = sub.add_parser("completion", help="Shell tab-completion (I2)")
    p_comp.add_argument("shell", choices=["bash", "zsh"])

    p_serve = sub.add_parser("serve", help="Browser-based gate signing server (W4.1)")
    p_serve.add_argument("--port", type=int, default=4000)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--repo-root", default=None)

    p_tenant = sub.add_parser("tenant", help="Product namespace management (W4.2)")
    p_tenant.add_argument(
        "tenant_sub", nargs="?",
        choices=["list", "init", "status"],
        metavar="SUBCOMMAND",
    )
    p_tenant.add_argument(
        "tenant_product_id", nargs="?", default=None, metavar="PRODUCT_ID"
    )
    p_tenant.add_argument("--repo-root", dest="tenant_repo_root", type=Path, default=None)
    p_tenant.add_argument(
        "--json", dest="tenant_as_json", action="store_true", default=False
    )

    p_campaign = sub.add_parser("campaign", help="Multi-repo campaign orchestration (W5.2)")
    p_campaign.add_argument(
        "campaign_action",
        choices=["init", "status", "orchestrate"],
        metavar="ACTION",
    )
    p_campaign.add_argument("--name", default=None, help="Campaign name (init).")
    p_campaign.add_argument("--repos", default=None,
                            help="Comma-separated repo paths (init).")
    p_campaign.add_argument("--campaign-root", dest="campaign_root",
                            default=None, metavar="PATH")
    p_campaign.add_argument("--wave", default=None, help="Wave ID (orchestrate).")
    p_campaign.add_argument("--plan", default=None, help="Plan path (orchestrate).")
    p_campaign.add_argument("--max-concurrent", dest="max_concurrent",
                            type=int, default=4)
    p_campaign.add_argument("--json", dest="as_json",
                            action="store_true", default=False)

    p_data = sub.add_parser("data", help="GDPR data-subject access and erasure (W6.2)")
    p_data.add_argument(
        "data_sub", nargs="?", choices=["export", "purge"], metavar="SUBCOMMAND"
    )
    p_data.add_argument("--subject", default=None, metavar="NAME")
    p_data.add_argument("--reason", default=None, metavar="TEXT")
    p_data.add_argument("--repo-root", dest="data_repo_root", default=None, metavar="PATH")
    p_data.add_argument("--json", dest="data_as_json", action="store_true", default=False)

    p_search = sub.add_parser("search", help="Search the plugin catalog (W4.3)")
    p_search.add_argument("keyword", help="Search keyword.")
    p_search.add_argument("--catalog", default=None, metavar="URL")
    p_search.add_argument("--json", dest="as_json", action="store_true", default=False)

    p_info = sub.add_parser("info", help="Show plugin provenance from catalog (W4.3)")
    p_info.add_argument("name", help="Exact plugin name.")
    p_info.add_argument("--catalog", default=None, metavar="URL")
    p_info.add_argument("--json", dest="as_json", action="store_true", default=False)

    # W8 — Design Pipeline (AMD-CORE-029)
    p_pre_design = sub.add_parser("pre-design", help="PO Brief scoping ceremony (W8.1)")
    p_pre_design.add_argument("--mode", required=True,
                               choices=["Expansion", "Selective Expansion", "Hold Scope", "Reduction"])
    p_pre_design.add_argument("--wave", required=True)
    p_pre_design.add_argument("--author", default="PO")
    p_pre_design.add_argument("--answers", default=None, metavar="JSON")
    p_pre_design.add_argument("--repo-root", default=None, metavar="PATH")
    p_pre_design.add_argument("--json", dest="as_json", action="store_true", default=False)

    p_design = sub.add_parser("design", help="Variant generation, approval, taste iteration (W8.2)")
    p_design.add_argument("subcommand", choices=["explore", "approve", "iterate"])
    p_design.add_argument("--wave", required=True)
    p_design.add_argument("--title", default="Design")
    p_design.add_argument("--count", type=int, default=3)
    p_design.add_argument("--variant", default=None, metavar="PATH")
    p_design.add_argument("--traits", default=None, metavar="JSON")
    p_design.add_argument("--verdict", choices=["approved", "rejected"], default=None)
    p_design.add_argument("--repo-root", default=None, metavar="PATH")
    p_design.add_argument("--json", dest="as_json", action="store_true", default=False)

    p_design_review = sub.add_parser("design-review", help="8-dimension variant rubric (W8.4)")
    p_design_review.add_argument("--variant", required=True, metavar="PATH")
    p_design_review.add_argument("--wave", required=True)
    p_design_review.add_argument("--scores", required=True, metavar="JSON")
    p_design_review.add_argument("--repo-root", default=None, metavar="PATH")
    p_design_review.add_argument("--json", dest="as_json", action="store_true", default=False)

    p_design_html = sub.add_parser("design-html", help="Promote variant to production HTML (W8.5)")
    p_design_html.add_argument("--variant", required=True, metavar="PATH")
    p_design_html.add_argument("--wave", required=True)
    p_design_html.add_argument("--framework", choices=["html", "jsx", "svelte"], default=None)
    p_design_html.add_argument("--repo-root", default=None, metavar="PATH")
    p_design_html.add_argument("--json", dest="as_json", action="store_true", default=False)

    # W9 — Knowledge Brain (AMD-CORE-030)
    p_brain = sub.add_parser("brain", help="Knowledge Brain — put/search/list/prune/export/upgrade (W9)")
    p_brain.add_argument("brain_action", nargs="?",
                         choices=["put", "search", "list", "prune", "export", "upgrade"],
                         metavar="ACTION")
    p_brain.add_argument("brain_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_learn = sub.add_parser("signal-learn", help="Human-facing brain review/search/prune/export (W9)")
    p_learn.add_argument("learn_action", nargs="?",
                         choices=["review", "search", "prune", "export"],
                         metavar="ACTION")
    p_learn.add_argument("learn_args", nargs=argparse.REMAINDER, metavar="ARGS")

    # W10 — Security Sprint (AMD-CORE-031)
    p_cso = sub.add_parser("signal-cso", help="Security Chief Officer — OWASP+STRIDE, canary, injection scan (W10)")
    p_cso.add_argument("cso_action", nargs="?", metavar="ACTION")
    p_cso.add_argument("cso_args", nargs=argparse.REMAINDER, metavar="ARGS")

    # W11 — Velocity Primitives (AMD-CORE-032)
    p_autoplan = sub.add_parser("signal-autoplan", help="Auto-generate PLAN tasks from a feature description (W11)")
    p_autoplan.add_argument("autoplan_action", nargs="?", metavar="ACTION")
    p_autoplan.add_argument("autoplan_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_ctx_restore = sub.add_parser("signal-context-restore", help="Checkpoint save/restore + doc drift (W11)")
    p_ctx_restore.add_argument("restore_action", nargs="?", metavar="ACTION")
    p_ctx_restore.add_argument("restore_args", nargs=argparse.REMAINDER, metavar="ARGS")

    # W12 — Post-Deploy Lifecycle (AMD-CORE-033)
    p_setup_deploy = sub.add_parser("signal-setup-deploy", help="Set up a deployment record (W12)")
    p_setup_deploy.add_argument("deploy_wave", nargs="?", metavar="WAVE")
    p_setup_deploy.add_argument("deploy_stage", nargs="?", metavar="STAGE")
    p_setup_deploy.add_argument("deploy_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_land_deploy = sub.add_parser("signal-land-deploy", help="Mark a deployment as landed (W12)")
    p_land_deploy.add_argument("land_deploy_id", nargs="?", metavar="DEPLOY_ID")
    p_land_deploy.add_argument("land_deploy_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_canary_deploy = sub.add_parser("signal-canary-deploy", help="Post-deploy canary check (W12)")
    p_canary_deploy.add_argument("canary_deploy_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_benchmark = sub.add_parser("signal-benchmark", help="Record Core Web Vitals benchmark (W12)")
    p_benchmark.add_argument("benchmark_url", nargs="?", metavar="URL")
    p_benchmark.add_argument("benchmark_args", nargs=argparse.REMAINDER, metavar="ARGS")

    # W13 — DevEx + Global Retro (AMD-CORE-034)
    p_devex_plan = sub.add_parser("signal-devex-plan", help="Plan DevEx work in EXPANSION/POLISH/TRIAGE modes (W13)")
    p_devex_plan.add_argument("devex_plan_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_devex = sub.add_parser("signal-devex", help="Record a DevEx metric (W13)")
    p_devex.add_argument("devex_metric", nargs="?", metavar="METRIC")
    p_devex.add_argument("devex_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_retro_global = sub.add_parser("signal-retro-global", help="Cross-product retrospective brain query (W13)")
    p_retro_global.add_argument("retro_query", nargs="?", metavar="QUERY")
    p_retro_global.add_argument("retro_args", nargs=argparse.REMAINDER, metavar="ARGS")

    # W14 — Safety Gates (AMD-CORE-035)
    p_careful = sub.add_parser("signal-careful", help="Enable/disable/check careful mode (W14)")
    p_careful.add_argument("careful_action", nargs="?", metavar="ACTION")
    p_careful.add_argument("careful_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_freeze = sub.add_parser("signal-freeze", help="Lock a directory against writes (W14)")
    p_freeze.add_argument("freeze_target", nargs="?", metavar="TARGET")
    p_freeze.add_argument("freeze_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_guard = sub.add_parser("signal-guard", help="Check if a directory is frozen (W14)")
    p_guard.add_argument("guard_target", nargs="?", metavar="TARGET")
    p_guard.add_argument("guard_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_unfreeze = sub.add_parser("signal-unfreeze", help="Remove a directory freeze (W14)")
    p_unfreeze.add_argument("unfreeze_target", nargs="?", metavar="TARGET")
    p_unfreeze.add_argument("unfreeze_args", nargs=argparse.REMAINDER, metavar="ARGS")

    # W15 — Second Opinion + Debugging (AMD-CORE-036)
    p_so = sub.add_parser("signal-second-opinion", help="Request independent cross-model review (W15)")
    p_so.add_argument("so_subject", nargs="?", metavar="SUBJECT")
    p_so.add_argument("so_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_so_record = sub.add_parser("signal-second-opinion-record", help="Record verdict on a second-opinion request (W15)")
    p_so_record.add_argument("so_record_id", nargs="?", metavar="OPINION_ID")
    p_so_record.add_argument("so_record_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_inv = sub.add_parser("signal-investigate", help="Iron-law systematic debugging protocol (W15)")
    p_inv.add_argument("inv_action", nargs="?", metavar="ACTION")
    p_inv.add_argument("inv_args", nargs=argparse.REMAINDER, metavar="ARGS")

    # W16 — Session Preamble Resolver (AMD-CORE-037)
    p_preamble = sub.add_parser("session-preamble",
        help="Resolve {{...}} vars in signalos-preamble.mdc and write .signalos/session-preamble.md (W16)")
    p_preamble.add_argument("preamble_args", nargs=argparse.REMAINDER, metavar="ARGS")

    # b1 release — bootstrap a new SignalOS project (no clone required)
    p_init = sub.add_parser("init",
        help="Bootstrap a new SignalOS project at <PATH>")
    p_init.add_argument("init_args", nargs=argparse.REMAINDER, metavar="ARGS")

    # W7 Sprint QA — Browser-driven scenario suite (gating + non-gating)
    p_qa = sub.add_parser("signal-qa",
        help="Run gating QA scenario suite (Gate 5 entry) (W7)")
    p_qa.add_argument("--scenarios", default="core/governance/QA/scenarios/*.yaml",
                      metavar="GLOB", help="Scenario YAML glob")
    p_qa.add_argument("--regressions", default="core/governance/QA/regressions/*.yaml",
                      metavar="GLOB", help="Regression scenario YAML glob")
    p_qa.add_argument("--wave", default="unknown", metavar="WAVE",
                      help="Wave ID for evidence front-matter")
    p_qa.add_argument("--out", dest="qa_out", default=None, metavar="PATH",
                      help="Override evidence JSON output path")
    p_qa.add_argument("--no-vitals", dest="qa_vitals", action="store_false",
                      help="Disable Web Vitals capture")
    p_qa.add_argument("--quiet", dest="qa_verbose", action="store_false",
                      help="Suppress per-scenario stdout")

    p_qa_only = sub.add_parser("signal-qa-only",
        help="Run non-gating QA scenarios without updating QUALITY_CHECK.md (W7)")
    p_qa_only.add_argument("--scenarios", default="core/governance/QA/scenarios/*.yaml",
                           metavar="GLOB", help="Scenario YAML glob")
    p_qa_only.add_argument("--regressions", default=None, metavar="GLOB",
                           help="Optional regression scenario YAML glob")
    p_qa_only.add_argument("--wave", default="unknown", metavar="WAVE")
    p_qa_only.add_argument("--out", dest="qa_out", default=None, metavar="PATH")
    p_qa_only.add_argument("--no-vitals", dest="qa_vitals", action="store_false")
    p_qa_only.add_argument("--quiet", dest="qa_verbose", action="store_false")

    return parser


def _dispatch_daemon(args: argparse.Namespace) -> int:
    root = Path(args.repo_root) if args.repo_root else _repo_root()
    deliver = root / "core" / "execution" / "deliver.sh"
    if not deliver.exists():
        sys.stderr.write(f"signalos daemon: deliver.sh not found at {deliver}\n")
        return 2
    cmd = ["bash", str(deliver), args.action]
    if args.action == "start":
        cmd += ["--mode", args.mode]
        if args.wave:
            cmd += ["--wave", args.wave]
        cmd += ["--repo-root", str(root), "--poll-interval", str(args.poll_interval)]
    try:
        return subprocess.run(cmd, cwd=str(root)).returncode
    except KeyboardInterrupt:
        return 0


def _dispatch_worktree(args: argparse.Namespace) -> int:
    root = Path(args.repo_root) if args.repo_root else _repo_root()
    wm = root / "core" / "execution" / "build" / "worktree-manager.sh"
    if not wm.exists():
        sys.stderr.write(f"signalos worktree: worktree-manager.sh not found at {wm}\n")
        return 2
    cmd = ["bash", str(wm), args.action]
    if args.wave:
        cmd += ["--wave", args.wave]
    if args.plan:
        cmd += ["--plan", args.plan]
    cmd += ["--repo-root", str(root), "--max-concurrent", str(args.max_concurrent)]
    return subprocess.run(cmd, cwd=str(root)).returncode


def _dispatch_signal_qa(args: argparse.Namespace, gating: bool) -> int:
    from signalos_lib.qa_runner import run_scenario_suite, SCENARIO_FAIL
    pack = run_scenario_suite(
        scenario_pattern=args.scenarios,
        regression_pattern=args.regressions,
        wave=args.wave,
        output_path=args.qa_out,
        capture_vitals=getattr(args, "qa_vitals", True),
        gating=gating,
        verbose=getattr(args, "qa_verbose", True),
    )
    return 1 if pack.fail_count > 0 else 0


def _dispatch_completion(args: argparse.Namespace) -> int:
    root = _repo_root()
    comp_dir = root / "cli" / "completions"
    comp_file = comp_dir / f"signalos-completion.{args.shell}"
    if comp_file.exists():
        sys.stdout.write(comp_file.read_text(encoding="utf-8"))
        return 0
    sys.stderr.write(
        f"signalos completion: {args.shell} completion not installed.\n"
        f"Run: install.sh --project-root <path> to generate completions.\n"
    )
    return 2


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args, remainder = parser.parse_known_args(argv[1:])

    if args.command is None:
        parser.print_help(sys.stderr)
        return 1

    cmd = args.command

    if cmd == "daemon":
        return _dispatch_daemon(args)
    if cmd == "worktree":
        return _dispatch_worktree(args)
    if cmd == "completion":
        return _dispatch_completion(args)
    if cmd == "signal-qa":
        return _dispatch_signal_qa(args, gating=True)
    if cmd == "signal-qa-only":
        return _dispatch_signal_qa(args, gating=False)

    rest = remainder

    if cmd == "session":
        from signalos_lib.commands import session as m
        action = [args.action] + ([args.session_id] if args.session_id else [])
        if getattr(args, "product", None) and args.action == "list":
            action += ["--product", args.product]
        return m.main(action)
    if cmd == "pause":
        from signalos_lib.commands import pause as m
        action = [args.action] + ([args.step_id] if args.step_id else [])
        return m.main(action + rest)
    if cmd == "harness":
        from signalos_lib.commands import harness as m
        extra: list[str] = [args.action]
        if args.step_id:
            extra += ["--step", args.step_id]
        if args.model:
            extra += ["--model", args.model]
        if args.provider:
            extra += ["--provider", args.provider]
        if args.cwd:
            extra += ["--cwd", args.cwd]
        return m.main(extra)
    if cmd in ("install", "verify", "list", "uninstall", "publish"):
        from signalos_lib.commands import registry as m
        extra = [cmd]
        if hasattr(args, "target") and args.target:
            extra += [args.target]
        if hasattr(args, "dir") and args.dir:
            extra += [args.dir]
        if hasattr(args, "key") and args.key:
            extra += ["--key", args.key]
        if hasattr(args, "out") and args.out:
            extra += ["--out", args.out]
        if hasattr(args, "allow_unsigned") and args.allow_unsigned:
            extra += ["--allow-unsigned"]
        if cmd == "publish" and getattr(args, "catalog_path", None):
            extra += ["--update-catalog", args.catalog_path]
        return m.main(extra)
    if cmd == "context":
        from signalos_lib.commands import context as m
        from signalos_lib import deprecated_warning
        extra = [args.action]
        if args.target:
            extra += [args.target]
        if args.scope:
            extra += ["--scope", args.scope]
        if args.turn is not None:
            deprecated_warning("--turn", "3.0", "--scope")
            # --turn was never forwarded to commands/context.py; drop silently.
        return m.main(extra + rest)
    if cmd == "orchestrate":
        from signalos_lib.commands import orchestrate as m
        extra = ["--wave", args.wave, "--plan", args.plan]
        if args.session_id:
            extra += ["--session-id", args.session_id]
        if args.repo_root:
            extra += ["--repo-root", args.repo_root]
        extra += ["--max-concurrent", str(args.max_concurrent)]
        if args.provider:
            extra += ["--provider", args.provider]
        return m.main(extra)
    if cmd == "status":
        from signalos_lib.commands import status as m
        extra = []
        if args.repo_root:
            extra += ["--repo-root", str(args.repo_root)]
        if args.as_json:
            extra += ["--json"]
        if args.watch:
            extra += ["--watch"]
        if args.interval != 2.0:
            extra += ["--interval", str(args.interval)]
        if getattr(args, "product", None):
            extra += ["--product", args.product]
        return m.main(extra)
    if cmd == "sign":
        from signalos_lib.commands import sign as m
        extra: list[str] = [args.gate]
        if args.check:
            extra += ["--check"]
        if args.signer:
            extra += ["--signer", args.signer]
        if args.role:
            extra += ["--role", args.role]
        if args.verdict:
            extra += ["--verdict", args.verdict]
        if args.conditions:
            extra += ["--conditions", args.conditions]
        if args.repo_root:
            extra += ["--repo-root", str(args.repo_root)]
        if getattr(args, "oidc", False):
            extra += ["--oidc"]
        return m.main(extra)
    if cmd == "intent":
        from signalos_lib.commands import intent as m
        extra = []
        if args.phrase:
            extra += [args.phrase]
        if args.all:
            extra += ["--all"]
        if args.as_json:
            extra += ["--json"]
        if args.threshold is not None:
            extra += ["--threshold", str(args.threshold)]
        return m.main(extra)
    if cmd == "plan":
        from signalos_lib.commands import plan as m
        extra = [args.action]
        if args.input:
            extra += ["--input", args.input]
        if args.output:
            extra += ["--output", args.output]
        if args.status:
            extra += ["--status", args.status]
        if args.as_json:
            extra += ["--json"]
        return m.main(extra)
    if cmd == "health":
        from signalos_lib.commands import health as m
        extra = []
        if args.repo_root:
            extra += ["--repo-root", args.repo_root]
        if args.as_json:
            extra += ["--json"]
        return m.main(extra)
    if cmd == "diagnose":
        from signalos_lib.commands import diagnose as m
        extra = []
        if args.repo_root:
            extra += ["--repo-root", args.repo_root]
        if args.wave:
            extra += ["--wave", args.wave]
        if args.output:
            extra += ["--output", args.output]
        if args.as_json:
            extra += ["--json"]
        return m.main(extra)
    if cmd == "validate":
        from signalos_lib.commands import validate_cmd as m
        extra = []
        if args.repo_root:
            extra += ["--repo-root", args.repo_root]
        if args.validator:
            extra += ["--validator", args.validator]
        if args.as_json:
            extra += ["--json"]
        return m.main(extra)
    if cmd == "hooks":
        from signalos_lib.commands import hooks as m
        extra = [args.action]
        if args.hook:
            extra += ["--hook", args.hook]
        if args.repo_root:
            extra += ["--repo-root", args.repo_root]
        return m.main(extra)
    if cmd == "recover":
        from signalos_lib.commands import recover as m
        extra = []
        if args.repo_root:
            extra += ["--repo-root", args.repo_root]
        if args.resume:
            extra += ["--resume"]
        if args.as_json:
            extra += ["--json"]
        return m.main(extra)

    if cmd == "serve":
        from signalos_lib.commands import serve as m
        return m.run(args)

    if cmd == "campaign":
        from signalos_lib.commands import campaign as m
        action = args.campaign_action
        extra: list[str] = [action]
        if action == "init":
            if getattr(args, "name", None):
                extra += ["--name", args.name]
            if getattr(args, "repos", None):
                extra += ["--repos", args.repos]
        if action in ("status", "orchestrate"):
            if getattr(args, "as_json", False):
                extra += ["--json"]
        if action == "orchestrate":
            if getattr(args, "wave", None):
                extra += ["--wave", args.wave]
            if getattr(args, "plan", None):
                extra += ["--plan", args.plan]
            extra += ["--max-concurrent", str(args.max_concurrent)]
        if getattr(args, "campaign_root", None):
            extra += ["--campaign-root", args.campaign_root]
        return m.main(extra)

    if cmd == "search":
        from signalos_lib.commands.catalog import cmd_search
        extra = [args.keyword]
        if getattr(args, "catalog", None):
            extra += ["--catalog", args.catalog]
        if getattr(args, "as_json", False):
            extra += ["--json"]
        return cmd_search(extra)

    if cmd == "info":
        from signalos_lib.commands.catalog import cmd_info
        extra = [args.name]
        if getattr(args, "catalog", None):
            extra += ["--catalog", args.catalog]
        if getattr(args, "as_json", False):
            extra += ["--json"]
        return cmd_info(extra)

    if cmd == "data":
        from signalos_lib.commands import data_privacy as m
        sub_argv: list[str] = []
        if getattr(args, "data_sub", None):
            sub_argv.append(args.data_sub)
        if getattr(args, "subject", None):
            sub_argv += ["--subject", args.subject]
        if getattr(args, "reason", None):
            sub_argv += ["--reason", args.reason]
        if getattr(args, "data_repo_root", None):
            sub_argv += ["--repo-root", args.data_repo_root]
        if getattr(args, "data_as_json", False):
            sub_argv.append("--json")
        return m.main(sub_argv)

    if cmd == "tenant":
        from signalos_lib.commands import tenant as m
        sub_argv: list[str] = []
        if getattr(args, "tenant_sub", None):
            sub_argv.append(args.tenant_sub)
        if getattr(args, "tenant_product_id", None):
            sub_argv.append(args.tenant_product_id)
        if getattr(args, "tenant_repo_root", None):
            sub_argv += ["--repo-root", str(args.tenant_repo_root)]
        if getattr(args, "tenant_as_json", False):
            sub_argv.append("--json")
        return m.main(sub_argv)

    # W8 — Design Pipeline (AMD-CORE-029)
    if cmd == "pre-design":
        from signalos_lib.commands.design import cmd_pre_design
        extra = ["--mode", args.mode, "--wave", args.wave, "--author", args.author]
        if getattr(args, "answers", None):
            extra += ["--answers", args.answers]
        if getattr(args, "repo_root", None):
            extra += ["--repo-root", args.repo_root]
        if getattr(args, "as_json", False):
            extra += ["--json"]
        return cmd_pre_design(extra)

    if cmd == "design":
        from signalos_lib.commands.design import cmd_design
        extra = [args.subcommand, "--wave", args.wave]
        if getattr(args, "title", None):
            extra += ["--title", args.title]
        if getattr(args, "count", None) is not None:
            extra += ["--count", str(args.count)]
        if getattr(args, "variant", None):
            extra += ["--variant", args.variant]
        if getattr(args, "traits", None):
            extra += ["--traits", args.traits]
        if getattr(args, "verdict", None):
            extra += ["--verdict", args.verdict]
        if getattr(args, "repo_root", None):
            extra += ["--repo-root", args.repo_root]
        if getattr(args, "as_json", False):
            extra += ["--json"]
        return cmd_design(extra)

    if cmd == "design-review":
        from signalos_lib.commands.design import cmd_design_review
        extra = ["--variant", args.variant, "--wave", args.wave, "--scores", args.scores]
        if getattr(args, "repo_root", None):
            extra += ["--repo-root", args.repo_root]
        if getattr(args, "as_json", False):
            extra += ["--json"]
        return cmd_design_review(extra)

    if cmd == "design-html":
        from signalos_lib.commands.design import cmd_design_html
        extra = ["--variant", args.variant, "--wave", args.wave]
        if getattr(args, "framework", None):
            extra += ["--framework", args.framework]
        if getattr(args, "repo_root", None):
            extra += ["--repo-root", args.repo_root]
        if getattr(args, "as_json", False):
            extra += ["--json"]
        return cmd_design_html(extra)

    # W9 — Knowledge Brain (AMD-CORE-030)
    if cmd == "brain":
        from signalos_lib.commands.brain import cmd_brain
        sub_argv: list[str] = []
        if getattr(args, "brain_action", None):
            sub_argv.append(args.brain_action)
        if getattr(args, "brain_args", None):
            sub_argv += list(args.brain_args)
        return cmd_brain(sub_argv)

    if cmd == "signal-learn":
        from signalos_lib.commands.brain import cmd_signal_learn
        sub_argv = []
        if getattr(args, "learn_action", None):
            sub_argv.append(args.learn_action)
        if getattr(args, "learn_args", None):
            sub_argv += list(args.learn_args)
        return cmd_signal_learn(sub_argv)

    # W10 — Security Sprint (AMD-CORE-031)
    if cmd == "signal-cso":
        from signalos_lib.commands.security import cmd_signal_cso
        sub_argv = []
        if getattr(args, "cso_action", None):
            sub_argv.append(args.cso_action)
        if getattr(args, "cso_args", None):
            sub_argv += list(args.cso_args)
        return cmd_signal_cso(sub_argv)

    # W11 — Velocity Primitives (AMD-CORE-032)
    if cmd == "signal-autoplan":
        from signalos_lib.commands.velocity import cmd_signal_autoplan
        sub_argv = []
        if getattr(args, "autoplan_action", None):
            sub_argv.append(args.autoplan_action)
        if getattr(args, "autoplan_args", None):
            sub_argv += list(args.autoplan_args)
        return cmd_signal_autoplan(sub_argv)

    if cmd == "signal-context-restore":
        from signalos_lib.commands.velocity import cmd_signal_context_restore
        sub_argv = []
        if getattr(args, "restore_action", None):
            sub_argv.append(args.restore_action)
        if getattr(args, "restore_args", None):
            sub_argv += list(args.restore_args)
        return cmd_signal_context_restore(sub_argv)

    # W12 — Post-Deploy Lifecycle (AMD-CORE-033)
    if cmd == "signal-setup-deploy":
        from signalos_lib.commands.deploy import cmd_signal_setup_deploy
        sub_argv = []
        if getattr(args, "deploy_wave", None):
            sub_argv.append(args.deploy_wave)
        if getattr(args, "deploy_stage", None):
            sub_argv.append(args.deploy_stage)
        if getattr(args, "deploy_args", None):
            sub_argv += list(args.deploy_args)
        return cmd_signal_setup_deploy(sub_argv)

    if cmd == "signal-land-deploy":
        from signalos_lib.commands.deploy import cmd_signal_land_deploy
        sub_argv = []
        if getattr(args, "land_deploy_id", None):
            sub_argv.append(args.land_deploy_id)
        if getattr(args, "land_deploy_args", None):
            sub_argv += list(args.land_deploy_args)
        return cmd_signal_land_deploy(sub_argv)

    if cmd == "signal-canary-deploy":
        from signalos_lib.commands.deploy import cmd_signal_canary_deploy
        # parse_known_args strips leading --flags into `rest`; prepend to reconstruct full argv
        sub_argv = list(rest) + list(getattr(args, "canary_deploy_args", None) or [])
        return cmd_signal_canary_deploy(sub_argv)

    if cmd == "signal-benchmark":
        from signalos_lib.commands.deploy import cmd_signal_benchmark
        sub_argv = []
        if getattr(args, "benchmark_url", None):
            sub_argv.append(args.benchmark_url)
        if getattr(args, "benchmark_args", None):
            sub_argv += list(args.benchmark_args)
        return cmd_signal_benchmark(sub_argv)

    # W13 — DevEx + Global Retro (AMD-CORE-034)
    if cmd == "signal-devex-plan":
        from signalos_lib.commands.devex import cmd_signal_devex_plan
        # parse_known_args strips leading --flags into `rest`; prepend to reconstruct full argv
        sub_argv = list(rest) + list(getattr(args, "devex_plan_args", None) or [])
        return cmd_signal_devex_plan(sub_argv)

    if cmd == "signal-devex":
        from signalos_lib.commands.devex import cmd_signal_devex
        sub_argv = []
        if getattr(args, "devex_metric", None):
            sub_argv.append(args.devex_metric)
        if getattr(args, "devex_args", None):
            sub_argv += list(args.devex_args)
        return cmd_signal_devex(sub_argv)

    if cmd == "signal-retro-global":
        from signalos_lib.commands.devex import cmd_signal_retro_global
        sub_argv = []
        if getattr(args, "retro_query", None):
            sub_argv.append(args.retro_query)
        if getattr(args, "retro_args", None):
            sub_argv += list(args.retro_args)
        return cmd_signal_retro_global(sub_argv)

    # W14 — Safety Gates (AMD-CORE-035)
    if cmd == "signal-careful":
        from signalos_lib.commands.safety import cmd_signal_careful
        sub_argv = []
        if getattr(args, "careful_action", None):
            sub_argv.append(args.careful_action)
        if getattr(args, "careful_args", None):
            sub_argv += list(args.careful_args)
        return cmd_signal_careful(sub_argv)

    if cmd == "signal-freeze":
        from signalos_lib.commands.safety import cmd_signal_freeze
        sub_argv = []
        if getattr(args, "freeze_target", None):
            sub_argv.append(args.freeze_target)
        if getattr(args, "freeze_args", None):
            sub_argv += list(args.freeze_args)
        return cmd_signal_freeze(sub_argv)

    if cmd == "signal-guard":
        from signalos_lib.commands.safety import cmd_signal_guard
        sub_argv = []
        if getattr(args, "guard_target", None):
            sub_argv.append(args.guard_target)
        if getattr(args, "guard_args", None):
            sub_argv += list(args.guard_args)
        return cmd_signal_guard(sub_argv)

    if cmd == "signal-unfreeze":
        from signalos_lib.commands.safety import cmd_signal_unfreeze
        sub_argv = []
        if getattr(args, "unfreeze_target", None):
            sub_argv.append(args.unfreeze_target)
        if getattr(args, "unfreeze_args", None):
            sub_argv += list(args.unfreeze_args)
        return cmd_signal_unfreeze(sub_argv)

    # W15 — Second Opinion + Debugging (AMD-CORE-036)
    if cmd == "signal-second-opinion":
        from signalos_lib.commands.second_opinion import cmd_signal_second_opinion
        sub_argv = []
        if getattr(args, "so_subject", None):
            sub_argv.append(args.so_subject)
        if getattr(args, "so_args", None):
            sub_argv += list(args.so_args)
        return cmd_signal_second_opinion(sub_argv)

    if cmd == "signal-second-opinion-record":
        from signalos_lib.commands.second_opinion import cmd_signal_second_opinion_record
        sub_argv = []
        if getattr(args, "so_record_id", None):
            sub_argv.append(args.so_record_id)
        if getattr(args, "so_record_args", None):
            sub_argv += list(args.so_record_args)
        return cmd_signal_second_opinion_record(sub_argv)

    if cmd == "signal-investigate":
        from signalos_lib.commands.investigate import cmd_signal_investigate
        sub_argv = []
        if getattr(args, "inv_action", None):
            sub_argv.append(args.inv_action)
        if getattr(args, "inv_args", None):
            sub_argv += list(args.inv_args)
        return cmd_signal_investigate(sub_argv)

    # W16 — Session Preamble Resolver (AMD-CORE-037)
    if cmd == "session-preamble":
        from signalos_lib.commands.preamble import cmd_session_preamble
        sub_argv = list(getattr(args, "preamble_args", None) or [])
        return cmd_session_preamble(sub_argv)

    # b1 release — `signalos init <PATH>` bootstraps a new project
    if cmd == "init":
        from signalos_lib.commands import init as m
        return m.main(list(getattr(args, "init_args", None) or []))

    return 1


def main_cli() -> None:  # pragma: no cover
    """Console-script entry point. Wraps main(sys.argv) with sys.exit.

    Wired into pyproject.toml [project.scripts] as `signalos = "signalos_lib.cli:main_cli"`.
    Pragma: this is a 1-line dispatch shim that's only invoked at process
    boot via the installed console-script; running it from a unit test
    would call sys.exit and abort the test runner.
    """
    sys.exit(main(sys.argv))
