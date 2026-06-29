from __future__ import annotations

import re
import shlex
from pathlib import Path

import signalos_ipc_server as ipc


APP_ROOT = Path(__file__).resolve().parents[1]


def _split_command(raw: str) -> tuple[str, list[str]]:
    if raw.startswith("/"):
        tokens = shlex.split(raw[1:])
        return tokens[0], tokens[1:]
    parsed = ipc.parse_signalos_alias(raw)
    if parsed:
        return parsed[0], parsed[1:]
    tokens = shlex.split(raw)
    return tokens[0], tokens[1:]


def test_build_palette_commands_execute_without_usage_errors(tmp_path: Path, monkeypatch) -> None:
    text = (APP_ROOT / "src" / "components" / "views" / "BuildView.tsx").read_text(encoding="utf-8")
    commands = re.findall(r"window\.runCmd\('([^']+)'\)", text)

    assert commands
    monkeypatch.chdir(tmp_path)
    for raw in commands:
        command, args = _split_command(raw)
        resp = ipc.route(f"palette-{command}", command, args)
        output = str(resp.get("output") or "")
        error = str(resp.get("error") or "")
        combined = f"{output}\n{error}".lower()

        assert "usage:" not in combined, raw
        assert "command failed" not in combined, raw
        if resp["ok"] is False:
            assert command == "test" and args == ["all"], f"{raw} failed: {error or output}"
            assert "signalos test all: blocked" in combined
            assert "evidence:" in combined
            continue
        assert resp["ok"] is True, f"{raw} failed: {error or output}"


def test_help_view_lists_only_routable_command_names() -> None:
    text = (APP_ROOT / "src" / "components" / "views" / "HelpView.tsx").read_text(encoding="utf-8")
    commands = re.findall(r'<div className="s-nm">([^<]+)</div>', text)

    assert commands
    assert "/signal-gate" not in commands
    for raw in commands:
        command, args = _split_command(raw.replace("...", "example"))
        assert ipc.is_dispatchable_cli_command(command, args, str(APP_ROOT)), raw
