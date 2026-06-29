from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.cli import _build_parser, main as cli_main
from signalos_lib.commands import integrity_witness


def _write_governance_file(root: Path, text: str = "# Constitution\n") -> Path:
    path = root / "core" / "governance" / "Governance" / "CONSTITUTION.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_integrity_witness_command_is_registered_in_cli() -> None:
    parser = _build_parser()
    choices = {}
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)
    assert "integrity-witness" in choices
    assert "verify" in choices


def test_init_writes_witness_and_audit_then_check_passes(tmp_path: Path) -> None:
    _write_governance_file(tmp_path)

    created = integrity_witness.create_witness(tmp_path, actor="Samer", role="PO")
    checked = integrity_witness.check_witness(tmp_path)

    assert created["status"] == "ok"
    assert created["entry_count"] == 1
    assert (tmp_path / ".signalos" / "INTEGRITY_WITNESS.yaml").is_file()
    assert checked["ok"] is True
    audit_rows = [
        json.loads(line)
        for line in (tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert audit_rows[-1]["action"] == "integrity-witness-init"


def test_hash_drift_returns_exit_drift(tmp_path: Path) -> None:
    watched = _write_governance_file(tmp_path)
    integrity_witness.create_witness(tmp_path, actor="Samer", role="PE")
    watched.write_text("# Constitution changed\n", encoding="utf-8")

    payload = integrity_witness.check_witness(tmp_path)
    rc = integrity_witness.main(["--repo-root", str(tmp_path), "--quiet"])

    assert payload["status"] == "drift"
    assert any("hash mismatch" in issue for issue in payload["issues"])
    assert rc == integrity_witness.EXIT_DRIFT


def test_new_watched_hook_returns_drift(tmp_path: Path) -> None:
    _write_governance_file(tmp_path)
    integrity_witness.create_witness(tmp_path, actor="Samer", role="QA")
    hook = tmp_path / "core" / "execution" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env sh\n", encoding="utf-8")

    payload = integrity_witness.check_witness(tmp_path)

    assert payload["status"] == "drift"
    assert any("new integrity file not in witness" in issue for issue in payload["issues"])


def test_agent_like_actor_is_refused(tmp_path: Path) -> None:
    _write_governance_file(tmp_path)

    rc = integrity_witness.main([
        "--init",
        "--repo-root",
        str(tmp_path),
        "--actor",
        "codex-agent",
        "--role",
        "PO",
    ])

    assert rc == integrity_witness.EXIT_BAD_ARGS
    assert not (tmp_path / ".signalos" / "INTEGRITY_WITNESS.yaml").exists()


def test_top_level_cli_reaches_integrity_witness(tmp_path: Path) -> None:
    _write_governance_file(tmp_path)

    rc = cli_main([
        "signalos",
        "integrity-witness",
        "--init",
        "--repo-root",
        str(tmp_path),
        "--actor",
        "Samer",
        "--role",
        "DevOps",
    ])

    assert rc == integrity_witness.EXIT_OK
    assert (tmp_path / ".signalos" / "INTEGRITY_WITNESS.yaml").is_file()
