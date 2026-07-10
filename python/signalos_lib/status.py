# SignalOS Core v2.1 — Wave status card (AMD-CORE-008).
#
# Renders a structured ASCII status card for the current Wave, showing:
#   - Wave ID and current delivery phase
#   - First line of the problem statement from BELIEF.md
#   - Scale track and delivery mode
#   - Gate status (G0–G5)
#   - Active tasks from .signalos/worktree-state.json
#   - Next blocking action
#
# Public API:
#   get_wave_status(repo_root: Path) -> dict
#   render_status_card(status: dict) -> str
#   print_status_card(repo_root: Path | None = None) -> None


from __future__ import annotations

__all__ = [
    "get_wave_status",
    "print_status_card",
    "watch_status",
    "_format_elapsed",
    "build_status_json",
    "_collect_gate_activities",
    "_collect_gate_criteria",
    "_load_plan_doc",
    "_load_plan_tasks",
]  # W-2/W3.2 + M3 (gate emissions)

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from signalos_lib.artifacts import gate_detection_paths
from signalos_lib.ide import detect_ide

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT_MARKER = ".signalos"
CARD_WIDTH = 62  # inner width (between the ║ chars)

# Phase names keyed by the highest open gate
_PHASE_NAMES = {
    0: "ONBOARDING",
    1: "BELIEF",
    2: "PLANNING",
    3: "DESIGN",
    4: "BUILD",
    5: "REVIEW",
    6: "DONE",
}


# ---------------------------------------------------------------------------
# Repo root helper
# ---------------------------------------------------------------------------

def _repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / REPO_ROOT_MARKER).is_dir():
            return cand
    return p  # fallback: return cwd (status card still renders partial data)


# ---------------------------------------------------------------------------
# Gate detection helpers
# ---------------------------------------------------------------------------

