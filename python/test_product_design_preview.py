"""Tests for signalos_lib.product.design_preview module."""

import pytest

from signalos_lib.product.design_preview import generate_design_preview_html


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mantine_design():
    return {
        "schema_version": "signalos.design_system.v1",
        "ui_library": {"name": "@mantine/core", "version": "^7.11.0", "reason": "Entity-rich"},
        "design_tokens": {
            "color_scheme": "light",
            "primary_color": "#059669",
            "border_radius": "8px",
            "font_family": "Inter, sans-serif",
            "spacing_unit": 8,
        },
        "state_management": {"name": "zustand", "version": "^4.5.0", "reason": "Simple"},
        "data_layer": {"name": "@tanstack/react-query", "version": "^5.40.0", "reason": "API"},
        "form_handling": {"name": "react-hook-form", "version": "^7.52.0", "reason": "Forms"},
        "additional_deps": {},
    }


@pytest.fixture
def shadcn_design():
    return {
        "schema_version": "signalos.design_system.v1",
        "ui_library": {"name": "shadcn/ui", "version": "latest", "reason": "Composable"},
        "design_tokens": {
            "color_scheme": "light",
            "primary_color": "#2563eb",
            "border_radius": "6px",
            "font_family": "Inter, sans-serif",
            "spacing_unit": 8,
        },
        "state_management": {"name": "zustand", "version": "^4.5.0", "reason": "Simple"},
        "data_layer": {"name": "local", "version": None, "reason": "Local"},
        "form_handling": {"name": "native", "version": None, "reason": "Simple"},
        "additional_deps": {},
    }


@pytest.fixture
def medical_intent():
    return {
        "product_name": "MedUnify",
        "entities": ["Patient", "ClinicalNote", "LabResult", "Prescription"],
        "primary_workflows": ["Record patient visit", "View lab results", "Prescribe medication"],
        "ux_surfaces": ["form", "table", "detail"],
        "product_type": "medical",
    }


@pytest.fixture
def empty_intent():
    return {
        "product_name": "",
        "entities": [],
        "primary_workflows": [],
        "ux_surfaces": [],
        "product_type": "custom",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGeneratesMantineHTML:
    def test_generates_html_for_mantine(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "<!DOCTYPE html>" in html
        # Mantine style uses CSS variables, not Tailwind CDN
        assert "--primary:" in html
        assert "tailwind" not in html.lower()

    def test_contains_mantine_font_link(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "fonts.googleapis.com" in html


class TestGeneratesShadcnHTML:
    def test_generates_html_for_shadcn(self, shadcn_design, medical_intent):
        html = generate_design_preview_html(shadcn_design, medical_intent)
        assert "<!DOCTYPE html>" in html
        # shadcn uses Tailwind CDN
        assert "tailwindcss" in html or "cdn.tailwindcss.com" in html

    def test_contains_tailwind_classes(self, shadcn_design, medical_intent):
        html = generate_design_preview_html(shadcn_design, medical_intent)
        assert "rounded-lg" in html
        assert "border" in html


class TestHTMLContainsEntityNames:
    def test_entity_names_appear(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "Patient" in html
        assert "Clinical Note" in html or "Clinicalnote" in html or "ClinicalNote" in html
        assert "Lab Result" in html or "Labresult" in html or "LabResult" in html
        assert "Prescription" in html

    def test_entity_names_in_shadcn(self, shadcn_design, medical_intent):
        html = generate_design_preview_html(shadcn_design, medical_intent)
        assert "Patient" in html
        assert "Prescription" in html


class TestHTMLContainsPrimaryColor:
    def test_mantine_primary_color(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "#059669" in html

    def test_shadcn_primary_color(self, shadcn_design, medical_intent):
        html = generate_design_preview_html(shadcn_design, medical_intent)
        assert "#2563eb" in html


class TestHTMLContainsFontFamily:
    def test_mantine_font(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "Inter" in html

    def test_shadcn_font(self, shadcn_design, medical_intent):
        html = generate_design_preview_html(shadcn_design, medical_intent)
        assert "Inter" in html


class TestHTMLIsSelfContained:
    def test_no_relative_imports_mantine(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        # No relative paths like ./something or ../something
        assert 'src="./' not in html
        assert 'href="./' not in html
        assert 'src="../' not in html
        assert 'href="../' not in html

    def test_no_relative_imports_shadcn(self, shadcn_design, medical_intent):
        html = generate_design_preview_html(shadcn_design, medical_intent)
        assert 'src="./' not in html
        assert 'href="./' not in html
        assert 'src="../' not in html
        assert 'href="../' not in html


class TestHTMLHasLayoutStructure:
    def test_mantine_has_header_sidebar_content(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "header" in html.lower()
        assert "sidebar" in html.lower()
        assert "content" in html.lower() or "main" in html.lower()

    def test_shadcn_has_header_sidebar_content(self, shadcn_design, medical_intent):
        html = generate_design_preview_html(shadcn_design, medical_intent)
        assert "header" in html.lower()
        assert "sidebar" in html.lower()
        assert "main" in html.lower()


class TestEmptyIntentStillRenders:
    def test_mantine_empty_intent(self, mantine_design, empty_intent):
        html = generate_design_preview_html(mantine_design, empty_intent)
        assert "<!DOCTYPE html>" in html
        assert "app-shell" in html or "min-h-screen" in html
        # Should still have a fallback card
        assert "Sample Item" in html or "Dashboard" in html

    def test_shadcn_empty_intent(self, shadcn_design, empty_intent):
        html = generate_design_preview_html(shadcn_design, empty_intent)
        assert "<!DOCTYPE html>" in html
        assert "Dashboard" in html or "Sample Item" in html


class TestNoInternalJargon:
    """Verify no library names or developer jargon in user-facing preview."""

    def test_no_mantine_string_in_mantine_output(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        # Should not show "Mantine" to the user
        assert "Mantine" not in html
        assert "mantine" not in html.lower().replace("@mantine", "")

    def test_no_shadcn_string_in_shadcn_output(self, shadcn_design, medical_intent):
        html = generate_design_preview_html(shadcn_design, medical_intent)
        # Should not show "shadcn" to the user
        assert "shadcn" not in html

    def test_no_zustand_jargon(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "zustand" not in html.lower()
        assert "react-hook-form" not in html.lower()


class TestProductNameAppearsInPreview:
    def test_product_name_in_header(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "MedUnify" in html

    def test_product_name_in_shadcn(self, shadcn_design, medical_intent):
        html = generate_design_preview_html(shadcn_design, medical_intent)
        assert "MedUnify" in html
