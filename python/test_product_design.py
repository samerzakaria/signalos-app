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

    def test_node_api_gets_non_ui_design_stub(self):
        design = build_design_system(_financial_dashboard_intent(), "node-api")
        assert design["ui_library"]["name"] == ""
        assert get_design_dependencies(design) == {}

    def test_agent_selected_does_not_emit_react_dependencies(self):
        intent = _financial_dashboard_intent()
        intent["stack_preferences"] = ["angular"]
        design = build_design_system(intent, "agent-selected")
        assert design["ui_library"]["name"] == ""
        assert "Agent-selected" in design["ui_library"]["reason"]
        assert get_design_dependencies(design) == {}


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
        assert "@mantine/charts" in deps
        mantine_versions = {
            name: version
            for name, version in deps.items()
            if name.startswith("@mantine/")
        }
        assert len(set(mantine_versions.values())) == 1
        assert not next(iter(mantine_versions.values())).startswith("^")
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


# ---------------------------------------------------------------------------
# LLM architect agent (with TestProvider fallback)
# ---------------------------------------------------------------------------

class TestLLMDesignSelection:
    def test_architect_prompt_sets_world_class_ux_bar(self):
        """Design LLM prompt must hold the highest UI/UX quality bar."""
        from signalos_lib.product.design import _ARCHITECT_SYSTEM_PROMPT

        prompt = _ARCHITECT_SYSTEM_PROMPT.lower()
        normalized = " ".join(prompt.split())
        assert "highest-level ui/ux designer ever" in normalized
        assert "best ui/ux designer in the world" in normalized
        assert "world-class frontend architect" in normalized
        assert "empty/loading/error states" in normalized

    def test_build_design_system_tries_llm_first(self, monkeypatch):
        """With SIGNALOS_HARNESS_TEST=1, LLM path is attempted but falls
        back to deterministic because TestProvider returns canned text."""
        monkeypatch.setenv("SIGNALOS_HARNESS_TEST", "1")
        monkeypatch.setenv("SIGNALOS_LLM_PROVIDER", "test")

        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        # Fallback should still produce a valid result
        assert design["schema_version"] == "signalos.design_system.v1"
        assert design["ui_library"]["name"] in ("shadcn/ui", "@mantine/core")
        assert design["state_management"]["name"] != ""

    def test_select_design_with_llm_returns_none_on_empty_response(self, monkeypatch):
        """select_design_with_llm returns None when provider returns empty."""
        monkeypatch.setenv("SIGNALOS_HARNESS_TEST", "1")

        from signalos_lib.product.design import select_design_with_llm
        result = select_design_with_llm(_financial_dashboard_intent(), "react-vite")
        # TestProvider returns canned text, not valid JSON -> None
        assert result is None

    def test_deterministic_fallback_unchanged(self):
        """Deterministic fallback works exactly as before without env vars."""
        import os
        # Ensure no LLM env vars
        for key in ("ANTHROPIC_API_KEY", "SIGNALOS_LLM_PROVIDER", "SIGNALOS_HARNESS_TEST"):
            os.environ.pop(key, None)

        design = build_design_system(_financial_dashboard_intent(), "react-vite")
        assert design["ui_library"]["name"] == "shadcn/ui"
        assert design["state_management"]["name"] == "zustand"


# ---------------------------------------------------------------------------
# #44: UI-library adapter registry -- the single, extensible source of truth.
# Registering ONE adapter must wire it across validator + deps + LLM prompt +
# heuristic + import allowlist, replacing the old hardcoded pair-in-5-places.
# ---------------------------------------------------------------------------

