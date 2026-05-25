"""Tests for product design decision artifacts."""

from __future__ import annotations

from pathlib import Path

from signalos_lib.product.design import build_design_system
from signalos_lib.product.design_decisions import (
    SCHEMA_VERSION,
    build_design_decisions,
    load_design_decisions,
    validate_design_decisions,
    write_design_decisions,
)


def _ui_intent() -> dict:
    return {
        "product_name": "OpsBoard",
        "product_type": "financial-dashboard",
        "entities": ["Revenue", "Churn", "Forecast"],
        "ux_surfaces": ["dashboard", "chart", "table"],
        "api_surfaces": ["rest-api"],
        "data_sources": ["database"],
        "primary_workflows": ["monitor revenue", "investigate churn"],
    }


def _valid_decisions() -> dict:
    intent = _ui_intent()
    design_system = build_design_system(intent, "react-vite")
    return build_design_decisions(
        intent,
        design_system,
        wave="08",
        taste_findings=[
            {
                "finding": "Dense metric cards are appropriate for operator review.",
                "disposition": "accepted",
            },
            {
                "finding": "Pure marketing hero layout is too broad for delivery scope.",
                "disposition": "rejected",
            },
        ],
        approved_by="product-owner",
    )


def test_valid_decision_passes_for_ui_product():
    decisions = _valid_decisions()
    result = validate_design_decisions(
        decisions,
        profile="react-vite",
        intent=_ui_intent(),
    )

    assert result["valid"] is True
    assert result["blockers"] == []
    assert decisions["schema_version"] == SCHEMA_VERSION
    assert len(decisions["variants"]) >= 3
    assert decisions["selected_variant"] in {v["id"] for v in decisions["variants"]}
    for variant in decisions["variants"]:
        assert {"id", "summary", "strengths", "weaknesses", "screenshot", "score"} <= set(variant)


def test_missing_selected_variant_fails_for_ui_product():
    decisions = _valid_decisions()
    decisions["selected_variant"] = ""

    result = validate_design_decisions(
        decisions,
        profile="react-vite",
        intent=_ui_intent(),
    )

    assert result["valid"] is False
    assert any("selected_variant" in blocker for blocker in result["blockers"])


def test_unknown_selected_variant_fails_for_ui_product():
    decisions = _valid_decisions()
    decisions["selected_variant"] = "variant-99-unknown"

    result = validate_design_decisions(
        decisions,
        profile="react-vite",
        intent=_ui_intent(),
    )

    assert result["valid"] is False
    assert any("not found in variants" in blocker for blocker in result["blockers"])


def test_missing_taste_disposition_fails():
    decisions = _valid_decisions()
    decisions["taste_findings"] = [{"finding": "Use compact tables."}]

    result = validate_design_decisions(
        decisions,
        profile="react-vite",
        intent=_ui_intent(),
    )

    assert result["valid"] is False
    assert any("disposition" in blocker for blocker in result["blockers"])


def test_design_decisions_round_trip(tmp_path: Path):
    decisions = _valid_decisions()
    signalos_dir = tmp_path / ".signalos"

    path = write_design_decisions(decisions, signalos_dir)
    loaded = load_design_decisions(signalos_dir, "08")

    assert path == signalos_dir / "designs" / "08" / "DESIGN_DECISIONS.yaml"
    assert path.is_file()
    assert loaded == decisions


def test_builder_does_not_self_authorize_scope():
    intent = _ui_intent()
    design_system = build_design_system(intent, "react-vite")

    decisions = build_design_decisions(intent, design_system, wave="08")
    result = validate_design_decisions(
        decisions,
        profile="react-vite",
        intent=intent,
    )

    assert decisions["approved_by"] == ""
    assert result["valid"] is True
    assert any("not delivery authorization" in warning for warning in result["warnings"])
