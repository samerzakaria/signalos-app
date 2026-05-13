# SignalOS Core — W8 Design Pipeline
# cli/signalos_lib/commands/design.py
# AMD-CORE-029
#
# CLI dispatch for: pre-design, design, design-review, design-html

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.design import (
    FORCING_QUESTIONS,
    REVIEW_DIMENSIONS,
    PreDesignMode,
    PoBrief,
    append_decision_dna,
    check_design_reviewed,
    check_po_brief_signed,
    decay_weight,
    detect_framework,
    generate_po_brief,
    generate_production_html,
    generate_variants,
    load_taste_context,
    record_taste,
    review_variant,
)


# ---------------------------------------------------------------------------
# Repo-root resolution
# ---------------------------------------------------------------------------

def _find_repo_root(start: Path | None = None) -> Path:
    cwd = start or Path.cwd()
    for p in [cwd, *cwd.parents]:
        if (p / ".signalos").exists() or (p / "core" / "governance").exists():
            return p
    return cwd


# ---------------------------------------------------------------------------
# /signal-pre-design
# ---------------------------------------------------------------------------

def cmd_pre_design(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos pre-design",
        description="W8.1 — PO Brief generator. Asks 6 forcing questions and writes PO_BRIEF.md.",
    )
    p.add_argument("--mode", choices=[m.value for m in PreDesignMode],
                   required=True, help="Design scope mode")
    p.add_argument("--wave", required=True, help="Wave number (e.g. 08)")
    p.add_argument("--author", default="PO", help="Author name / email")
    p.add_argument("--answers", metavar="JSON",
                   help="JSON object mapping question text → answer (skips interactive prompts)")
    p.add_argument("--repo-root", metavar="PATH",
                   help="Override repo root path")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Print result as JSON")
    args = p.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else _find_repo_root()
    mode = PreDesignMode(args.mode)

    # Collect answers
    if args.answers:
        try:
            raw_answers: dict[str, str] = json.loads(args.answers)
        except json.JSONDecodeError as exc:
            print(f"error: --answers is not valid JSON: {exc}", file=sys.stderr)
            return 2
    else:
        # Interactive: print each question and prompt
        raw_answers = {}
        print(f"\n/signal-pre-design  Wave {args.wave}  Mode: {mode.value}\n")
        print("Answer the 6 forcing questions:\n")
        for i, q in enumerate(FORCING_QUESTIONS, 1):
            print(f"Q{i}: {q}")
            raw_answers[q] = input("→ ").strip()
            print()

    brief = PoBrief(
        wave=args.wave,
        mode=mode,
        answers=raw_answers,
        authored_by=args.author,
    )

    out_path = generate_po_brief(brief, repo_root)

    # Auto-log to DECISION-DNA
    append_decision_dna(
        repo_root=repo_root,
        decision=f"Design scope set to {mode.value} for Wave {args.wave}",
        rationale=raw_answers.get(FORCING_QUESTIONS[0], "See PO_BRIEF.md for full rationale."),
        author=args.author,
        wave=args.wave,
        artifact="core/strategy/PO_BRIEF.md",
    )

    if args.as_json:
        print(json.dumps({
            "wave": args.wave,
            "mode": mode.value,
            "path": str(out_path),
            "status": "written",
        }))
    else:
        print(f"\n✓  PO_BRIEF.md written → {out_path.relative_to(repo_root)}")
        print(f"   Mode: {mode.value}")
        print(f"   Next: sign the brief with  signalos sign G3  then run  /signal-design explore\n")
    return 0


# ---------------------------------------------------------------------------
# /signal-design (explore / approve / iterate)
# ---------------------------------------------------------------------------

