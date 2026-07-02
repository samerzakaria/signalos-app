"""Belief auto-resolution -- the loop-closer (C-bridge / Wave 3.2).

A shipped product's live telemetry resolves the very hypothesis that justified
building it: Keep (confirmed), Refute (disproven), or Iterate (partial). The
belief is the same one signed at G1 and tagged on telemetry by observability.py,
so idea -> Belief -> Go -> build -> telemetry -> resolution is one thread.

Gate (dev-review): telemetry may only resolve a belief whose success metrics were
SIGNED earlier. Auto-resolving against an unsigned target is refused -- otherwise
a moving goalpost could confirm anything.
"""
from __future__ import annotations

from typing import Any

KEEP, REFUTE, ITERATE = "keep", "refute", "iterate"
UNSIGNED, PENDING = "unsigned", "pending"


def resolve_belief(
    signals: dict[str, float],
    thresholds: dict[str, float],
    *,
    thresholds_signed: bool,
) -> str:
    """Resolve a belief from telemetry *signals* against SIGNED *thresholds*.

    Returns:
      - ``"unsigned"`` if the success metrics were not signed (refused);
      - ``"pending"`` if no comparable signals are in yet;
      - ``"keep"`` if every measured metric meets its threshold;
      - ``"refute"`` if none do;
      - ``"iterate"`` if some do and some don't.
    """
    if not thresholds_signed:
        return UNSIGNED
    met = 0
    measured = 0
    for metric, target in thresholds.items():
        if metric in signals:
            measured += 1
            if signals[metric] >= target:
                met += 1
    if measured == 0:
        return PENDING
    if met == measured:
        return KEEP
    if met == 0:
        return REFUTE
    return ITERATE
