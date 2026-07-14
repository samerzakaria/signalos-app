"""projects.py — multi-project registry (WAVE-ENGINE-DESIGN §3.2, Task #19).

Owns `.signalos/projects.json`, the workspace-level registry that maps
project ids to display metadata and records which project is *active*:

    {
      "schema_version": "signalos.projects.v1",
      "active": "default",
      "projects": {
        "default": {"name": "Default", "created_at": "..."},
        "alpha":   {"name": "Alpha",   "created_at": "..."}
      }
    }

Contract:
  - The "default" project ALWAYS exists (implicitly when the file is
    absent) and its id is reserved — `create_project` refuses names that
    slugify to "default".
  - `get_active_project` returns "default" when the registry file is
    absent, empty, or corrupt — full backward compatibility with
    single-project workspaces that never touched the registry.
  - All writes are atomic (tmp file + os.replace, following the
    `pause._atomic_write_json` / `_worktree_state` pattern) so a crash
    mid-write never leaves a partial registry on disk.

Path namespacing: `project_state_dir` is the single source of truth for
the per-project state layout (mirrors `wave_engine._state_file_path`):
"default" keeps today's workspace-root `.signalos/` layout; any other id
namespaces under `.signalos/projects/<project_id>/`.

Shared-vs-per-project: per-project *state* lives in the namespace
(wave-engine-state.json, worktree-state.json, PLAN.tasks.yaml via
`project_plan_path`) and so do the signed gate artifacts (§3.2 shipped —
`project_governance_dir` is the base root under which the canonical
`core/...` gate-artifact rel_paths resolve). Workspace-global things —
AUDIT_TRAIL.jsonl (one append-only chain per workspace), the vault,
git checkpoints, sessions/, missing-deps.json — intentionally do NOT
move.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_PROJECT_ID",
    "validate_project_id",
    "registry_path",
    "project_state_dir",
    "project_plan_path",
    "project_governance_dir",
    "list_projects",
    "create_project",
    "set_active_project",
    "get_active_project",
]


SCHEMA_VERSION = "signalos.projects.v1"
DEFAULT_PROJECT_ID = "default"
_PROJECT_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")


def validate_project_id(value: str) -> str:
    """Return one canonical registry/path segment or fail closed."""
    if not isinstance(value, str):
        raise ValueError("project_id must be a string")
    project_id = value.strip()
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError(
            "project_id must be 1-64 lowercase letters, numbers, or hyphens"
        )
    return project_id


def registry_path(root: Path | str) -> Path:
    """Return the on-disk registry file for *root*."""
    return Path(root) / ".signalos" / "projects.json"


def _safe_registry_path(root: Path | str) -> Path:
    """Return the registry path only when no authority path is redirected.

    ``projects.json`` selects the active project namespace for every request,
    so both its parent directory and the file itself are authority-bearing.
    Refuse symlinks and Windows junction/reparse aliases instead of following
    them outside (or elsewhere inside) the selected workspace.
    """
    workspace = Path(root).resolve()
    cursor = workspace
    for part in (".signalos", "projects.json"):
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            try:
                resolved = cursor.resolve()
            except OSError as exc:
                raise ValueError("project registry path cannot be resolved safely") from exc
            try:
                resolved.relative_to(workspace)
            except ValueError as exc:
                raise ValueError(
                    "project registry path resolves outside the workspace"
                ) from exc
            if cursor.is_symlink() or resolved != cursor.absolute():
                raise ValueError(
                    "project registry path must not traverse a symlink or junction"
                )
    return cursor


def project_state_dir(root: Path | str, project_id: str = DEFAULT_PROJECT_ID) -> Path:
    """Resolve the per-project state directory (per §3.2).

    "default" → workspace-root `.signalos/` (today's layout, unchanged);
    any other id → `.signalos/projects/<project_id>/`.
    """
    project_id = validate_project_id(project_id)
    base = Path(root) / ".signalos"
    if project_id == DEFAULT_PROJECT_ID:
        return base
    return base / "projects" / project_id


def project_plan_path(root: Path | str, project_id: str = DEFAULT_PROJECT_ID) -> Path:
    """Resolve the per-project PLAN.tasks.yaml location.

    "default" → workspace-root `PLAN.tasks.yaml` (today's layout, byte-
    identical); any other id → `.signalos/projects/<project_id>/PLAN.tasks.yaml`
    (inside the project's state dir, next to its worktree-state.json).
    A missing per-project plan behaves exactly like a missing root plan —
    callers treat the path uniformly.
    """
    if project_id == DEFAULT_PROJECT_ID:
        return Path(root) / "PLAN.tasks.yaml"
    return project_state_dir(root, project_id) / "PLAN.tasks.yaml"


def project_governance_dir(root: Path | str, project_id: str = DEFAULT_PROJECT_ID) -> Path:
    """Resolve the base root under which the signed gate artifacts live
    (WAVE-ENGINE-DESIGN §3.2 — shipped).

    The gate manifest (gate_artifacts.json) addresses artifacts with
    rel_paths spanning THREE canonical subtrees — `core/governance/...`,
    `core/strategy/...`, `core/execution/...` — so the resolver returns a
    *base root*, not a single directory, and every rel_path stays
    byte-identical under it:

      "default" → the workspace root itself (today's layout, unchanged:
                  `<root>/core/governance/...` etc.);
      any other id → `.signalos/projects/<project_id>/governance/`
                  (so `<base>/core/governance/...`, `<base>/core/strategy/...`).

    Layout choice: `.signalos/projects/<id>/governance/` was picked over
    `core/governance/projects/<id>/` because (a) the artifact set is not
    confined to core/governance — nesting core/strategy under
    core/governance would break the relative structure, while a base-dir
    swap keeps it identical; (b) it keeps ALL per-project state under the
    one `.signalos/projects/<id>/` home that project_state_dir already
    owns; and (c) `.signalos/**` is already excluded from workspace
    snapshots/scans, so per-project gate artifacts never leak into
    evidence-freshness or scope-card scans.

    Every gate-artifact reader/writer (sign.py, wave_engine.inspect,
    status gate detection, orchestrator gating, validate-wave-status,
    product.gate_orchestrator) MUST resolve through this function so the
    engine and the status board can never disagree about where a
    project's gates live.
    """
    project_id = validate_project_id(project_id)
    if project_id == DEFAULT_PROJECT_ID:
        return Path(root)
    return project_state_dir(root, project_id) / "governance"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_registry() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "active": DEFAULT_PROJECT_ID,
        "projects": {
            DEFAULT_PROJECT_ID: {"name": "Default", "created_at": ""},
        },
    }


def _load(
    root: Path | str,
    *,
    for_mutation: bool = False,
) -> dict[str, Any]:
    """Read the registry, falling back to the implicit default registry.

    Missing / empty / corrupt files and malformed shapes all normalize to
    the default registry — the registry can never make a workspace
    unusable. The "default" project is re-inserted if a hand-edited file
    dropped it, and a dangling "active" pointer falls back to "default".
    """
    path = _safe_registry_path(root)
    data: Any = None
    if path.exists() and not path.is_file():
        if for_mutation:
            raise ValueError("project registry is corrupt: projects.json is not a file")
        return _default_registry()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            if for_mutation:
                raise ValueError(
                    f"project registry is corrupt and was not modified: {exc}"
                ) from exc
            data = None
    if not isinstance(data, dict):
        if for_mutation and path.exists():
            raise ValueError(
                "project registry is corrupt and was not modified: expected a JSON object"
            )
        return _default_registry()

    if for_mutation:
        problems: list[str] = []
        if data.get("schema_version") != SCHEMA_VERSION:
            problems.append(f"schema_version must be {SCHEMA_VERSION!r}")
        raw_projects = data.get("projects")
        if not isinstance(raw_projects, dict):
            problems.append("projects must be an object")
            raw_projects = {}
        for raw_pid, raw_meta in raw_projects.items():
            if not isinstance(raw_pid, str):
                problems.append("every project id must be a string")
                continue
            try:
                canonical_pid = validate_project_id(raw_pid)
            except ValueError:
                problems.append(f"invalid project id {raw_pid!r}")
                continue
            if canonical_pid != raw_pid:
                problems.append(f"non-canonical project id {raw_pid!r}")
            if not isinstance(raw_meta, dict):
                problems.append(f"metadata for {raw_pid!r} must be an object")
                continue
            if not isinstance(raw_meta.get("name"), str) or not raw_meta["name"].strip():
                problems.append(f"metadata for {raw_pid!r} requires a name")
            if not isinstance(raw_meta.get("created_at"), str):
                problems.append(f"metadata for {raw_pid!r} requires created_at")
        if DEFAULT_PROJECT_ID not in raw_projects:
            problems.append("the default project is missing")
        raw_active = data.get("active")
        if not isinstance(raw_active, str) or raw_active not in raw_projects:
            problems.append("active must name a registered project")
        if problems:
            raise ValueError(
                "project registry is corrupt and was not modified: "
                + "; ".join(problems)
            )

    projects = data.get("projects")
    if not isinstance(projects, dict):
        projects = {}
    normalized: dict[str, Any] = {}
    for pid, meta in projects.items():
        if not isinstance(pid, str):
            continue
        try:
            pid = validate_project_id(pid)
        except ValueError:
            continue
        meta = meta if isinstance(meta, dict) else {}
        normalized[pid] = {
            "name": str(meta.get("name") or pid),
            "created_at": str(meta.get("created_at") or ""),
        }
    normalized.setdefault(
        DEFAULT_PROJECT_ID, {"name": "Default", "created_at": ""},
    )

    active = data.get("active")
    if not isinstance(active, str) or active not in normalized:
        active = DEFAULT_PROJECT_ID

    return {
        "schema_version": SCHEMA_VERSION,
        "active": active,
        "projects": normalized,
    }


def _save(root: Path | str, registry: dict[str, Any]) -> None:
    """Atomically write *registry* to `.signalos/projects.json`.

    tmp file + os.replace so a crash mid-write leaves either the old file
    or the new one — never a partial. On failure the tmp file is removed
    and the exception propagates (registry mutations must not fail
    silently: the caller reported success to the user).
    """
    path = _safe_registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Re-check after materialising the parent.  This also catches a leaf that
    # appeared between mutation validation and the atomic replacement.
    path = _safe_registry_path(root)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(registry, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _slugify(name: str) -> str:
    """Lowercase, alphanumeric-plus-hyphen project id from a display name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:64].rstrip("-")


def list_projects(root: Path | str) -> dict[str, Any]:
    """Return {"active": <id>, "projects": [{id, name, created_at}, ...]}.

    Works without a registry file (implicit default-only registry).
    """
    reg = _load(root)
    projects = [
        {"id": pid, "name": meta["name"], "created_at": meta["created_at"]}
        for pid, meta in reg["projects"].items()
    ]
    # Stable order: default first, then creation order (dict insertion).
    projects.sort(key=lambda p: (p["id"] != DEFAULT_PROJECT_ID,))
    return {"active": reg["active"], "projects": projects}


def create_project(root: Path | str, name: str) -> dict[str, Any]:
    """Register a new project and make it active.

    The id is the slugified *name*; collisions get a numeric suffix
    ("alpha", "alpha-2", "alpha-3", ...). "default" is reserved.
    Returns {"id", "name", "created_at"}. Raises ValueError on an
    unusable name.
    """
    display = str(name or "").strip()
    if not display:
        raise ValueError("project name must not be empty")
    slug = _slugify(display)
    if not slug:
        raise ValueError(
            "project name must contain at least one alphanumeric character"
        )
    if slug == DEFAULT_PROJECT_ID:
        raise ValueError("'default' is a reserved project id")

    reg = _load(root, for_mutation=True)
    candidate = slug
    suffix = 2
    while candidate in reg["projects"]:
        suffix_text = f"-{suffix}"
        candidate = f"{slug[:64 - len(suffix_text)].rstrip('-')}{suffix_text}"
        suffix += 1
    validate_project_id(candidate)

    entry = {"name": display, "created_at": _now_iso()}
    reg["projects"][candidate] = entry
    # Creating a project switches to it (Task #19 contract).
    reg["active"] = candidate
    _save(root, reg)
    return {"id": candidate, **entry}


def set_active_project(root: Path | str, project_id: str) -> str:
    """Switch the active project. The id must exist in the registry
    ("default" always exists). Returns the new active id."""
    pid = validate_project_id(project_id)
    reg = _load(root, for_mutation=True)
    if pid not in reg["projects"]:
        raise ValueError(f"unknown project id: {pid!r}")
    reg["active"] = pid
    _save(root, reg)
    return pid


def get_active_project(root: Path | str) -> str:
    """Return the active project id — "default" when the registry file is
    absent, empty, or corrupt (full backward compatibility)."""
    return _load(root)["active"]
