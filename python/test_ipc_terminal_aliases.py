from __future__ import annotations

import ast
import inspect
import json

import signalos_ipc_server as ipc
from signalos_lib.cli import _build_parser
from signalos_lib.skill_catalog import BUNDLE_ROOT


def _direct_alias_literals() -> list[str]:
    """Extract the literal string entries of the ``direct`` set defined inside
    ``map_slash_command`` so the guard stays in lockstep with the source."""
    tree = ast.parse(inspect.getsource(ipc.map_slash_command))
    literals: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "direct" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Set):
            continue
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                literals.append(elt.value)
    return literals


def test_help_alias_is_handled_by_sidecar() -> None:
    resp = ipc.route("req-1", "help", [])

    assert resp["ok"] is True
    assert "Supported commands" in resp["output"]
    assert "signalos cost" in resp["output"]
    assert "signalos test" in resp["output"]
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


def test_visible_signalos_cost_alias_maps_to_core_command(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_dispatch(command: str, args: list[str], req_id: str = "", project_id: str = "default") -> str:
        seen.update(command=command, args=args, req_id=req_id, project_id=project_id)
        return "cost ok"

    monkeypatch.setattr(ipc, "dispatch_cli", fake_dispatch)

    resp = ipc.route("req-5", "signalos cost --json", ["--budget-usd", "1"], project_id="demo")

    assert resp["ok"] is True
    assert resp["output"] == "cost ok"
    assert seen == {
        "command": "cost",
        "args": ["--json", "--budget-usd", "1"],
        "req_id": "req-5",
        "project_id": "demo",
    }


def test_signalos_alias_parser_preserves_windows_paths() -> None:
    parsed = ipc.parse_signalos_alias('signalos cost --ledger "C:\\tmp\\ai usage.jsonl"')

    assert parsed == ["cost", "--ledger", "C:\\tmp\\ai usage.jsonl"]


def test_sidecar_core_registry_matches_cli_parser_without_fallback() -> None:
    parser = _build_parser()
    choices = {}
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)

    assert ipc.core_cli_command_names() == frozenset(choices)


def test_command_catalog_has_no_unwired_sidecar_entries(tmp_path) -> None:
    shared = BUNDLE_ROOT / "core" / "tool-adapters" / "_shared"
    catalog = json.loads((shared / "commands.json").read_text(encoding="utf-8"))
    missing: list[str] = []

    for entry in catalog:
        command = entry["name"]
        if ipc.is_core_cli_command(command):
            continue
        if ipc.map_slash_command(command, ["--help"], str(tmp_path)) is not None:
            continue
        missing.append(command)

    assert missing == []


def test_all_core_cli_commands_are_direct_sidecar_commands(tmp_path) -> None:
    assert "validate-gate" in ipc.core_cli_command_names()
    assert "trust-tier" in ipc.core_cli_command_names()
    assert "release-proof" in ipc.core_cli_command_names()

    for command in sorted(ipc.core_cli_command_names()):
        assert ipc.map_slash_command(command, ["--json"], str(tmp_path)) == [command, "--json"]


def test_direct_alias_literals_are_all_real_parser_commands() -> None:
    # Anti-drift guard: the hardcoded literal entries of the `direct` set are
    # unioned with the live parser registry and dispatched as `signalos <cmd>`.
    # If a parser command is renamed or removed, a stale literal would silently
    # rot. Assert every literal still resolves to a real core CLI command.
    literals = _direct_alias_literals()
    # Sanity: the extraction actually found the set (guards against a refactor
    # that renames the local or changes the literal type).
    assert literals, "could not extract literal entries from the direct alias set"

    registry = ipc.core_cli_command_names()
    stale = [name for name in literals if not ipc.is_core_cli_command(name)]
    assert stale == [], f"stale direct alias entries are not parser commands: {stale}"
    assert set(literals).issubset(registry)

    # And each literal dispatches as a direct `signalos <command>` invocation.
    for name in literals:
        assert ipc.map_slash_command(name, ["--json"], "C:\\workspace") == [name, "--json"]


def test_signal_plan_falls_back_to_real_cli_command() -> None:
    assert ipc.map_slash_command("signal-plan", ["--help"], "C:\\workspace") == ["plan", "--help"]


def test_visible_signalos_validate_gate_alias_maps_to_core_command(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_dispatch(command: str, args: list[str], req_id: str = "", project_id: str = "default") -> str:
        seen.update(command=command, args=args, req_id=req_id, project_id=project_id)
        return "gate ok"

    monkeypatch.setattr(ipc, "dispatch_cli", fake_dispatch)

    resp = ipc.route("req-6", "signalos validate-gate --gate 5", ["--json"], project_id="demo")

    assert resp["ok"] is True
    assert resp["output"] == "gate ok"
    assert seen == {
        "command": "validate-gate",
        "args": ["--gate", "5", "--json"],
        "req_id": "req-6",
        "project_id": "demo",
    }


def test_visible_signalos_protocol_alias_maps_to_sidecar_command(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_dispatch(command: str, args: list[str], req_id: str = "", project_id: str = "default") -> str:
        seen.update(command=command, args=args, req_id=req_id, project_id=project_id)
        return "plan ok"

    monkeypatch.setattr(ipc, "dispatch_cli", fake_dispatch)

    resp = ipc.route("req-7", "signalos signal-plan --help", [], project_id="demo")

    assert resp["ok"] is True
    assert resp["output"] == "plan ok"
    assert seen == {
        "command": "signal-plan",
        "args": ["--help"],
        "req_id": "req-7",
        "project_id": "demo",
    }


def test_slash_ceremony_command_executes_without_spec_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    resp = ipc.route("req-8", "/signal-review", ["--json", "--no-evidence"])

    assert resp["ok"] is True
    assert "This beta shows the command brief" not in str(resp["output"])
    payload = json.loads(resp["output"])
    assert payload["action"] == "signal-review"
    assert payload["status"] == "ceremony-recorded"
    assert (tmp_path / "core" / "governance" / "QUALITY_CHECK.md").is_file()
    assert (tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl").is_file()


def test_unknown_signal_command_fails_instead_of_showing_spec_success() -> None:
    resp = ipc.route("req-9", "/signal-made-up", [])

    assert resp["ok"] is False
    assert "Unknown SignalOS command" in resp["error"]


def test_cli_usage_error_returns_failed_sidecar_response() -> None:
    resp = ipc.route("req-usage", "/signal-design", [])

    assert resp["ok"] is False
    assert "usage:" in resp["error"].lower()


def test_non_signal_alias_routes_through_explicit_mapping(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_dispatch(command: str, args: list[str], req_id: str = "", project_id: str = "default") -> str:
        seen.update(command=command, args=args, req_id=req_id, project_id=project_id)
        return "harness ok"

    monkeypatch.setattr(ipc, "dispatch_cli", fake_dispatch)

    resp = ipc.route("req-10", "harness-call", ["--step", "T-W01-001"], project_id="demo")

    assert resp["ok"] is True
    assert resp["output"] == "harness ok"
    assert seen == {
        "command": "harness-call",
        "args": ["--step", "T-W01-001"],
        "req_id": "req-10",
        "project_id": "demo",
    }