class TestUILibraryRegistry:
    def test_registry_accessors(self):
        from signalos_lib.product import design as d
        names = d.supported_ui_library_names()
        assert "@mantine/core" in names
        assert "shadcn/ui" in names
        assert d.get_ui_library("@mantine/core").id == "mantine"
        assert d.get_ui_library("mantine").name == "@mantine/core"   # by id too
        # #10: MUI + Chakra are first-class registry entries now
        assert "@mui/material" in names
        assert "@chakra-ui/react" in names
        assert d.get_ui_library("mui").name == "@mui/material"
        assert d.get_ui_library("chakra").name == "@chakra-ui/react"
        assert d.get_ui_library("antd") is None                      # unsupported

    def test_validator_uses_registry(self):
        from signalos_lib.product import design as d
        # a name in the registry parses; one outside is rejected
        good = json.dumps({
            "ui_library": {"name": "@mantine/core", "version": "7", "reason": "x"},
            "design_tokens": {"primary_color": "#111", "font_family": "Inter"},
            "state_management": {"name": "zustand"},
            "data_layer": {"name": "local"},
            "form_handling": {"name": "native"},
        })
        bad = good.replace("@mantine/core", "antd")
        assert d._parse_design_response(good) is not None
        assert d._parse_design_response(bad) is None

    def test_deps_and_prompt_come_from_registry(self):
        from signalos_lib.product import design as d
        for lib in d.ui_library_registry():
            deps = d.get_design_dependencies({"ui_library": {"name": lib.name}})
            # every dependency the adapter declares is installed
            for pkg in lib.dependencies:
                assert pkg in deps, (lib.name, pkg)
            # the adapter is offered in the LLM prompt
            assert lib.name in d._ARCHITECT_SYSTEM_PROMPT

    def test_new_adapter_flows_everywhere(self, monkeypatch):
        # The extensibility contract: append ONE adapter -> it is supported,
        # validated, and its deps resolve, with NO other code change.
        from signalos_lib.product import design as d
        fake = d.UILibraryAdapter(
            id="antd",
            name="antd",
            version="^5.18.0",
            prompt_desc="enterprise-grade Ant Design components",
            dependencies={"antd": "^5.18.0", "@ant-design/icons": "^5.3.0"},
            import_packages=("antd", "@ant-design/icons"),
        )
        monkeypatch.setattr(
            d, "_UI_LIBRARY_REGISTRY", d._UI_LIBRARY_REGISTRY + (fake,)
        )
        assert "antd" in d.supported_ui_library_names()
        deps = d.get_design_dependencies({"ui_library": {"name": "antd"}})
        assert "antd" in deps and "@ant-design/icons" in deps
        # and the import allowlist (agent_dispatch) honors it too
        from signalos_lib.product.agent_dispatch import _import_allowlist_lines
        lines = _import_allowlist_lines(
            "src/components/Foo.tsx", "source",
            {"profile": "react-vite", "design_constraints": {"ui_library": "antd"}},
            [],
        )
        blob = "\n".join(lines)
        assert "antd" in blob and "@ant-design/icons" in blob

    def test_heuristic_precedence_preserved(self):
        from signalos_lib.product.design import _select_ui_library
        dash = _select_ui_library(
            {"product_type": "financial-dashboard", "ux_surfaces": ["chart"]}, None)
        forms = _select_ui_library(
            {"entities": ["A", "B", "C", "D"], "ux_surfaces": ["form"]}, None)
        default = _select_ui_library({"entities": ["A"], "ux_surfaces": []}, None)
        assert dash["name"] == "shadcn/ui"
        assert forms["name"] == "@mantine/core"
        assert default["name"] == "shadcn/ui"


# ---------------------------------------------------------------------------
# #44 -- founder-DECLARED design system ("A proposes, B signs")
# ---------------------------------------------------------------------------

