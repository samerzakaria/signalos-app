from __future__ import annotations

import signalos_ipc_server as ipc


def test_help_alias_is_handled_by_sidecar() -> None:
    resp = ipc.route("req-1", "help", [])

    assert resp["ok"] is True
    assert "Supported commands" in resp["output"]
    assert "Unknown command" not in resp["output"]


def test_visible_signalos_status_alias_maps_to_core_command(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_dispatch(command: str, args: list[str], req_id: str = "", project_id: str = "default") -> str:
        seen.update(command=command, args=args, req_id=req_id, project_id=project_id)
        return "status ok"

    monkeypatch.setattr(ipc, "dispatch_cli", fake_dispatch)

    resp = ipc.route("req-2", "signalos status", [], project_id="demo")

    assert resp["ok"] is True
    assert resp["output"] == "status ok"
    assert seen == {
        "command": "signal-status",
        "args": [],
        "req_id": "req-2",
        "project_id": "demo",
    }


def test_git_status_alias_does_not_dead_end_without_git_repo(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    resp = ipc.route("req-3", "git status", [])

    assert resp["ok"] is True
    assert "No git repository" in resp["output"]
    assert "Unknown command" not in resp["output"]


def test_npm_run_dev_alias_points_to_preview_runner() -> None:
    resp = ipc.route("req-4", "npm run dev", [])

    assert resp["ok"] is True
    assert "Preview tab" in resp["output"]
