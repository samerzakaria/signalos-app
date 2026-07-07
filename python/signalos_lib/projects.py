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


def registry_path(root: Path | str) -> Path:
    """Return the on-disk registry file for *root*."""
    return Path(root) / ".signalos" / "projects.json"


def project_state_dir(root: Path | str, project_id: str = DEFAULT_PROJECT_ID) -> Path:
    """Resolve the per-project state directory (per §3.2).

    "default" → workspace-root `.signalos/` (today's layout, unchanged);
    any other id → `.signalos/projects/<project_id>/`.
    """
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


def _load(root: Path | str) -> dict[str, Any]:
    """Read the registry, falling back to the implicit default registry.

    Missing / empty / corrupt files and malformed shapes all normalize to
    the default registry — the registry can never make a workspace
    unusable. The "default" project is re-inserted if a hand-edited file
    dropped it, and a dangling "active" pointer falls back to "default".
    """
    path = registry_path(root)
    data: Any = None
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
    if not isinstance(data, dict):
        return _default_registry()

    projects = data.get("projects")
    if not isinstance(projects, dict):
        projects = {}
    normalized: dict[str, Any] = {}
    for pid, meta in projects.items():
        if not isinstance(pid, str) or not pid:
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
    path = registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


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

    reg = _load(root)
    candidate = slug
    suffix = 2
    while candidate in reg["projects"]:
        candidate = f"{slug}-{suffix}"
        suffix += 1

    entry = {"name": display, "created_at": _now_iso()}
    reg["projects"][candidate] = entry
    # Creating a project switches to it (Task #19 contract).
    reg["active"] = candidate
    _save(root, reg)
    return {"id": candidate, **entry}


def set_active_project(root: Path | str, project_id: str) -> str:
    """Switch the active project. The id must exist in the registry
    ("default" always exists). Returns the new active id."""
    pid = str(project_id or "").strip()
    reg = _load(root)
    if pid not in reg["projects"]:
        raise ValueError(f"unknown project id: {pid!r}")
    reg["active"] = pid
    _save(root, reg)
    return pid


def get_active_project(root: Path | str) -> str:
    """Return the active project id — "default" when the registry file is
    absent, empty, or corrupt (full backward compatibility)."""
    return _load(root)["active"]