class TestFounderDeclaredUILibrary:
    def test_declared_library_is_honored_verbatim(self):
        intent = _minimal_intent()
        intent["declared_ui_library"] = "@mantine/core"
        design = build_design_system(intent, "react-vite")
        assert design["ui_library"]["name"] == "@mantine/core"
        assert "Founder-declared" in design["ui_library"]["reason"]

    def test_declaration_OVERRIDES_the_heuristic(self):
        # A financial dashboard heuristically picks shadcn; a founder who declares
        # Mantine must get Mantine -- the agent does not overrule a signed choice.
        intent = _financial_dashboard_intent()
        assert build_design_system(intent, "react-vite")["ui_library"]["name"] == "shadcn/ui"
        intent["declared_ui_library"] = "@mantine/core"
        assert build_design_system(intent, "react-vite")["ui_library"]["name"] == "@mantine/core"

    def test_declaration_accepts_short_id(self):
        intent = _minimal_intent()
        intent["declared_ui_library"] = "shadcn"  # short id, not the full name
        design = build_design_system(intent, "react-vite")
        assert design["ui_library"]["name"] == "shadcn/ui"

    def test_unsupported_declaration_is_rejected_never_guessed(self):
        intent = _minimal_intent()
        intent["declared_ui_library"] = "antd"  # not in the registry
        with pytest.raises(ValueError) as exc:
            build_design_system(intent, "react-vite")
        # the error names the supported set so the founder can fix the declaration
        assert "antd" in str(exc.value)
        assert "shadcn/ui" in str(exc.value)

    def test_no_declaration_falls_back_to_proposal(self):
        intent = _minimal_intent()
        intent["declared_ui_library"] = ""
        design = build_design_system(intent, "react-vite")
        # a real library is still chosen (the "A proposes" path), just not forced
        assert design["ui_library"]["name"] in {"shadcn/ui", "@mantine/core"}
        assert "Founder-declared" not in design["ui_library"]["reason"]


# ---------------------------------------------------------------------------
# #8 -- freed design tokens: validated ranges, intent-derived fallback, and
# the end-to-end flow into the deterministically rendered theme + stylesheet.
# ---------------------------------------------------------------------------

def _token_response(**token_overrides) -> str:
    tokens = {
        "color_scheme": "dark",
        "primary_color": "#0f766e",
        "border_radius": "12px",
        "font_family": "JetBrains Mono, monospace",
        "spacing_unit": 4,
        "type_scale": "compact",
    }
    tokens.update(token_overrides)
    return json.dumps({
        "ui_library": {"name": "shadcn/ui", "version": "latest", "reason": "x"},
        "design_tokens": tokens,
        "state_management": {"name": "zustand"},
        "data_layer": {"name": "local"},
        "form_handling": {"name": "native"},
    })


