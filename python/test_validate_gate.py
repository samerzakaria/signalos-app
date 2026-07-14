from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.cli import _build_parser, main as cli_main
from signalos_lib.commands import sign as sign_command
from signalos_lib.commands.validate_gate import validate_gate
from signalos_lib.sign import sign_artifact


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _expectation_map(root: Path) -> Path:
    path = root / "core" / "strategy" / "EXPECTATION_MAP.md"
    _write(
        path,
        "# Expectation Map\n\n"
        "Actor: Customer\n"
        "Outcome: A verified product result\n"
        "Acceptance: The observable result matches the agreed expectation.\n",
    )
    return path


def test_validate_gate_passes_for_signed_audit_linked_gate(tmp_path: Path) -> None:
    _expectation_map(tmp_path)

    rc = sign_command.main([
        "G2",
        "--repo-root",
        str(tmp_path),
        "--signer",
        "Product Owner",
        "--role",
        "PO",
        "--verdict",
        "APPROVED",
        "--wave",
        "1",
    ])

    assert rc == 0
    result = validate_gate(tmp_path, "2", wave="W01")

    assert result["ok"] is True
    assert result["gate"] == "G2"
    assert result["wave"] == "01"
    assert result["summary"]["failed"] == 0
    assert (tmp_path / ".signalos" / "evidence" / "gates" / "validate-gate-g2-w01.json").is_file()


def test_validate_gate_fails_without_audit_link(tmp_path: Path) -> None:
    artifact = _expectation_map(tmp_path)
    sign_artifact(artifact, "Product Owner", "PO", "G2", "APPROVED")

    result = validate_gate(tmp_path, "G2", write_evidence=False)

    assert result["ok"] is False
    blocker_ids = {blocker["id"] for blocker in result["blockers"]}
    assert "gate-audit-trail-present" in blocker_ids
    assert "gate-audit-linked" in blocker_ids


def test_validate_gate_fails_on_wave_mismatch(tmp_path: Path) -> None:
    _expectation_map(tmp_path)
    assert sign_command.main([
        "G2",
        "--repo-root",
        str(tmp_path),
        "--signer",
        "Product Owner",
        "--role",
        "PO",
        "--verdict",
        "APPROVED",
        "--wave",
        "1",
    ]) == 0

    result = validate_gate(tmp_path, "G2", wave="2", write_evidence=False)

    assert result["ok"] is False
    linked = next(check for check in result["checks"] if check["id"] == "gate-audit-linked")
    assert linked["details"]["missing_links"] == ["core/strategy/EXPECTATION_MAP.md"]


def test_cli_exposes_validate_gate_command(tmp_path: Path, capsys) -> None:
    _expectation_map(tmp_path)
    assert sign_command.main([
        "G2",
        "--repo-root",
        str(tmp_path),
        "--signer",
        "Product Owner",
        "--role",
        "PO",
        "--verdict",
        "APPROVED",
        "--wave",
        "1",
    ]) == 0
    capsys.readouterr()

    parser = _build_parser()
    commands: set[str] = set()
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            commands = set(action.choices)
            break
    assert "validate-gate" in commands

    rc = cli_main([
        "signalos",
        "validate-gate",
        "--repo-root",
        str(tmp_path),
        "--gate",
        "2",
        "--wave",
        "01",
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["gate"] == "G2"
