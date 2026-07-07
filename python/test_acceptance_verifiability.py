"""Tests for mechanical-verification Layer 1: verifiability tiers +
the contract-verification metric (mechanical_pct).

Semantics under test (acceptance.classify_criterion_verifiability):
- "mechanical": executable test target + objective wording.
- "partial":    executable target + subjective wording, OR objective
                wording with no executable target yet.
- "human":      subjective wording (look/feel/tone) with no executable
                target.
Classification is deterministic and purely additive (no blocking change).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.acceptance import (
    apply_verifiability_tiers,
    build_acceptance_matrix,
    classify_criterion_verifiability,
    reconcile_acceptance_evidence,
)


def _criterion(description: str, entity=None, workflow=None, test_ids=None):
    return {
        "id": "AC-001",
        "source": "intent",
        "description": description,
        "entity": entity,
        "workflow": workflow,
        "test_ids": test_ids or [],
        "status": "pending",
        "evidence": None,
    }


def _scenario(target: str, description: str = "scenario"):
    return {
        "id": "TS-001",
        "acceptance_id": "AC-001",
        "description": description,
        "kind": "integration",
        "profile_target": target,
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# classify_criterion_verifiability
# ---------------------------------------------------------------------------

class TestClassifyCriterionVerifiability:
    def test_objective_with_executable_target_is_mechanical(self) -> None:
        tier = classify_criterion_verifiability(
            _criterion("CRUD operations for Task", entity="Task"),
            [_scenario("src/task.test.tsx")],
        )
        assert tier == "mechanical"

    def test_subjective_with_executable_target_is_partial(self) -> None:
        tier = classify_criterion_verifiability(
            _criterion("Dashboard should look beautiful and polished"),
            [_scenario("src/dashboard.test.tsx")],
        )
        assert tier == "partial"

    def test_pure_judgment_without_target_is_human(self) -> None:
        tier = classify_criterion_verifiability(
            _criterion("The app should feel intuitive with a friendly tone"),
            [],
        )
        assert tier == "human"

    def test_objective_without_target_is_partial(self) -> None:
        # Provable in principle, not yet machine-checked -> partial, not human.
        tier = classify_criterion_verifiability(
            _criterion("CRUD operations for Task", entity="Task"),
            [],
        )
        assert tier == "partial"

    def test_non_executable_target_does_not_count_as_mechanical(self) -> None:
        # .http files are not executable test evidence (same contract as
        # reconciliation's _is_executable_test_target).
        tier = classify_criterion_verifiability(
            _criterion("CRUD operations for Order", entity="Order"),
            [_scenario("tests/order.http")],
        )
        assert tier == "partial"

    def test_word_boundary_feel_does_not_match_feelings(self) -> None:
        # "feelings" (an entity domain word) must not trip the "feel"
        # subjective-wording heuristic.
        tier = classify_criterion_verifiability(
            _criterion("CRUD operations for feelings journal", entity="Feeling Entry"),
            [_scenario("src/feelings.test.tsx")],
        )
        assert tier == "mechanical"

    def test_should_look_phrase_is_subjective(self) -> None:
        assert classify_criterion_verifiability(
            _criterion("The landing page should look like a premium product"),
            [],
        ) == "human"
        assert classify_criterion_verifiability(
            _criterion("The landing page should look like a premium product"),
            [_scenario("src/landing.test.tsx")],
        ) == "partial"

    def test_scenario_wording_participates_in_classification(self) -> None:
        # Subjective language in the linked scenario also downgrades.
        tier = classify_criterion_verifiability(
            _criterion("Workflow: onboard a customer", workflow="onboard a customer"),
            [_scenario("src/onboard.test.tsx", description="onboarding feels delightful")],
        )
        assert tier == "partial"


# ---------------------------------------------------------------------------
# apply_verifiability_tiers + pct math
# ---------------------------------------------------------------------------

class TestApplyVerifiabilityTiers:
    def test_summary_counts_and_pct(self) -> None:
        matrix = {
            "criteria": [
                {"id": "AC-001", "description": "CRUD operations for Task",
                 "entity": "Task", "workflow": None, "test_ids": ["TS-001"]},
                {"id": "AC-002", "description": "Workflow: track expenses",
                 "entity": None, "workflow": "track expenses",
                 "test_ids": ["TS-002"]},
                {"id": "AC-003", "description": "Dashboard should look beautiful",
                 "entity": None, "workflow": None, "test_ids": ["TS-003"]},
                {"id": "AC-004", "description": "The tone must feel friendly",
                 "entity": None, "workflow": None, "test_ids": []},
            ],
            "test_scenarios": [
                {"id": "TS-001", "profile_target": "src/task.test.tsx",
                 "description": "task crud works"},
                {"id": "TS-002", "profile_target": "tests/test_track.py",
                 "description": "expenses tracked"},
                {"id": "TS-003", "profile_target": "src/dashboard.test.tsx",
                 "description": "dashboard renders"},
            ],
        }
        result = apply_verifiability_tiers(matrix)
        tiers = [c["verifiability"] for c in result["criteria"]]
        assert tiers == ["mechanical", "mechanical", "partial", "human"]
        summary = result["verifiability_summary"]
        assert summary == {
            "mechanical": 2,
            "partial": 1,
            "human": 1,
            "mechanical_pct": 50.0,
        }

    def test_empty_matrix_pct_is_zero(self) -> None:
        result = apply_verifiability_tiers({"criteria": [], "test_scenarios": []})
        assert result["verifiability_summary"] == {
            "mechanical": 0,
            "partial": 0,
            "human": 0,
            "mechanical_pct": 0.0,
        }

    def test_pct_rounding(self) -> None:
        matrix = {
            "criteria": [
                {"id": f"AC-{i:03d}", "description": "CRUD operations for Task",
                 "entity": "Task", "test_ids": ["TS-001"] if i == 1 else []}
                for i in range(1, 4)
            ],
            "test_scenarios": [
                {"id": "TS-001", "profile_target": "src/task.test.tsx",
                 "description": "d"},
            ],
        }
        result = apply_verifiability_tiers(matrix)
        # 1 of 3 mechanical -> 33.3
        assert result["verifiability_summary"]["mechanical_pct"] == 33.3

    def test_idempotent(self) -> None:
        matrix = {
            "criteria": [
                {"id": "AC-001", "description": "CRUD operations for Task",
                 "entity": "Task", "test_ids": ["TS-001"]},
            ],
            "test_scenarios": [
                {"id": "TS-001", "profile_target": "src/task.test.tsx",
                 "description": "d"},
            ],
        }
        once = apply_verifiability_tiers(matrix)
        summary_once = dict(once["verifiability_summary"])
        twice = apply_verifiability_tiers(once)
        assert twice["verifiability_summary"] == summary_once


# ---------------------------------------------------------------------------
# Matrix construction + reconciliation carry the tiers
# ---------------------------------------------------------------------------

class TestMatrixIntegration:
    def test_build_acceptance_matrix_persists_tiers(self) -> None:
        intent = {
            "product_name": "taskapp",
            "entities": ["Task"],
            "primary_workflows": ["track expenses"],
            "ux_surfaces": ["dashboard"],
        }
        matrix = build_acceptance_matrix(intent, None, "react-vite")
        assert matrix["criteria"], "expected criteria"
        for criterion in matrix["criteria"]:
            assert criterion["verifiability"] in ("mechanical", "partial", "human")
        summary = matrix["verifiability_summary"]
        assert set(summary) == {"mechanical", "partial", "human", "mechanical_pct"}
        total = sum(summary[k] for k in ("mechanical", "partial", "human"))
        assert total == len(matrix["criteria"])
        assert summary["mechanical_pct"] == round(
            100.0 * summary["mechanical"] / total, 1,
        )

    def test_reconcile_reapplies_tiers_to_legacy_matrix(self, tmp_path: Path) -> None:
        # A matrix built before the tier feature (no verifiability keys)
        # gains tiers at reconciliation.
        matrix = {
            "criteria": [
                {"id": "AC-001", "description": "CRUD operations for Task",
                 "entity": "Task", "workflow": None, "test_ids": ["TS-001"],
                 "status": "pending", "evidence": None},
            ],
            "test_scenarios": [
                {"id": "TS-001", "acceptance_id": "AC-001",
                 "profile_target": "src/task.test.tsx",
                 "description": "task crud", "status": "pending"},
            ],
        }
        result = reconcile_acceptance_evidence(
            matrix, tmp_path, validation_result=None,
        )
        assert result["criteria"][0]["verifiability"] == "mechanical"
        assert result["verifiability_summary"]["mechanical_pct"] == 100.0
