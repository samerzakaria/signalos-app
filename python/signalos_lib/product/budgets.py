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
DEFAULT_GATE_REOPEN_BUDGET = 3
# G4 subagent-driven build knobs (subagent_build.py). Each subagent is a FRESH
# bounded conversation; the per-task/fixer cycles bound convergence loops.
DEFAULT_BUILD_IMPLEMENTER_TOOL_BUDGET = 40
DEFAULT_BUILD_REVIEWER_TOOL_BUDGET = 20
DEFAULT_BUILD_TASK_FIX_CYCLES = 3
DEFAULT_BUILD_MAX_TASKS = 12
DEFAULT_BUILD_FIXER_ERROR_BATCH = 12

AGENT_LOOP_TOOL_BUDGET_ENV = "SIGNALOS_AGENT_LOOP_TOOL_BUDGET"
REPAIR_CYCLE_BUDGET_ENV = "SIGNALOS_AGENT_REPAIR_CYCLE_BUDGET"
GATE_REWORK_BUDGET_ENV = "SIGNALOS_GATE_REWORK_BUDGET"
GATE_REOPEN_BUDGET_ENV = "SIGNALOS_GATE_REOPEN_BUDGET"
BUILD_IMPLEMENTER_TOOL_BUDGET_ENV = "SIGNALOS_BUILD_IMPLEMENTER_TOOL_BUDGET"
BUILD_REVIEWER_TOOL_BUDGET_ENV = "SIGNALOS_BUILD_REVIEWER_TOOL_BUDGET"
BUILD_TASK_FIX_CYCLES_ENV = "SIGNALOS_BUILD_TASK_FIX_CYCLES"
BUILD_MAX_TASKS_ENV = "SIGNALOS_BUILD_MAX_TASKS"
BUILD_FIXER_ERROR_BATCH_ENV = "SIGNALOS_BUILD_FIXER_ERROR_BATCH"


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


def resolve_gate_reopen_budget(value: int | None = None) -> int:
    """Per-gate budget for reopening an already-signed gate (#4).

    Bounds the reopen loop the same way rework/rejections are bounded, so a
    delivery cannot oscillate forever between sign and reopen."""
    return _resolve_budget(
        value,
        env_name=GATE_REOPEN_BUDGET_ENV,
        default=DEFAULT_GATE_REOPEN_BUDGET,
        label="gate reopen budget",
    )


def resolve_build_implementer_tool_budget(value: int | None = None) -> int:
    """Tool-call budget for ONE G4 implementer/fixer subagent conversation."""
    return _resolve_budget(
        value,
        env_name=BUILD_IMPLEMENTER_TOOL_BUDGET_ENV,
        default=DEFAULT_BUILD_IMPLEMENTER_TOOL_BUDGET,
        label="build implementer tool budget",
    )


def resolve_build_reviewer_tool_budget(value: int | None = None) -> int:
    """Tool-call budget for ONE G4 reviewer subagent conversation."""
    return _resolve_budget(
        value,
        env_name=BUILD_REVIEWER_TOOL_BUDGET_ENV,
        default=DEFAULT_BUILD_REVIEWER_TOOL_BUDGET,
        label="build reviewer tool budget",
    )


def resolve_build_task_fix_cycles(value: int | None = None) -> int:
    """Bounded fixer passes to drive ONE plan task's test green before the
    next task (the per-task green gate)."""
    return _resolve_budget(
        value,
        env_name=BUILD_TASK_FIX_CYCLES_ENV,
        default=DEFAULT_BUILD_TASK_FIX_CYCLES,
        label="build per-task fix cycles",
    )


def resolve_build_max_tasks(value: int | None = None) -> int:
    """Cap on how many plan/acceptance tasks the build fans into; overflow is
    folded into the last task (never silently dropped)."""
    return _resolve_budget(
        value,
        env_name=BUILD_MAX_TASKS_ENV,
        default=DEFAULT_BUILD_MAX_TASKS,
        label="build max tasks",
    )


def resolve_build_fixer_error_batch(value: int | None = None) -> int:
    """Max diagnostics quoted per fixer prompt (context-window guard)."""
    return _resolve_budget(
        value,
        env_name=BUILD_FIXER_ERROR_BATCH_ENV,
        default=DEFAULT_BUILD_FIXER_ERROR_BATCH,
        label="build fixer error batch",
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