class TestFreedDesignTokens:
    def test_prompt_offers_the_freed_token_ranges(self):
        from signalos_lib.product.design import _ARCHITECT_SYSTEM_PROMPT
        p = _ARCHITECT_SYSTEM_PROMPT
        assert '"dark"' in p and '"light"' in p
        for radius in ("0px", "4px", "8px", "12px", "16px", "9999px"):
            assert f'"{radius}"' in p
        assert "4 or 8" in p
        assert "compact" in p and "spacious" in p
        # the hardcoded light-only template is gone
        assert '"color_scheme": "light"' not in p
        assert '"border_radius": "8px"' not in p

    def test_validator_accepts_full_valid_token_set(self):
        from signalos_lib.product.design import _parse_design_response
        design = _parse_design_response(_token_response())
        assert design is not None
        tokens = design["design_tokens"]
        assert tokens["color_scheme"] == "dark"
        assert tokens["border_radius"] == "12px"
        assert tokens["spacing_unit"] == 4
        assert tokens["type_scale"] == "compact"

    @pytest.mark.parametrize("override", [
        {"color_scheme": "midnight"},
        {"border_radius": "37px"},
        {"spacing_unit": 13},
        {"spacing_unit": "lots"},
        {"type_scale": "gigantic"},
        {"primary_color": "not-a-hex"},
        {"primary_color": "#12345"},
    ])
    def test_validator_rejects_out_of_range_tokens(self, override):
        from signalos_lib.product.design import _parse_design_response
        assert _parse_design_response(_token_response(**override)) is None

    def test_validator_fills_missing_tokens_with_defaults(self):
        # older/lean provider responses (no scheme/radius/spacing) still parse
        from signalos_lib.product.design import _parse_design_response
        lean = json.dumps({
            "ui_library": {"name": "@mantine/core", "version": "7", "reason": "x"},
            "design_tokens": {"primary_color": "#111", "font_family": "Inter"},
            "state_management": {"name": "zustand"},
            "data_layer": {"name": "local"},
            "form_handling": {"name": "native"},
        })
        design = _parse_design_response(lean)
        assert design is not None
        tokens = design["design_tokens"]
        assert tokens["color_scheme"] == "light"
        assert tokens["border_radius"] == "8px"
        assert tokens["spacing_unit"] == 8
        assert tokens["type_scale"] == "regular"

    def test_dev_tool_intent_derives_dark_mono_tokens(self):
        intent = {
            "product_name": "LogLens",
            "product_type": "custom",
            "entities": ["LogEntry"],
            "target_users": ["developers"],
            "ux_surfaces": [],
            "api_surfaces": [],
            "data_sources": [],
            "primary_workflows": ["monitoring deployments"],
        }
        tokens = build_design_system(intent, "react-vite")["design_tokens"]
        assert tokens["color_scheme"] == "dark"
        assert "JetBrains Mono" in tokens["font_family"]
        assert tokens["border_radius"] == "4px"

    def test_playful_intent_derives_rounder_tokens(self):
        intent = {
            "product_name": "PartyPlanner",
            "product_type": "custom",
            "entities": ["Party", "Game"],
            "ux_surfaces": [],
            "api_surfaces": [],
            "data_sources": [],
            "primary_workflows": [],
        }
        tokens = build_design_system(intent, "react-vite")["design_tokens"]
        assert tokens["color_scheme"] == "light"
        assert tokens["border_radius"] == "16px"
        assert tokens["type_scale"] == "spacious"

    def test_default_intent_keeps_prior_light_defaults(self):
        tokens = build_design_system(_minimal_intent(), "react-vite")["design_tokens"]
        assert tokens["color_scheme"] == "light"
        assert tokens["border_radius"] == "8px"
        assert tokens["spacing_unit"] == 8
        assert "Inter" in tokens["font_family"]

    @pytest.mark.parametrize("ui_name", ["shadcn/ui", "@mantine/core"])
    def test_dark_scheme_flows_into_rendered_theme_and_css(self, ui_name):
        # The CRITICAL end-to-end: a dark design must reach the
        # deterministically rendered theme.ts + product.css for BOTH the
        # shadcn and Mantine paths (they share the foundation renderers).
        from signalos_lib.product.agent_dispatch import (
            _render_product_css,
            _render_theme,
        )
        dc = {
            "ui_library": ui_name,
            "design_tokens": {
                "color_scheme": "dark",
                "primary_color": "#0f766e",
                "border_radius": "12px",
                "font_family": "JetBrains Mono, monospace",
                "spacing_unit": 4,
                "type_scale": "compact",
            },
        }
        theme = _render_theme(dc)
        assert "colorScheme: \"dark\"" in theme
        assert '"#0f766e"' in theme
        assert '"12px"' in theme
        # dark palette, not the light hardcodes
        assert '"#111827"' in theme      # dark background
        assert "'#ffffff'" not in theme and '"#ffffff"' not in theme
        # spacing scale derived from the 4px unit
        assert 'sm: "4px"' in theme and 'md: "8px"' in theme

        css = _render_product_css(dc)
        assert "color-scheme: dark" in css
        assert "border-radius: 12px" in css
        assert "#0f766e" in css
        assert "background: #111827" in css
        assert "background: #ffffff" not in css and "background: #fff;" not in css

    def test_light_render_matches_prior_palette(self):
        from signalos_lib.product.agent_dispatch import (
            _render_product_css,
            _render_theme,
        )
        theme = _render_theme({"design_tokens": {}})
        assert "colorScheme: \"light\"" in theme
        assert '"#ffffff"' in theme
        css = _render_product_css({"design_tokens": {}})
        assert "color-scheme: light" in css
        assert "padding: 32px" in css       # 8px unit * 4, as before
        assert "border-radius: 8px" in css

    def test_dark_design_reaches_generated_theme_via_packet(self):
        # Full pipeline: intent -> design -> generation packet -> rendered
        # foundation files (the local build path).
        import tempfile
        from signalos_lib.product.generation import build_generation_packet
        from signalos_lib.product.agent_dispatch import _render_react_vite_files

        intent = {
            "product_name": "OpsLens",
            "product_type": "custom",
            "entities": ["Deployment"],
            "target_users": ["devops engineers"],
            "ux_surfaces": [],
            "api_surfaces": [],
            "data_sources": [],
            "primary_workflows": [],
        }
        design = build_design_system(intent, "react-vite")
        assert design["design_tokens"]["color_scheme"] == "dark"
        with tempfile.TemporaryDirectory() as d:
            packet = build_generation_packet(
                repo_root=Path(d), intent=intent, blueprint=None,
                profile="react-vite", design=design,
            )
            files = _render_react_vite_files(packet)
        assert "colorScheme: \"dark\"" in files["src/ui/theme.ts"]
        assert "color-scheme: dark" in files["src/product.css"]

    def test_prompt_carries_freed_tokens_to_the_agent(self):
        from signalos_lib.product.agent_dispatch import (
            _build_shared_context,
            _build_single_file_prompt,
        )
        gen = {
            "profile": "react-vite",
            "product": "OpsLens",
            "design_constraints": {
                "ui_library": "shadcn/ui",
                "design_tokens": {
                    "color_scheme": "dark",
                    "primary_color": "#0f766e",
                    "border_radius": "12px",
                    "font_family": "Inter",
                    "spacing_unit": 4,
                    "type_scale": "compact",
                },
            },
            "entities": [],
            "workflows": [],
            "acceptance_criteria": [],
            "file_specs": [
                {"path": "src/components/Foo.tsx", "kind": "source",
                 "description": "Foo"},
            ],
        }
        prompt = _build_single_file_prompt(
            gen["file_specs"][0], gen, {}, _build_shared_context(gen),
        )
        assert "Color scheme: dark" in prompt
        assert "Border radius: 12px" in prompt
        assert "Spacing unit: 4px" in prompt
        assert "Type scale: compact" in prompt


