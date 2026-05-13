# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/commands/intent.py
# W3.3 — signalos intent subcommand (AMD-CORE-016)

from __future__ import annotations

__all__ = ["main"]

import json
import sys


def main(argv: list[str]) -> int:
    import argparse
    from signalos_lib.intent import CONFIDENCE_THRESHOLD, route_or_clarify, classify

    parser = argparse.ArgumentParser(
        prog="signalos intent",
        description=(
            "Route a free-form phrase to the matching signalos command (W3.3, AMD-CORE-016).\n"
            "Pure stdlib — no LLM call on the routing path.\n\n"
            "Exit codes:\n"
            "  0 — high-confidence route (confidence >= {threshold})\n"
            "  1 — low confidence; clarifying question printed\n"
            "  2 — no phrase provided / usage error"
        ).format(threshold=CONFIDENCE_THRESHOLD),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "phrase",
        nargs="?",
        default=None,
        help='Free-form intent phrase, e.g. "I want to add a payment feature"',
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show scores for all intents, not just the top match.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON instead of the human card.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=CONFIDENCE_THRESHOLD,
        metavar="FLOAT",
        help=f"Confidence threshold (default {CONFIDENCE_THRESHOLD}).",
    )

    args = parser.parse_args(argv)

    if not args.phrase:
        parser.print_help(sys.stderr)
        return 2

    phrase = args.phrase.strip()
    if not phrase:
        sys.stderr.write("signalos intent: phrase must not be empty\n")
        return 2

    result = route_or_clarify(phrase)
    # Override threshold if user passed --threshold
    if args.threshold != CONFIDENCE_THRESHOLD:
        from signalos_lib.intent import classify
        ranked = classify(phrase)
        best = ranked[0]
        routed = best.confidence >= args.threshold
        result = dict(result, routed=routed, confidence=round(best.confidence, 3))
        if not routed:
            result["clarify"] = best.clarify

    if args.as_json:
        if args.all:
            from signalos_lib.intent import classify
            all_matches = classify(phrase)
            result["all"] = [
                {"intent": m.name, "confidence": round(m.confidence, 3), "command": m.command}
                for m in all_matches
            ]
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        return 0 if result["routed"] else 1

    # Human-readable card
    _render_card(result, phrase, args)
    return 0 if result["routed"] else 1


def _render_card(result: dict, phrase: str, args) -> None:
    from signalos_lib.intent import classify, CONFIDENCE_THRESHOLD

    bar_width = 24
    routed = result["routed"]
    conf = result["confidence"]
    filled = int(conf * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    conf_pct = f"{conf * 100:.0f}%"
    threshold_marker = int(CONFIDENCE_THRESHOLD * bar_width)

    icon = "✓" if routed else "?"

    print()
    print(f"  {icon}  intent     {result['intent']}")
    print(f"     confidence [{bar}] {conf_pct}")
    print(f"     command    {result['command']}")

    if not routed:
        print()
        print(f"  ↳  {result['clarify']}")

    if args.all:
        print()
        print("  All intents:")
        for m in classify(phrase):
            b = "█" * int(m.confidence * 12) + "░" * (12 - int(m.confidence * 12))
            marker = " ← routed" if m.name == result["intent"] else ""
            print(f"     {m.name:<12} [{b}] {m.confidence * 100:4.0f}%{marker}")

    print()
