"""Tests for product strategy and scope decision artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.strategy import (
    SCOPE_DECISIONS_FILENAME,
    STRATEGY_REVIEW_FILENAME,
    build_scope_decisions,
    build_strategy_review,
    load_scope_decisions,
    load_strategy_review,
    validate_scope_decisions,
    validate_strategy_review,
    write_scope_decisions,
    write_strategy_review,
)


def _strategy_review() -> dict:
    return build_strategy_review(
        product_thesis=(
            "A focused delivery gate should force product tradeoffs before "
            "implementation starts."
        ),
        target_user="Product delivery lead",
        job_to_be_done=(
            "Decide which slice is worth building and which requests should "
            "be rejected or deferred."
        ),
        literal_request_risk=(
            "Literal execution can expand scope without proving the primary "
            "user job."
        ),
        ten_star_options=[
            {
                "id": "TSO-001",
                "title": "Decision artifact gate",
                "rationale": "Makes strategic tradeoffs explicit.",
                "disposition": "accepted",
            },
            {
                "id": "TSO-002",
                "title": "Advisory-only strategy notes",
                "rationale": "Useful context but too easy to bypass.",
                "disposition": "rejected",
            },
        ],
        scope_reduction_options=[
            {
                "id": "SRO-001",
                "title": "Defer delivery integration",
                "rationale": "Parent pipeline owns integration.",
                "disposition": "accepted",
            }
        ],
        required_questions=[
            "Which accepted scope decisions map to tickets?",
        ],
        assumptions=[
            {
                "field": "integration",
                "assumed_value": "parent-owned",
                "reason": "This module only creates and validates artifacts.",
            }
        ],
    )


def _scope_decisions() -> dict:
    return build_scope_decisions(
        [
            {
                "id": "SD-001",
                "decision": "Build the strategy/scope artifact module.",
                "disposition": "accepted",
                "tickets": ["PROD-001"],
            },
            {
                "id": "SD-002",
                "decision": "Wire the artifacts into delivery.py.",
                "disposition": "rejected",
                "rationale": "Integration belongs to the parent task.",
            },
        ]
    )


def test_valid_strategy_review_passes() -> None:
    review = _strategy_review()
    assert validate_strategy_review(review) == []


def test_valid_scope_decisions_passes() -> None:
    scope = _scope_decisions()
    assert validate_scope_decisions(scope) == []


def test_missing_option_disposition_fails() -> None:
    review = _strategy_review()
    del review["ten_star_options"][0]["disposition"]

    errors = validate_strategy_review(review)

    assert any("disposition" in error for error in errors)


def test_accepted_scope_decision_without_trace_fails() -> None:
    scope = build_scope_decisions(
        [
            {
                "id": "SD-001",
                "decision": "Ship the artifact gate.",
                "disposition": "accepted",
            }
        ]
    )

    errors = validate_scope_decisions(scope)

    assert any("accepted decisions must trace" in error for error in errors)


def test_rejected_scope_decision_without_trace_passes() -> None:
    scope = build_scope_decisions(
        [
            {
                "id": "SD-001",
                "decision": "Do not wire into delivery.py here.",
                "disposition": "rejected",
            }
        ]
    )

    assert validate_scope_decisions(scope) == []


def test_files_round_trip(tmp_path: Path) -> None:
    review = _strategy_review()
    scope = _scope_decisions()

    review_path = write_strategy_review(review, tmp_path)
    scope_path = write_scope_decisions(scope, tmp_path)

    assert (
        review_path
        == tmp_path / ".signalos" / "product" / STRATEGY_REVIEW_FILENAME
    )
    assert (
        scope_path
        == tmp_path / ".signalos" / "product" / SCOPE_DECISIONS_FILENAME
    )
    assert json.loads(review_path.read_text(encoding="utf-8")) == review
    assert json.loads(scope_path.read_text(encoding="utf-8")) == scope
    assert load_strategy_review(tmp_path) == review
    assert load_scope_decisions(tmp_path) == scope
