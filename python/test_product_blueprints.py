"""Tests for the P2 Blueprint Registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signalos_lib.product.blueprints.registry import (
    _BLUEPRINTS_DIR,
    apply_blueprint_intent_defaults,
    load_blueprint,
    load_registry,
    list_blueprints,
    match_blueprint,
    validate_blueprint,
)


# ---------------------------------------------------------------------------
# load_registry
# ---------------------------------------------------------------------------

class TestLoadRegistry:
    def test_returns_both_blueprints(self):
        reg = load_registry()
        ids = [e["id"] for e in reg["blueprints"]]
        assert "task-management" in ids
        assert "financial-dashboard" in ids

    def test_has_schema_version(self):
        reg = load_registry()
        assert reg["schema_version"] == 1


# ---------------------------------------------------------------------------
# load_blueprint
# ---------------------------------------------------------------------------

class TestLoadBlueprint:
    def test_task_management_loads(self):
        bp = load_blueprint("task-management")
        assert bp is not None
        assert bp["id"] == "task-management"
        assert bp["display_name"] == "Task Management"

    def test_task_management_has_sub_files(self):
        bp = load_blueprint("task-management")
        assert bp is not None
        for sub in ("api_detail", "ui_detail", "tests_detail",
                     "seed_detail", "acceptance_detail"):
            assert sub in bp, f"missing merged sub-file: {sub}"

    def test_task_management_covers_workload_utilization_and_kpis(self):
        bp = load_blueprint("task-management")
        assert bp is not None
        text = json.dumps(bp, sort_keys=True).lower()
        for expected in (
            "workloadsnapshot",
            "kpimetric",
            "balance_workload",
            "track_utilization",
            "review_kpis",
            "workload-dashboard",
            "kpi-dashboard",
            "/workload",
            "/kpis",
            "tenant-isolation",
            "audit-trail",
        ):
            assert expected in text

    def test_task_management_intent_defaults_are_blueprint_data(self):
        bp = load_blueprint("task-management")
        assert bp is not None
        intent = {
            "product_name": "",
            "product_type": "task-management",
            "target_users": [],
            "primary_workflows": [],
            "entities": [],
            "entity_relationships": [],
            "ux_surfaces": [],
            "api_surfaces": [],
            "data_sources": [],
            "integrations": [],
            "auth_requirements": [],
            "permissions": [],
            "audit_requirements": [],
            "security_constraints": [],
            "performance_expectations": [],
            "deployment_intent": "none",
            "stack_preferences": [],
            "unknowns": [],
            "assumptions": [],
            "out_of_scope": [],
        }
        enriched = apply_blueprint_intent_defaults(intent, bp)
        assert enriched["product_name"] == "Team Task Operations"
        assert "WorkloadSnapshot" in enriched["entities"]
        assert "KpiMetric" in enriched["entities"]
        assert "rbac" in enriched["auth_requirements"]
        assert enriched["deployment_intent"] == "docker"

    def test_financial_dashboard_loads(self):
        bp = load_blueprint("financial-dashboard")
        assert bp is not None
        assert bp["id"] == "financial-dashboard"
        assert bp["display_name"] == "Financial Dashboard"

    def test_financial_dashboard_has_sub_files(self):
        bp = load_blueprint("financial-dashboard")
        assert bp is not None
        for sub in ("api_detail", "ui_detail", "tests_detail",
                     "seed_detail", "acceptance_detail"):
            assert sub in bp, f"missing merged sub-file: {sub}"

    def test_nonexistent_returns_none(self):
        bp = load_blueprint("nonexistent")
        assert bp is None


# ---------------------------------------------------------------------------
# list_blueprints
# ---------------------------------------------------------------------------

class TestListBlueprints:
    def test_returns_both_entries(self):
        entries = list_blueprints()
        ids = [e["id"] for e in entries]
        assert "task-management" in ids
        assert "financial-dashboard" in ids

    def test_entries_have_display_name(self):
        entries = list_blueprints()
        for entry in entries:
            assert "display_name" in entry
            assert isinstance(entry["display_name"], str)
            assert len(entry["display_name"]) > 0


# ---------------------------------------------------------------------------
# match_blueprint
# ---------------------------------------------------------------------------

class TestMatchBlueprint:
    def test_match_task_management_by_type(self):
        intent = {"product_type": "task-management", "entities": []}
        assert match_blueprint(intent) == "task-management"

    def test_match_financial_dashboard_by_type(self):
        intent = {"product_type": "financial-dashboard", "entities": []}
        assert match_blueprint(intent) == "financial-dashboard"

    def test_match_task_management_by_entities(self):
        intent = {"product_type": "custom", "entities": ["task", "project"]}
        assert match_blueprint(intent) == "task-management"

    def test_match_financial_dashboard_by_entities(self):
        intent = {"product_type": "custom", "entities": ["revenue", "churn"]}
        assert match_blueprint(intent) == "financial-dashboard"

    def test_match_by_keywords(self):
        intent = {
            "product_type": "custom",
            "entities": [],
            "primary_workflows": ["kanban board"],
        }
        assert match_blueprint(intent) == "task-management"

    def test_match_financial_by_keywords(self):
        intent = {
            "product_type": "custom",
            "entities": [],
            "primary_workflows": ["revenue dashboard"],
        }
        assert match_blueprint(intent) == "financial-dashboard"

    def test_no_match_returns_none(self):
        intent = {
            "product_type": "custom",
            "entities": ["spaceship", "asteroid"],
            "primary_workflows": [],
        }
        assert match_blueprint(intent) is None


# ---------------------------------------------------------------------------
# validate_blueprint
# ---------------------------------------------------------------------------

class TestValidateBlueprint:
    def test_valid_task_management(self):
        bp = load_blueprint("task-management")
        assert bp is not None
        errors = validate_blueprint(bp)
        assert errors == [], f"unexpected errors: {errors}"

    def test_valid_financial_dashboard(self):
        bp = load_blueprint("financial-dashboard")
        assert bp is not None
        errors = validate_blueprint(bp)
        assert errors == [], f"unexpected errors: {errors}"

    def test_missing_required_fields(self):
        bp = {"id": "broken"}
        errors = validate_blueprint(bp)
        assert len(errors) > 0
        # Should mention at least one missing field
        assert any("missing required field" in e for e in errors)

    def test_invalid_entities_format(self):
        bp = load_blueprint("task-management")
        assert bp is not None
        bad = {**bp, "entities": "not-a-list"}
        errors = validate_blueprint(bad)
        assert any("entities must be a list" in e for e in errors)

    def test_invalid_profile_support(self):
        bp = load_blueprint("task-management")
        assert bp is not None
        bad = {**bp, "profile_support": ["nonexistent-profile"]}
        errors = validate_blueprint(bad)
        assert any("invalid profile_support" in e for e in errors)

    def test_invalid_intent_defaults_rejected(self):
        bp = load_blueprint("task-management")
        assert bp is not None
        bad = {**bp, "intent_defaults": {"entities": "Task"}}
        errors = validate_blueprint(bad)
        assert any("intent_defaults.entities must be a list" in e for e in errors)


# ---------------------------------------------------------------------------
# Cross-contamination checks
# ---------------------------------------------------------------------------

class TestNoCrossContamination:
    """Verify blueprint content belongs to its own domain, not the other."""

    def _read_all_json(self, blueprint_dir: str) -> str:
        """Read all JSON files in a blueprint directory and concatenate."""
        bp_dir = _BLUEPRINTS_DIR / blueprint_dir
        contents: list[str] = []
        for json_file in bp_dir.glob("*.json"):
            contents.append(json_file.read_text(encoding="utf-8"))
        return "\n".join(contents)

    def test_task_management_has_no_financial_content(self):
        text = self._read_all_json("task-management")
        # These terms should only appear in the financial-dashboard blueprint
        for term in ("Revenue", "Churn", "CashRunway", "Subscription",
                     "runway_months", "burn_rate", "mrr_lost"):
            assert term not in text, (
                f"task-management blueprint contains financial term: {term}"
            )

    def test_financial_dashboard_has_no_task_content(self):
        text = self._read_all_json("financial-dashboard")
        # These terms should only appear in the task-management blueprint
        for term in ("assignee", "due_date", "project_id", "kanban",
                     "assign_task", "complete_task"):
            assert term not in text, (
                f"financial-dashboard blueprint contains task term: {term}"
            )

    def test_task_enterprise_defaults_not_hardcoded_in_core_modules(self):
        repo_root = Path(__file__).resolve().parents[1]
        core_files = [
            repo_root / "python" / "signalos_lib" / "product" / "intent.py",
            repo_root / "python" / "signalos_lib" / "product" / "ownership.py",
            repo_root / "python" / "signalos_lib" / "product" / "delivery.py",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in core_files)
        for term in (
            "Team Task Operations",
            "WorkloadSnapshot",
            "KpiMetric",
            "balance workload",
            "track utilization by team member",
            "team managers",
        ):
            assert term not in text, f"{term!r} belongs in blueprint data"


# ---------------------------------------------------------------------------
# Profile support validation
# ---------------------------------------------------------------------------

class TestProfileSupport:
    """Both blueprints must list only valid profile_support values."""

    _VALID = {"react-vite", "generic", "existing-repo"}

    def test_task_management_profiles_valid(self):
        bp = load_blueprint("task-management")
        assert bp is not None
        for ps in bp["profile_support"]:
            assert ps in self._VALID, f"invalid profile: {ps}"

    def test_financial_dashboard_profiles_valid(self):
        bp = load_blueprint("financial-dashboard")
        assert bp is not None
        for ps in bp["profile_support"]:
            assert ps in self._VALID, f"invalid profile: {ps}"
