# test_product_acceptance.py
# Phase P6 — Acceptance matrix tests
#
# Covers: matrix generation from intent + blueprint, persistence round-trip,
# status updates, and closure readiness checks.

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the library is importable from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.intent import extract_product_intent
from signalos_lib.product.blueprints.registry import load_blueprint
from signalos_lib.product.acceptance import (
    build_acceptance_matrix,
    check_closure_readiness,
    load_acceptance_matrix,
    update_criterion_status,
    write_acceptance_matrix,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def task_intent() -> dict:
    return extract_product_intent(
        "Build a task management app with tasks, projects, and users. "
        "Create tasks, complete tasks, list tasks, assign tasks. "
        "Kanban board and dashboard."
    )


@pytest.fixture()
def financial_intent() -> dict:
    return extract_product_intent(
        "Build a financial dashboard with revenue, churn, and metrics. "
        "Track revenue, compute churn, visualize charts, "
        "monitor metrics on a dashboard."
    )


@pytest.fixture()
def task_blueprint() -> dict:
    bp = load_blueprint("task-management")
    assert bp is not None
    return bp


@pytest.fixture()
def financial_blueprint() -> dict:
    bp = load_blueprint("financial-dashboard")
    assert bp is not None
    return bp


@pytest.fixture()
def empty_intent() -> dict:
    return extract_product_intent("")


# ---------------------------------------------------------------------------
# Task-management intent + blueprint
# ---------------------------------------------------------------------------

class TestTaskManagementMatrix:
    def test_produces_criteria_for_entities(self, task_intent, task_blueprint):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        descriptions = [c["description"] for c in m["criteria"]]
        # Should have CRUD criteria for entities found in the intent
        crud_descs = [d for d in descriptions if d.startswith("CRUD operations")]
        assert len(crud_descs) > 0

    def test_produces_workflow_criteria(self, task_intent, task_blueprint):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        workflow_descs = [
            c["description"] for c in m["criteria"]
            if c["description"].startswith("Workflow:")
        ]
        assert len(workflow_descs) > 0

    def test_produces_blueprint_criteria(self, task_intent, task_blueprint):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        bp_criteria = [c for c in m["criteria"] if c["source"] == "blueprint"]
        assert len(bp_criteria) > 0
        # Blueprint criteria should reference task-management acceptance outcomes
        outcomes = {c["description"] for c in bp_criteria}
        assert any("task" in o.lower() for o in outcomes)


# ---------------------------------------------------------------------------
# Financial-dashboard intent + blueprint
# ---------------------------------------------------------------------------

class TestFinancialDashboardMatrix:
    def test_produces_criteria_for_metrics(self, financial_intent, financial_blueprint):
        m = build_acceptance_matrix(financial_intent, financial_blueprint, "react-vite")
        descriptions = [c["description"].lower() for c in m["criteria"]]
        # Should cover metrics/revenue/churn in some form
        assert any("revenue" in d or "churn" in d or "metric" in d for d in descriptions)

    def test_produces_chart_criteria(self, financial_intent, financial_blueprint):
        m = build_acceptance_matrix(financial_intent, financial_blueprint, "react-vite")
        descriptions = [c["description"].lower() for c in m["criteria"]]
        assert any("chart" in d or "dashboard" in d for d in descriptions)


# ---------------------------------------------------------------------------
# Intent-only (no blueprint)
# ---------------------------------------------------------------------------

class TestIntentOnly:
    def test_intent_only_produces_criteria(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        assert len(m["criteria"]) > 0
        assert m["blueprint_id"] is None
        # All criteria should be from intent
        assert all(c["source"] == "intent" for c in m["criteria"])

    def test_intent_only_produces_test_scenarios(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        assert len(m["test_scenarios"]) > 0


# ---------------------------------------------------------------------------
# Empty intent
# ---------------------------------------------------------------------------

class TestEmptyIntent:
    def test_empty_intent_no_crash(self, empty_intent):
        m = build_acceptance_matrix(empty_intent, None, "generic")
        assert m["criteria"] == []
        assert m["test_scenarios"] == []
        assert m["summary"]["total_criteria"] == 0
        assert m["summary"]["total_tests"] == 0


# ---------------------------------------------------------------------------
# Test scenario linking
# ---------------------------------------------------------------------------

class TestScenarioLinking:
    def test_test_ids_link_back(self, task_intent, task_blueprint):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        all_ts_ids = {ts["id"] for ts in m["test_scenarios"]}
        for crit in m["criteria"]:
            for tid in crit["test_ids"]:
                assert tid in all_ts_ids, f"{tid} referenced by {crit['id']} not found in test_scenarios"

    def test_acceptance_ids_link_back(self, task_intent, task_blueprint):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        all_ac_ids = {c["id"] for c in m["criteria"]}
        for ts in m["test_scenarios"]:
            assert ts["acceptance_id"] in all_ac_ids, (
                f"{ts['id']} references {ts['acceptance_id']} not in criteria"
            )


# ---------------------------------------------------------------------------
# Profile affects test targets
# ---------------------------------------------------------------------------

class TestProfileTargets:
    def test_react_vite_targets(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        for ts in m["test_scenarios"]:
            assert ts["profile_target"].startswith("src/")
            assert ts["profile_target"].endswith(".test.tsx")

    def test_generic_targets(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "generic")
        for ts in m["test_scenarios"]:
            assert ts["profile_target"].startswith("tests/test_")
            assert ts["profile_target"].endswith(".py")


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_write_load_roundtrip(self, task_intent, task_blueprint, tmp_path):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        signalos_dir = tmp_path / ".signalos"
        path = write_acceptance_matrix(m, signalos_dir)
        assert path.is_file()
        loaded = load_acceptance_matrix(signalos_dir)
        assert loaded is not None
        assert loaded["schema_version"] == m["schema_version"]
        assert len(loaded["criteria"]) == len(m["criteria"])
        assert len(loaded["test_scenarios"]) == len(m["test_scenarios"])

    def test_load_missing_returns_none(self, tmp_path):
        assert load_acceptance_matrix(tmp_path / ".signalos") is None


# ---------------------------------------------------------------------------
# Status updates
# ---------------------------------------------------------------------------

class TestStatusUpdate:
    def test_update_criterion_status(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        first_id = m["criteria"][0]["id"]
        updated = update_criterion_status(m, first_id, "passed", evidence="tests green")
        crit = next(c for c in updated["criteria"] if c["id"] == first_id)
        assert crit["status"] == "passed"
        assert crit["evidence"] == "tests green"

    def test_update_unknown_criterion_is_noop(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        original_statuses = [c["status"] for c in m["criteria"]]
        update_criterion_status(m, "AC-999", "passed")
        assert [c["status"] for c in m["criteria"]] == original_statuses


# ---------------------------------------------------------------------------
# Closure readiness
# ---------------------------------------------------------------------------

class TestClosureReadiness:
    def test_pending_not_ready(self, task_intent, task_blueprint):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        result = check_closure_readiness(m)
        assert result["ready"] is False
        assert result["pending"] > 0
        assert len(result["blockers"]) > 0

    def test_all_passed_is_ready(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        for crit in m["criteria"]:
            crit["status"] = "passed"
        result = check_closure_readiness(m)
        assert result["ready"] is True
        assert result["passed"] > 0
        assert result["blockers"] == []

    def test_any_failed_not_ready(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        for crit in m["criteria"]:
            crit["status"] = "passed"
        # Fail one
        m["criteria"][0]["status"] = "failed"
        result = check_closure_readiness(m)
        assert result["ready"] is False
        assert result["failed"] == 1
        assert len(result["blockers"]) == 1

    def test_only_skipped_not_ready(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        for crit in m["criteria"]:
            crit["status"] = "skipped"
        result = check_closure_readiness(m)
        assert result["ready"] is False
        assert result["passed"] == 0
        assert result["skipped"] > 0


# ---------------------------------------------------------------------------
# Summary counts
# ---------------------------------------------------------------------------

class TestSummaryCounts:
    def test_summary_counts_intent_only(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        s = m["summary"]
        assert s["total_criteria"] == len(m["criteria"])
        assert s["total_tests"] == len(m["test_scenarios"])
        assert s["from_intent"] == s["total_criteria"]
        assert s["from_blueprint"] == 0

    def test_summary_counts_with_blueprint(self, task_intent, task_blueprint):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        s = m["summary"]
        assert s["total_criteria"] == len(m["criteria"])
        assert s["total_tests"] == len(m["test_scenarios"])
        assert s["from_intent"] + s["from_blueprint"] == s["total_criteria"]
        assert s["from_blueprint"] > 0


# ---------------------------------------------------------------------------
# Source tags
# ---------------------------------------------------------------------------

class TestSourceTags:
    def test_blueprint_criteria_source(self, task_intent, task_blueprint):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        bp = [c for c in m["criteria"] if c["source"] == "blueprint"]
        assert len(bp) > 0

    def test_intent_criteria_source(self, task_intent, task_blueprint):
        m = build_acceptance_matrix(task_intent, task_blueprint, "react-vite")
        intent_crit = [c for c in m["criteria"] if c["source"] == "intent"]
        assert len(intent_crit) > 0
