from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.cli import _build_parser, main as cli_main
from signalos_lib.commands.ceremonies import CEREMONY_COMMANDS


def _parser_choices() -> set[str]:
    parser = _build_parser()
    choices: set[str] = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)
    return choices


def _run(command: str, root: Path, *args: str) -> None:
    rc = cli_main([
        "signalos",
        command,
        "--repo-root",
        str(root),
        "--json",
        "--force",
        *args,
    ])
    assert rc == 0


def test_ceremony_commands_are_registered_in_top_level_cli() -> None:
    choices = _parser_choices()

    for command in CEREMONY_COMMANDS:
        assert command in choices


def test_ceremony_commands_write_artifacts_evidence_and_audit(tmp_path: Path) -> None:
    _run("signal-discovery", tmp_path, "--name", "Acme", "--summary", "Capture the first signal.")
    _run("signal-onboard", tmp_path, "--name", "Acme App", "--actor", "PO")
    _run("signal-pre-wave", tmp_path, "--wave", "3", "--summary", "Build the smallest useful slice.")
    _run("signal-review", tmp_path, "--wave", "W03", "--verdict", "approved")
    _run("signal-wave-review", tmp_path, "--wave", "W03", "--summary", "Signal threshold was met.")
    _run("signal-debrief", tmp_path, "--wave", "W03", "--summary", "Keep the belief and iterate.")

    expected_files = [
        "core/strategy/discovery-briefs/wave-0-session-001.md",
        "core/governance/Governance/SOUL-DOCUMENT.md",
        "core/execution/SURFACE_INVENTORY.md",
        "core/execution/PERMANENTLY_T3.md",
        "core/execution/onboarding-report.md",
        "core/governance/Governance/DECISION-DNA.md",
        ".signalos/wave.json",
        "core/strategy/BELIEF.md",
        "core/execution/EXPECTATION_MAP.md",
        "core/execution/ROLE_ACTIVATION_CARD.md",
        "core/governance/QUALITY_CHECK.md",
        ".signalos/evidence/W03/signal-review.json",
        "core/governance/Governance/CLIENT-SIGNAL-LOG.md",
        "core/execution/WAVE_REVIEW.md",
        "core/execution/WAVE_DEBRIEF.md",
        "core/governance/Governance/RETROSPECTIVE.md",
    ]
    for rel_path in expected_files:
        assert (tmp_path / rel_path).is_file(), rel_path

    wave = json.loads((tmp_path / ".signalos" / "wave.json").read_text(encoding="utf-8"))
    assert wave["wave"] == "W03"
    assert wave["status"] == "ACTIVE"

    for command in CEREMONY_COMMANDS:
        evidence = tmp_path / ".signalos" / "evidence" / "ceremonies" / f"{command}.json"
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        assert payload["action"] == command
        assert payload["status"] == "ceremony-recorded"

    audit_rows = [
        json.loads(line)
        for line in (tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["action"] for row in audit_rows] == [
        "signal-discovery",
        "signal-onboard",
        "signal-pre-wave",
        "signal-review",
        "signal-wave-review",
        "signal-debrief",
    ]


def test_signal_pre_wave_refuses_to_clobber_active_wave_without_force(tmp_path: Path, capsys) -> None:
    _run("signal-pre-wave", tmp_path, "--wave", "1")
    capsys.readouterr()  # discard setup JSON so only the clobber attempt is captured

    rc = cli_main([
        "signalos",
        "signal-pre-wave",
        "--repo-root",
        str(tmp_path),
        "--wave",
        "2",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["status"] == "ceremony-blocked"
    wave = json.loads((tmp_path / ".signalos" / "wave.json").read_text(encoding="utf-8"))
    assert wave["wave"] == "W01"


def test_signal_pre_wave_force_replaces_active_wave(tmp_path: Path) -> None:
    _run("signal-pre-wave", tmp_path, "--wave", "1")
    _run("signal-pre-wave", tmp_path, "--wave", "2")

    wave = json.loads((tmp_path / ".signalos" / "wave.json").read_text(encoding="utf-8"))
    assert wave["wave"] == "W02"


def test_signal_review_fail_verdict_exits_nonzero(tmp_path: Path, capsys) -> None:
    _run("signal-pre-wave", tmp_path, "--wave", "1")
    capsys.readouterr()  # discard setup JSON so only the review result is captured

    rc = cli_main([
        "signalos",
        "signal-review",
        "--repo-root",
        str(tmp_path),
        "--wave",
        "W01",
        "--verdict",
        "fail",
        "--json",
        "--force",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["status"] == "review-blocked"
