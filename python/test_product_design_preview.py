"""Tests for signalos_lib.product.design_preview module."""

import pytest

from signalos_lib.product.design import _MANTINE_VERSION
from signalos_lib.product.design_preview import generate_design_preview_html


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mantine_design():
    return {
        "schema_version": "signalos.design_system.v1",
        "ui_library": {"name": "@mantine/core", "version": _MANTINE_VERSION, "reason": "Entity-rich"},
        "design_tokens": {
            "color_scheme": "light",
            "primary_color": "#059669",
            "border_radius": "8px",
            "font_family": "Inter, sans-serif",
            "spacing_unit": 8,
        },
    }


@pytest.fixture
def medical_intent():
    return {
        "product_name": "MedUnify",
        "product_type": "custom",
        "entities": ["Patient", "ClinicalNote", "LabResult", "Prescription"],
        "primary_workflows": ["intake", "schedule"],
        "ux_surfaces": ["table", "form", "detail"],
    }


@pytest.fixture
def empty_intent():
    return {
        "product_name": "",
        "product_type": "custom",
        "entities": [],
        "primary_workflows": [],
        "ux_surfaces": [],
    }


# ---------------------------------------------------------------------------
# Tests -- without API key, returns "needs key" page
# ---------------------------------------------------------------------------

class TestDesignPreviewNoKey:
    """Without an API key, preview returns honest 'needs key' page."""

    def test_returns_html(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert html.startswith("<!DOCTYPE html>")

    def test_contains_product_name(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "MedUnify" in html

    def test_contains_primary_color(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "#059669" in html

    def test_contains_font_family(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "Inter" in html

    def test_contains_entity_names(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "Patient" in html
        assert "ClinicalNote" in html
        assert "LabResult" in html
        assert "Prescription" in html

    def test_explains_api_key_needed(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        assert "ANTHROPIC_API_KEY" in html or "AI provider" in html

    def test_is_self_contained(self, mantine_design, medical_intent):
        html = generate_design_preview_html(mantine_design, medical_intent)
        # No relative imports
        assert 'src="/' not in html
        assert "import " not in html

    def test_empty_intent_no_crash(self, mantine_design, empty_intent):
        html = generate_design_preview_html(mantine_design, empty_intent)
        assert html.startswith("<!DOCTYPE html>")
        assert "No entities" in html or "Product" in html
