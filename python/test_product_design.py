"""Tests for the product design phase (UX library / token selection)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from signalos_lib.product.design import (
    build_design_system,
    get_design_dependencies,
    get_design_instructions,
    load_design,
    scaffold_design_system,
    write_design,
)


# ---------------------------------------------------------------------------
# Intent factories
# ---------------------------------------------------------------------------

def _financial_dashboard_intent() -> dict:
    return {
        "product_name": "FinDash",
        "product_type": "financial-dashboard",
        "entities": ["Revenue", "Churn"],
        "ux_surfaces": ["chart", "dashboard"],
        "api_surfaces": ["rest-api"],
        "data_sources": ["database"],
        "primary_workflows": [],
    }


def _medical_records_intent() -> dict:
    return {
        "product_name": "MedRecords",
        "product_type": "custom",
        "entities": ["Patient", "ClinicalNote", "Prescription", "LabResult", "Appointment"],
        "ux_surfaces": ["form", "table", "detail"],
        "api_surfaces": ["rest-api"],
        "data_sources": ["database"],
        "primary_workflows": ["record patient", "schedule appointment"],
    }


def _simple_task_intent() -> dict:
    return {
        "product_name": "SimpleTasks",
        "product_type": "task-management",
        "entities": ["Task", "Project"],
        "ux_surfaces": ["list", "kanban"],
        "api_surfaces": [],
        "data_sources": [],
        "primary_workflows": ["create task"],
    }


def _calendar_intent() -> dict:
    return {
        "product_name": "ScheduleApp",
        "product_type": "custom",
        "entities": ["Event", "Attendee"],
        "ux_surfaces": ["calendar", "form"],
        "api_surfaces": [],
        "data_sources": [],
        "primary_workflows": ["schedule event"],
    }


def _minimal_intent() -> dict:
    return {
        "product_name": "Mini",
        "product_type": "custom",
        "entities": ["Item"],
        "ux_surfaces": [],
        "api_surfaces": [],
        "data_sources": [],
        "primary_workflows": [],
    }


# ---------------------------------------------------------------------------
# UI Library selection
# ---------------------------------------------------------------------------

class TestUILibrarySelection:
    def test_financial_dashboard_selects_shadcn(self):
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        assert design["ui_library"]["name"] == "shadcn/ui"

    def test_medical_records_selects_mantine(self):
        design = build_design_system(_medical_records_intent(), "react-vite")
        assert design["ui_library"]["name"] == "@mantine/core"

    def test_simple_task_few_entities_selects_shadcn(self):
        design = build_design_system(_simple_task_intent(), "react-vite")
        assert design["ui_library"]["name"] == "shadcn/ui"

    def test_calendar_surface_triggers_mantine(self):
        design = build_design_system(_calendar_intent(), "react-vite")
        assert design["ui_library"]["name"] == "@mantine/core"

    def test_many_entities_triggers_mantine(self):
        intent = _minimal_intent()
        intent["entities"] = ["A", "B", "C", "D"]  # >= 4
        design = build_design_system(intent, "react-vite")
        assert design["ui_library"]["name"] == "@mantine/core"

    def test_form_surface_triggers_mantine(self):
        intent = _minimal_intent()
        intent["ux_surfaces"] = ["form"]
        design = build_design_system(intent, "react-vite")
        assert design["ui_library"]["name"] == "@mantine/core"

    def test_table_surface_triggers_mantine(self):
        intent = _minimal_intent()
        intent["ux_surfaces"] = ["table"]
        design = build_design_system(intent, "react-vite")
        assert design["ui_library"]["name"] == "@mantine/core"


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

class TestStateManagement:
    def test_always_zustand(self):
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        assert design["state_management"]["name"] == "zustand"

    def test_zustand_for_simple_intent(self):
        design = build_design_system(_minimal_intent(), "react-vite")
        assert design["state_management"]["name"] == "zustand"


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

class TestDataLayer:
    def test_api_surfaces_trigger_react_query(self):
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        assert design["data_layer"]["name"] == "@tanstack/react-query"

    def test_no_api_surfaces_uses_local(self):
        design = build_design_system(_minimal_intent(), "react-vite")
        assert design["data_layer"]["name"] == "local"


# ---------------------------------------------------------------------------
# Form handling
# ---------------------------------------------------------------------------

class TestFormHandling:
    def test_many_entities_trigger_react_hook_form(self):
        design = build_design_system(_medical_records_intent(), "react-vite")
        assert design["form_handling"]["name"] == "react-hook-form"

    def test_few_entities_use_native(self):
        design = build_design_system(_simple_task_intent(), "react-vite")
        assert design["form_handling"]["name"] == "native"


# ---------------------------------------------------------------------------
# Color scheme
# ---------------------------------------------------------------------------

class TestColorScheme:
    def test_medical_product_gets_green(self):
        design = build_design_system(_medical_records_intent(), "react-vite")
        assert design["design_tokens"]["primary_color"] == "#059669"

    def test_financial_product_gets_blue(self):
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        assert design["design_tokens"]["primary_color"] == "#2563eb"

    def test_task_management_gets_violet(self):
        design = build_design_system(_simple_task_intent(), "react-vite")
        assert design["design_tokens"]["primary_color"] == "#7c3aed"


# ---------------------------------------------------------------------------
# Dependency aggregation
# ---------------------------------------------------------------------------

class TestGetDesignDependencies:
    def test_mantine_deps(self):
        design = build_design_system(_medical_records_intent(), "react-vite")
        deps = get_design_dependencies(design)
        assert "@mantine/core" in deps
        assert "@mantine/hooks" in deps
        assert "@mantine/form" in deps
        assert "@mantine/dates" in deps
        assert "@tabler/icons-react" in deps
        assert "dayjs" in deps
        # Also zustand
        assert "zustand" in deps
        # react-hook-form for many entities
        assert "react-hook-form" in deps
        assert "zod" in deps
        # react-query for API surfaces
        assert "@tanstack/react-query" in deps

    def test_shadcn_deps(self):
        design = build_design_system(_simple_task_intent(), "react-vite")
        deps = get_design_dependencies(design)
        assert "tailwindcss" in deps
        assert "class-variance-authority" in deps
        assert "clsx" in deps
        assert "tailwind-merge" in deps
        assert "lucide-react" in deps
        assert "zustand" in deps
        # No react-hook-form (2 entities < 3)
        assert "react-hook-form" not in deps

    def test_shadcn_dashboard_includes_recharts(self):
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        deps = get_design_dependencies(design)
        assert "recharts" in deps


# ---------------------------------------------------------------------------
# Write / load round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_design_round_trips(self, tmp_path):
        signalos_dir = tmp_path / ".signalos"
        signalos_dir.mkdir(parents=True)

        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        write_design(design, signalos_dir)

        loaded = load_design(signalos_dir)
        assert loaded is not None
        assert loaded["schema_version"] == "signalos.design_system.v1"
        assert loaded["ui_library"]["name"] == "shadcn/ui"

    def test_load_nonexistent_returns_none(self, tmp_path):
        signalos_dir = tmp_path / ".signalos"
        signalos_dir.mkdir(parents=True)
        assert load_design(signalos_dir) is None


# ---------------------------------------------------------------------------
# Design instructions (replaces scaffold_design_system file writing)
# ---------------------------------------------------------------------------

class TestDesignInstructions:
    def test_returns_design_system_files(self):
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        instructions = get_design_instructions(design)

        assert "src/ui/theme.ts" in instructions["design_system_files"]
        assert "src/ui/index.ts" in instructions["design_system_files"]
        assert "src/ui/layouts/AppLayout.tsx" in instructions["design_system_files"]
        assert "src/ui/layouts/PageLayout.tsx" in instructions["design_system_files"]

    def test_theme_file_has_tokens(self):
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        instructions = get_design_instructions(design)

        theme_spec = instructions["design_system_files"]["src/ui/theme.ts"]
        assert "tokens" in theme_spec
        assert theme_spec["tokens"]["primary_color"] == "#2563eb"

    def test_conventions_present(self):
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        instructions = get_design_instructions(design)

        assert len(instructions["conventions"]) >= 3
        assert any("src/ui" in c for c in instructions["conventions"])

    def test_empty_for_non_ui_profile(self):
        design = build_design_system(_minimal_intent(), "generic")
        instructions = get_design_instructions(design)

        assert instructions["design_system_files"] == {}
        assert instructions["conventions"] == []


class TestScaffoldDesignSystem:
    def test_returns_expected_paths(self, tmp_path):
        """scaffold_design_system returns paths but does NOT write files."""
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        paths = scaffold_design_system(tmp_path, design)

        assert "src/ui/theme.ts" in paths
        assert "src/ui/index.ts" in paths
        assert "src/ui/layouts/AppLayout.tsx" in paths
        assert "src/ui/layouts/PageLayout.tsx" in paths

    def test_does_not_write_files(self, tmp_path):
        """scaffold_design_system does NOT create files on disk."""
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        scaffold_design_system(tmp_path, design)

        # No files should exist
        assert not (tmp_path / "src" / "ui" / "theme.ts").is_file()
        assert not (tmp_path / "src" / "ui" / "index.ts").is_file()
        assert not (tmp_path / "src" / "ui" / "layouts" / "AppLayout.tsx").is_file()

    def test_no_scaffold_for_empty_ui(self, tmp_path):
        """Non-UI profile produces no scaffold paths."""
        design = build_design_system(_minimal_intent(), "generic")
        paths = scaffold_design_system(tmp_path, design)
        assert paths == []


# ---------------------------------------------------------------------------
# Consistency rules
# ---------------------------------------------------------------------------

class TestConsistencyRules:
    def test_rules_present_in_output(self):
        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        rules = design.get("consistency_rules", [])
        assert len(rules) >= 3
        assert any("src/ui" in r for r in rules)
        assert any("inline styles" in r.lower() or "design tokens" in r.lower() for r in rules)


# ---------------------------------------------------------------------------
# Generic profile
# ---------------------------------------------------------------------------

class TestGenericProfile:
    def test_generic_design_is_minimal(self):
        design = build_design_system(_minimal_intent(), "generic")
        assert design["ui_library"]["name"] == ""
        assert design["state_management"]["name"] == ""
        assert design["data_layer"]["name"] == ""
        assert design["form_handling"]["name"] == ""
        assert design["additional_deps"] == {}
        assert design["consistency_rules"] == []

    def test_generic_deps_empty(self):
        design = build_design_system(_minimal_intent(), "generic")
        deps = get_design_dependencies(design)
        assert deps == {}