def cmd_design(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos design",
        description="W8.2 — Design variants. Modes: explore, approve, iterate.",
    )
    p.add_argument("subcommand", choices=["explore", "approve", "iterate"])
    p.add_argument("--wave", required=True, help="Wave number")
    p.add_argument("--title", default="Design", help="Product / feature title for generated HTML")
    p.add_argument("--count", type=int, default=3, help="Variant count (3–5, default 3)")
    p.add_argument("--variant", metavar="PATH",
                   help="Variant HTML file to approve (required for approve/iterate)")
    p.add_argument("--traits", metavar="JSON",
                   help="JSON list of trait strings to record in taste memory")
    p.add_argument("--verdict", choices=["approved", "rejected"],
                   help="Taste verdict for iterate mode")
    p.add_argument("--repo-root", metavar="PATH")
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else _find_repo_root()
    sub = args.subcommand

    # Gate: PO_BRIEF.md must be signed before any design work
    if not check_po_brief_signed(repo_root):
        print("GATE VIOLATION: core/strategy/PO_BRIEF.md is not signed.", file=sys.stderr)
        print("Sign the PO Brief first:  signalos sign G3", file=sys.stderr)
        return 1

    if sub == "explore":
        taste_ctx = load_taste_context(repo_root)
        # Need mode from PO_BRIEF — read it
        brief_path = repo_root / "core" / "strategy" / "PO_BRIEF.md"
        mode_line = next(
            (l for l in brief_path.read_text(encoding="utf-8").splitlines()
             if l.startswith("**") and any(m.value in l for m in PreDesignMode)),
            None,
        )
        mode = PreDesignMode.HOLD_SCOPE
        if mode_line:
            for m in PreDesignMode:
                if m.value in mode_line:
                    mode = m
                    break

        from signalos_lib.design import PoBrief
        brief_obj = PoBrief(wave=args.wave, mode=mode, answers={}, authored_by="PO")
        variants = generate_variants(args.wave, args.title, brief_obj, repo_root,
                                     taste_context=taste_ctx, count=args.count)
        compare_board = repo_root / ".signalos" / "design" / "variants" / f"wave-{args.wave}" / "index.html"
        if args.as_json:
            print(json.dumps({"variants": [str(v.path) for v in variants],
                               "compare_board": str(compare_board)}))
        else:
            print(f"\n✓  Generated {len(variants)} variants in .signalos/design/variants/wave-{args.wave}/")
            for v in variants:
                print(f"   • {v.archetype} — {v.description}")
            print(f"\n   Comparison board → {compare_board.relative_to(repo_root)}")
            print(f"   Next: run  /signal-design-review  on your preferred variant\n")
        return 0

    elif sub == "approve":
        if not args.variant:
            print("error: --variant PATH required for approve", file=sys.stderr)
            return 2
        variant_path = Path(args.variant)
        if not variant_path.exists():
            print(f"error: variant not found: {variant_path}", file=sys.stderr)
            return 2
        # Write approval marker
        review_dir = repo_root / ".signalos" / "design" / "reviews" / f"wave-{args.wave}"
        review_dir.mkdir(parents=True, exist_ok=True)
        approval = {
            "variant": str(variant_path),
            "wave": args.wave,
            "action": "approved",
        }
        (review_dir / "approval.json").write_text(
            json.dumps(approval, indent=2), encoding="utf-8"
        )
        # Auto-log DECISION-DNA
        append_decision_dna(
            repo_root=repo_root,
            decision=f"Design variant approved: {variant_path.name}",
            rationale="Variant selected after /signal-design-review.",
            author="PO",
            wave=args.wave,
            artifact=str(variant_path.relative_to(repo_root) if variant_path.is_relative_to(repo_root) else variant_path),
        )
        if args.as_json:
            print(json.dumps({"approved": str(variant_path), "wave": args.wave}))
        else:
            print(f"\n✓  Variant approved: {variant_path.name}")
            print(f"   Next: run  /signal-design-html  to generate production HTML\n")
        return 0

    else:  # iterate
        if not args.variant:
            print("error: --variant PATH required for iterate", file=sys.stderr)
            return 2
        if not args.verdict:
            print("error: --verdict required for iterate", file=sys.stderr)
            return 2
        traits_raw: list[str] = json.loads(args.traits) if args.traits else ["general"]
        variant_path = Path(args.variant)
        archetype = variant_path.stem.split("-", 2)[-1] if "-" in variant_path.stem else variant_path.stem
        record_taste(repo_root, args.wave, archetype, args.verdict, traits_raw)
        if args.as_json:
            print(json.dumps({"recorded": args.verdict, "archetype": archetype,
                               "traits": traits_raw}))
        else:
            print(f"\n✓  Taste recorded: {args.verdict} · {archetype}")
            print(f"   Traits: {', '.join(traits_raw)}")
            print(f"   Next: run  /signal-design explore  again for a fresh round with updated taste\n")
        return 0


