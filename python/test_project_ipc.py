"""test_project_ipc.py — project:* IPC commands + active-project threading (Task #19).

Covers:
  - project:list / project:create / project:switch routing and contracts
  - delivery-active refusal (create/switch while _ACTIVE_DELIVERIES holds runs)
  - resolution precedence: explicit req["project_id"] > registry active > "default"
  - dispatch_cli --project-id passthrough to project-aware CLI subcommands
  - wave-engine state landing in the active project's namespace end-to-end
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import signalos_ipc_server as ipc
from signalos_lib.projects import create_project, registry_path, set_active_project


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".signalos").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _handle(command: str, args: list | None = None, project_id: str | None = None) -> dict:
    req = {
        "id": "test-req",
        "command": command,
        "args": args or [],
        "cwd": os.getcwd(),
    }
    if project_id is not None:
        req["project_id"] = project_id
    return ipc.handle(req)


# ---------------------------------------------------------------------------
# project:list / project:create / project:switch
# ---------------------------------------------------------------------------


def test_project_list_fresh_workspace(workspace: Path) -> None:
    resp = _handle("project:list")
    assert resp["ok"], resp
    data = resp["data"]
    assert data["status"] == "ok"
    assert data["active"] == "default"
    assert [p["id"] for p in data["projects"]] == ["default"]


def test_project_create_switches_and_persists(workspace: Path) -> None:
    resp = _handle("project:create", [json.dumps({"name": "Alpha App"})])
    assert resp["ok"], resp
    data = resp["data"]
    assert data["status"] == "ok"
    assert data["project"]["id"] == "alpha-app"
    assert data["active"] == "alpha-app"
    assert registry_path(workspace).is_file()

    listing = _handle("project:list")["data"]
    assert listing["active"] == "alpha-app"
    assert {p["id"] for p in listing["projects"]} == {"default", "alpha-app"}


def test_project_create_requires_name(workspace: Path) -> None:
    resp = _handle("project:create", [json.dumps({})])
    assert not resp["ok"]
    assert "name" in resp["error"]


def test_project_create_reserved_name_is_domain_error(workspace: Path) -> None:
    resp = _handle("project:create", [json.dumps({"name": "default"})])
    assert resp["ok"]  # IPC succeeded; domain refusal in data
    assert resp["data"]["status"] == "error"
    assert "reserved" in resp["data"]["error"]


def test_project_switch_round_trip(workspace: Path) -> None:
    _handle("project:create", [json.dumps({"name": "Alpha"})])
    resp = _handle("project:switch", [json.dumps({"project_id": "default"})])
    assert resp["ok"], resp
    assert resp["data"] == {"status": "ok", "active": "default"}

    resp = _handle("project:switch", [json.dumps({"project_id": "alpha"})])
    assert resp["data"]["active"] == "alpha"


def test_project_switch_unknown_id_is_domain_error(workspace: Path) -> None:
    resp = _handle("project:switch", [json.dumps({"project_id": "ghost"})])
    assert resp["ok"]
    assert resp["data"]["status"] == "error"
    assert "ghost" in resp["data"]["error"]


def test_project_switch_requires_project_id(workspace: Path) -> None:
    resp = _handle("project:switch", [json.dumps({})])
    assert not resp["ok"]


# ---------------------------------------------------------------------------
# Safety: no project change while a delivery is running
# ---------------------------------------------------------------------------


def test_switch_refused_while_delivery_active(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_project(workspace, "Alpha")
    monkeypatch.setitem(ipc._ACTIVE_DELIVERIES, "run-123", object())

    resp = _handle("project:switch", [json.dumps({"project_id": "default"})])
    assert resp["ok"]
    assert resp["data"]["status"] == "delivery-active"
    assert resp["data"]["runs"] == ["run-123"]
    # Active project unchanged.
    assert _handle("project:list")["data"]["active"] == "alpha"


def test_create_refused_while_delivery_active(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(ipc._ACTIVE_DELIVERIES, "run-9", object())
    resp = _handle("project:create", [json.dumps({"name": "Beta"})])
    assert resp["data"]["status"] == "delivery-active"
    assert _handle("project:list")["data"]["projects"] == [
        {"id": "default", "name": "Default", "created_at": ""},
    ]


# ---------------------------------------------------------------------------
# Resolution precedence: explicit project_id > registry active > "default"
# ---------------------------------------------------------------------------


@pytest.fixture()
def captured_route(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def fake_route(req_id, command, args, project_id="default"):
        captured["project_id"] = project_id
        return ipc.ok(req_id, output="stub")

    monkeypatch.setattr(ipc, "route", fake_route)
    return captured


def test_no_registry_resolves_default(workspace: Path, captured_route: dict) -> None:
    _handle("state:wave")
    assert captured_route["project_id"] == "default"


def test_registry_active_project_is_threaded(
    workspace: Path, captured_route: dict,
) -> None:
    create_project(workspace, "Alpha")
    _handle("state:wave")
    assert captured_route["project_id"] == "alpha"


def test_explicit_project_id_wins_over_active(
    workspace: Path, captured_route: dict,
) -> None:
    create_project(workspace, "Alpha")
    set_active_project(workspace, "alpha")
    _handle("state:wave", project_id="beta")
    assert captured_route["project_id"] == "beta"


def test_agent_route_gets_active_project(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_route_agent(req_id, command, raw_args, project_id="default"):
        captured["project_id"] = project_id
        return ipc.ok(req_id, output="stub")

    monkeypatch.setattr(ipc, "route_agent", fake_route_agent)
    create_project(workspace, "Alpha")
    _handle("agent:run", [json.dumps({"prompt": "x"})])
    assert captured["project_id"] == "alpha"


# ---------------------------------------------------------------------------
# CLI --project-id passthrough (dispatch_cli)
# ---------------------------------------------------------------------------


@pytest.fixture()
def captured_cli(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def fake_run_core_cli(argv, req_id=""):
        captured["argv"] = list(argv)
        return 0, "ok", ""

    monkeypatch.setattr(ipc, "run_core_cli", fake_run_core_cli)
    return captured


def test_status_cli_gets_project_id(workspace: Path, captured_cli: dict) -> None:
    out = ipc.dispatch_cli("signal-status", [], project_id="alpha")
    assert out == "ok"
    argv = captured_cli["argv"]
    assert argv[0] == "status"
    assert argv[-2:] == ["--project-id", "alpha"]


def test_orchestrate_cli_gets_project_id(workspace: Path, captured_cli: dict) -> None:
    ipc.dispatch_cli("signalos-orchestrate", ["--wave", "1"], project_id="alpha")
    argv = captured_cli["argv"]
    assert argv[0] == "orchestrate"
    assert argv[-2:] == ["--project-id", "alpha"]


def test_explicit_project_id_flag_not_duplicated(
    workspace: Path, captured_cli: dict,
) -> None:
    # "signalos-status" (unlike "signal-status") forwards user args, so an
    # explicit --project-id can reach argv and must win over the resolved one.
    ipc.dispatch_cli("signalos-status", ["--project-id", "beta"], project_id="alpha")
    argv = captured_cli["argv"]
    assert argv.count("--project-id") == 1
    assert "beta" in argv
    assert "alpha" not in argv


def test_non_project_aware_cli_untouched(workspace: Path, captured_cli: dict) -> None:
    ipc.dispatch_cli("signal-release-readiness", [], project_id="alpha")
    assert "--project-id" not in captured_cli["argv"]


# ---------------------------------------------------------------------------
# End-to-end: wave engine state lands in the active project's namespace
# ---------------------------------------------------------------------------


def test_wave_begin_persists_into_active_project_namespace(workspace: Path) -> None:
    _handle("project:create", [json.dumps({"name": "Alpha"})])

    resp = _handle("wave:begin", ["Build a todo app"])  # no explicit project_id
    assert resp["ok"], resp

    alpha_state = workspace / ".signalos" / "projects" / "alpha" / "wave-engine-state.json"
    default_state = workspace / ".signalos" / "wave-engine-state.json"
    assert alpha_state.is_file()
    assert not default_state.exists()
    assert json.loads(alpha_state.read_text(encoding="utf-8"))["project_id"] == "alpha"

    # Explicit project_id still wins over the active project.
    resp = _handle("wave:begin", ["Build a todo app"], project_id="default")
    assert resp["ok"], resp
    assert default_state.is_file()
