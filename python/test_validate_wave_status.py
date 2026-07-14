from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.cli import _build_parser, main as cli_main
from signalos_lib.commands import sign as sign_command
from signalos_lib.commands.validate_wave_status import _audit_status, validate_wave_status
from signalos_lib.artifacts import expected_gate_artifacts
from signalos_lib.sign import (
    SOLO_FOUNDER_GATE0_CONSENT,
    _append_audit,
    approve_gate0_as_solo_founder,
    sign_artifact,
)
from conftest import seed_governed_release_proof


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_gate_artifacts(root: Path) -> None:
    artifacts = {
        "core/governance/Governance/SOUL-DOCUMENT.md": (
            "# Soul Document\n\n"
            "Product mandate: deliver useful software.\n"
            "Decision rule: evidence before closeout.\n"
            "Governance rule: signed gates are required.\n"
        ),
        "core/governance/Governance/CONSTITUTION.md": "# Constitution\n\nLaw 1\nLaw 2\nLaw 3\n",
        "core/governance/Governance/SURFACE_INVENTORY.md": "# Surface Inventory\n\n- app\n- cli\n- hooks\n",
        "core/governance/Governance/PERMANENTLY_T3.md": "# Permanently T3\n\n- secrets\n- deploy\n- billing\n",
        "core/strategy/BELIEF.md": "# Belief\n\n## Problem\nUsers need trusted delivery status.\n",
        "core/execution/ROLE_ACTIVATION_CARD.md": "# Role Activation Card\n\nPO activates wave.\n",
        "core/strategy/EXPECTATION_MAP.md": "# Expectation Map\n\n- status is validated\n",
        "core/strategy/DESIGN_NOTE.md": "# Design Note\n\nStatus validator design.\n",
        "core/execution/PLAN.md": "# Plan\n\n- implement validator\n",
        "core/execution/ACCEPTANCE_CRITERIA.md": "# Acceptance Criteria\n\n- validator blocks on evidence gaps\n",
        "core/execution/TRUST_TIER.md": "# Trust Tier\n\nT2 governance validator.\n",
        "core/execution/BUILD_EVIDENCE.md": "# Build Evidence\n\nBuild and test evidence attached.\n",
        "core/governance/QUALITY_CHECK.md": "# Quality Check\n\nStage 1 review: PASS\nStage 2 review: PASS\n",
    }
    for rel, text in artifacts.items():
        _write(root / rel, text)


def _sign(root: Path, gate: str, role: str) -> None:
    rc = sign_command.main([
        gate,
        "--repo-root",
        str(root),
        "--signer",
        f"{role} Signer",
        "--role",
        role,
        "--verdict",
        "APPROVED",
        "--wave",
        "1",
    ])
    assert rc == 0


def _sign_complete_wave(root: Path) -> None:
    _write(
        root / ".signalos" / "identity.json",
        json.dumps({"name": "Fixture Founder", "role": "PO"}) + "\n",
    )
    approval = approve_gate0_as_solo_founder(
        root,
        consent=SOLO_FOUNDER_GATE0_CONSENT,
        via="simulation",
        expected_workspace=str(root.resolve()),
        approval_id="validate-wave-fixture-g0",
        project_id="default",
        expected_project_id="default",
    )
    assert approval.get("signed") is True, approval
    delegated = "Fixture Founder (sole founder; primary PO; delegated G0 PE)"
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    for artifact in expected_gate_artifacts("G0"):
        _append_audit(
            audit,
            delegated,
            "PE",
            "G0",
            artifact.rel_path,
            root / artifact.rel_path,
            "APPROVED",
            wave="1",
        )
    _sign(root, "G1", "PO")
    _sign(root, "G2", "PO")
    _sign(root, "G3", "PO")
    _sign(root, "G3", "PE")
    # Outcome gates cannot be signed through the public raw CLI.  This fixture
    # models the artifacts written by the governed delivery and then persists
    # the same ordered G4->G5 proof strict readers require.
    for gate in ("G4", "G5"):
        for artifact in expected_gate_artifacts(gate):
            role = artifact.required_roles[0]
            signer = f"{role} Signer"
            path = root / artifact.rel_path
            sign_artifact(path, signer, role, gate, "APPROVED")
            _append_audit(
                audit,
                signer,
                role,
                gate,
                artifact.rel_path,
                path,
                "APPROVED",
                wave="1",
            )
    seed_governed_release_proof(root, run_id="validate-wave-fixture")


