"""Tests for SignalOS architecture review artifacts."""

from __future__ import annotations

from pathlib import Path

from signalos_lib.product.reviews import (
    ARCH_REVIEW_FILENAME,
    build_arch_review,
    load_arch_review,
    validate_arch_review,
    write_arch_review,
)


def _valid_arch_review() -> dict:
    return build_arch_review(
        system_boundaries=["Browser UI", "Application API", "SQLite store"],
        data_flow=["Browser submits requests to API; API persists domain state"],
        state_transitions=["Draft -> submitted -> reviewed -> closed"],
        failure_modes=["Database unavailable", "Invalid user input"],
        trust_boundaries=["Browser to server", "Server to local filesystem"],
        edge_cases=["Empty dataset", "Duplicate submission"],
        test_strategy=["Unit tests for reducers", "Integration tests for API"],
        open_risks=[],
        blocking_findings=[],
    )


def test_arch_review_valid_artifact_passes() -> None:
    result = validate_arch_review(_valid_arch_review())

    assert result["valid"] is True
    assert result["passes"] is True
    assert result["blocked"] is False
    assert result["errors"] == []


def test_arch_review_missing_section_fails() -> None:
    artifact = _valid_arch_review()
    del artifact["data_flow"]

    result = validate_arch_review(artifact)

    assert result["valid"] is False
    assert result["passes"] is False
    assert result["missing_sections"] == ["data_flow"]
    assert "data_flow" in result["errors"][0]


def test_arch_review_blocking_findings_report_blockers() -> None:
    artifact = _valid_arch_review()
    artifact["blocking_findings"] = ["Threat model missing for admin actions"]

    result = validate_arch_review(artifact)

    assert result["valid"] is True
    assert result["passes"] is False
    assert result["blocked"] is True
    assert result["blockers"] == ["Threat model missing for admin actions"]


def test_arch_review_round_trip(tmp_path: Path) -> None:
    signalos_dir = tmp_path / ".signalos"
    artifact = _valid_arch_review()

    path = write_arch_review(artifact, signalos_dir)
    loaded = load_arch_review(signalos_dir)

    assert path == signalos_dir / "product" / ARCH_REVIEW_FILENAME
    assert loaded == artifact
    assert validate_arch_review(loaded)["passes"] is True
