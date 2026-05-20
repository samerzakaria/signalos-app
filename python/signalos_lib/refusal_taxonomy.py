"""Refusal taxonomy + violation-confirmation flow (M-W7).

Per WAVE-ENGINE-DESIGN §8 (auto-sign protocol) and §9 (refusal taxonomy).

The wave engine refuses only in narrow categories:

  Category A — Hard safety (no override):
    LLM provider safety refusals (illegal/harmful), destructive
    workspace ops without confirmation.

  Category B/C — Re-route, not refuse:
    Missing prior gate (handled by router) or missing infrastructure
    (no workspace, no API key, no git remote) → ask user, never refuse.

  Category D — Override-with-audit:
    User wants to skip a protection → surface the violation, ask
    explicit confirmation, proceed + log.

  Category E — Defense floor (silent refuse + audit):
    Direct CLI bypass of the wave engine, status read failure or gate
    state corruption.

This module owns the taxonomy and the violation-confirmation builder.
The engine surfaces a 3-way prompt (fix-now / defer / override-with-log)
when a skill validator fails and the user might want to ship anyway.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


__all__ = [
    "RefusalCategory",
    "VIOLATION_OPTIONS",
    "build_violation_prompt",
    "record_violation_confirmation",
]


class RefusalCategory(str, Enum):
    """Refusal taxonomy categories per WAVE-ENGINE-DESIGN §9."""

    HARD_SAFETY = "A:hard-safety"
    REROUTE = "B:reroute"
    INFRA_ASK = "C:infra-ask"
    OVERRIDE_WITH_AUDIT = "D:override-with-audit"
    DEFENSE_FLOOR = "E:defense-floor"


# The three options surfaced when a violation can be skipped.
VIOLATION_OPTIONS: tuple[str, str, str] = ("fix-now", "defer", "override-with-log")


def build_violation_prompt(
    *,
    violation_kind: str,
    findings: list[str] | None = None,
    gate: str | None = None,
) -> dict[str, Any]:
    """Construct the 3-way per-violation prompt per §8.

    *violation_kind* names the skill/check that flagged the issue
    (e.g., "code-review", "security-audit", "test-coverage").

    *findings* is the list of specific issues (e.g., ["uses eval()",
    "missing null check on payload"]). The first 5 are surfaced; the
    rest are referenced by count so the chat bubble stays readable.

    Returns a structured dict the chat layer renders. The user's reply
    must be unambiguous — "a"/"b"/"c" or the literal option names. The
    engine refuses to interpret free-text as a violation override —
    integrity floor (§8 "no silent overrides in interactive mode").
    """
    findings = findings or []
    shown = findings[:5]
    extra = len(findings) - len(shown)
    extra_phrase = f" (+{extra} more)" if extra > 0 else ""

    if shown:
        findings_line = "; ".join(shown) + extra_phrase
    else:
        findings_line = "(no specific findings reported)"

    text = (
        f"The {violation_kind} check reported "
        f"{len(findings) or 'a'} finding{'s' if len(findings) != 1 else ''}: "
        f"{findings_line}.\n"
        "How do you want to proceed?\n"
        "  (a) Fix now — re-run after addressing the findings\n"
        "  (b) Defer to next wave — track in backlog, ship as-is\n"
        "  (c) Override with audit log — ship anyway; reason will be "
        "recorded as a violation in the audit trail"
    )

    return {
        "category": RefusalCategory.OVERRIDE_WITH_AUDIT.value,
        "violation_kind": violation_kind,
        "gate": gate,
        "findings": findings,
        "options": list(VIOLATION_OPTIONS),
        "text": text,
        "prompt_id": f"violation:{violation_kind}",
    }


def record_violation_confirmation(
    *,
    violation_kind: str,
    choice: str,
    user_reply: str,
    gate: str | None = None,
    findings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the audit-trail entry for a violation confirmation per §8.

    The caller appends this to AUDIT_TRAIL.jsonl. The engine never
    silently signs an override — *user_reply* is the verbatim text the
    user typed to confirm, captured as evidence.

    Choice must be one of VIOLATION_OPTIONS or one of the single-letter
    shortcuts (a/b/c). Anything else raises so the engine doesn't
    silently log a malformed confirmation.
    """
    short_to_full = {
        "a": "fix-now",
        "b": "defer",
        "c": "override-with-log",
    }
    normalised = (choice or "").strip().lower()
    full = short_to_full.get(normalised, normalised)
    if full not in VIOLATION_OPTIONS:
        raise ValueError(
            f"Unknown violation choice: {choice!r}. "
            f"Expected one of {sorted(VIOLATION_OPTIONS)} or a/b/c shortcut."
        )

    return {
        "action": f"violation:{violation_kind}:{full}",
        "violation_kind": violation_kind,
        "gate": gate,
        "choice": full,
        "evidence": user_reply,
        "findings": findings or [],
    }
