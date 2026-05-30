# test_product_intent_extraction.py
# Phase P1 — Tests for product intent extraction, questions, and assumptions

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from signalos_lib.product.intent import (
    EMPTY_INTENT,
    extract_product_intent,
    load_intent,
    write_intent,
)
from signalos_lib.product.questions import generate_questions
from signalos_lib.product.assumptions import record_assumptions, write_assumptions


# ---------------------------------------------------------------------------
# Task-management prompt
# ---------------------------------------------------------------------------

class TestTaskManagementPrompt:
    PROMPT = "Build me a task management app with projects, tasks, and team members"

    def test_product_type(self):
        intent = extract_product_intent(self.PROMPT)
        assert intent["product_type"] == "task-management"

    def test_entities_extracted(self):
        intent = extract_product_intent(self.PROMPT)
        entities_lower = [e.lower() for e in intent["entities"]]
        assert any("project" in e for e in entities_lower)
        assert any("task" in e for e in entities_lower)

    def test_team_members_in_target_users(self):
        intent = extract_product_intent(self.PROMPT)
        users_lower = [u.lower() for u in intent["target_users"]]
        assert any("team" in u or "member" in u for u in users_lower)

    def test_product_name_extracted(self):
        intent = extract_product_intent(self.PROMPT)
        # Should extract something related to "task management"
        assert intent["product_name"] != ""


class TestMinimumEnterpriseTaskPrompt:
    PROMPT = (
        "I want to do a task management system to manage my team's tasks, "
        "utilization, workload and their KPIs"
    )

    def test_minimum_prompt_expands_to_enterprise_task_operations(self):
        intent = extract_product_intent(self.PROMPT)
        assert intent["product_type"] == "task-management"
        assert intent["product_name"] == "Team Task Operations"

        entities = {item.lower() for item in intent["entities"]}
        for expected in (
            "task", "project", "team", "teammember",
            "taskassignment", "workloadsnapshot", "kpimetric",
        ):
            assert expected in entities

        workflows = " ".join(intent["primary_workflows"]).lower()
        for expected in ("manage team tasks", "balance workload",
                         "track utilization", "review team kpis"):
            assert expected in workflows

        assert "team managers" in intent["target_users"]
        assert "team members" in intent["target_users"]
        assert "dashboard" in intent["ux_surfaces"]
        assert "chart" in intent["ux_surfaces"]
        assert "report" in intent["ux_surfaces"]
        assert "login" in intent["auth_requirements"]
        assert "rbac" in intent["auth_requirements"]
        assert "audit-trail" in intent["audit_requirements"]
        assert "docker" == intent["deployment_intent"]


# ---------------------------------------------------------------------------
# Financial-dashboard prompt
# ---------------------------------------------------------------------------

class TestFinancialDashboardPrompt:
    PROMPT = (
        "Build me a financial dashboard for recurring revenue, churn, and cash runway"
    )

    def test_product_type(self):
        intent = extract_product_intent(self.PROMPT)
        assert intent["product_type"] == "financial-dashboard"

    def test_dashboard_surface(self):
        intent = extract_product_intent(self.PROMPT)
        assert "dashboard" in intent["ux_surfaces"]

    def test_entities_contain_metrics(self):
        intent = extract_product_intent(self.PROMPT)
        all_text = " ".join(intent["entities"]).lower()
        # At least some of the financial terms should be captured
        assert any(
            term in all_text
            for term in ("revenue", "churn", "runway")
        )


# ---------------------------------------------------------------------------
# Vague prompt
# ---------------------------------------------------------------------------

class TestVaguePrompt:
    PROMPT = "Make me something cool"

    def test_no_crash(self):
        intent = extract_product_intent(self.PROMPT)
        assert isinstance(intent, dict)

    def test_produces_questions(self):
        intent = extract_product_intent(self.PROMPT)
        questions = generate_questions(intent)
        assert len(questions) > 0

    def test_produces_assumptions(self):
        intent = extract_product_intent(self.PROMPT)
        assumptions = record_assumptions(intent)
        assert len(assumptions) > 0

    def test_product_type_is_custom(self):
        intent = extract_product_intent(self.PROMPT)
        assert intent["product_type"] == "custom"


# ---------------------------------------------------------------------------
# Empty prompt
# ---------------------------------------------------------------------------

