"""Budget policy for governed product agent execution.

The production loop should be bounded by an explicit execution budget, not by
small historical constants hidden in each subsystem. Callers may still pass
smaller values for deterministic tests or operator control.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_AGENT_LOOP_TOOL_CALL_BUDGET = 250
DEFAULT_REPAIR_CYCLE_BUDGET = 8
DEFAULT_GATE_REWORK_BUDGET = 8

AGENT_LOOP_TOOL_BUDGET_ENV = "SIGNALOS_AGENT_LOOP_TOOL_BUDGET"
REPAIR_CYCLE_BUDGET_ENV = "SIGNALOS_AGENT_REPAIR_CYCLE_BUDGET"
GATE_REWORK_BUDGET_ENV = "SIGNALOS_GATE_REWORK_BUDGET"


def resolve_agent_loop_tool_budget(value: int | None = None) -> int:
    return _resolve_budget(
        value,
        env_name=AGENT_LOOP_TOOL_BUDGET_ENV,
        default=DEFAULT_AGENT_LOOP_TOOL_CALL_BUDGET,
        label="agent loop tool-call budget",
    )


def resolve_repair_cycle_budget(value: int | None = None) -> int:
    return _resolve_budget(
        value,
        env_name=REPAIR_CYCLE_BUDGET_ENV,
        default=DEFAULT_REPAIR_CYCLE_BUDGET,
        label="repair cycle budget",
        allow_zero=True,
    )


def resolve_gate_rework_budget(value: int | None = None) -> int:
    return _resolve_budget(
        value,
        env_name=GATE_REWORK_BUDGET_ENV,
        default=DEFAULT_GATE_REWORK_BUDGET,
        label="gate rework budget",
    )


def build_execution_budget_policy(
    *,
    tool_call_budget: int | None = None,
    repair_cycle_budget: int | None = None,
    gate_rework_budget: int | None = None,
) -> dict[str, Any]:
    """Return the budget contract written into agent packets/evidence."""

    return {
        "schema_version": "signalos.execution_budget.v1",
        "tool_call_budget": resolve_agent_loop_tool_budget(tool_call_budget),
        "repair_cycle_budget": resolve_repair_cycle_budget(repair_cycle_budget),
        "gate_rework_budget": resolve_gate_rework_budget(gate_rework_budget),
        "stop_policy": (
            "Continue iterating until acceptance criteria and validation are "
            "green, or stop truthfully when this explicit budget is exhausted."
        ),
        "overrides": {
            "tool_call_budget_env": AGENT_LOOP_TOOL_BUDGET_ENV,
            "repair_cycle_budget_env": REPAIR_CYCLE_BUDGET_ENV,
            "gate_rework_budget_env": GATE_REWORK_BUDGET_ENV,
        },
    }


def _resolve_budget(
    value: int | None,
    *,
    env_name: str,
    default: int,
    label: str,
    allow_zero: bool = False,
) -> int:
    if value is not None:
        return _validate_budget(value, label=label, allow_zero=allow_zero)

    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer for {label}") from exc
    return _validate_budget(parsed, label=label, allow_zero=allow_zero)


def _validate_budget(value: int, *, label: str, allow_zero: bool) -> int:
    floor = 0 if allow_zero else 1
    if value < floor:
        if allow_zero:
            raise ValueError(f"{label} must be >= 0")
        raise ValueError(f"{label} must be >= 1")
    return value
