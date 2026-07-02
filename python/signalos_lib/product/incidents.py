"""Failure-state incident cards (Wave 1.10).

Maturity lives in the unhappy paths. Every failure surfaces as a plain-words
card -- what failed, cost so far, and named recovery options -- never a stack
trace or a silent stall. This module ships the framework plus the cards for the
scenarios that exist in today's pipeline; feature-specific cards (thin research,
low-confidence expert) ship with their features.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Present-day scenarios (plain language, no severity codes or validator ids).
_SCENARIOS: dict[str, dict[str, Any]] = {
    "gate-deadlock": {
        "title": "A decision keeps getting sent back",
        "what_failed": "The same artifact was rejected several times.",
        "recovery": ["Change the approach", "Reduce the scope", "Record a No-Go with what you learned"],
    },
    "integration-outage": {
        "title": "A connected service is unavailable",
        "what_failed": "A service Foundry relies on is not responding right now.",
        "recovery": ["Retry automatically with backoff", "Keep working from the plan", "Nothing is lost while it's down"],
    },
    "credential-revoked": {
        "title": "A connection needs to be re-authorized",
        "what_failed": "A saved credential was revoked or expired.",
        "recovery": ["Re-authorize the connection", "Dependent steps resume where they paused"],
    },
    "deploy-failure": {
        "title": "The release didn't complete",
        "what_failed": "The deployment failed or only partly went out.",
        "recovery": ["Roll back automatically where supported", "Re-arm the deploy gate once fixed"],
    },
}

KNOWN_SCENARIOS = tuple(_SCENARIOS)


@dataclass
class IncidentCard:
    scenario: str
    title: str
    what_failed: str
    recovery_options: list[str] = field(default_factory=list)
    cost_so_far: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "incident",
            "scenario": self.scenario,
            "title": self.title,
            "what_failed": self.what_failed,
            "recovery_options": list(self.recovery_options),
            "cost_so_far": self.cost_so_far,
        }


def build_incident_card(scenario: str, *, detail: str = "", cost_so_far: str = "") -> IncidentCard:
    """Return a plain-words incident card for *scenario*. An unknown scenario
    still yields a card (never a stack trace or a silent stall)."""
    spec = _SCENARIOS.get(scenario)
    if spec is None:
        return IncidentCard(
            scenario=scenario,
            title="Something needs your attention",
            what_failed=detail or "An unexpected issue came up and was paused safely.",
            recovery_options=["Review the details", "Try again", "Ask for help"],
            cost_so_far=cost_so_far,
        )
    what = spec["what_failed"] + (f" {detail}" if detail else "")
    return IncidentCard(
        scenario=scenario,
        title=spec["title"],
        what_failed=what,
        recovery_options=list(spec["recovery"]),
        cost_so_far=cost_so_far,
    )