class TestEmptyPrompt:
    def test_no_crash(self):
        intent = extract_product_intent("")
        assert isinstance(intent, dict)

    def test_valid_structure(self):
        intent = extract_product_intent("")
        for key in EMPTY_INTENT:
            assert key in intent, f"missing key: {key}"

    def test_all_fields_empty(self):
        intent = extract_product_intent("")
        assert intent["product_name"] == ""
        assert intent["product_type"] == ""
        assert intent["entities"] == []
        assert intent["primary_workflows"] == []


# ---------------------------------------------------------------------------
# Adoption context merge
# ---------------------------------------------------------------------------

class TestAdoptionContextMerge:
    PROMPT = "Build a dashboard"

    def test_merge_surfaces_from_repo(self):
        repo_context = {
            "surface_inventory": {
                "project_name": "my-project",
                "detected_profile": "react-vite",
                "surfaces": [
                    {"type": "frontend", "path": "package.json", "evidence": ["react"]},
                    {"type": "tauri", "path": "src-tauri", "evidence": []},
                    {"type": "python", "path": "pyproject.toml", "evidence": []},
                ],
            },
        }
        intent = extract_product_intent(self.PROMPT, repo_context=repo_context)
        assert "web-ui" in intent["ux_surfaces"]
        assert "desktop-app" in intent["ux_surfaces"]
        assert "python" in intent["stack_preferences"]
        assert "react" in intent["stack_preferences"]
        assert "vite" in intent["stack_preferences"]

    def test_merge_product_name_from_repo(self):
        repo_context = {
            "surface_inventory": {
                "project_name": "acme-app",
                "detected_profile": "generic",
                "surfaces": [],
            },
        }
        # Prompt without a clear name; repo_context should fill it
        intent = extract_product_intent("Build something", repo_context=repo_context)
        assert intent["product_name"] == "acme-app"


# ---------------------------------------------------------------------------
# Write / load round-trip
# ---------------------------------------------------------------------------

class TestIntentPersistence:
    def test_round_trip(self):
        intent = extract_product_intent(
            "Build me a task management app with projects and tasks"
        )
        with tempfile.TemporaryDirectory() as tmp:
            signalos_dir = Path(tmp) / ".signalos"
            signalos_dir.mkdir()
            path = write_intent(intent, signalos_dir)
            assert path.exists()
            loaded = load_intent(signalos_dir)
            assert loaded is not None
            assert loaded == intent

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            signalos_dir = Path(tmp) / ".signalos"
            signalos_dir.mkdir()
            assert load_intent(signalos_dir) is None


# ---------------------------------------------------------------------------
# Stack preferences
# ---------------------------------------------------------------------------

class TestStackPreferences:
    def test_react_detected(self):
        intent = extract_product_intent("Build a React app for managing tasks")
        assert "react" in intent["stack_preferences"]

    def test_vite_detected(self):
        intent = extract_product_intent("Use Vite for the build toolchain")
        assert "vite" in intent["stack_preferences"]

    def test_python_fastapi_detected(self):
        intent = extract_product_intent("Backend in Python with FastAPI")
        assert "python" in intent["stack_preferences"]
        assert "fastapi" in intent["stack_preferences"]


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

class TestGenerateQuestions:
    def test_blocking_for_missing_product_name(self):
        intent = extract_product_intent("")
        questions = generate_questions(intent)
        blocking = [q for q in questions if q["blocking"]]
        fields = [q["field"] for q in blocking]
        assert "product_name" in fields

    def test_blocking_for_missing_product_type(self):
        intent = extract_product_intent("")
        questions = generate_questions(intent)
        blocking = [q for q in questions if q["blocking"]]
        fields = [q["field"] for q in blocking]
        assert "product_type" in fields

    def test_no_blocking_when_critical_present(self):
        intent = extract_product_intent(
            "Build me a task management app with projects, tasks, and team members"
        )
        questions = generate_questions(intent)
        blocking = [q for q in questions if q["blocking"]]
        # product_name and product_type should be filled, not blocking
        blocking_fields = [q["field"] for q in blocking]
        assert "product_name" not in blocking_fields
        assert "product_type" not in blocking_fields

    def test_non_blocking_for_optional_fields(self):
        intent = extract_product_intent("")
        questions = generate_questions(intent)
        non_blocking = [q for q in questions if not q["blocking"]]
        assert len(non_blocking) > 0


# ---------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------