# ---------------------------------------------------------------------------
# /signal-design-review
# ---------------------------------------------------------------------------

def cmd_design_review(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos design-review",
        description="W8.4 — Score a variant against the 8-dimension rubric.",
    )
    p.add_argument("--variant", required=True, metavar="PATH", help="Variant HTML file to review")
    p.add_argument("--wave", required=True)
    p.add_argument("--scores", required=True, metavar="JSON",
                   help='JSON object: {"clarity": 8, "slop": 1, ...}')
    p.add_argument("--repo-root", metavar="PATH")
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else _find_repo_root()
    variant_path = Path(args.variant)
    if not variant_path.exists():
        print(f"error: variant not found: {variant_path}", file=sys.stderr)
        return 2

    try:
        raw_scores: dict[str, float] = {k: float(v) for k, v in json.loads(args.scores).items()}
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"error: --scores is not valid JSON: {exc}", file=sys.stderr)
        return 2

    result = review_variant(variant_path, raw_scores)

    # Persist review to .signalos/design/reviews/wave-{N}/
    review_dir = repo_root / ".signalos" / "design" / "reviews" / f"wave-{args.wave}"
    review_dir.mkdir(parents=True, exist_ok=True)
    review_file = review_dir / f"{variant_path.stem}-review.json"
    review_data = {
        "variant": str(variant_path),
        "wave": args.wave,
        "scores": result.scores,
        "overall": result.overall,
        "passed": result.passed,
        "issues": result.issues,
    }
    review_file.write_text(json.dumps(review_data, indent=2), encoding="utf-8")

    if args.as_json:
        print(json.dumps(review_data))
        return 0 if result.passed else 1

    print(f"\n{'✓  PASS' if result.passed else '✗  FAIL'}  overall {result.overall:.1f}/10")
    print(f"   Variant: {variant_path.name}")
    for dim, label in REVIEW_DIMENSIONS:
        score = result.scores.get(dim, 0.0)
        marker = "✓" if score >= 7.0 else "✗"
        print(f"   {marker} {dim:15s} {score:.1f}")
    if result.issues:
        print("\n   Issues:")
        for issue in result.issues:
            print(f"   ✗ {issue}")
    if not result.passed:
        print("\n   Score < 7.0 — fix issues before running  /signal-design approve\n")
    else:
        print(f"\n   Next: signalos design approve --variant {variant_path} --wave {args.wave}\n")
    return 0 if result.passed else 1


# ---------------------------------------------------------------------------
# /signal-design-html
# ---------------------------------------------------------------------------

def cmd_design_html(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="signalos design-html",
        description="W8.5 — Promote approved variant to production HTML/JSX/Svelte.",
    )
    p.add_argument("--variant", required=True, metavar="PATH")
    p.add_argument("--wave", required=True)
    p.add_argument("--framework", choices=["html", "jsx", "svelte"],
                   help="Override framework detection (html|jsx|svelte)")
    p.add_argument("--repo-root", metavar="PATH")
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else _find_repo_root()
    variant_path = Path(args.variant)
    if not variant_path.exists():
        print(f"error: variant not found: {variant_path}", file=sys.stderr)
        return 2

    framework = args.framework or detect_framework(repo_root)
    out_path = generate_production_html(variant_path, repo_root, args.wave, framework)

    if args.as_json:
        print(json.dumps({"output": str(out_path), "framework": framework, "wave": args.wave}))
    else:
        print(f"\n✓  Production {framework.upper()} written → {out_path.relative_to(repo_root)}")
        print(f"   Framework: {framework}")
        print(f"   Wave: {args.wave}\n")
    return 0
