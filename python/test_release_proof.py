"""Focused tests for technology-neutral release artifact proof."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from signalos_lib.cli import main as cli_main
from signalos_lib.commands.ship import normalize_wave_segment
from signalos_lib.product.release_proof import (
    _safe_segment,
    produce_clean_machine_proof,
    produce_signature_proof,
    validate_release_proof,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_proof_passes_with_required_artifact_evidence(tmp_path: Path):
    artifact = tmp_path / "dist" / "signalos-app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"release artifact bytes")
    digest = _sha256(artifact)
    signature = tmp_path / "dist" / "signalos-app.zip.sig.json"
    clean = tmp_path / "proof" / "clean-machine.json"
    installer = tmp_path / "proof" / "installer.json"
    readiness = tmp_path / ".signalos" / "evidence" / "W7" / "release-readiness.json"

    _write_json(signature, {"artifact_sha256": f"sha256:{digest}", "signed_by": "Release Bot"})
    _write_json(
        clean,
        {
            "status": "pass",
            "environment": {"os": "ubuntu-latest", "fresh_workspace": True},
            "commands": [{"command": "signalos install ./signalos-app.zip", "status": "pass"}],
        },
    )
    _write_json(
        installer,
        {
            "ok": True,
            "checks": [{"name": "install", "status": "pass"}],
        },
    )
    _write_json(readiness, {"ok": True, "status": "ready-to-publish", "blockers": []})

    payload = validate_release_proof(
        tmp_path,
        artifact="dist/signalos-app.zip",
        signature="dist/signalos-app.zip.sig.json",
        clean_machine_proof="proof/clean-machine.json",
        installer_proof="proof/installer.json",
        readiness_evidence=".signalos/evidence/W7/release-readiness.json",
        require_signature=True,
        require_clean_machine=True,
        require_installer_proof=True,
        require_readiness=True,
        wave="W7",
    )

    assert payload["ok"] is True
    assert payload["status"] == "release-proofed"
    assert payload["artifact"]["sha256"] == digest
    assert payload["artifact"]["kind"] == "zip"
    assert payload["summary"]["failed"] == 0
    # Wave "W7" normalizes to the unified "W07" evidence segment shared with ship.
    assert payload["wave"] == "W07"
    assert ".signalos/evidence/W07/release-proof.json" in payload["evidence"]
    assert (tmp_path / ".signalos" / "evidence" / "W07" / "release-proof.json").is_file()


def test_release_proof_blocks_signature_digest_mismatch(tmp_path: Path):
    artifact = tmp_path / "release" / "bundle.tar.gz"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"real artifact")
    signature = tmp_path / "release" / "bundle.tar.gz.sig.json"
    _write_json(signature, {"artifact_sha256": "0" * 64})

    payload = validate_release_proof(
        tmp_path,
        artifact=artifact,
        signature=signature,
        require_signature=True,
        write_evidence=False,
    )

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["release-artifact"]["status"] == "PASS"
    assert checks["artifact-signature"]["status"] == "FAIL"
    assert checks["artifact-signature"]["details"]["artifact_digest_match"] is False
    assert {blocker["id"] for blocker in payload["blockers"]} == {"artifact-signature"}


def test_release_proof_requires_structured_json_signature_when_signature_required(tmp_path: Path):
    artifact = tmp_path / "release" / "bundle.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"real artifact")
    signature = tmp_path / "release" / "bundle.zip.sig"
    signature.write_text("opaque-signature\n", encoding="utf-8")

    payload = validate_release_proof(
        tmp_path,
        artifact=artifact,
        signature=signature,
        require_signature=True,
        write_evidence=False,
    )

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["artifact-signature"]["status"] == "FAIL"
    assert checks["artifact-signature"]["message"] == (
        "artifact signature proof must be structured JSON when required"
    )


def test_signature_producer_writes_validator_accepted_evidence(tmp_path: Path):
    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact bytes")
    signature = tmp_path / "dist" / "app.zip.sig"
    signature.write_bytes(b"detached signature bytes")

    proof = produce_signature_proof(
        tmp_path,
        artifact="dist/app.zip",
        signature_file="dist/app.zip.sig",
        signed_by="Release Bot",
        signing_tool="cosign",
    )

    assert proof["ok"] is True
    assert proof["artifact_sha256"] == _sha256(artifact)
    assert proof["signature"]["sha256"] == _sha256(signature)
    assert (tmp_path / proof["path"]).is_file()

    payload = validate_release_proof(
        tmp_path,
        artifact="dist/app.zip",
        signature=proof["path"],
        require_signature=True,
        write_evidence=False,
    )

    assert payload["ok"] is True
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["artifact-signature"]["details"]["artifact_digest_match"] is True


def test_signature_producer_blocks_without_real_signature_file(tmp_path: Path):
    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact bytes")

    proof = produce_signature_proof(
        tmp_path,
        artifact="dist/app.zip",
        signature_file="dist/app.zip.sig",
    )

    assert proof["ok"] is False
    payload = validate_release_proof(
        tmp_path,
        artifact="dist/app.zip",
        signature=proof["path"],
        require_signature=True,
        write_evidence=False,
    )

    checks = {check["id"]: check for check in payload["checks"]}
    assert payload["ok"] is False
    assert checks["artifact-signature"]["status"] == "FAIL"
    assert checks["artifact-signature"]["message"] == "artifact signature proof did not pass"


def test_release_proof_requires_clean_machine_environment_and_steps(tmp_path: Path):
    artifact = tmp_path / "dist" / "app.whl"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"wheel")
    clean = tmp_path / "proof" / "clean.json"
    _write_json(clean, {"status": "pass", "commands": []})

    payload = validate_release_proof(
        tmp_path,
        artifact=artifact,
        clean_machine_proof=clean,
        require_clean_machine=True,
        write_evidence=False,
    )

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["clean-machine-proof"]["status"] == "FAIL"
    assert checks["clean-machine-proof"]["message"] == "clean-machine proof must list at least one command or check"


def test_clean_machine_producer_writes_validator_accepted_evidence(tmp_path: Path):
    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact bytes")

    proof = produce_clean_machine_proof(
        tmp_path,
        artifact="dist/app.zip",
        fresh_workspace=True,
        environment_label="clean-runner-1",
    )

    assert proof["ok"] is True
    assert proof["environment"]["fresh_workspace"] is True
    assert (tmp_path / proof["path"]).is_file()

    payload = validate_release_proof(
        tmp_path,
        artifact="dist/app.zip",
        clean_machine_proof=proof["path"],
        require_clean_machine=True,
        write_evidence=False,
    )

    assert payload["ok"] is True
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["clean-machine-proof"]["status"] == "PASS"


def test_clean_machine_producer_fails_without_clean_marker(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SIGNALOS_CLEAN_MACHINE", raising=False)
    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact bytes")

    proof = produce_clean_machine_proof(tmp_path, artifact="dist/app.zip")

    assert proof["ok"] is False
    payload = validate_release_proof(
        tmp_path,
        artifact="dist/app.zip",
        clean_machine_proof=proof["path"],
        require_clean_machine=True,
        write_evidence=False,
    )

    checks = {check["id"]: check for check in payload["checks"]}
    assert payload["ok"] is False
    assert checks["clean-machine-proof"]["status"] == "FAIL"
    assert checks["clean-machine-proof"]["message"] == "clean-machine proof did not pass"


def test_release_proof_auto_discovers_latest_release_readiness(tmp_path: Path):
    artifact = tmp_path / "dist" / "app.tar"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")
    _write_json(
        tmp_path / ".signalos" / "evidence" / "W1" / "release-readiness.json",
        {"ok": False, "status": "blocked", "blockers": [{"id": "test"}]},
    )
    latest = tmp_path / ".signalos" / "evidence" / "W2" / "release-readiness.json"
    _write_json(latest, {"ok": True, "status": "ready-to-publish", "blockers": []})

    payload = validate_release_proof(
        tmp_path,
        artifact=artifact,
        require_readiness=True,
        write_evidence=False,
    )

    assert payload["ok"] is True
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["release-readiness-evidence"]["status"] == "PASS"
    assert checks["release-readiness-evidence"]["evidence"] == [
        ".signalos/evidence/W2/release-readiness.json"
    ]


def test_release_proof_readiness_requires_ok_true(tmp_path: Path):
    # Negative: status says ready-to-publish and blockers are empty, but ok is
    # not True. Fail-closed: the status alone must NOT pass readiness.
    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")
    readiness = tmp_path / "proof" / "readiness.json"
    _write_json(readiness, {"status": "ready-to-publish", "blockers": []})

    payload = validate_release_proof(
        tmp_path,
        artifact=artifact,
        readiness_evidence=readiness,
        require_readiness=True,
        write_evidence=False,
    )

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["release-readiness-evidence"]["status"] == "FAIL"
    assert "release-readiness-evidence" in {b["id"] for b in payload["blockers"]}


def test_release_proof_readiness_blocks_ok_false_even_when_ready_status(tmp_path: Path):
    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")
    readiness = tmp_path / "proof" / "readiness.json"
    _write_json(readiness, {"ok": False, "status": "published", "blockers": []})

    payload = validate_release_proof(
        tmp_path,
        artifact=artifact,
        readiness_evidence=readiness,
        require_readiness=True,
        write_evidence=False,
    )

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["release-readiness-evidence"]["status"] == "FAIL"


def test_wave_segment_unified_between_ship_and_release_proof(tmp_path: Path):
    # release_proof's segment helper and ship's shared normalizer agree, so the
    # same numeric wave maps to one evidence directory (no W01 vs 1 split).
    assert _safe_segment("1") == "W01"
    assert _safe_segment("W1") == "W01"
    assert _safe_segment("W7") == normalize_wave_segment("7") == "W07"

    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")

    payload = validate_release_proof(tmp_path, artifact=artifact, wave="7")

    assert payload["wave"] == "W07"
    # Exact-path linkage: ship writes/reads evidence under the same W07 segment.
    assert payload["evidence_path"] == ".signalos/evidence/W07/release-proof.json"
    assert (tmp_path / ".signalos" / "evidence" / "W07" / "release-proof.json").is_file()


def test_release_proof_cli_emits_json_and_exit_code(tmp_path: Path, capsys):
    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")

    rc = cli_main(
        [
            "signalos",
            "release-proof",
            "validate",
            "--repo-root",
            str(tmp_path),
            "--artifact",
            "dist/app.zip",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["artifact"]["path"] == "dist/app.zip"
    assert payload["evidence_path"] == ".signalos/evidence/release-proof/release-proof.json"


def test_release_proof_cli_produce_signature_and_validate(tmp_path: Path, capsys):
    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")
    signature = tmp_path / "dist" / "app.zip.sig"
    signature.write_bytes(b"signature")

    rc = cli_main(
        [
            "signalos",
            "release-proof",
            "produce-signature",
            "--repo-root",
            str(tmp_path),
            "--artifact",
            "dist/app.zip",
            "--signature-file",
            "dist/app.zip.sig",
            "--json",
        ]
    )

    assert rc == 0
    proof = json.loads(capsys.readouterr().out)
    assert proof["ok"] is True

    rc = cli_main(
        [
            "signalos",
            "release-proof",
            "validate",
            "--repo-root",
            str(tmp_path),
            "--artifact",
            "dist/app.zip",
            "--signature",
            proof["path"],
            "--require-signature",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_release_proof_cli_produce_clean_machine_fails_without_marker(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SIGNALOS_CLEAN_MACHINE", raising=False)
    artifact = tmp_path / "dist" / "app.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")

    rc = cli_main(
        [
            "signalos",
            "release-proof",
            "produce-clean-machine",
            "--repo-root",
            str(tmp_path),
            "--artifact",
            "dist/app.zip",
            "--json",
        ]
    )

    assert rc == 1
    proof = json.loads(capsys.readouterr().out)
    assert proof["ok"] is False
