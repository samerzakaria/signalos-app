"""Tests for SignalOS review-readiness artifacts."""

from __future__ import annotations

from pathlib import Path

from signalos_lib.product.reviews import (
    REVIEW_READINESS_FILENAME,
    build_review_readiness,
    load_review_readiness,
    validate_review_readiness,
    write_review_readiness,
)


def _valid_review_readiness() -> dict:
    return build_review_readiness(
        strategy_status="approved",
        scope_status="approved",
        architecture_status="approved",
        design_status="approved",
        build_status="passed",
        test_status="passed",
        browser_qa_status="passed",
        security_status="passed",
        docs_status="complete",
        handoff_status="complete",
        blocking_items=[],
        ready=True,
    )


def test_review_readiness_valid_artifact_passes() -> None:
    result = validate_review_readiness(_valid_review_readiness())

    assert result["valid"] is True
    assert result["ready"] is True
    assert result["passes"] is True
    assert result["blocked"] is False


def test_review_readiness_missing_status_fails() -> None:
    artifact = _valid_review_readiness()
    del artifact["security_status"]

    result = validate_review_readiness(artifact)

    assert result["valid"] is False
    assert result["ready"] is False
    assert result["missing_statuses"] == ["security_status"]
    assert "security_status" in result["errors"][0]


def test_review_readiness_blocking_items_report_blockers() -> None:
    artifact = _valid_review_readiness()
    artifact["ready"] = False
    artifact["blocking_items"] = ["Browser QA evidence is missing"]

    result = validate_review_readiness(artifact)

    assert result["valid"] is True
    assert result["ready"] is False
    assert result["passes"] is False
    assert result["blocked"] is True
    assert result["blockers"] == ["Browser QA evidence is missing"]


def test_review_readiness_false_ready_detection() -> None:
    artifact = _valid_review_readiness()
    artifact["blocking_items"] = ["Security review has not signed off"]

    result = validate_review_readiness(artifact)

    assert result["valid"] is False
    assert result["declared_ready"] is True
    assert result["ready"] is False
    assert result["blocked"] is True
    assert "ready=true" in result["errors"][0]


def test_review_readiness_round_trip(tmp_path: Path) -> None:
    signalos_dir = tmp_path / ".signalos"
    artifact = _valid_review_readiness()

    path = write_review_readiness(artifact, signalos_dir)
    loaded = load_review_readiness(signalos_dir)

    assert path == signalos_dir / "product" / REVIEW_READINESS_FILENAME
    assert loaded == artifact
    assert validate_review_readiness(loaded)["passes"] is True
