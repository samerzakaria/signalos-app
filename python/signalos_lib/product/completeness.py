"""Completeness-rubric inversion pass (Wave 1.9).

Structure validation proves that what is *said* is well-said; it cannot see what
is *unsaid*. This pass asks the inversion question -- "what does this artifact
silently assume but never address?" -- against a standard omission checklist.

It is deliberately ADVISORY, not blocking: deterministic keyword detection can
miss a concern an artifact addresses in other words, so it surfaces "may not
address X" signals for the founder/critic rather than hard-failing a gate. A
future LLM critic pass can layer on top. Money & billing is intentionally out of
scope for now (billing is deferred), so it never cries wolf about it.
"""
from __future__ import annotations

import re
from typing import Any

# Concern -> phrases whose presence means the artifact plausibly addresses it.
# In-scope concerns only; "money & billing" is intentionally excluded.
_CONCERNS: dict[str, tuple[str, ...]] = {
    "identity & accounts": ("identity", "account", "sign in", "sign-in", "log in",
                            "login", "auth", "user record", "authenticate"),
    "permissions & isolation": ("permission", "role", "access control", "isolation",
                                "tenant", "scope", "least privilege"),
    "onboarding & empty states": ("onboarding", "empty state", "first run", "first-run",
                                  "first-time", "getting started", "no data yet"),
    "data lifecycle": ("retention", "deletion", "delete", "export", "backup",
                       "data lifecycle", "purge", "archive"),
    "operations & failure states": ("failure", "error handling", "incident", "recovery",
                                    "rollback", "timeout", "degraded", "retry"),
}

# Below this length an artifact is treated as a stub, not a silent omission.
_MIN_CHARS = 400


def completeness_findings(content: str, min_chars: int = _MIN_CHARS) -> list[dict[str, Any]]:
    """Return advisory 'may not address X' findings for a substantial artifact.

    Empty for artifacts shorter than *min_chars* (too small to judge) and for
    concerns the artifact appears to address. Never includes money/billing.
    """
    text = (content or "")
    if len(text) < min_chars:
        return []
    lowered = text.lower()
    findings: list[dict[str, Any]] = []
    for concern, phrases in _CONCERNS.items():
        if not any(_mentions(lowered, phrase) for phrase in phrases):
            findings.append({
                "concern": concern,
                "advisory": f"artifact may not address '{concern}'",
            })
    return findings


def _mentions(lowered_text: str, phrase: str) -> bool:
    # word-ish boundary match so 'auth' doesn't fire inside 'author', etc.
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", lowered_text) is not None
