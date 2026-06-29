"""Focused tests for the executable ``signalos ship`` ceremony."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from signalos_lib.cli import main as cli_main
from signalos_lib.commands.ship import ship_wave
from signalos_lib.sign import sign_artifact


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_ready_repo(root: Path, *, signer: str = "QA User") -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "qa@example.test")
    _git(root, "config", "user.name", "QA User")
    _write(root / ".signalos" / "waves" / "W01" / ".keep", "\n")
    _write(root / ".signalos" / "AUDIT_TRAIL.jsonl", "")
    _write(
        root / "core" / "governance" / "QUALITY_CHECK.md",
        "# Quality Check\n\nSelf Assessment\n- Coverage integrity PASS\n- Human gate readiness PASS\n",
    )
    sign_artifact(
        root / "core" / "governance" / "QUALITY_CHECK.md",
        signer=signer,
        role="QA",
        gate="G5",
        verdict="APPROVED",
    )
    _write(
        root / ".signalos" / "evidence" / "W01" / "release-readiness.json",
        json.dumps({"ok": True, "status": "ready-to-publish", "blockers": []}) + "\n",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "ready to ship")


def test_ship_dry_run_blocks_missing_wave_directory(tmp_path: Path):
    _git(tmp_path, "init")

    payload = ship_wave(tmp_path, wave="W01", dry_run=True, write_evidence=False)

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert {blocker["id"] for blocker in payload["blockers"]} >= {
        "wave-directory",
        "gate-5-signed",
        "release-readiness",
    }
    assert not (tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl").exists()


def test_ship_rejects_agent_self_signature(tmp_path: Path):
    _init_ready_repo(tmp_path, signer="Claude Agent")

    payload = ship_wave(tmp_path, wave=1, dry_run=True, write_evidence=False)

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["gate-5-signed"]["status"] == "FAIL"
    assert "self-signed by agent" in checks["gate-5-signed"]["message"]


def test_ship_rejects_g5_signature_without_artifact_hash(tmp_path: Path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "qa@example.test")
    _git(tmp_path, "config", "user.name", "QA User")
    _write(tmp_path / ".signalos" / "waves" / "W01" / ".keep", "\n")
    _write(tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl", "")
    _write(
        tmp_path / "core" / "governance" / "QUALITY_CHECK.md",
        "# Quality Check\n\nSelf Assessment\n- Coverage integrity PASS\n\n"
        "## Signatures\n\n```yaml\n"
        "- signer: QA User\n"
        "  role: QA\n"
        "  gate: Gate 5\n"
        "  verdict: APPROVED\n"
        "```\n",
    )
    _write(
        tmp_path / ".signalos" / "evidence" / "W01" / "release-readiness.json",
        json.dumps({"ok": True, "status": "ready-to-publish", "blockers": []}) + "\n",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "forged signature")

    payload = ship_wave(tmp_path, wave=1, dry_run=True, write_evidence=False)

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["gate-5-signed"]["status"] == "FAIL"
    assert "signature missing artifact hash" in checks["gate-5-signed"]["message"]


def test_ship_live_creates_local_tag_and_appends_audit(tmp_path: Path):
    _init_ready_repo(tmp_path)

    payload = ship_wave(tmp_path, wave=1, actor="Release Owner")

    assert payload["ok"] is True
    assert payload["status"] == "shipped"
    assert payload["tag"]["created"] is True
    assert payload["tag"]["tag"] == "wave-W01"
    assert (tmp_path / ".signalos" / "evidence" / "W01" / "ship.json").is_file()
    tag = _git(tmp_path, "rev-parse", "wave-W01").stdout.strip()
    assert tag
    audit_rows = [
        json.loads(line)
        for line in (tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert audit_rows[-1]["action"] == "ship-confirmed"
    assert audit_rows[-1]["actor"] == "Release Owner"
    assert audit_rows[-1]["wave"] == "W01"


def test_ship_blocks_dirty_worktree_unless_explicitly_allowed(tmp_path: Path):
    _init_ready_repo(tmp_path)
    _write(tmp_path / "dirty.txt", "changed\n")

    blocked = ship_wave(tmp_path, wave=1, dry_run=True, write_evidence=False)
    assert blocked["ok"] is False
    checks = {check["id"]: check for check in blocked["checks"]}
    assert checks["clean-tree"]["status"] == "FAIL"

    allowed = ship_wave(tmp_path, wave=1, dry_run=True, allow_dirty=True, write_evidence=False)
    checks = {check["id"]: check for check in allowed["checks"]}
    assert checks["clean-tree"]["status"] == "PASS"


def test_ship_self_assessment_allows_fail_prose(tmp_path: Path):
    # Item 16: bare prose containing "fail" must NOT block. Verdicts are PASS.
    _init_ready_repo(tmp_path)
    _write(
        tmp_path / "core" / "governance" / "QUALITY_CHECK.md",
        "# Quality Check\n\n"
        "No failures were observed during this review.\n"
        "The system uses a fail-closed design and rejects unverified work.\n"
        "Verdict: PASS\n"
        "- [x] All gates green\n",
    )

    payload = ship_wave(tmp_path, wave=1, dry_run=True, write_evidence=False)

    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["self-assessment-no-fail"]["status"] == "PASS"


def test_ship_self_assessment_blocks_explicit_fail_verdict(tmp_path: Path):
    # Item 16: an explicit fail verdict field DOES block.
    _init_ready_repo(tmp_path)
    _write(
        tmp_path / "core" / "governance" / "QUALITY_CHECK.md",
        "# Quality Check\n\n"
        "No failures were observed in earlier waves.\n"
        "Verdict: FAIL\n",
    )

    payload = ship_wave(tmp_path, wave=1, dry_run=True, write_evidence=False)

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["self-assessment-no-fail"]["status"] == "FAIL"
    assert {b["id"] for b in payload["blockers"]} >= {"self-assessment-no-fail"}


def test_ship_self_assessment_blocks_checked_fail_checkbox(tmp_path: Path):
    # Item 16: a checked markdown checkbox labelled FAIL DOES block.
    _init_ready_repo(tmp_path)
    _write(
        tmp_path / "core" / "governance" / "QUALITY_CHECK.md",
        "# Quality Check\n\nSelf Assessment\n- [x] FAIL\n",
    )

    payload = ship_wave(tmp_path, wave=1, dry_run=True, write_evidence=False)

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["self-assessment-no-fail"]["status"] == "FAIL"


def test_ship_release_readiness_requires_ok_true(tmp_path: Path):
    # Item 17: status ready-to-publish + empty blockers is not enough; ok must
    # be explicitly True. ok-missing must fail-closed and block.
    _init_ready_repo(tmp_path)
    _write(
        tmp_path / ".signalos" / "evidence" / "W01" / "release-readiness.json",
        json.dumps({"status": "ready-to-publish", "blockers": []}) + "\n",
    )

    payload = ship_wave(tmp_path, wave=1, dry_run=True, write_evidence=False)

    assert payload["ok"] is False
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["release-readiness"]["status"] == "FAIL"
    assert {b["id"] for b in payload["blockers"]} >= {"release-readiness"}


def test_ship_release_readiness_blocks_ok_false(tmp_path: Path):
    # Item 17: explicit ok:false with a ready status must still block.
    _init_ready_repo(tmp_path)
    _write(
        tmp_path / ".signalos" / "evidence" / "W01" / "release-readiness.json",
        json.dumps({"ok": False, "status": "published", "blockers": []}) + "\n",
    )

    payload = ship_wave(tmp_path, wave=1, dry_run=True, write_evidence=False)

    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["release-readiness"]["status"] == "FAIL"


def test_ship_numeric_wave_normalizes_to_padded_segment(tmp_path: Path):
    # Item 18: ship resolves numeric wave 1 to the unified W01 evidence segment.
    _init_ready_repo(tmp_path)

    payload = ship_wave(tmp_path, wave=1, dry_run=True, write_evidence=True)

    assert payload["wave"] == "W01"
    assert payload["evidence_path"] == ".signalos/evidence/W01/ship.json"
    assert (tmp_path / ".signalos" / "evidence" / "W01" / "ship.json").is_file()


def test_ship_cli_json_dispatches_from_top_level(tmp_path: Path, capsys):
    _init_ready_repo(tmp_path)

    rc = cli_main(
        [
            "signalos",
            "ship",
            "W01",
            "--repo-root",
            str(tmp_path),
            "--dry-run",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "signalos.ship.v1"
    assert payload["status"] == "ship-ready"
    assert payload["tag"]["created"] is False
