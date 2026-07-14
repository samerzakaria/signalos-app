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
from signalos_lib.product.blueprints.registry import (
    apply_blueprint_intent_defaults,
    load_blueprint,
)
from signalos_lib.product.capabilities import apply_capability_choices
from signalos_lib.product.acceptance import (
    UX_ACCEPTANCE_MIN_CONTROLS,
    UX_ACCEPTANCE_MIN_STYLED,
    build_acceptance_matrix,
    check_closure_readiness,
    ensure_ux_acceptance_test,
    has_responsive_breakpoints,
    load_acceptance_matrix,
    reconcile_acceptance_evidence,
    run_ux_acceptance,
    scan_ux_state_coverage,
    update_criterion_status,
    ux_acceptance_applies,
    ux_acceptance_test_source,
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

    def test_frontend_none_removes_blueprint_ui_surface_criteria(self, task_blueprint):
        intent = extract_product_intent("Build a REST API for task management")
        intent = apply_capability_choices(
            intent,
            technologies=["node"],
            frontend="none",
            database="postgresql",
            cache="redis",
        )
        intent = apply_blueprint_intent_defaults(intent, task_blueprint)
        intent = apply_capability_choices(
            intent,
            technologies=["node"],
            frontend="none",
            database="postgresql",
            cache="redis",
        )

        m = build_acceptance_matrix(intent, task_blueprint, "node-api")
        descriptions = [c["description"] for c in m["criteria"]]

        assert not any(description.startswith("UX surface") for description in descriptions)
        assert not any("dashboard shows" in description.lower() for description in descriptions)
        assert any("GET /kpis" in description for description in descriptions)
        assert all(
            scenario["profile_target"].startswith("tests/")
            for scenario in m["test_scenarios"]
        )


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
# Evidence reconciliation
# ---------------------------------------------------------------------------

def _validation_result(*, dry_run: bool = False) -> dict:
    return {
        "dry_run": dry_run,
        "results": {
            "build": {"status": "passed"},
            "test": {"status": "passed"},
            "security": {"status": "passed"},
        },
    }


class TestAcceptanceReconciliation:
    def test_reconciles_passed_criteria_from_real_test_evidence(self, task_intent, tmp_path):
        m = build_acceptance_matrix(task_intent, None, "generic")
        first = m["test_scenarios"][0]
        target = tmp_path / first["profile_target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("import unittest\n", encoding="utf-8")
        reconciled = reconcile_acceptance_evidence(
            m,
            tmp_path,
            validation_result=_validation_result(),
            runtime_proof={"status": "skipped"},
            ux_proof={"status": "skipped"},
            security_result={"status": "passed"},
        )

        first_criterion = reconciled["criteria"][0]
        assert first_criterion["status"] == "passed"
        assert "build/test validation passed" in first_criterion["evidence"]
        assert reconciled["reconciliation"]["passed"] >= 1

    def test_dry_run_validation_keeps_acceptance_pending(self, task_intent, tmp_path):
        m = build_acceptance_matrix(task_intent, None, "generic")
        first = m["test_scenarios"][0]
        target = tmp_path / first["profile_target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("import unittest\n", encoding="utf-8")

        reconciled = reconcile_acceptance_evidence(
            m,
            tmp_path,
            validation_result=_validation_result(dry_run=True),
            runtime_proof={"status": "skipped"},
            ux_proof={"status": "skipped"},
            security_result={"status": "passed"},
        )

        assert reconciled["criteria"][0]["status"] == "pending"
        assert any("dry-run" in b for b in reconciled["reconciliation"]["blockers"])

    def test_missing_test_target_keeps_acceptance_pending(self, task_intent, tmp_path):
        m = build_acceptance_matrix(task_intent, None, "generic")

        reconciled = reconcile_acceptance_evidence(
            m,
            tmp_path,
            validation_result=_validation_result(),
            runtime_proof={"status": "skipped"},
            ux_proof={"status": "skipped"},
            security_result={"status": "passed"},
        )

        assert reconciled["criteria"][0]["status"] == "pending"
        assert any("missing" in b for b in reconciled["reconciliation"]["blockers"])


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


# ---------------------------------------------------------------------------
# FIX 2 — UX baseline must-haves as RED-gated acceptance criteria
# ---------------------------------------------------------------------------

_UX_BASELINE_MARKERS = {"responsive", "empty_state", "loading_state", "error_state"}


def _baseline_only_matrix() -> dict:
    return {
        "schema_version": "signalos.acceptance_matrix.v1",
        "criteria": [
            {"id": f"AC-00{i + 1}", "source": "intent", "description": desc,
             "entity": None, "workflow": None, "test_ids": [],
             "status": "pending", "evidence": None, "ux_baseline": marker}
            for i, (marker, desc) in enumerate([
                ("responsive", "Responsive layout"),
                ("empty_state", "Empty state"),
                ("loading_state", "Loading state"),
                ("error_state", "Error state"),
            ])
        ],
        "test_scenarios": [],
    }


class TestResponsiveScan:
    def test_tailwind_breakpoints_detected(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "App.tsx").write_text(
            'export default () => <div className="grid sm:grid-cols-2 lg:grid-cols-4" />',
            encoding="utf-8")
        assert has_responsive_breakpoints(tmp_path) is True

    def test_media_query_detected(self, tmp_path):
        (tmp_path / "styles.css").write_text(
            "@media (min-width: 768px){ .x{display:flex} }", encoding="utf-8")
        assert has_responsive_breakpoints(tmp_path) is True

    def test_no_breakpoints_is_false(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "App.tsx").write_text(
            'export default () => <div className="flex gap-2" />', encoding="utf-8")
        assert has_responsive_breakpoints(tmp_path) is False

    def test_node_modules_pruned(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "x.css").write_text("@media (min-width: 1px){}", encoding="utf-8")
        assert has_responsive_breakpoints(tmp_path) is False


class TestUxStateScan:
    def test_states_detected_from_mounting_tests(self, tmp_path):
        t = tmp_path / "src" / "App.test.tsx"
        t.parent.mkdir(parents=True)
        t.write_text(
            "import { render, screen } from '@testing-library/react';\n"
            "test('empty', () => { render(<App items={[]} />); "
            "screen.getByText(/no expenses yet/i); });\n"
            "test('loading', () => { render(<App loading />); "
            "screen.getByText(/loading/i); });\n"
            "test('error', () => { render(<App error='boom' />); "
            "screen.getByText(/something went wrong/i); });\n",
            encoding="utf-8")
        assert scan_ux_state_coverage(tmp_path) == {
            "empty_state": True, "loading_state": True, "error_state": True}

    def test_unit_test_without_mount_does_not_count(self, tmp_path):
        # references states but never MOUNTS the UI -> not counted (a real
        # behavioural test must render the component, not just call a helper).
        t = tmp_path / "src" / "util.test.ts"
        t.parent.mkdir(parents=True)
        t.write_text("test('x', () => { expect(isEmpty([])).toBe(true); "
                     "expect(loadingFlag).toBe(false); });\n", encoding="utf-8")
        assert scan_ux_state_coverage(tmp_path) == {
            "empty_state": False, "loading_state": False, "error_state": False}


class TestUxBaselineCriteria:
    def test_browser_intent_adds_ux_baseline_criteria(self, task_intent):
        m = build_acceptance_matrix(task_intent, None, "react-vite")
        markers = {c.get("ux_baseline") for c in m["criteria"] if c.get("ux_baseline")}
        assert markers == _UX_BASELINE_MARKERS
        # counted as intent-derived (keeps from_intent == total for intent-only)
        assert m["summary"]["from_intent"] == m["summary"]["total_criteria"]
        # baseline criteria carry no test scenarios (verified by scan, not a file)
        assert all(not c["test_ids"] for c in m["criteria"] if c.get("ux_baseline"))

    def test_frontend_none_has_no_ux_baseline(self):
        intent = extract_product_intent("Build a REST API for task management")
        intent = apply_capability_choices(
            intent, technologies=["node"], frontend="none",
            database="postgresql", cache="redis")
        m = build_acceptance_matrix(intent, None, "node-api")
        assert not any(c.get("ux_baseline") for c in m["criteria"])

    def test_reconcile_passes_baseline_when_scan_satisfied(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "App.tsx").write_text(
            'export default () => <div className="grid sm:grid-cols-2" />',
            encoding="utf-8")
        (tmp_path / "src" / "App.test.tsx").write_text(
            "import { render, screen } from 'x';\n"
            "test('e', () => { render(<App items={[]} />); screen.getByText(/no data/i); });\n"
            "test('l', () => { render(<App loading />); screen.getByText(/loading/i); });\n"
            "test('r', () => { render(<App error />); screen.getByText(/error/i); });\n",
            encoding="utf-8")
        reconciled = reconcile_acceptance_evidence(
            _baseline_only_matrix(), tmp_path,
            validation_result=_validation_result())
        statuses = {c["ux_baseline"]: c["status"] for c in reconciled["criteria"]}
        assert statuses == {"responsive": "passed", "empty_state": "passed",
                            "loading_state": "passed", "error_state": "passed"}

    def test_reconcile_blocks_baseline_when_scan_empty(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "App.tsx").write_text(
            'export default () => <div className="flex" />', encoding="utf-8")
        reconciled = reconcile_acceptance_evidence(
            _baseline_only_matrix(), tmp_path,
            validation_result=_validation_result())
        assert all(c["status"] == "pending" for c in reconciled["criteria"])
        blockers = " ".join(reconciled["reconciliation"]["blockers"])
        assert "responsive breakpoints" in blockers
        assert "empty" in blockers


# ---------------------------------------------------------------------------
# UX/behavioral acceptance -- the build-time "ship a real, usable UI" hard gate
# ---------------------------------------------------------------------------

def _react_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text(
        "export default () => null;\n", encoding="utf-8")
    return tmp_path


class TestUxAcceptanceMechanism:
    def test_source_is_a_rendered_dom_measurement(self):
        src = ux_acceptance_test_source("./App")
        # renders the REAL app and measures the MOUNTED DOM (never source/
        # className string counting)
        assert "render(<App />)" in src
        assert "queryAllByRole" in src
        assert "querySelectorAll('*')" in src
        assert "import App from './App'" in src
        # the exact thresholds the model must satisfy are baked in
        assert f"toBeGreaterThanOrEqual({UX_ACCEPTANCE_MIN_CONTROLS})" in src
        assert f"toBeGreaterThanOrEqual({UX_ACCEPTANCE_MIN_STYLED})" in src

    def test_applies_only_to_browser_with_app_entry(self, tmp_path):
        (tmp_path / "src").mkdir()
        assert ux_acceptance_applies(tmp_path, "react-vite") is False  # no App
        (tmp_path / "src" / "App.tsx").write_text(
            "export default () => null;\n", encoding="utf-8")
        assert ux_acceptance_applies(tmp_path, "react-vite") is True
        assert ux_acceptance_applies(tmp_path, "node-api") is False

    def test_ensure_authors_for_react_and_skips_non_browser(self, tmp_path):
        _react_repo(tmp_path)
        path = ensure_ux_acceptance_test(
            tmp_path, source_dir="src", profile="react-vite")
        assert path is not None and path.is_file()
        assert "queryAllByRole" in path.read_text(encoding="utf-8")
        # non-browser profile -> never authored
        assert ensure_ux_acceptance_test(
            tmp_path, source_dir="src", profile="node-api") is None

    def test_ensure_is_idempotent(self, tmp_path):
        _react_repo(tmp_path)
        p1 = ensure_ux_acceptance_test(tmp_path, source_dir="src",
                                       profile="react-vite")
        first = p1.read_text(encoding="utf-8")
        p2 = ensure_ux_acceptance_test(tmp_path, source_dir="src",
                                       profile="react-vite")
        assert p2 == p1
        assert p2.read_text(encoding="utf-8") == first  # canonical, unchanged

    def test_run_skips_without_installed_deps_never_false_fails(self, tmp_path):
        # No node_modules -> the render measurement cannot run offline. It must
        # SKIP (ran=False, ok=True), never false-fail a build on tooling grounds.
        _react_repo(tmp_path)
        r = run_ux_acceptance(tmp_path, source_dir="src", profile="react-vite")
        assert r["ran"] is False
        assert r["ok"] is True

    def test_run_uses_funded_container_verifier_not_host(self, tmp_path, monkeypatch):
        from signalos_lib.product.sandbox import CommandOutput

        calls = []

        class _Verifier:
            def run(self, command, cwd, timeout, env):
                calls.append((command, cwd, timeout, env))
                return 0, CommandOutput(stdout="PASS", stderr="")

        _react_repo(tmp_path)
        (tmp_path / "node_modules").mkdir()
        monkeypatch.setattr(
            "signalos_lib.product.validation._select_verifier_runner",
            lambda root: _Verifier(),
        )
        monkeypatch.setattr(
            "signalos_lib.product.acceptance.subprocess.run",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("host subprocess must not run")
            ),
        )

        result = run_ux_acceptance(
            tmp_path, source_dir="src", profile="react-vite"
        )

        assert result["ok"] is True and result["ran"] is True
        assert len(calls) == 1
        command, cwd, timeout, env = calls[0]
        assert command.startswith("npx vitest run ")
        assert "\\" not in command
        assert cwd == tmp_path
        assert timeout == 420
        assert env == {"CI": "1", "FORCE_COLOR": "0"}

    def test_run_propagates_funded_container_outage(self, tmp_path, monkeypatch):
        from signalos_lib.product.sandbox import SandboxUnavailableError

        class _Verifier:
            def run(self, *args, **kwargs):
                raise SandboxUnavailableError("daemon unavailable")

        _react_repo(tmp_path)
        (tmp_path / "node_modules").mkdir()
        monkeypatch.setattr(
            "signalos_lib.product.validation._select_verifier_runner",
            lambda root: _Verifier(),
        )

        with pytest.raises(SandboxUnavailableError, match="daemon unavailable"):
            run_ux_acceptance(tmp_path, source_dir="src", profile="react-vite")

    def test_run_is_na_for_non_browser_profile(self, tmp_path):
        r = run_ux_acceptance(tmp_path, source_dir="src", profile="node-api")
        assert r["ran"] is False
        assert r["ok"] is True
