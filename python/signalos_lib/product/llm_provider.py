# signalos_lib/product/llm_provider.py
# Shared LLM provider resolution for all product modules.
#
# Single source of truth for "is an LLM available?" and "call it."
# Every product module imports from here instead of checking env vars
# and catching exceptions independently.

from __future__ import annotations

__all__ = [
    "is_llm_available",
    "call_llm",
    "LLMCallResult",
]

import os
from dataclasses import dataclass
from typing import Any


# All env vars that Tauri injects from the OS keychain.
# ANY of these being set means an LLM provider is available.
_PROVIDER_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "DASHSCOPE_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "TOGETHER_API_KEY",
    "XAI_API_KEY",
    "SIGNALOS_LLM_PROVIDER",
]

_DISABLE_VALUES = {"1", "true", "yes", "on"}


@dataclass
class LLMCallResult:
    """Result of an LLM call."""
    success: bool
    text: str
    error: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


def is_llm_available() -> bool:
    """Check if ANY LLM provider is configured.

    Returns True if any provider API key or SIGNALOS_LLM_PROVIDER is
    set in the environment. Tauri injects all keychain-stored keys at
    sidecar spawn time.
    """
    if os.environ.get("SIGNALOS_DISABLE_LLM", "").strip().lower() in _DISABLE_VALUES:
        return False
    return any(os.environ.get(var) for var in _PROVIDER_ENV_VARS)


def call_llm(
    prompt: str,
    provider_name: str | None = None,
    model: str | None = None,
) -> LLMCallResult:
    """Call the LLM provider and return the result.

    Never raises; returns LLMCallResult with success=False on any error.
    The caller sees the error message and can surface it to the user.
    """
    try:
        from signalos_lib.harness import _resolve_provider, DEFAULT_MODEL
    except ImportError as exc:
        return LLMCallResult(
            success=False,
            text="",
            error=f"LLM harness not available: {exc}",
        )

    try:
        provider = _resolve_provider(provider_name)
    except Exception as exc:
        return LLMCallResult(
            success=False,
            text="",
            error=f"Provider resolution failed: {exc}",
        )

    try:
        text, tokens_in, tokens_out = provider.call(
            prompt, model or DEFAULT_MODEL,
        )
        return LLMCallResult(
            success=True,
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
    except Exception as exc:
        return LLMCallResult(
            success=False,
            text="",
            error=f"LLM call failed: {exc}",
        )
