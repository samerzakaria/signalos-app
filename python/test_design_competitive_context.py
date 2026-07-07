# test_design_competitive_context.py
# Design-phase consumption hook for competitor:analyze output:
# .signalos/product/COMPETITORS.json (when present) contributes a compact
# competitive-context block to the architect prompt. Absent file -> the
# prompt (and build_design_system behavior) is EXACTLY what it was before.

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_lib.harness as harness  # noqa: E402
from signalos_lib.product import design as design_mod  # noqa: E402
from signalos_lib.product.design import (  # noqa: E402
    _competitive_context_block,
    build_design_system,
    select_design_with_llm,
)

_VALID_DESIGN_JSON = json.dumps({
    "ui_library": {"name": "shadcn/ui", "version": "latest", "reason": "r"},
    "design_tokens": {
        "color_scheme": "light",
        "primary_color": "#3b82f6",
        "border_radius": "8px",
        "font_family": "Inter, sans-serif",
        "spacing_unit": 8,
        "type_scale": "regular",
    },
    "state_management": {"name": "zustand", "version": "^4.5.0", "reason": "r"},
    "data_layer": {"name": "local", "version": None, "reason": "r"},
    "form_handling": {"name": "native", "version": None, "reason": "r"},
})


def _seed_competitors(root: Path) -> None:
    d = root / ".signalos" / "product"
    d.mkdir(parents=True, exist_ok=True)
    (d / "COMPETITORS.json").write_text(json.dumps({
        "schema_version": "signalos.competitors.v1",
        "matrix": [{
            "url": "https://acme.test",
            "headline": "Run your team on Acme",
            "primary_cta": "Start free trial",
            "feature_count": 3,
            "has_pricing": "yes",
        }],
        "insights": "- differentiate on speed",
    }), encoding="utf-8")


class _RecordingProvider:
    def __init__(self):
        self.prompts: list[str] = []

    def call(self, prompt: str, model: str):
        self.prompts.append(prompt)
        return _VALID_DESIGN_JSON, 1, 1


def _patch_llm(monkeypatch) -> _RecordingProvider:
    provider = _RecordingProvider()
    monkeypatch.setattr(harness, "_resolve_provider", lambda name=None: provider)
    monkeypatch.setattr(
        harness, "resolve_model", lambda model=None, provider_name=None: "test-model"
    )
    return provider


def _intent() -> dict:
    return {"product_name": "Acme Killer", "product_type": "custom", "entities": []}


# ---------------------------------------------------------------------------
# _competitive_context_block
# ---------------------------------------------------------------------------


def test_block_is_none_without_file(tmp_path):
    assert _competitive_context_block(tmp_path) is None
    assert _competitive_context_block(None) is None


def test_block_is_none_for_unreadable_or_empty_content(tmp_path):
    d = tmp_path / ".signalos" / "product"
    d.mkdir(parents=True)
    (d / "COMPETITORS.json").write_text("{not json", encoding="utf-8")
    assert _competitive_context_block(tmp_path) is None
    (d / "COMPETITORS.json").write_text(json.dumps({"matrix": []}), encoding="utf-8")
    assert _competitive_context_block(tmp_path) is None
    (d / "COMPETITORS.json").write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert _competitive_context_block(tmp_path) is None


def test_block_renders_matrix_rows_and_insights(tmp_path):
    _seed_competitors(tmp_path)
    block = _competitive_context_block(tmp_path)
    assert block is not None
    assert "Competitive Context" in block
    assert "https://acme.test" in block
    assert "Run your team on Acme" in block
    assert "Start free trial" in block
    assert "differentiate on speed" in block


# ---------------------------------------------------------------------------
# select_design_with_llm prompt wiring
# ---------------------------------------------------------------------------


def test_architect_prompt_includes_competitive_context(tmp_path, monkeypatch):
    _seed_competitors(tmp_path)
    provider = _patch_llm(monkeypatch)
    result = select_design_with_llm(_intent(), "react-vite", root=tmp_path)
    assert result is not None
    assert len(provider.prompts) == 1
    assert "Competitive Context" in provider.prompts[0]
    assert "https://acme.test" in provider.prompts[0]


def test_architect_prompt_unchanged_without_file(tmp_path, monkeypatch):
    provider = _patch_llm(monkeypatch)
    result = select_design_with_llm(_intent(), "react-vite", root=tmp_path)
    assert result is not None
    assert "Competitive Context" not in provider.prompts[0]


def test_architect_prompt_unchanged_without_root(monkeypatch):
    # Existing callers that pass no root keep the exact current behavior.
    provider = _patch_llm(monkeypatch)
    result = select_design_with_llm(_intent(), "react-vite")
    assert result is not None
    assert "Competitive Context" not in provider.prompts[0]


# ---------------------------------------------------------------------------
# build_design_system threads root through to the architect prompt
# ---------------------------------------------------------------------------


def test_build_design_system_threads_root(tmp_path, monkeypatch):
    _seed_competitors(tmp_path)
    provider = _patch_llm(monkeypatch)
    monkeypatch.setattr(design_mod, "is_llm_available", lambda root=None: True)
    design = build_design_system(_intent(), "react-vite", root=tmp_path)
    assert design["ui_library"]["name"] == "shadcn/ui"
    assert "Competitive Context" in provider.prompts[0]


def test_build_design_system_absent_file_prompt_unchanged(tmp_path, monkeypatch):
    provider = _patch_llm(monkeypatch)
    monkeypatch.setattr(design_mod, "is_llm_available", lambda root=None: True)
    design = build_design_system(_intent(), "react-vite", root=tmp_path)
    assert design["ui_library"]["name"] == "shadcn/ui"
    assert "Competitive Context" not in provider.prompts[0]


def test_build_design_system_without_llm_is_deterministic(tmp_path, monkeypatch):
    # COMPETITORS.json present but LLM unavailable -> deterministic path,
    # identical to today's behavior (the hook only feeds the architect prompt).
    _seed_competitors(tmp_path)
    monkeypatch.setattr(design_mod, "is_llm_available", lambda root=None: False)
    with_file = build_design_system(_intent(), "react-vite", root=tmp_path)
    without_root = build_design_system(_intent(), "react-vite")
    assert with_file == without_root