def _is_non_template(path: Path) -> bool:
    """Return True if the file exists and has some non-template content."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    # Template markers found in SignalOS scaffold files
    template_markers = [
        "<!-- Template",
        "# Template",
        "YYYY-MM-DD",
        "{product-name}",
        "<!-- PLACEHOLDER",
    ]
    # Non-template: file exists and doesn't look like a blank scaffold
    filled_lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("<!--") and not ln.strip().startswith("#")
    ]
    if len(filled_lines) < 3:
        return False
    for marker in template_markers:
        if marker in text:
            return len(filled_lines) > 10  # large enough to not be all-template
    return True


def _governance_base(root: Path, project_id: str = "default") -> Path:
    """Base root for the canonical `core/...` gate-artifact rel_paths.

    Delegates to projects.project_governance_dir (§3.2 — the single
    resolver every gate reader/writer shares): "default" → the workspace
    root itself (byte-identical), any other id →
    .signalos/projects/<project_id>/governance/.
    """
    from signalos_lib.projects import project_governance_dir

    return project_governance_dir(root, project_id)


def _detect_gates(root: Path, project_id: str = "default") -> dict[str, bool]:
    """Return dict with G0..G5 as bool (True = gate passed).

    Paths come from the canonical gate manifest (gate_artifacts.json) so this
    board can never drift from what the gate validator enforces. G0 keeps its
    content check: a template-only Soul Document does not count as onboarded.
    *project_id* namespaces the artifact base per §3.2.
    """
    # A gate counts as passed only when EVERY required artifact EXISTS *and
    # carries a signature* (sign.check_gate reads the in-file signature blocks).
    # Two prior bugs this closes:
    #   * Bare existence was fail-open: an honest not-green BUILD_EVIDENCE.md
    #     made G4 read "done".
    #   * `any(signed)` was fail-open the other way: ONE signed artifact marked
    #     the whole gate passed, so a partially-signed project advanced here and
    #     was then blocked at G4 preflight (preflight requires EVERY prior-gate
    #     artifact signed -- validate_build_readiness). Requiring `all` makes the
    #     board agree with preflight: the SAME sign.check_gate manifest is the
    #     source of truth for "required" on both sides.
    # G0 additionally keeps its non-template content check (a template-only Soul
    # Document is not onboarded). An empty status list (unknown gate / read
    # error) is fail-closed, not vacuously passed.
    from . import sign as _sign
    g = {}
    for gate in gate_detection_paths():
        try:
            statuses = _sign.check_gate(root, gate, project_id=project_id)
        except Exception:
            statuses = []
        if not statuses:
            g[gate] = False
            continue
        if gate == "G0":
            g[gate] = all(st.exists and st.has_signatures and _is_non_template(st.path)
                          for st in statuses)
        else:
            g[gate] = all(st.exists and st.has_signatures for st in statuses)
    return g


def _detect_phase(gates: dict[str, bool]) -> str:
    """Return phase name based on highest passed gate."""
    if gates.get("G5"):
        return "REVIEW"
    if gates.get("G4"):
        return "BUILD"
    if gates.get("G3"):
        return "DESIGN"
    if gates.get("G2"):
        return "PLANNING"
    if gates.get("G1"):
        return "BELIEF"
    if gates.get("G0"):
        return "ONBOARDING"
    return "ONBOARDING"


# ---------------------------------------------------------------------------
# Belief text extraction
# ---------------------------------------------------------------------------

def _read_belief_line(root: Path, project_id: str = "default") -> str:
    """Read first non-empty line after '## Problem' in BELIEF.md."""
    base = _governance_base(root, project_id)
    for belief_path in [
        base / "core" / "strategy" / "BELIEF.md",
        base / "core" / "strategy" / "BELIEF_LITE.md",
    ]:
        if not belief_path.is_file():
            continue
        text = belief_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        in_problem = False
        for line in lines:
            stripped = line.strip()
            if re.match(r"^#{1,3}\s+Problem", stripped) or stripped == "**Problem**":
                in_problem = True
                continue
            if in_problem and stripped and not stripped.startswith("#"):
                # Strip markdown formatting
                clean = re.sub(r"\*{1,2}|_{1,2}|`", "", stripped)
                clean = re.sub(r"^\s*[-*>]+\s*", "", clean)
                return clean[:50]
    return "No belief statement found"


def _read_scale_track(root: Path, project_id: str = "default") -> str:
    """Read scale_track from BELIEF.md front-matter."""
    base = _governance_base(root, project_id)
    for belief_path in [
        base / "core" / "strategy" / "BELIEF_LITE.md",
        base / "core" / "strategy" / "BELIEF.md",
    ]:
        if not belief_path.is_file():
            continue
        for line in belief_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^scale_track:\s*(\S+)", line.strip())
            if m:
                return m.group(1)
    return "wave"


def _read_delivery_mode(root: Path, project_id: str = "default") -> str:
    """Read delivery_mode from SOUL-DOCUMENT.md or CONSTITUTION.md."""
    base = _governance_base(root, project_id)
    for path in [
        base / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md",
        base / "core" / "governance" / "Governance" / "CONSTITUTION.md",
    ]:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^delivery_mode:\s*(\S+)", line.strip())
            if m:
                return m.group(1)
    return "fresh-wave"


# ---------------------------------------------------------------------------
# Task data from worktree-state.json
# ---------------------------------------------------------------------------

def _read_tasks(
    root: Path,
    product_id: str | None = None,
    project_id: str = "default",
) -> list[dict[str, Any]]:
    """Read task list from worktree-state.json.

    When *product_id* is provided the product-scoped path is used:
      .signalos/products/<id>/worktree-state.json
    Otherwise the project-scoped path (Task #19 — projects.project_state_dir):
      .signalos/worktree-state.json                       (project "default")
      .signalos/projects/<project_id>/worktree-state.json (any other id)
    """
    if product_id:
        state_file = root / REPO_ROOT_MARKER / "products" / product_id / "worktree-state.json"
    else:
        from signalos_lib.projects import project_state_dir

        state_file = project_state_dir(root, project_id) / "worktree-state.json"
    if not state_file.is_file():
        return []
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data.get("worktrees", [])
    except Exception:
        return []


def _task_icon(status: str) -> str:
    """Return display icon for a task status."""
    icons = {
        "active": "⟳",
        "running": "⟳",
        "paused": "⏸",
        "completed": "✓",
        "failed": "✗",
        "merged": "✓",
    }
    return icons.get(status.lower() if status else "", "○")


def _infer_tier(task: dict[str, Any]) -> str:
    """Infer task trust tier from branch name or task dict."""
    # Try explicit tier field first
    if "tier" in task:
        return str(task["tier"])
    # Try to infer from branch name patterns
    branch = task.get("branch", "")
    if "t3" in branch.lower():
        return "T3"
    if "t2" in branch.lower():
        return "T2"
    return "T1"


# ---------------------------------------------------------------------------
# Next action
# ---------------------------------------------------------------------------

def _next_action(gates: dict[str, bool], tasks: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (role, command) for the next blocking action."""
    # Check for paused tasks
    for task in tasks:
        status = task.get("status", "")
        if status in {"paused"}:
            step_id = task.get("step_id") or task.get("branch", "unknown")
            return "PE", f"signalos pause resume {step_id}"
    # Check for failed tasks
    for task in tasks:
        if task.get("status", "") == "failed":
            step_id = task.get("step_id") or task.get("branch", "unknown")
            return "PE", f"signalos harness status {step_id}"
    # All tasks done and G5 open
    all_done = all(
        t.get("status", "") in {"completed", "merged"} for t in tasks
    ) if tasks else False
    if all_done and not gates.get("G5"):
        return "QA", "sign QUALITY_CHECK.md"
    # G0 not passed
    if not gates.get("G0"):
        return "PO", "signalos signal-onboard"
    # G1 not passed
    if not gates.get("G1"):
        return "PO", "signalos signal-pre-wave"
    return "—", "No blocking action"


# ---------------------------------------------------------------------------
# State aggregation
# ---------------------------------------------------------------------------

def _detect_wave_id(
    root: Path,
    tasks: list[dict[str, Any]],
    project_id: str = "default",
) -> str:
    """Try to determine current wave ID."""
    # From tasks
    for t in tasks:
        wave = t.get("wave", "")
        if wave:
            return str(wave)
    # From worktree-state.json top-level field (project-scoped, Task #19)
    from signalos_lib.projects import project_state_dir

    state_file = project_state_dir(root, project_id) / "worktree-state.json"
    if state_file.is_file():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            w = data.get("wave_id") or data.get("wave")
            if w:
                return str(w)
        except Exception:
            pass
    return "—"


# ---------------------------------------------------------------------------
# Milestone 3 — per-gate activities + criteria emissions
#
# DashboardView reads `gateActivities` and `gateCriteria` signals which are
# populated from each gate's `activities` / `criteria` arrays in the JSON
# emitted by `signalos status --json`. Previously these arrays did not exist
# and the dashboard rendered "No activities yet" placeholders. The helpers
# below derive both:
#
#   - Activities: PLAN.tasks.yaml entries mapped to a phase / gate by the
#     task's `gate` field (when present) or by the task's `skills` (e.g. a
#     `writing-plans` task belongs to G2). One activity per task; status is
#     translated from PLAN vocabulary (pending/in_progress/done/blocked) into
#     the UI vocabulary the DashboardView understands.
#
#   - Criteria: a check is emitted for every skill_validators._validate_*
#     function that has at least one task tagged with that skill in the
#     current plan. Status comes from .signalos/skill-validation/<wave>/<skill>.json
#     if the orchestrator persisted it (it currently does not, so the
#     fallback is "pending"). This keeps the contract forward-compatible
#     with a future orchestrator change that writes validator output to disk.
# ---------------------------------------------------------------------------

# Map a skill name to the gate where validating it makes sense. Skills that
# don't appear here are still surfaced as a criterion on whatever gate hosts
# the task that uses them — see _criterion_gate().
_SKILL_TO_GATE: dict[str, int] = {
    # G2 — Planning
    "writing-plans": 2,
    # G3 — Design
    # (design skills go on G3 by default via _criterion_gate fallback)
    # G4 — Build / Trust Tier
    "executing-plans": 4,
    "using-git-worktrees": 4,
    "finishing-a-development-branch": 4,
    "systematic-debugging": 4,
    "test-generation": 4,
    "security-audit": 4,
    # G5 — Quality / Review
    "comprehensive-code-review": 5,
    "requesting-code-review": 5,
    "receiving-code-review": 5,
    "verification-before-completion": 5,
    "retro-run": 5,
    "retrospective-analyze": 5,
}

# Default gate when a task's gate isn't declared and no skill hints exist.
# G4 (Build) is the workhorse phase where most plan tasks live.
_DEFAULT_TASK_GATE = 4

# Human-readable descriptions for each criterion. The validator function
# name is the canonical key; this map gives the dashboard a one-liner.
_CRITERION_DESCRIPTIONS: dict[str, str] = {
    "security-audit": "No obvious security foot-guns in written files",
    "test-generation": "At least one test file produced for the change",
    "comprehensive-code-review": "Review notes emitted with severity sections",
    "systematic-debugging": "Debug trace recorded (Reproduce/Hypothesis/Test/Fix)",
    "writing-plans": "PLAN.tasks.yaml present after planner task",
    "executing-plans": "Audit trail shows execution events",
    "using-git-worktrees": "Worktree state recorded when worktrees are used",
    "finishing-a-development-branch": "Worktrees marked merged/retired/done",
    "receiving-code-review": "Review response file maps each comment to a verdict",
    "requesting-code-review": "Review request has Summary / Changes / Test plan",
    "verification-before-completion": "Verification artifact or section emitted",
    "retro-run": "Wave retrospective WAVE_REVIEW.md written",
    "retrospective-analyze": "Cross-wave analysis artifact written",
}


def _ui_activity_status(plan_status: str) -> str:
    """Translate PLAN.tasks.yaml status to the UI vocabulary DashboardView reads.

    DashboardView treats: 'completed' → done pill, 'in_progress' → ongoing,
    anything else → pending. We keep 'failed' explicit so a future UI tweak
    can render it differently without rebuilding the data layer.
    """
    s = (plan_status or "").lower()
    if s == "done":
        return "completed"
    if s == "in_progress":
        return "in_progress"
    if s in {"blocked", "failed"}:
        return "failed"
    # pending, skipped, or unknown → pending
    return "pending"


def _task_gate(task: dict[str, Any]) -> int:
    """Return the gate (0-5) a PLAN task belongs to.

    Priority:
      1. explicit `gate: G<n>` or `gate: <n>` field on the task
      2. first skill in `skills:` that maps to a known gate
      3. _DEFAULT_TASK_GATE (G4 — Build)
    """
    raw = task.get("gate")
    if raw is not None:
        # Accept "G3", "g3", "3", 3
        s = str(raw).strip().upper().lstrip("G")
        try:
            n = int(s)
            if 0 <= n <= 5:
                return n
        except ValueError:
            pass
    for skill in task.get("skills", []) or []:
        gate = _SKILL_TO_GATE.get(str(skill))
        if gate is not None:
            return gate
    return _DEFAULT_TASK_GATE


def _criterion_gate(skill: str) -> int:
    """Return the gate a skill-derived criterion belongs to.

    Falls back to G3 (Design) for skills not in the map so they at least
    surface somewhere — better than dropping them silently.
    """
    return _SKILL_TO_GATE.get(skill, 3)


def _load_plan_doc(
    repo_root: Path,
    project_id: str = "default",
) -> tuple[list[dict[str, Any]], str]:
    """Load PLAN.tasks.yaml and return (tasks, wave_id).

    For the default project, tries the canonical locations in order
    (byte-identical to the historical behavior):
      1. <root>/PLAN.tasks.yaml
      2. <root>/core/execution/PLAN.tasks.yaml
      3. <root>/core/execution/plan/PLAN.tasks.yaml

    Any other *project_id* reads ONLY the project's own plan via
    projects.project_plan_path (.signalos/projects/<id>/PLAN.tasks.yaml);
    a missing per-project plan behaves like a missing root plan.

    Returns ([], "") on any failure (parse error, file missing,
    yaml not installed). This is best-effort — status output must never
    crash because the plan file is malformed.
    """
    from signalos_lib.projects import project_plan_path

    if project_id == "default":
        candidates = [
            repo_root / "PLAN.tasks.yaml",
            repo_root / "core" / "execution" / "PLAN.tasks.yaml",
            repo_root / "core" / "execution" / "plan" / "PLAN.tasks.yaml",
        ]
    else:
        candidates = [project_plan_path(repo_root, project_id)]
    plan_path: Path | None = None
    for c in candidates:
        if c.is_file():
            plan_path = c
            break
    if plan_path is None:
        return ([], "")
    try:
        from signalos_lib.plan import load_tasks
        doc = load_tasks(plan_path)
        return ([t.to_dict() for t in doc.tasks], str(doc.wave or ""))
    except Exception:
        return ([], "")


def _load_plan_tasks(
    repo_root: Path,
    project_id: str = "default",
) -> list[dict[str, Any]]:
    """Backwards-compat wrapper returning only the task list."""
    tasks, _wave = _load_plan_doc(repo_root, project_id=project_id)
    return tasks


def _read_validator_evidence(
    repo_root: Path, wave_id: str, skill: str
) -> tuple[str, str | None]:
    """Return (status, evidence_path) for a skill's validator output.

    Looks for `.signalos/skill-validation/<wave>/<skill>.json`. If present
    and parseable, status is 'passing' when violations==0 else 'failing';
    evidence_path is the relative file path. If absent, returns ('pending',
    None) — the orchestrator does not currently persist validator output,
    so this is the expected fallback today. The schema is forward-compatible
    with a future orchestrator change that writes:
        { "violations": [...], "ok": bool, "ts": "..." }
    """
    if not wave_id or wave_id == "-":
        wave_id = "current"
    rel = Path(".signalos") / "skill-validation" / str(wave_id) / f"{skill}.json"
    abs_path = repo_root / rel
    if not abs_path.is_file():
        return ("pending", None)
    try:
        data = json.loads(abs_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ("pending", None)
    # Schema: explicit `ok` wins; else infer from violations[]
    if isinstance(data, dict):
        if "ok" in data:
            return ("passing" if bool(data["ok"]) else "failing", str(rel).replace("\\", "/"))
        violations = data.get("violations")
        if isinstance(violations, list):
            return ("passing" if len(violations) == 0 else "failing", str(rel).replace("\\", "/"))
    return ("pending", str(rel).replace("\\", "/"))


def _collect_gate_activities(
    repo_root: Path, plan_tasks: list[dict[str, Any]]
) -> dict[int, list[dict[str, Any]]]:
    """Group plan tasks into per-gate activity lists.

    Returns a dict keyed by gate id (0..5) → list of activity dicts. Each
    activity has the spec shape (task_id, title, status, skills) PLUS a
    `name` alias for the title (the DashboardView reads `a.name`).
    """
    by_gate: dict[int, list[dict[str, Any]]] = {i: [] for i in range(6)}
    for t in plan_tasks:
        gate = _task_gate(t)
        title = str(t.get("title") or t.get("id") or "")
        plan_status = str(t.get("status") or "pending")
        skills = [str(s) for s in (t.get("skills") or [])]
        by_gate[gate].append({
            "task_id": str(t.get("id") or ""),
            "title": title,
            "name": title,  # DashboardView reads `a.name`
            "status": _ui_activity_status(plan_status),
            "plan_status": plan_status,  # raw value for callers that want it
            "skills": skills,
        })
    return by_gate


def _collect_gate_criteria(
    repo_root: Path,
    wave_id: str,
    plan_tasks: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """Build per-gate criteria from skill_validators registry × plan tasks.

    For every skill tagged on at least one task, emit a criterion on the
    gate that skill maps to. Status is read from any persisted validator
    output under .signalos/skill-validation/<wave>/<skill>.json; otherwise
    "pending".
    """
    by_gate: dict[int, list[dict[str, Any]]] = {i: [] for i in range(6)}

    # Lazy import: skill_validators is heavy and may not import in minimal
    # installs. Fall back to empty criteria if it can't load.
    try:
        from signalos_lib.skill_validators import VALIDATORS as _VALIDATORS
        known_validators = set(_VALIDATORS.keys())
    except Exception:
        known_validators = set()

    # Collect the union of skills referenced by any task.
    skills_in_use: set[str] = set()
    for t in plan_tasks:
        for s in (t.get("skills") or []):
            skills_in_use.add(str(s))

    # Only emit criteria for skills that (a) appear on a task AND (b) have
    # a registered validator function. Skills without a validator are pure
    # advisories — surfacing them as criteria would be noise.
    for skill in sorted(skills_in_use & known_validators):
        gate = _criterion_gate(skill)
        status, evidence = _read_validator_evidence(repo_root, wave_id, skill)
        by_gate[gate].append({
            "name": skill,
            "description": _CRITERION_DESCRIPTIONS.get(
                skill, f"Validator {skill!r} must pass."
            ),
            "status": status,
            "evidence": evidence,
        })
    return by_gate


def _enrich_gates_with_details(
    repo_root: Path,
    gates: dict[str, bool],
    wave_id: str,
    project_id: str = "default",
) -> list[dict[str, Any]]:
    """Return a list of gate detail dicts, one per gate G0..G5.

    Each dict has: id (int), key ('G<n>'), signed (bool), activities (list),
    criteria (list). The top-level `gates` dict (G<n> → bool) is preserved
    separately for backwards compatibility.
    """
    plan_tasks, plan_wave = _load_plan_doc(repo_root, project_id=project_id)
    # Prefer the wave id declared in PLAN.tasks.yaml; fall back to the
    # caller-supplied id (from worktree-state.json). This matters for
    # locating persisted validator evidence under
    # .signalos/skill-validation/<wave>/<skill>.json.
    effective_wave = plan_wave or wave_id
    activities_by_gate = _collect_gate_activities(repo_root, plan_tasks)
    criteria_by_gate = _collect_gate_criteria(repo_root, effective_wave, plan_tasks)
    out: list[dict[str, Any]] = []
    for i in range(6):
        key = f"G{i}"
        out.append({
            "id": i,
            "key": key,
            "signed": bool(gates.get(key)),
            "activities": activities_by_gate.get(i, []),
            "criteria": criteria_by_gate.get(i, []),
        })
    return out


def build_status_json(
    repo_root: str | Path,
    product_id: str | None = None,
    project_id: str = "default",
) -> dict[str, Any]:
    """Return the full status payload that `signalos status --json` emits.

    Wraps :func:`get_wave_status` and attaches the M3 per-gate `activities`
    and `criteria` arrays alongside the legacy `gates` boolean map. This
    is the function the verification command in the audit plan invokes:

        python -c "from signalos_lib.status import build_status_json; \\
                   import json; print(json.dumps(build_status_json('.')))"

    The *project_id* parameter namespaces the whole payload per
    WAVE-ENGINE-DESIGN §3.2: ids come from the `.signalos/projects.json`
    registry (Sidebar project picker / `signalos status --project-id`,
    appended by dispatch_cli); "default" remains the workspace-root
    namespace. Gate detection, plan activities and task state all resolve
    through the projects.py resolvers, and the value flows through to the
    returned payload so callers (IPC, CLI, tests) round-trip it.
    """
    root = Path(repo_root).resolve() if not isinstance(repo_root, Path) else repo_root.resolve()
    data = get_wave_status(root, product_id=product_id, project_id=project_id)
    data["gate_details"] = _enrich_gates_with_details(
        root, data.get("gates", {}), str(data.get("wave_id") or "-"),
        project_id=project_id,
    )
    return data


def get_wave_status(
    repo_root: Path,
    product_id: str | None = None,
    project_id: str = "default",
) -> dict[str, Any]:
    """Read all state from disk and return a status dict.

    When *product_id* is provided tasks are read from the product-scoped
    worktree-state.json (.signalos/products/<id>/worktree-state.json).
    Gate and belief state are always repo-level.

    Per WAVE-ENGINE-DESIGN §3.2 (Task #19), *project_id* namespaces the
    per-project wave state: with "default" the layout is the workspace
    root (unchanged); any other id reads worktree-state.json from
    `.signalos/projects/<project_id>/`, PLAN.tasks.yaml via
    projects.project_plan_path, and the gate/belief/governance artifacts
    via projects.project_governance_dir
    (`.signalos/projects/<project_id>/governance/` — §3.2 shipped).
    AUDIT_TRAIL.jsonl and the vault stay workspace-global.
    """
    gates = _detect_gates(repo_root, project_id=project_id)
    phase = _detect_phase(gates)
    belief_line = _read_belief_line(repo_root, project_id=project_id)
    scale_track = _read_scale_track(repo_root, project_id=project_id)
    delivery_mode = _read_delivery_mode(repo_root, project_id=project_id)
    tasks = _read_tasks(repo_root, product_id=product_id, project_id=project_id)
    wave_id = _detect_wave_id(repo_root, tasks, project_id=project_id)
    role, action_cmd = _next_action(gates, tasks)
    return {
        "wave_id": wave_id,
        "phase": phase,
        "belief_line": belief_line,
        "scale_track": scale_track,
        "delivery_mode": delivery_mode,
        "gates": gates,
        "tasks": tasks,
        "next_action": {"role": role, "command": action_cmd},
        "repo_root": str(repo_root),
        "product_id": product_id,
        "project_id": project_id,
        "ide": detect_ide(),
    }




# ---------------------------------------------------------------------------
# W3.2 — time_in_state helpers (AMD-CORE-015)
# ---------------------------------------------------------------------------

def _format_elapsed(seconds: float) -> str:
    """Return human-readable elapsed time: '4m 32s', '1h 20m', '< 1s'."""
    if seconds < 1:
        return "< 1s"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _task_elapsed(task: dict) -> str:
    """Return elapsed-time string for a task, or '' if not available."""
    import time as _time
    started = task.get("started_at") or task.get("start_time") or task.get("created_at")
    if not started:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        elapsed = _time.time() - dt.timestamp()
        return _format_elapsed(elapsed)
    except Exception:
        return ""

# ---------------------------------------------------------------------------
# Card renderer
# ---------------------------------------------------------------------------

def _pad(text: str, width: int) -> str:
    """Left-justify text padded to `width` chars (truncate if longer)."""
    if len(text) > width:
        text = text[:width - 1] + "…"
    return text.ljust(width)


def render_status_card(status: dict[str, Any]) -> str:
    """Render the ASCII status card from a status dict.

    Uses box-drawing characters ╔ ╠ ╚ ║ ═.
    """
    W = CARD_WIDTH
    sep_top    = "╔" + "═" * W + "╗"
    sep_mid    = "╠" + "═" * W + "╣"
    sep_bot    = "╚" + "═" * W + "╝"

    def row(text: str = "") -> str:
        return "║" + _pad("  " + text, W) + "║"

    lines = []
    lines.append(sep_top)

    # ── Header ──────────────────────────────────────────────────────────────
    wave_id = status.get("wave_id", "—")
    phase   = status.get("phase", "—")
    header  = f"SignalOS · Wave {wave_id} · {phase}"
    lines.append(row(header))
    lines.append(sep_mid)

    # ── Belief / Track ───────────────────────────────────────────────────────
    belief_line  = status.get("belief_line", "—")
    scale_track  = status.get("scale_track", "wave")
    delivery_mode = status.get("delivery_mode", "fresh-wave")
    lines.append(row(f"Belief  {belief_line[:50]}"))
    lines.append(row(f"Track   {scale_track} · Mode: {delivery_mode}"))
    lines.append(sep_mid)

    # ── Gates ────────────────────────────────────────────────────────────────
    lines.append(row("GATES"))
    gates = status.get("gates", {})
    gate_names = {
        "G0": "Onboarding",
        "G1": "Belief",
        "G2": "Planning",
        "G3": "Design",
        "G4": "Build",
        "G5": "Review",
    }
    # Two columns of 3 gates each
    gate_keys = list(gate_names.keys())
    gate_row1 = "  ".join(
        f"{'✓' if gates.get(k) else '○'} {k} {gate_names[k]:<10}"
        for k in gate_keys[:3]
    )
    gate_row2 = "  ".join(
        f"{'✓' if gates.get(k) else '○'} {k} {gate_names[k]:<10}"
        for k in gate_keys[3:]
    )
    lines.append(row(gate_row1))
    lines.append(row(gate_row2))
    lines.append(sep_mid)

    # ── Tasks ────────────────────────────────────────────────────────────────
    # Header
    task_hdr = f"{'TASKS':<34}{'TIER':<8}STATUS"
    lines.append(row(task_hdr))
    tasks = status.get("tasks", [])
    if not tasks:
        lines.append(row("No active tasks"))
    else:
        for task in tasks[:8]:  # cap at 8 rows to keep card bounded
            icon   = _task_icon(task.get("status", ""))
            name   = task.get("task") or task.get("branch", "?")
            if len(str(name)) > 26:
                name = str(name)[:25] + "…"
            tier   = _infer_tier(task)
            tstatus = (task.get("status") or "active").upper()
            elapsed = _task_elapsed(task)
            time_tag = f" ({elapsed})" if elapsed else ""
            task_line = f"{icon}  {str(name):<28}{tier:<6}{tstatus}{time_tag}"
            lines.append(row(task_line))
    lines.append(sep_mid)

    # ── Next action ──────────────────────────────────────────────────────────
    lines.append(row("NEXT ACTION"))
    na = status.get("next_action", {})
    role = na.get("role", "—")
    cmd  = na.get("command", "—")
    lines.append(row(f"{role} → {cmd}"))
    lines.append(sep_bot)

    return "\n".join(lines)



# ---------------------------------------------------------------------------
# W3.2 — watch mode (AMD-CORE-015 T1)
# ---------------------------------------------------------------------------

def _clear_screen() -> None:
    """ANSI clear + cursor-home."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _inotifywait_available() -> bool:
    """Return True if inotifywait is present on PATH."""
    import shutil
    return shutil.which("inotifywait") is not None


def _watch_with_inotify(watch_dir: str, timeout: float = 5.0) -> bool:
    """
    Block until inotifywait fires an event in *watch_dir*, or *timeout* elapses.
    Returns True if an event was detected, False on timeout.
    """
    import subprocess as _sp
    try:
        result = _sp.run(
            ["inotifywait", "-r", "-q", "--timeout", str(int(timeout)),
             "-e", "modify,create,moved_to", watch_dir],
            capture_output=True, timeout=timeout + 2,
        )
        return result.returncode == 0
    except Exception:
        return False


def watch_status(
    repo_root: Path | None = None,
    interval: float = 2.0,
    clear: bool = True,
) -> None:
    """
    Continuously refresh the status card on any journal/daemon-events change.

    Strategy:
      1. Try inotifywait on .signalos/ (Linux inotify).
      2. Fall back to polling every *interval* seconds.

    Ctrl-C exits cleanly.
    """
    import time as _time

    root = repo_root or _repo_root()
    watch_dir = str(root / REPO_ROOT_MARKER)
    use_inotify = _inotifywait_available()

    if use_inotify:
        sys.stderr.write(f"  watch: inotifywait on {watch_dir}\n")
    else:
        sys.stderr.write(
            f"  watch: polling every {interval}s (install inotify-tools for event-driven mode)\n"
        )

    def _render() -> None:
        if clear:
            _clear_screen()
        print_status_card(root)

    try:
        _render()
        while True:
            if use_inotify:
                _watch_with_inotify(watch_dir, timeout=interval)
            else:
                _time.sleep(interval)
            _render()
    except KeyboardInterrupt:
        sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Convenience wrapper

# ---------------------------------------------------------------------------
# W4.2 — multi-product aggregated dashboard (AMD-CORE-020)
# ---------------------------------------------------------------------------

def render_multi_product_dashboard(records: list[dict[str, Any]]) -> str:
    """Render an ASCII dashboard summarising all registered products.

    Each record is a dict from tenant.product_status().
    """
    W = CARD_WIDTH
    sep_top = "╔" + "═" * W + "╗"
    sep_mid = "╠" + "═" * W + "╣"
    sep_bot = "╚" + "═" * W + "╝"

    def row(text: str = "") -> str:
        return "║" + _pad("  " + text, W) + "║"

    lines = [sep_top]
    lines.append(row("SignalOS · Multi-Product Dashboard"))
    lines.append(sep_mid)

    if not records:
        lines.append(row("No product namespaces registered."))
        lines.append(row("  signalos tenant init <id>  to create one."))
    else:
        hdr = f"{'PRODUCT':<20} {'C':<3} {'S':<3} {'SESS':>5} {'TASKS':>5} {'VALID':<6}"
        lines.append(row(hdr))
        lines.append(row("─" * (W - 2)))
        for r in records:
            c = "✓" if r["constitution"] else "✗"
            s = "✓" if r["soul_document"] else "✗"
            v = "✓" if r["valid"] else "✗"
            data_row = (
                f"{r['product_id']:<20} {c:<3} {s:<3} "
                f"{r['session_count']:>5} {r['active_tasks']:>5} {v:<6}"
            )
            lines.append(row(data_row))
        lines.append(row())
        lines.append(row("C=Constitution  S=Soul-Document  SESS=sessions  VALID=both ok"))

    lines.append(sep_bot)
    return "\n".join(lines)


# ---------------------------------------------------------------------------

def print_status_card(
    repo_root: Path | None = None,
    product_id: str | None = None,
    project_id: str = "default",
) -> None:
    """Print the status card for the given repo root (or cwd).

    When *product_id* is provided tasks are scoped to that product
    namespace. When None and products exist the multi-product dashboard
    is printed instead of the single-product card.

    *project_id* namespaces the card per WAVE-ENGINE-DESIGN §3.2 — ids
    come from the `.signalos/projects.json` registry (Sidebar picker /
    `--project-id`); "default" remains the workspace-root namespace.
    """
    root = repo_root or _repo_root()

    if product_id is None:
        # Auto-detect: show multi-product dashboard when products are registered
        try:
            from signalos_lib.tenant import list_products, multi_product_summary
            products = list_products(root)
        except Exception:
            products = []
        if products:
            records = multi_product_summary(root)
            sys.stdout.write(render_multi_product_dashboard(records) + "\n")
            return

    status = get_wave_status(root, product_id=product_id, project_id=project_id)
    card = render_status_card(status)
    sys.stdout.write(card + "\n")
