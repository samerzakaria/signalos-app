"""Tests for product LLM provider availability controls."""

from __future__ import annotations

from signalos_lib.product.llm_provider import is_llm_available


def test_disable_llm_env_overrides_configured_provider(monkeypatch):
    monkeypatch.setenv("SIGNALOS_LLM_PROVIDER", "test")
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")

    assert is_llm_available() is False


def test_provider_env_reports_available_when_not_disabled(monkeypatch):
    monkeypatch.delenv("SIGNALOS_DISABLE_LLM", raising=False)
    monkeypatch.setenv("SIGNALOS_LLM_PROVIDER", "test")

    assert is_llm_available() is True
