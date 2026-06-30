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


# All env vars that Tauri injects from the OS keychain. ANY of these being
# set means an LLM provider is available.
#
# DERIVED from the harness provider table (the single source of truth) so
# the two lists can never drift again. The provider *key* env vars come
# straight from `harness.PROVIDER_ENV_VARS`; `SIGNALOS_LLM_PROVIDER` is the
# provider *selector* (not a credential) and is appended here because it
# also counts toward "is an LLM available?" for app-level availability.
def _derive_provider_env_vars() -> list[str]:
    try:
        from signalos_lib.harness import PROVIDER_ENV_VARS as _KEYS
    except Exception:
        # Stdlib-only / partial install fallback. Kept in lock-step with the
        # harness table; if the import ever fails we still know the keys.
        _KEYS = [
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
            "GROQ_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY",
            "OPENROUTER_API_KEY", "XAI_API_KEY", "TOGETHER_API_KEY",
            "CEREBRAS_API_KEY", "DASHSCOPE_API_KEY",
        ]
    return [*_KEYS, "SIGNALOS_LLM_PROVIDER"]


_PROVIDER_ENV_VARS = _derive_provider_env_vars()

_DISABLE_VALUES = {"1", "true", "yes", "on"}


@dataclass
class LLMCallResult:
    """Result of an LLM call."""
    success: bool
    text: str
    error: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


def is_llm_available(root=None) -> bool:
    """Check if ANY LLM provider is configured.

    Returns True if any provider API key or SIGNALOS_LLM_PROVIDER is set --
    either at the app level (process env, injected from the OS keychain at
    sidecar spawn) or, when ``root`` is given, at the product level (the
    workspace .env files). Product and app both satisfy availability, so a
    "no key" result only happens when both miss. See secrets_resolver for the
    product-wins resolution order.
    """
    from .secrets_resolver import is_llm_available as _resolve_available
    return _resolve_available(root)


def call_llm(
    prompt: str,
    provider_name: str | None = None,
    model: str | None = None,
    root=None,
) -> LLMCallResult:
    """Call the LLM provider and return the result.

    Never raises; returns LLMCallResult with success=False on any error.
    The caller sees the error message and can surface it to the user.

    When ``root`` (a product workspace) is given, the product's own provider
    keys override the app-level keys for the duration of the call.
    """
    try:
        from signalos_lib.harness import _resolve_provider, resolve_model
    except ImportError as exc:
        return LLMCallResult(
            success=False,
            text="",
            error=f"LLM harness not available: {exc}",
        )

    from .secrets_resolver import apply_product_secrets

    # Product keys win over app keys; provider *selection*, model discovery,
    # and the call itself must all see the overlay, so wrap them together.
    with apply_product_secrets(root):
        try:
            provider = _resolve_provider(provider_name)
        except Exception as exc:
            return LLMCallResult(
                success=False,
                text="",
                error=f"Provider resolution failed: {exc}",
            )

        try:
            # No hardcoded default: resolve the model via explicit arg →
            # SIGNALOS_LLM_MODEL → discovery from the resolved provider's API.
            resolved_model = resolve_model(model, provider_name)
        except Exception as exc:
            return LLMCallResult(
                success=False,
                text="",
                error=f"Model resolution failed: {exc}",
            )

        try:
            text, tokens_in, tokens_out = provider.call(prompt, resolved_model)
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