class TestRecordAssumptions:
    def test_defaults_for_empty_fields(self):
        intent = extract_product_intent("")
        assumptions = record_assumptions(intent)
        assert len(assumptions) > 0
        fields = [a["field"] for a in assumptions]
        assert "deployment_intent" in fields
        assert "auth_requirements" in fields

    def test_no_assumption_for_filled_fields(self):
        intent = extract_product_intent(
            "Build a React dashboard with login and deploy on Docker"
        )
        assumptions = record_assumptions(intent)
        fields = [a["field"] for a in assumptions]
        # auth_requirements should be filled (login detected), not assumed
        assert "auth_requirements" not in fields
        # deployment_intent should be filled (Docker detected), not assumed
        assert "deployment_intent" not in fields

    def test_write_assumptions(self):
        intent = extract_product_intent("")
        assumptions = record_assumptions(intent)
        with tempfile.TemporaryDirectory() as tmp:
            signalos_dir = Path(tmp) / ".signalos"
            signalos_dir.mkdir()
            path = write_assumptions(assumptions, signalos_dir)
            assert path.exists()
            loaded = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(loaded, list)
            assert len(loaded) == len(assumptions)


# ---------------------------------------------------------------------------
# Medical records prompt — smart entity/role/workflow classification
# ---------------------------------------------------------------------------

class TestMedicalRecordsPrompt:
    PROMPT = (
        "Build a medical records system for patient intake, clinical notes, "
        "lab results, prescriptions, and provider scheduling. "
        "Users include doctors, nurses, and admin staff. "
        "Must be HIPAA compliant with role-based access control and audit trail."
    )

    def test_entities_are_pascal_case_no_spaces(self):
        intent = extract_product_intent(self.PROMPT)
        for entity in intent["entities"]:
            assert " " not in entity, f"Entity has spaces: {entity}"
            assert entity[0].isupper(), f"Entity not PascalCase: {entity}"

    def test_entities_include_domain_nouns(self):
        intent = extract_product_intent(self.PROMPT)
        entities_lower = [e.lower() for e in intent["entities"]]
        assert any("clinicalnote" in e for e in entities_lower)
        assert any("labresult" in e for e in entities_lower)
        assert any("prescription" in e for e in entities_lower)

    def test_roles_in_target_users_not_entities(self):
        intent = extract_product_intent(self.PROMPT)
        users_lower = [u.lower() for u in intent["target_users"]]
        assert any("doctor" in u for u in users_lower)
        assert any("nurse" in u for u in users_lower)
        assert any("admin" in u for u in users_lower)
        # Should NOT appear in entities
        entities_lower = [e.lower() for e in intent["entities"]]
        assert not any("doctor" in e for e in entities_lower)
        assert not any("nurse" in e for e in entities_lower)

    def test_workflows_detected(self):
        intent = extract_product_intent(self.PROMPT)
        workflows_joined = " ".join(intent["primary_workflows"]).lower()
        assert "intake" in workflows_joined
        assert "schedul" in workflows_joined

    def test_ux_surfaces_detected(self):
        intent = extract_product_intent(self.PROMPT)
        surfaces = intent["ux_surfaces"]
        assert "table" in surfaces
        assert "calendar" in surfaces

    def test_hipaa_in_security_constraints(self):
        intent = extract_product_intent(self.PROMPT)
        assert "hipaa" in intent["security_constraints"]

    def test_audit_trail_detected(self):
        intent = extract_product_intent(self.PROMPT)
        assert "audit-trail" in intent["audit_requirements"]

    def test_rbac_in_auth_requirements(self):
        intent = extract_product_intent(self.PROMPT)
        assert "rbac" in intent["auth_requirements"]


# ---------------------------------------------------------------------------
# Project tracking — entity extraction and singularization
# ---------------------------------------------------------------------------

class TestProjectTrackingPrompt:
    PROMPT = "Build a project tracking tool with tasks and milestones"

    def test_entities_extracted_pascal_case(self):
        intent = extract_product_intent(self.PROMPT)
        entities_lower = [e.lower() for e in intent["entities"]]
        assert any("task" in e for e in entities_lower)
        assert any("milestone" in e for e in entities_lower)

    def test_entities_are_singular(self):
        intent = extract_product_intent(self.PROMPT)
        # "tasks" -> "Task", "milestones" -> "Milestone"
        for entity in intent["entities"]:
            assert not entity.endswith("s") or entity.endswith("ss"), (
                f"Entity not singularized: {entity}"
            )


# ---------------------------------------------------------------------------
# Plural singularization
# ---------------------------------------------------------------------------