def _commands() -> set[str]:
    parser = _build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            return set(action.choices)
    return set()


def test_validate_wave_status_passes_for_complete_signed_audit_linked_wave(tmp_path: Path) -> None:
    _seed_gate_artifacts(tmp_path)
    _sign_complete_wave(tmp_path)

    payload = validate_wave_status(tmp_path, wave="W01")

    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert payload["signed_gate_count"] == 5
    assert payload["next_gate"] is None
    assert payload["journey"]["phase"] == "closeout-ready"
    assert payload["blockers"] == []
    evidence_path = tmp_path / ".signalos" / "evidence" / "waves" / "validate-wave-status-w01.json"
    assert evidence_path.is_file()
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["evidence_path"] == payload["evidence_path"]


def test_validate_wave_status_fails_on_missing_artifacts_and_audit(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()

    payload = validate_wave_status(tmp_path, wave="1", write_evidence=False)

    assert payload["ok"] is False
    kinds = {blocker["kind"] for blocker in payload["blockers"]}
    assert "audit-trail-missing" in kinds
    assert "gate-artifacts-present" in kinds
    assert payload["has_blocking_issue"] is True


def test_validate_wave_status_blocks_wrong_signer_role(tmp_path: Path) -> None:
    from signalos_lib.artifacts import resolve_gate_artifacts

    _write(
        tmp_path / "core" / "governance" / "QUALITY_CHECK.md",
        "# Quality Check\n\nStage 1 review: PASS\nStage 2 review: PASS\n",
    )
    # #17 now blocks signing G5 with the wrong role (PE) at sign time via
    # sign_gate. To prove validate_wave_status is an INDEPENDENT check (it must
    # still flag a hand-forged wrong-role signature that bypassed sign_gate),
    # write the wrong-role signature via the low-level sign_artifact.
    from signalos_lib.sign import sign_artifact

    for artifact in resolve_gate_artifacts(tmp_path, "G5"):
        if artifact.path.exists():
            sign_artifact(artifact.path, "PE Signer", "PE", "G5", "APPROVED")

    payload = validate_wave_status(tmp_path, wave="W01", write_evidence=False)

    assert payload["ok"] is False
    role_blockers = [
        blocker for blocker in payload["blockers"]
        if blocker["kind"] == "wrong-signer-role"
    ]
    assert role_blockers
    assert role_blockers[0]["gate_code"] == "G5"
    assert "requires one of QA" in role_blockers[0]["message"]


def test_validate_wave_status_cli_exposes_json_dispatch(tmp_path: Path, capsys) -> None:
    _seed_gate_artifacts(tmp_path)
    _sign_complete_wave(tmp_path)
    capsys.readouterr()

    assert "validate-wave-status" in _commands()

    rc = cli_main([
        "signalos",
        "validate-wave-status",
        "--repo-root",
        str(tmp_path),
        "--wave",
        "01",
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["schema_version"] == "signalos.validate_wave_status.v1"


def test_audit_status_reports_honest_presence_not_a_hash_chain(tmp_path: Path) -> None:
    audit = tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit.parent.mkdir(parents=True)

    assert _audit_status(tmp_path) == "missing"

    audit.write_text("", encoding="utf-8")
    assert _audit_status(tmp_path) == "empty"

    audit.write_text(
        json.dumps({"event": "a"}) + "\n" + json.dumps({"event": "b"}) + "\n",
        encoding="utf-8",
    )
    # No sha256/previousSha256 hash-chain reporting remains: only honest presence.
    status = _audit_status(tmp_path)
    assert status == "present (2 rows)"
    assert "hash" not in status

    audit.write_text("not json\n", encoding="utf-8")
    assert _audit_status(tmp_path).startswith("invalid json")

    audit.write_text(json.dumps(["not", "a", "dict"]) + "\n", encoding="utf-8")
    assert _audit_status(tmp_path).startswith("invalid row shape")


def test_validate_wave_status_api_url_is_explicitly_blocked(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()

    payload = validate_wave_status(
        tmp_path,
        wave="1",
        api_url="http://localhost:44300",
        token="secret",
        write_evidence=False,
    )

    assert payload["ok"] is False
    assert payload["token_supplied"] is True
    assert any(
        blocker["kind"] == "remote-status-api-unsupported"
        for blocker in payload["blockers"]
    )
