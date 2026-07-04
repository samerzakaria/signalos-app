# conftest.py
# Hermetic LLM env for the offline test suite.
#
# Many tests assert deterministic "without-LLM" behavior (is_llm_available() is
# False -> deterministic fallbacks, "needs API key" messaging, empty clarifying
# questions, etc.). Those tests are correct in CI (no keys) but FAIL on a
# developer's machine that exports a real ANTHROPIC_API_KEY / OPENAI_API_KEY
# (e.g. from a sourced .env), because the code then sees a live provider.
#
# This autouse fixture clears provider keys by default so every test runs
# hermetically regardless of the ambient shell. A test that genuinely needs a
# provider sets it explicitly via monkeypatch.setenv AFTER this fixture runs, so
# its intent still holds (its setenv overrides the clear).
from __future__ import annotations

import os

import pytest

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "COHERE_API_KEY",
    "TOGETHER_API_KEY",
    "DEEPSEEK_API_KEY",
    "XAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "SIGNALOS_LLM_PROVIDER",
    "SIGNALOS_LLM_MODEL",
)


# Clear provider keys at conftest IMPORT (pytest loads conftest before it
# imports/collects any test module). Some modules capture a live flag at import
# time -- e.g. `_LIVE = os.getenv("ANTHROPIC_API_KEY") ...` -- which a per-test
# fixture cannot undo. Popping here makes those import-time flags hermetic too,
# so the offline suite never routes to a real (possibly out-of-credit) provider.
# A live-integration run should set keys explicitly and opt in, not rely on the
# ambient shell leaking into the unit suite.
for _var in _PROVIDER_ENV:
    os.environ.pop(_var, None)


@pytest.fixture(autouse=True)
def _hermetic_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_ENV:
        monkeypatch.delenv(var, raising=False)
