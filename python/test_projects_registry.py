"""test_projects_registry.py — .signalos/projects.json registry (Task #19).

Covers the registry CRUD contract (absent-file default, reserved id,
slug collisions, atomic writes) plus the project_state_dir namespacing
that status.py / orchestrator.py derive their worktree-state paths from.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import projects
from signalos_lib.projects import (
    create_project,
    get_active_project,
    list_projects,
    project_state_dir,
    registry_path,
    set_active_project,
)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / ".signalos").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Absent-file backward compatibility
# ---------------------------------------------------------------------------


def test_get_active_project_absent_file_is_default(root: Path) -> None:
    assert not registry_path(root).exists()
    assert get_active_project(root) == "default"
    # Reading must not materialize the file.
    assert not registry_path(root).exists()


def test_list_projects_absent_file_has_implicit_default(root: Path) -> None:
    reg = list_projects(root)
    assert reg["active"] == "default"
    assert [p["id"] for p in reg["projects"]] == ["default"]


def test_get_active_project_corrupt_file_is_default(root: Path) -> None:
    registry_path(root).write_text("{not json", encoding="utf-8")
    assert get_active_project(root) == "default"


def test_dangling_active_pointer_falls_back_to_default(root: Path) -> None:
    registry_path(root).write_text(json.dumps({
        "schema_version": projects.SCHEMA_VERSION,
        "active": "ghost",
        "projects": {"default": {"name": "Default", "created_at": ""}},
    }), encoding="utf-8")
    assert get_active_project(root) == "default"


# ---------------------------------------------------------------------------
# create / switch
# ---------------------------------------------------------------------------


def test_create_project_slugifies_and_switches(root: Path) -> None:
    project = create_project(root, "My Cool App!")
    assert project["id"] == "my-cool-app"
    assert project["name"] == "My Cool App!"
    assert project["created_at"]

    # Creating switches the active project.
    assert get_active_project(root) == "my-cool-app"

    on_disk = json.loads(registry_path(root).read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == "signalos.projects.v1"
    assert on_disk["active"] == "my-cool-app"
    assert set(on_disk["projects"]) == {"default", "my-cool-app"}


def test_create_project_collision_suffixes(root: Path) -> None:
    assert create_project(root, "Alpha")["id"] == "alpha"
    assert create_project(root, "alpha")["id"] == "alpha-2"
    assert create_project(root, "ALPHA!!")["id"] == "alpha-3"
    reg = list_projects(root)
    assert [p["id"] for p in reg["projects"]] == [
        "default", "alpha", "alpha-2", "alpha-3",
    ]
    assert reg["active"] == "alpha-3"


def test_create_project_default_is_reserved(root: Path) -> None:
    with pytest.raises(ValueError, match="reserved"):
        create_project(root, "Default")
    with pytest.raises(ValueError, match="reserved"):
        create_project(root, "  default  ")


def test_create_project_rejects_unusable_names(root: Path) -> None:
    with pytest.raises(ValueError):
        create_project(root, "")
    with pytest.raises(ValueError):
        create_project(root, "   ")
    with pytest.raises(ValueError):
        create_project(root, "!!!")


def test_set_active_project_round_trip(root: Path) -> None:
    create_project(root, "Alpha")
    assert set_active_project(root, "default") == "default"
    assert get_active_project(root) == "default"
    assert set_active_project(root, "alpha") == "alpha"
    assert get_active_project(root) == "alpha"


def test_set_active_project_unknown_id_raises(root: Path) -> None:
    with pytest.raises(ValueError, match="unknown project id"):
        set_active_project(root, "ghost")


def test_set_active_default_works_without_prior_file(root: Path) -> None:
    # "default" always exists, even before the registry file does.
    assert set_active_project(root, "default") == "default"
    assert registry_path(root).is_file()


# ---------------------------------------------------------------------------
# Atomicity — a failed write never leaves a partial registry
# ---------------------------------------------------------------------------


def test_failed_write_leaves_old_registry_intact(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_project(root, "Alpha")
    before = registry_path(root).read_text(encoding="utf-8")

    def _boom(*args, **kwargs):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(projects.json, "dump", _boom)
    with pytest.raises(OSError, match="disk full"):
        create_project(root, "Beta")

    # Old file byte-identical, no tmp litter, registry still parses.
    assert registry_path(root).read_text(encoding="utf-8") == before
    assert list((root / ".signalos").glob("*.tmp")) == []
    assert get_active_project(root) == "alpha"


def test_failed_write_with_no_prior_file_leaves_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(projects.json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        create_project(root, "Alpha")
    assert not registry_path(root).exists()
    assert list((root / ".signalos").glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Namespaced state layout (project_state_dir + consumers)
# ---------------------------------------------------------------------------


def test_project_state_dir_layout(root: Path) -> None:
    assert project_state_dir(root, "default") == root / ".signalos"
    assert (
        project_state_dir(root, "alpha")
        == root / ".signalos" / "projects" / "alpha"
    )


def _write_worktree_state(root: Path, project_id: str, wave: str) -> None:
    state_dir = project_state_dir(root, project_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "worktree-state.json").write_text(json.dumps({
        "wave_id": wave,
        "worktrees": [{
            "wave": wave,
            "task": f"task-{project_id}",
            "step_id": f"step-{project_id}",
            "branch": f"feat/{project_id}",
            "status": "active",
        }],
    }), encoding="utf-8")


def test_status_reads_project_scoped_worktree_state(root: Path) -> None:
    from signalos_lib.status import get_wave_status

    _write_worktree_state(root, "alpha", "7")
    alpha = get_wave_status(root, project_id="alpha")
    assert [t["task"] for t in alpha["tasks"]] == ["task-alpha"]
    assert alpha["wave_id"] == "7"
    assert alpha["project_id"] == "alpha"

    # Default project does NOT see alpha's tasks (isolation both ways).
    default = get_wave_status(root)
    assert default["tasks"] == []
    assert default["wave_id"] == "—"


def test_orchestrator_reads_project_scoped_worktree_state(root: Path) -> None:
    from signalos_lib.orchestrator import _read_tasks, _state_file

    _write_worktree_state(root, "alpha", "7")
    assert _state_file(root, "alpha") == (
        root / ".signalos" / "projects" / "alpha" / "worktree-state.json"
    )
    assert [t["task"] for t in _read_tasks(root, "alpha")] == ["task-alpha"]
    assert _read_tasks(root) == []


def test_wave_engine_state_round_trip_does_not_collide(root: Path) -> None:
    """Project "alpha" engine state lands in .signalos/projects/alpha/ and
    never collides with the default project's state."""
    from signalos_lib.wave_engine import load_persisted_state, save_persisted_state

    save_persisted_state(root, {"state": "DISPATCH", "current_gate": "G2"}, "alpha")
    save_persisted_state(root, {"state": "ENTRY", "current_gate": "G0"})

    assert (root / ".signalos" / "projects" / "alpha" / "wave-engine-state.json").is_file()
    assert (root / ".signalos" / "wave-engine-state.json").is_file()

    assert load_persisted_state(root, "alpha")["current_gate"] == "G2"
    assert load_persisted_state(root)["current_gate"] == "G0"