# ---------------------------------------------------------------------------
# #9 -- founder brand brief: precedence declared > brand > heuristic
# ---------------------------------------------------------------------------

class TestBrandBriefInDesign:
    def test_brand_brief_end_to_end_from_prompt(self):
        # The canonical scenario: "premium dark fintech app, brand color
        # #0f766e" -> brand object extracted -> design honors it.
        from signalos_lib.product.intent import extract_product_intent
        intent = extract_product_intent(
            "Build a premium dark fintech app, brand color #0f766e, to "
            "track revenue and runway."
        )
        brand = intent.get("brand")
        assert brand is not None
        assert brand["primary_color"] == "#0f766e"
        assert brand["mood"] == "premium"
        assert brand["color_scheme"] == "dark"

        design = build_design_system(intent, "react-vite")
        tokens = design["design_tokens"]
        assert tokens["primary_color"] == "#0f766e"
        assert tokens["color_scheme"] == "dark"
        # premium mood -> sharper radius
        assert tokens["border_radius"] == "4px"

    def test_brand_overrides_heuristic_domain_color(self):
        intent = _medical_records_intent()
        intent["brand"] = {"primary_color": "#db2777"}
        tokens = build_design_system(intent, "react-vite")["design_tokens"]
        # heuristic alone would pick medical green #059669
        assert tokens["primary_color"] == "#db2777"

    def test_mood_maps_deterministically(self):
        intent = _minimal_intent()
        intent["brand"] = {"mood": "playful"}
        tokens = build_design_system(intent, "react-vite")["design_tokens"]
        assert tokens["border_radius"] == "16px"
        assert tokens["type_scale"] == "spacious"

        intent["brand"] = {"mood": "minimal"}
        tokens = build_design_system(intent, "react-vite")["design_tokens"]
        assert tokens["border_radius"] == "0px"

    def test_font_hint_maps_to_safe_font_stack(self):
        intent = _minimal_intent()
        intent["brand"] = {"font_hint": "mono"}
        tokens = build_design_system(intent, "react-vite")["design_tokens"]
        assert tokens["font_family"] == "JetBrains Mono, monospace"

    def test_invalid_brand_values_are_ignored_not_guessed(self):
        intent = _minimal_intent()
        intent["brand"] = {
            "primary_color": "chartreuse-ish",
            "mood": "brutalist",
            "color_scheme": "sepia",
            "font_hint": "wingdings",
        }
        tokens = build_design_system(intent, "react-vite")["design_tokens"]
        assert tokens == build_design_system(
            _minimal_intent(), "react-vite")["design_tokens"]

    def test_declared_library_keeps_precedence_over_brand(self):
        # declared > brand > heuristic: brand styles the tokens, but the
        # founder-declared library still wins the ui_library slot.
        intent = _minimal_intent()
        intent["declared_ui_library"] = "@mantine/core"
        intent["brand"] = {"mood": "playful", "primary_color": "#db2777"}
        design = build_design_system(intent, "react-vite")
        assert design["ui_library"]["name"] == "@mantine/core"
        assert "Founder-declared" in design["ui_library"]["reason"]
        assert design["design_tokens"]["primary_color"] == "#db2777"
        assert design["design_tokens"]["border_radius"] == "16px"

    def test_absent_brand_is_fully_backward_compatible(self):
        from signalos_lib.product.intent import extract_product_intent
        intent = extract_product_intent("Build a task manager for a small team")
        assert "brand" not in intent
        design = build_design_system(intent, "react-vite")
        assert design["design_tokens"]["color_scheme"] == "light"


