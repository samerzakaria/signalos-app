"""Tests for GTM auto-generation (deterministic path)."""

from __future__ import annotations

from signalos_lib.product.gtm import generate_gtm, generate_gtm_markdown

_INTENT = {
    "product_name": "TaskFlow",
    "target_users": ["team managers", "team members"],
    "primary_workflows": ["manage team tasks", "assign tasks", "track utilization"],
}


def test_generates_three_assets_without_llm(monkeypatch):
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    gtm = generate_gtm(_INTENT, use_llm=True)
    assert gtm["llm_authored"] is False
    assert set(("landing_page", "app_store", "product_hunt")).issubset(gtm)


def test_landing_uses_intent(monkeypatch):
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    gtm = generate_gtm(_INTENT)
    assert "TaskFlow" in gtm["landing_page"]["headline"]
    assert gtm["landing_page"]["sections"]  # one per workflow
    assert "TaskFlow" in gtm["landing_page"]["cta"]


def test_app_store_limits_respected(monkeypatch):
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    store = generate_gtm(_INTENT)["app_store"]
    assert len(store["title"]) <= 30
    assert len(store["subtitle"]) <= 30
    assert isinstance(store["keywords"], list)


def test_product_hunt_tagline_bounded(monkeypatch):
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    ph = generate_gtm(_INTENT)["product_hunt"]
    assert len(ph["tagline"]) <= 60
    assert ph["first_comment"]
    assert 1 <= len(ph["topics"]) <= 4


def test_handles_sparse_intent(monkeypatch):
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    gtm = generate_gtm({})  # nothing provided
    assert gtm["landing_page"]["headline"]  # never empty
    assert gtm["app_store"]["title"]


def test_markdown_renders_all_sections(monkeypatch):
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    md = generate_gtm_markdown(generate_gtm(_INTENT))
    for heading in ("# Go-to-market assets", "## Landing page",
                    "## App store listing", "## Product Hunt"):
        assert heading in md
    # Deterministic draft carries the connect-a-provider note.
    assert "Connect a provider" in md


def test_no_fabricated_metrics(monkeypatch):
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    md = generate_gtm_markdown(generate_gtm(_INTENT)).lower()
    # The template must not invent adoption/stat claims.
    for bogus in ("% faster", "guaranteed", "best-in-class", "thousands of", "#1 "):
        assert bogus not in md
