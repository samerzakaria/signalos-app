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
        assert any("team" in e or "member" in e for e in entities_lower)

    def test_product_name_extracted(self):
        intent = extract_product_intent(self.PROMPT)
        # Should extract something related to "task management"
        assert intent["product_name"] != ""


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
