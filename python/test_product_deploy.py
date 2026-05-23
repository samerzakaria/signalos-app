"""Tests for signalos_lib.product.deploy — deploy decision and evidence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from signalos_lib.product.deploy import (
    load_deploy_decision,
    load_deploy_evidence,
    make_deploy_decision,
    prepare_deploy_evidence,
    write_deploy_decision,
    write_deploy_evidence,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Minimal repo root with a .signalos directory."""
    (tmp_path / ".signalos").mkdir()
    return tmp_path


def _passing_validation() -> dict:
    return {"status": "PASS", "closeable": True, "ok": True, "level": "full"}


def _failing_validation() -> dict:
    return {"status": "FAIL", "closeable": False, "ok": False, "level": "incomplete"}


# ---------------------------------------------------------------------------
# make_deploy_decision — mode="none"
# ---------------------------------------------------------------------------

class TestModeNone:
    def test_deploy_not_allowed(self, repo: Path) -> None:
        result = make_deploy_decision("none", None, repo)
        assert result["deploy_allowed"] is False

    def test_records_explicit_no_deploy(self, repo: Path) -> None:
        """No-deploy mode records an explicit decision, not just absence."""
        result = make_deploy_decision("none", None, repo)
        assert result["mode"] == "none"
        assert result["reason"] == "No deployment requested"
        assert result["schema_version"] == "signalos.deploy_decision.v1"


# ---------------------------------------------------------------------------
# make_deploy_decision — mode="prepare"
# ---------------------------------------------------------------------------

class TestModePrepare:
    def test_deploy_not_allowed(self, repo: Path) -> None:
        result = make_deploy_decision("prepare", _passing_validation(), repo)
        assert result["deploy_allowed"] is False

    def test_reason_mentions_prepare(self, repo: Path) -> None:
        result = make_deploy_decision("prepare", _passing_validation(), repo)
        assert "Prepare mode" in result["reason"]
        assert "no live deploy" in result["reason"]


# ---------------------------------------------------------------------------
# make_deploy_decision — mode="live"
# ---------------------------------------------------------------------------

class TestModeLive:
    def test_no_validation_blocked(self, repo: Path) -> None:
        result = make_deploy_decision("live", None, repo)
        assert result["deploy_allowed"] is False
        assert "No validation result" in result["blockers"]

    def test_failed_validation_blocked(self, repo: Path) -> None:
        result = make_deploy_decision("live", _failing_validation(), repo)
        assert result["deploy_allowed"] is False
        assert any("Validation not ready" in b for b in result["blockers"])

    def test_passed_validation_allowed(self, repo: Path) -> None:
        result = make_deploy_decision("live", _passing_validation(), repo)
        assert result["deploy_allowed"] is True
        assert result["reason"] == "Validation passed, live deploy authorized"

    def test_blocker_message_specific(self, repo: Path) -> None:
        result = make_deploy_decision("live", _failing_validation(), repo)
        assert result["blockers"]
        # Blocker should mention the validation level
        assert "incomplete" in result["blockers"][0]


# ---------------------------------------------------------------------------
# Evidence from validation
# ---------------------------------------------------------------------------

class TestEvidenceRecording:
    def test_records_validation_closeable(self, repo: Path) -> None:
        result = make_deploy_decision("live", _passing_validation(), repo)
        assert result["evidence"]["validation_closeable"] is True

    def test_records_validation_level(self, repo: Path) -> None:
        result = make_deploy_decision("live", _passing_validation(), repo)
        assert result["evidence"]["validation_level"] == "full"

    def test_no_validation_evidence_is_none(self, repo: Path) -> None:
        result = make_deploy_decision("none", None, repo)
        assert result["evidence"]["validation_closeable"] is None
        assert result["evidence"]["validation_level"] is None


# ---------------------------------------------------------------------------
# Timestamp
# ---------------------------------------------------------------------------

