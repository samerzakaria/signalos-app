"""CLI command for ``signalos deliver`` -- full product delivery pipeline."""

from __future__ import annotations

import argparse
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the deliver subcommand."""
    p = subparsers.add_parser("deliver", help="Run the product delivery bridge")
    p.add_argument("--prompt", required=True, help="Product request prompt")
    p.add_argument("--name", default=None, help="Product/repo name")
    p.add_argument("--repo-root", default=None, help="Existing or target repo root")
    p.add_argument("--target-root", default=None, help="Parent folder for greenfield repos")
    p.add_argument(
        "--mode",
        choices=["greenfield", "adopt", "refresh", "auto"],
        default="auto",
    )
    p.add_argument("--profile", default="auto")
    p.add_argument("--blueprint", default="auto")
    p.add_argument(
        "--deploy",
        choices=["none", "prepare", "live"],
        default="none",
    )
    p.add_argument("--yes", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-repair-cycles", type=int, default=3)
    p.add_argument(
        "--agent",
        choices=["none", "packet-only", "orchestrator", "auto"],
        default="none",
    )
    p.add_argument("--json", action="store_true", dest="as_json")
    return p


def cmd_deliver_intent(args: argparse.Namespace) -> int:
    """Preview intent extraction without running full delivery."""
    import json as _json

    from ..product.intent import extract_product_intent
    from ..product.questions import generate_questions
    from ..product.assumptions import record_assumptions
    from ..product.blueprints.registry import match_blueprint
    from ..product.design import build_design_system

    intent = extract_product_intent(args.prompt)
    if args.name:
        intent["product_name"] = args.name

    questions = generate_questions(intent)
    assumptions = record_assumptions(intent)
    blueprint_id = match_blueprint(intent)

    payload = {
        "intent": intent,
        "questions": questions,
        "assumptions": assumptions,
        "blueprint_id": blueprint_id,
    }

    if getattr(args, "as_json", True):
        _json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        print()

    return 0


def cmd_deliver_design(args: argparse.Namespace) -> int:
    """Preview design decisions without running full delivery."""
    import json as _json

    from ..product.intent import extract_product_intent
    from ..product.blueprints.registry import load_blueprint, match_blueprint
    from ..product.design import build_design_system, get_design_dependencies

    intent = extract_product_intent(args.prompt)
    if args.name:
        intent["product_name"] = args.name

    profile = args.profile if args.profile != "auto" else "react-vite"
    blueprint_id = match_blueprint(intent)
    bp = load_blueprint(blueprint_id) if blueprint_id else None
    design = build_design_system(intent, profile, bp)
    deps = get_design_dependencies(design)

    payload = {
        "design": design,
        "dependencies": deps,
        "blueprint_id": blueprint_id,
        "profile": profile,
    }

    if getattr(args, "as_json", True):
        _json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        print()

    return 0


def cmd_deliver_design_preview(args: argparse.Namespace) -> int:
    """Generate and return design preview HTML path."""
    import json as _json

    from ..product.intent import extract_product_intent
    from ..product.blueprints.registry import load_blueprint, match_blueprint
    from ..product.design import build_design_system
    from ..product.design_preview import generate_design_preview_html

    intent = extract_product_intent(args.prompt)
    if args.name:
        intent["product_name"] = args.name

    profile = args.profile if args.profile != "auto" else "react-vite"
    blueprint_id = match_blueprint(intent)
    bp = load_blueprint(blueprint_id) if blueprint_id else None
    design = build_design_system(intent, profile, bp)

    preview_html = generate_design_preview_html(design, intent)

    # Write to .signalos/product/design-preview.html
    from pathlib import Path
    repo_root = Path(args.repo_root) if getattr(args, "repo_root", None) else Path.cwd()
    signalos_dir = repo_root / ".signalos"
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    preview_path = product_dir / "design-preview.html"
    preview_path.write_text(preview_html, encoding="utf-8")

    payload = {
        "preview_path": str(preview_path),
        "preview_html": preview_html,
    }

    if getattr(args, "as_json", True):
        _json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        print()

    return 0


def cmd_deliver(args: argparse.Namespace) -> int:
    """Execute the deliver command."""
    import json as _json

    from ..product.delivery import run_delivery

    def _run() -> dict:
        return run_delivery(
            prompt=args.prompt,
            name=args.name,
            repo_root=Path(args.repo_root) if args.repo_root else None,
            target_root=Path(args.target_root) if args.target_root else None,
            mode=args.mode,
            profile=args.profile,
            blueprint=args.blueprint,
            deploy=args.deploy,
            yes=args.yes,
            dry_run=args.dry_run,
            max_repair_cycles=args.max_repair_cycles,
            agent_mode=args.agent,
            json_output=args.as_json,
        )

    if args.as_json:
        captured = StringIO()
        with redirect_stdout(captured):
            closeout = _run()
        _json.dump(closeout, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        closeout = _run()

    level = closeout.get("closure_level", "unknown")
    if not args.as_json:
        print(f"\nDelivery complete: {level}")
        print(f"Repo: {closeout.get('repo_path', 'unknown')}")
        if closeout.get("how_to_run"):
            print("\nHow to run:")
            for step in closeout["how_to_run"]:
                print(f"  {step}")

    return 0 if level in ("ready", "verified") else 1
