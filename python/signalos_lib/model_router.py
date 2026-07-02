"""Task-class model routing (Wave 1.3).

Maps task classes (triage / research / strategy / narrative / coding / critique)
to a model. Routing is AUTO by default (everything runs on the primary model),
PINNABLE per class, and applies the cross-vendor critique rule (Wave 1.4): a
critique runs on a different vendor than the artifact's author when a second
vendor is configured. The actual model *call* is delegated to the provider layer
(adopt LiteLLM-class routing) -- this module owns the *policy*, not the wire.

Outcome-adaptive selection (2026 best practice: offline priors + online update)
layers on top by adjusting `pins` from observed outcomes in the audit trail; that
feed is intentionally external so this policy stays pure and testable.
"""
from __future__ import annotations

from typing import Optional

from .second_opinion import choose_cross_vendor_reviewer

TASK_CLASSES = (
    "triage", "research", "strategy", "narrative", "coding", "critique",
)


def route(
    task_class: str,
    primary_model: str,
    *,
    pins: Optional[dict[str, str]] = None,
    available: Optional[list[str]] = None,
    author_model: Optional[str] = None,
    vendors: Optional[dict[str, str]] = None,
    default_vendor: Optional[str] = None,
) -> str:
    """Return the model to use for *task_class*.

    Priority: an explicit per-class *pin* wins; otherwise a critique routes to a
    different vendor than *author_model* when a second vendor is available
    (Wave 1.4); otherwise the *primary_model* (auto default). Never returns empty
    when *primary_model* is set.
    """
    pins = pins or {}
    pinned = pins.get(task_class)
    if pinned:
        return pinned

    if task_class == "critique" and author_model:
        candidates = available or []
        reviewer = choose_cross_vendor_reviewer(
            author_model, candidates, vendors=vendors, default=default_vendor)
        if reviewer:
            return reviewer

    return primary_model
