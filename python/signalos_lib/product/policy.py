"""Founder policy controls (Wave 1.11).

The founder shapes the workflow through plain-language POLICY -- gate mode,
research depth, budget cap, standards profile, allowed deploy targets -- never by
editing the invariant structure. Deliberately NO workflow-graph editor: handing a
non-technical founder the power to rewire gates would let them break the
governance that is the product's value. The one hard rule enforced here: no gate
mode may ever remove a FLOOR gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GATE_MODES = ("strict", "standard", "fast-lane")
RESEARCH_DEPTHS = ("light", "standard", "deep")

# The five gates no mode may remove (BRD §11 floor gates).
FLOOR_GATES = ("qualification", "go-no-go", "design", "deploy", "launch")

# Plain-language labels so the founder never reads internal codes.
GATE_MODE_LABELS = {
    "strict": "Sign off on everything (most control)",
    "standard": "Sign off on the decisions that matter",
    "fast-lane": "Sign off only at the essential gates",
}


@dataclass
class FounderPolicy:
    gate_mode: str = "standard"
    research_depth: str = "standard"
    budget_cap_usd: float = 0.0  # 0 = no cap
    standards_profile: str = "default"
    allowed_deploy_targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_mode": self.gate_mode,
            "research_depth": self.research_depth,
            "budget_cap_usd": self.budget_cap_usd,
            "standards_profile": self.standards_profile,
            "allowed_deploy_targets": list(self.allowed_deploy_targets),
        }


def validate_policy(policy: FounderPolicy) -> list[str]:
    """Return policy violations; empty means valid."""
    problems: list[str] = []
    if policy.gate_mode not in GATE_MODES:
        problems.append(f"unknown gate mode: {policy.gate_mode}")
    if policy.research_depth not in RESEARCH_DEPTHS:
        problems.append(f"unknown research depth: {policy.research_depth}")
    if policy.budget_cap_usd < 0:
        problems.append("budget cap cannot be negative")
    return problems


def gates_for_mode(mode: str, gate_set: list[str], floor: tuple[str, ...] = FLOOR_GATES) -> list[str]:
    """Which gates are active under *mode*. Floor gates ALWAYS survive. Fast-lane
    keeps only floor gates; strict/standard keep the full set. An unknown mode is
    fail-closed to the full set (never fewer)."""
    floor_present = [g for g in gate_set if g in floor]
    if mode == "fast-lane":
        return floor_present
    # strict, standard, and any unknown mode -> keep everything (never drop a gate)
    return list(gate_set)