class TestTimestamp:
    def test_valid_iso_format(self, repo: Path) -> None:
        result = make_deploy_decision("none", None, repo)
        ts = result["decided_at"]
        # Must parse without error
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.year >= 2024


# ---------------------------------------------------------------------------
# Invalid mode
# ---------------------------------------------------------------------------

class TestInvalidMode:
    def test_invalid_mode_returns_error(self, repo: Path) -> None:
        result = make_deploy_decision("yolo", None, repo)
        assert result["deploy_allowed"] is False
        assert result.get("error") is True
        assert any("Invalid mode" in b for b in result["blockers"])


# ---------------------------------------------------------------------------
# prepare_deploy_evidence
# ---------------------------------------------------------------------------

class TestPrepareEvidence:
    def test_creates_evidence_with_checklist(self, repo: Path) -> None:
        decision = make_deploy_decision("prepare", _passing_validation(), repo)
        evidence = prepare_deploy_evidence(repo, decision, "my-app", "generic")
        assert isinstance(evidence["deploy_checklist"], list)
        assert len(evidence["deploy_checklist"]) > 0

    def test_react_vite_includes_build_step(self, repo: Path) -> None:
        decision = make_deploy_decision("prepare", _passing_validation(), repo)
        evidence = prepare_deploy_evidence(repo, decision, "my-app", "react-vite")
        assert any("production build" in item.lower() for item in evidence["deploy_checklist"])

    def test_generic_checklist_simpler(self, repo: Path) -> None:
        decision = make_deploy_decision("prepare", _passing_validation(), repo)
        evidence_rv = prepare_deploy_evidence(repo, decision, "my-app", "react-vite")
        evidence_gen = prepare_deploy_evidence(repo, decision, "my-app", "generic")
        assert len(evidence_gen["deploy_checklist"]) <= len(evidence_rv["deploy_checklist"])

    def test_evidence_includes_release_notes(self, repo: Path) -> None:
        decision = make_deploy_decision("prepare", _passing_validation(), repo)
        evidence = prepare_deploy_evidence(repo, decision, "my-app", "generic")
        assert isinstance(evidence["release_notes"], str)
        assert "my-app" in evidence["release_notes"]

    def test_common_checklist_items_always_present(self, repo: Path) -> None:
        decision = make_deploy_decision("prepare", _passing_validation(), repo)
        for profile in ("react-vite", "generic"):
            evidence = prepare_deploy_evidence(repo, decision, "app", profile)
            items_lower = [i.lower() for i in evidence["deploy_checklist"]]
            assert any("stakeholder" in i for i in items_lower)
            assert any("tests pass" in i for i in items_lower)


# ---------------------------------------------------------------------------
# Round-trip persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_decision_round_trip(self, repo: Path) -> None:
        decision = make_deploy_decision("live", _passing_validation(), repo)
        signalos_dir = repo / ".signalos"
        path = write_deploy_decision(decision, signalos_dir)
        assert path.is_file()
        loaded = load_deploy_decision(signalos_dir)
        assert loaded == decision

    def test_evidence_round_trip(self, repo: Path) -> None:
        decision = make_deploy_decision("prepare", _passing_validation(), repo)
        evidence = prepare_deploy_evidence(repo, decision, "my-app", "generic")
        signalos_dir = repo / ".signalos"
        # prepare_deploy_evidence already writes, so just load
        loaded = load_deploy_evidence(signalos_dir)
        assert loaded == evidence

    def test_load_missing_returns_none(self, repo: Path) -> None:
        signalos_dir = repo / ".signalos"
        assert load_deploy_decision(signalos_dir) is None
        assert load_deploy_evidence(signalos_dir) is None

    def test_written_file_is_valid_json(self, repo: Path) -> None:
        decision = make_deploy_decision("none", None, repo)
        signalos_dir = repo / ".signalos"
        path = write_deploy_decision(decision, signalos_dir)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == "signalos.deploy_decision.v1"
