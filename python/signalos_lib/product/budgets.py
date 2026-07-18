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
# conversation. The PRIMARY control on a build/fixer seat is PROGRESS, not a
# tool-call count: the seat's turn ends naturally on end_turn or on the loop's
# stall detector (no state change across a window), NOT on a small call cap.
# This value is therefore NOT the seat's working budget -- it is a single,
# deliberately-high anti-runaway backstop that only ever fires on a pathological
# infinite loop (an order of magnitude above the old 40-call seat cap). Money
# ($ per model) and the G4 build timeout are the ultimate wall-clock/$ walls;
# this guard just prevents a truly unbounded loop. Do NOT treat it as "the
# budget" and do NOT lower it into normal-operation range.
DEFAULT_BUILD_IMPLEMENTER_RUNAWAY_GUARD = 1000
DEFAULT_BUILD_REVIEWER_TOOL_BUDGET = 20
# STALL_ROUNDS for the per-task CONVERGENCE gate (subagent_build.py): the
# PRIMARY control on the red-test fix loop. A red task keeps earning fixer
# cycles WHILE it CONVERGES (its failing-check count keeps reaching a new
# minimum); it stops only after this many CONSECUTIVE cycles that set no new
# minimum -- churn that changes files/errors without reducing failures counts
# toward the stall. This is NOT a fixed cycle count: a converging task runs as
# many cycles as it needs (bounded ultimately by money/time), a non-converging
# one stops fast and finishes honestly red. 3 allows one exploratory setup cycle
# before the failure count must start dropping -- but no more (a churn build
# that never reduces failures must stop cleanly in ~10-20 min, not burn the
# whole wall-clock to a driver timeout).
DEFAULT_BUILD_TASK_STALL_ROUNDS = 3
# Small bound for the SECONDARY convergence loops only -- the deterministic
# Definition-of-Done quality fixer and the reviewer re-review loop. These are
# NOT the primary fix control (that is the progress gate above); they iterate a
# bounded, shrinking set of concrete findings, so a small fixed bound is
# correct.
DEFAULT_BUILD_TASK_FIX_CYCLES = 3
DEFAULT_BUILD_MAX_TASKS = 12
DEFAULT_BUILD_FIXER_ERROR_BATCH = 12
# Prompt-content caps (chars). Deliberately GENEROUS: a tight cap silently
# hides part of the signed spec from the builder (observed: a plan test's
# validation assertions truncated out of the prompt, then the builder graded
# on expectations it could not see). Operators tune per model via env; the
# default judges nobody's context needs.
DEFAULT_BUILD_TEST_EMBED_CAP = 100_000
DEFAULT_BUILD_DOC_CAP = 24_000

AGENT_LOOP_TOOL_BUDGET_ENV = "SIGNALOS_AGENT_LOOP_TOOL_BUDGET"
REPAIR_CYCLE_BUDGET_ENV = "SIGNALOS_AGENT_REPAIR_CYCLE_BUDGET"
GATE_REWORK_BUDGET_ENV = "SIGNALOS_GATE_REWORK_BUDGET"
GATE_REOPEN_BUDGET_ENV = "SIGNALOS_GATE_REOPEN_BUDGET"
BUILD_IMPLEMENTER_RUNAWAY_GUARD_ENV = "SIGNALOS_BUILD_IMPLEMENTER_RUNAWAY_GUARD"
BUILD_REVIEWER_TOOL_BUDGET_ENV = "SIGNALOS_BUILD_REVIEWER_TOOL_BUDGET"
BUILD_TASK_STALL_ROUNDS_ENV = "SIGNALOS_BUILD_TASK_STALL_ROUNDS"
BUILD_TASK_FIX_CYCLES_ENV = "SIGNALOS_BUILD_TASK_FIX_CYCLES"
BUILD_MAX_TASKS_ENV = "SIGNALOS_BUILD_MAX_TASKS"
BUILD_FIXER_ERROR_BATCH_ENV = "SIGNALOS_BUILD_FIXER_ERROR_BATCH"
BUILD_TEST_EMBED_CAP_ENV = "SIGNALOS_BUILD_TEST_EMBED_CAP"
BUILD_DOC_CAP_ENV = "SIGNALOS_BUILD_DOC_CAP"


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


def resolve_build_implementer_runaway_guard(value: int | None = None) -> int:
    """Anti-runaway backstop (NOT the working budget) for ONE G4 implementer/
    fixer subagent conversation. The seat's turn normally ends on end_turn or the
    loop's stall detector (no state change across a window) -- PROGRESS is the
    primary control, not this number. This guard only fires on a pathological
    infinite loop and is deliberately an order of magnitude above the old seat
    cap; money/time are the real walls."""
    return _resolve_budget(
        value,
        env_name=BUILD_IMPLEMENTER_RUNAWAY_GUARD_ENV,
        default=DEFAULT_BUILD_IMPLEMENTER_RUNAWAY_GUARD,
        label="build implementer runaway guard",
    )


def resolve_build_reviewer_tool_budget(value: int | None = None) -> int:
    """Tool-call budget for ONE G4 reviewer subagent conversation."""
    return _resolve_budget(
        value,
        env_name=BUILD_REVIEWER_TOOL_BUDGET_ENV,
        default=DEFAULT_BUILD_REVIEWER_TOOL_BUDGET,
        label="build reviewer tool budget",
    )


def resolve_build_task_stall_rounds(value: int | None = None) -> int:
    """STALL_ROUNDS for the per-task PROGRESS gate: consecutive no-progress fixer
    cycles tolerated before a still-red task is declared stalled. This is NOT a
    fixed fixer-pass count -- while a task keeps progressing (its failure
    signature changes or its source changes) it earns more cycles; only a true
    stall (no change at all for this many rounds) stops it."""
    return _resolve_budget(
        value,
        env_name=BUILD_TASK_STALL_ROUNDS_ENV,
        default=DEFAULT_BUILD_TASK_STALL_ROUNDS,
        label="build per-task stall rounds",
    )


def resolve_build_task_fix_cycles(value: int | None = None) -> int:
    """Small bound for the SECONDARY per-task convergence loops only: the
    deterministic Definition-of-Done quality fixer and the reviewer re-review
    loop. NOT the primary red-test fix control -- that is the progress gate
    (resolve_build_task_stall_rounds). These loops iterate a bounded, shrinking
    set of concrete findings, so a small fixed bound is correct."""
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


def resolve_build_test_embed_cap(value: int | None = None) -> int:
    """Chars of a plan-authored test embedded verbatim in the implementer's
    prompt. Generous by default -- truncating hides the signed spec."""
    return _resolve_budget(
        value,
        env_name=BUILD_TEST_EMBED_CAP_ENV,
        default=DEFAULT_BUILD_TEST_EMBED_CAP,
        label="build test embed cap",
    )


def resolve_build_doc_cap(value: int | None = None) -> int:
    """Chars per bundled-skill/artifact doc quoted into build prompts."""
    return _resolve_budget(
        value,
        env_name=BUILD_DOC_CAP_ENV,
        default=DEFAULT_BUILD_DOC_CAP,
        label="build doc cap",
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