# ---------------------------------------------------------------------------
# #10 -- MUI + Chakra registry entries wire through every registry consumer
# ---------------------------------------------------------------------------

class TestMuiAndChakraRegistryEntries:
    def test_entries_expose_required_shape(self):
        from signalos_lib.product import design as d
        for key, expected_id in (("@mui/material", "mui"), ("@chakra-ui/react", "chakra")):
            lib = d.get_ui_library(key)
            assert lib is not None and lib.id == expected_id
            assert lib.prompt_desc
            assert lib.fit is not None
            assert lib.dependencies and lib.import_packages
            # both are Emotion-styled libraries -- peers must ship
            assert "@emotion/react" in lib.dependencies
            assert "@emotion/styled" in lib.dependencies
        chakra = d.get_ui_library("chakra")
        assert "framer-motion" in chakra.dependencies  # Chakra v2 peer

    def test_entries_offered_in_architect_prompt(self):
        from signalos_lib.product.design import _ARCHITECT_SYSTEM_PROMPT
        assert "@mui/material" in _ARCHITECT_SYSTEM_PROMPT
        assert "@chakra-ui/react" in _ARCHITECT_SYSTEM_PROMPT

    def test_validator_accepts_new_entries(self):
        from signalos_lib.product.design import _parse_design_response
        for name in ("@mui/material", "@chakra-ui/react"):
            resp = json.dumps({
                "ui_library": {"name": name, "version": "latest", "reason": "x"},
                "design_tokens": {"primary_color": "#111", "font_family": "Inter"},
                "state_management": {"name": "zustand"},
                "data_layer": {"name": "local"},
                "form_handling": {"name": "native"},
            })
            assert _parse_design_response(resp) is not None, name

    def test_deps_resolve_for_new_entries(self):
        deps = get_design_dependencies({"ui_library": {"name": "@mui/material"}})
        assert "@mui/material" in deps
        assert "@mui/icons-material" in deps
        assert "@emotion/react" in deps and "@emotion/styled" in deps

        deps = get_design_dependencies({"ui_library": {"name": "@chakra-ui/react"}})
        assert "@chakra-ui/react" in deps
        assert "@chakra-ui/icons" in deps
        assert "framer-motion" in deps

    def test_import_allowlist_honors_new_entries(self):
        from signalos_lib.product.agent_dispatch import _import_allowlist_lines
        for name, expected in (
            ("@mui/material", "@mui/icons-material"),
            ("@chakra-ui/react", "@chakra-ui/icons"),
        ):
            blob = "\n".join(_import_allowlist_lines(
                "src/components/Foo.tsx", "source",
                {"profile": "react-vite", "design_constraints": {"ui_library": name}},
                [],
            ))
            assert name in blob and expected in blob

    def test_enterprise_admin_intent_selects_mui(self):
        intent = {
            "product_name": "FleetAdmin",
            "product_type": "custom",
            "entities": ["Vehicle"],
            "target_users": ["enterprise admins"],
            "ux_surfaces": [],       # no dashboard/form/table -> mantine/shadcn fits miss
            "api_surfaces": [],
            "data_sources": [],
            "primary_workflows": [],
        }
        design = build_design_system(intent, "react-vite")
        assert design["ui_library"]["name"] == "@mui/material"

    def test_consumer_marketing_intent_selects_chakra(self):
        intent = {
            "product_name": "LaunchPage",
            "product_type": "custom",
            "entities": ["Signup"],
            "target_users": ["consumers"],
            "ux_surfaces": [],
            "api_surfaces": [],
            "data_sources": [],
            "primary_workflows": ["publish landing page"],
        }
        design = build_design_system(intent, "react-vite")
        assert design["ui_library"]["name"] == "@chakra-ui/react"

    def test_existing_fits_keep_precedence_over_new_entries(self):
        # dashboard still beats the enterprise fit; entity-rich forms still
        # beat it too (priority order: shadcn 20 > mantine 10 > mui > chakra)
        intent = _financial_dashboard_intent()
        intent["target_users"] = ["enterprise admins"]
        assert build_design_system(intent, "react-vite")["ui_library"]["name"] == "shadcn/ui"

        intent = _medical_records_intent()
        intent["target_users"] = ["enterprise admins"]
        assert build_design_system(intent, "react-vite")["ui_library"]["name"] == "@mantine/core"

    def test_declared_new_library_is_honored(self):
        intent = _minimal_intent()
        intent["declared_ui_library"] = "mui"
        design = build_design_system(intent, "react-vite")
        assert design["ui_library"]["name"] == "@mui/material"
        assert "Founder-declared" in design["ui_library"]["reason"]

    def test_prompt_declaration_detects_new_libraries(self):
        from signalos_lib.product.intent import extract_product_intent
        intent = extract_product_intent("Build an admin portal using Chakra")
        assert intent["declared_ui_library"] == "@chakra-ui/react"
        intent = extract_product_intent("Build an admin portal with MUI")
        assert intent["declared_ui_library"] == "@mui/material"

    def test_provider_constraints_reach_react_vite_file_specs(self):
        # #10: Chakra needs ChakraProvider, MUI gets ThemeProvider+CssBaseline
        # guidance -- both must land in the component constraints.
        from signalos_lib.product.generation import _build_react_vite_file_specs
        intent = {"entities": ["Item"], "product_type": "custom", "ux_surfaces": []}

        specs = _build_react_vite_file_specs(
            intent, None, {"source": "src"},
            design={"ui_library": {"name": "@chakra-ui/react"}},
        )
        blob = json.dumps(specs)
        assert "ChakraProvider" in blob

        specs = _build_react_vite_file_specs(
            intent, None, {"source": "src"},
            design={"ui_library": {"name": "@mui/material"}},
        )
        blob = json.dumps(specs)
        assert "ThemeProvider" in blob and "CssBaseline" in blob