class TestSingularization:
    def test_prescriptions_singular(self):
        intent = extract_product_intent(
            "Build a system with prescriptions, patients, and categories"
        )
        entities = intent["entities"]
        # Should be "Prescription", not "Prescriptions"
        assert any(e == "Prescription" for e in entities), (
            f"Expected 'Prescription', got {entities}"
        )

    def test_results_singular(self):
        intent = extract_product_intent("Build an app with lab results")
        entities = intent["entities"]
        assert any("Result" in e for e in entities), (
            f"Expected entity containing 'Result', got {entities}"
        )


# ---------------------------------------------------------------------------
# LLM agent fallback tests (questions + assumptions)
# ---------------------------------------------------------------------------

class TestLLMQuestionsFallback:
    def test_question_prompt_sets_deep_domain_analyst_bar(self):
        """Discovery LLM prompt must require deep product-domain analysis."""
        from signalos_lib.product.questions import _ACCOUNT_MANAGER_SYSTEM_PROMPT

        prompt = " ".join(_ACCOUNT_MANAGER_SYSTEM_PROMPT.lower().split())
        assert "highest-level domain analyst ever" in prompt
        assert "greatest product analyst ever" in prompt
        assert "very deep domain knowledge" in prompt
        assert "hands-on operating experience" in prompt
        assert "product domain" in prompt

    def test_generate_questions_tries_llm_first(self, monkeypatch):
        """With SIGNALOS_HARNESS_TEST=1, LLM path is attempted but falls
        back to deterministic because TestProvider returns canned text."""
        monkeypatch.setenv("SIGNALOS_HARNESS_TEST", "1")
        monkeypatch.setenv("SIGNALOS_LLM_PROVIDER", "test")

        intent = extract_product_intent("")
        questions = generate_questions(intent)
        # Fallback produces valid questions
        assert isinstance(questions, list)
        assert len(questions) > 0
        blocking = [q for q in questions if q["blocking"]]
        assert "product_name" in [q["field"] for q in blocking]

    def test_generate_questions_with_llm_returns_none_on_canned(self, monkeypatch):
        """generate_questions_with_llm returns None on non-JSON response."""
        monkeypatch.setenv("SIGNALOS_HARNESS_TEST", "1")

        from signalos_lib.product.questions import generate_questions_with_llm
        result = generate_questions_with_llm(extract_product_intent(""))
        assert result is None


class TestLLMIntentRefinementPrompt:
    def test_refine_prompt_sets_deep_domain_analyst_bar(self):
        """Intent refinement LLM prompt must require hands-on domain analysis."""
        from signalos_lib.product.intent import _REFINE_PROMPT

        prompt = " ".join(_REFINE_PROMPT.lower().split())
        assert "highest-level domain analyst ever" in prompt
        assert "greatest product analyst ever" in prompt
        assert "very deep domain knowledge" in prompt
        assert "hands-on operating experience" in prompt
        assert "failure modes" in prompt


class TestLLMAssumptionsFallback:
    def test_assumptions_prompt_sets_deep_domain_analyst_bar(self):
        """Assumption LLM prompt must require domain-specific expertise."""
        from signalos_lib.product.assumptions import _ASSUMPTIONS_SYSTEM_PROMPT

        prompt = " ".join(_ASSUMPTIONS_SYSTEM_PROMPT.lower().split())
        assert "highest-level domain analyst ever" in prompt
        assert "greatest product analyst ever" in prompt
        assert "very deep domain knowledge" in prompt
        assert "hands-on operating experience" in prompt
        assert "failure modes" in prompt

    def test_record_assumptions_tries_llm_first(self, monkeypatch):
        """With SIGNALOS_HARNESS_TEST=1, LLM path is attempted but falls
        back to deterministic because TestProvider returns canned text."""
        monkeypatch.setenv("SIGNALOS_HARNESS_TEST", "1")
        monkeypatch.setenv("SIGNALOS_LLM_PROVIDER", "test")

        intent = extract_product_intent("")
        assumptions = record_assumptions(intent)
        # Fallback produces valid assumptions
        assert isinstance(assumptions, list)
        assert len(assumptions) > 0
        fields = [a["field"] for a in assumptions]
        assert "deployment_intent" in fields

    def test_record_assumptions_with_llm_returns_none_on_canned(self, monkeypatch):
        """record_assumptions_with_llm returns None on non-JSON response."""
        monkeypatch.setenv("SIGNALOS_HARNESS_TEST", "1")

        from signalos_lib.product.assumptions import record_assumptions_with_llm
        result = record_assumptions_with_llm(extract_product_intent(""))
        assert result is None
