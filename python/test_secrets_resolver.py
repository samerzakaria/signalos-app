"""Tests for product-aware secret resolution (product wins, app fallback)."""

from __future__ import annotations

from pathlib import Path

import pytest

from signalos_lib.product import secrets_resolver as sr
from signalos_lib.product.llm_provider import is_llm_available


def _clear_provider_env(monkeypatch):
    for var in sr._PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("SIGNALOS_DISABLE_LLM", raising=False)
    # #23 made is_llm_available() also require an importable provider SDK, which
    # isn't installed on the CI runner. These tests exercise KEY RESOLUTION
    # (product wins, app fallback), not SDK availability -- force the SDK check
    # True to isolate that concern. (A dev machine has the wheel installed,
    # which is why this only failed in CI.)
    monkeypatch.setattr(sr, "_provider_sdk_importable", lambda root=None: True)


def test_parse_env_file_tolerates_comments_quotes_and_export(tmp_path):
    f = tmp_path / ".env.local"
    f.write_text(
        "# comment\n"
        "\n"
        "export ANTHROPIC_API_KEY='sk-product'\n"
        'OPENAI_API_KEY="sk-openai"\n'
        "MALFORMED LINE\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    parsed = sr.parse_env_file(f)
    assert parsed["ANTHROPIC_API_KEY"] == "sk-product"
    assert parsed["OPENAI_API_KEY"] == "sk-openai"
    assert parsed["EMPTY"] == ""
    assert "MALFORMED LINE" not in parsed


def test_parse_env_file_missing_returns_empty(tmp_path):
    assert sr.parse_env_file(tmp_path / "nope.env") == {}


def test_env_local_overrides_env(tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-env\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("ANTHROPIC_API_KEY=from-local\n", encoding="utf-8")
    keys = sr.product_provider_keys(tmp_path)
    assert keys["ANTHROPIC_API_KEY"] == "from-local"


def test_product_key_wins_over_app_key(monkeypatch, tmp_path):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "app-level")
    (tmp_path / ".env.local").write_text("ANTHROPIC_API_KEY=product-level\n", encoding="utf-8")
    assert sr.resolve_provider_key("ANTHROPIC_API_KEY", tmp_path) == "product-level"


def test_falls_back_to_app_key_when_product_missing(monkeypatch, tmp_path):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "app-level")
    assert sr.resolve_provider_key("ANTHROPIC_API_KEY", tmp_path) == "app-level"


def test_is_available_with_only_product_key(monkeypatch, tmp_path):
    _clear_provider_env(monkeypatch)
    (tmp_path / ".env.local").write_text("OPENAI_API_KEY=sk-x\n", encoding="utf-8")
    # No app-level key, but the product defines one.
    assert is_llm_available(tmp_path) is True
    # Without the product root, the app-level check finds nothing.
    assert is_llm_available() is False


def test_no_prompt_when_app_has_key_and_no_product(monkeypatch, tmp_path):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "app-level")
    # The duplicate-prompt bug: onboarding key present, no product .env -> available.
    assert is_llm_available(tmp_path) is True


def test_disable_llm_forces_unavailable(monkeypatch, tmp_path):
    _clear_provider_env(monkeypatch)
    (tmp_path / ".env.local").write_text("ANTHROPIC_API_KEY=sk-x\n", encoding="utf-8")
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    assert is_llm_available(tmp_path) is False


def test_apply_product_secrets_overlays_and_restores(monkeypatch, tmp_path):
    _clear_provider_env(monkeypatch)
    import os

    monkeypatch.setenv("ANTHROPIC_API_KEY", "app-level")
    (tmp_path / ".env.local").write_text("ANTHROPIC_API_KEY=product-level\n", encoding="utf-8")
    with sr.apply_product_secrets(tmp_path):
        assert os.environ["ANTHROPIC_API_KEY"] == "product-level"
    # Restored after the context exits.
    assert os.environ["ANTHROPIC_API_KEY"] == "app-level"


def test_apply_product_secrets_noop_without_root(monkeypatch):
    _clear_provider_env(monkeypatch)
    import os

    monkeypatch.setenv("ANTHROPIC_API_KEY", "app-level")
    with sr.apply_product_secrets(None):
        assert os.environ["ANTHROPIC_API_KEY"] == "app-level"
