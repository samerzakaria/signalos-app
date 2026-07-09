# signalos_lib/product/preflight.py
# Build-gate preflight: verify every precondition the governed build will rely
# on BEFORE the first model dispatch -- no LLM call, no spend. The system must
# fail loud and early at the precondition level, never degrade silently and
# burn resources discovering a broken precondition mid-walk (observed: an
# unsigned G2 in a benchmark fixture sent six models into plan re-authoring and
# fail-fast loops -- an entire evaluation round measuring our fixture instead
# of the models).
#
# Composes the SAME readers the walk itself uses (never a parallel
# implementation that could drift):
#   - sign.check_gate            -> signature state of every prior-gate artifact
#   - EnforcementProvider        -> the active governance rules load
#   - stacks/validation plan     -> the stack can objectively verify build+tests
#   - plan task decomposition    -> every plan-authored test path resolves
#
# The Rust-side twin (enforcement.rs build_precheck) guards the app's IPC
# path; this module guards the Python build path and any harness that drives
# it directly.

from __future__ import annotations

__all__ = ["validate_build_readiness"]

from pathlib import Path
from typing import Any, Optional

# Gates whose signed artifacts the BUILD gate depends on.
_PRIOR_GATES = ("G0", "G1", "G2", "G3")


def validate_build_readiness(
    repo_root: Path,
    project_id: str = "default",
    enforcement_provider: Any = None,
) -> list[str]:
    """Return blocking diagnostics (empty list = ready to build).

    Each entry names exactly which precondition is broken and where, so a
    failed preflight is actionable without reading logs. Never raises.
    """
    problems: list[str] = []
    repo_root = Path(repo_root)

    # 1. Prior gates: every required artifact exists AND carries a signature
    #    (the same sign.check_gate reader the walk's detection uses).
    try:
        from .. import sign
        for gate in _PRIOR_GATES:
            for st in sign.check_gate(repo_root, gate, project_id=project_id):
                if not st.exists:
                    problems.append(
                        f"{gate}: required artifact missing: {st.rel_path}")
                elif not st.has_signatures:
                    problems.append(
                        f"{gate}: artifact exists but is UNSIGNED: {st.rel_path}")
    except Exception as exc:
        problems.append(f"gate signature check failed: {type(exc).__name__}: {exc}")

    # 2. Governance rules load (the loop reads them once at run start; a repo
    #    whose enforcement state cannot load would dispatch and then die).
    if enforcement_provider is not None:
        try:
            enforcement_provider.get_enforcement_state(repo_root)
        except Exception as exc:
            problems.append(f"enforcement state cannot load: {type(exc).__name__}: {exc}")

    # 3. The stack can OBJECTIVELY verify build + tests (without this the
    #    build's green gates and the sign wall have nothing to stand on).
    try:
        from .stacks import detect_profile
        from .validation import build_validation_plan
        profile = detect_profile(repo_root)
        plan = build_validation_plan(repo_root, profile)
        if not plan.get("can_validate_build"):
            problems.append(f"profile '{profile}' cannot verify builds (no build command)")
        if not plan.get("can_validate_tests"):
            problems.append(f"profile '{profile}' cannot verify tests (no test command)")
    except Exception as exc:
        problems.append(f"stack validation plan failed: {type(exc).__name__}: {exc}")

    # 4. Plan contract: every plan-authored acceptance test the tasks reference
    #    must resolve to a real file (a broken path silently degrades the
    #    per-task green gate into blind building).
    try:
        from .subagent_build import decompose_plan_tasks, _workspace_path
        for task in decompose_plan_tasks(repo_root, project_id):
            if not task.test:
                continue
            p = _workspace_path(repo_root, task.test, project_id)
            if p is None or not p.is_file():
                problems.append(
                    f"plan task {task.id}: authored test not found: {task.test}")
    except Exception as exc:
        problems.append(f"plan task decomposition failed: {type(exc).__name__}: {exc}")

    return problems
