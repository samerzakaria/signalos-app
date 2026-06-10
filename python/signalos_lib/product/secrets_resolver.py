# signalos_lib/product/secrets_resolver.py
# Product-aware secret resolution.
#
# Resolution order for any provider key: the product-level secret (a value in
# the workspace's .env files) wins; if absent, fall back to the app-level value
# already in the process environment (injected from the OS keychain at sidecar
# spawn time, where the onboarding key lives).
#
# This is the single place that knows a product may override the app-wide
# provider key. Without it, a product re-prompts for a key the user already
# entered at onboarding, because the app-level keychain key and the per-product
# .env store were never unified.

from __future__ import annotations

__all__ = [
    "PROVIDER_ENV_VARS",
    "parse_env_file",
    "load_workspace_env",
    "product_provider_keys",
    "resolve_provider_key",
    "is_llm_available",
    "apply_product_secrets",
]

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .llm_provider import _DISABLE_VALUES, _PROVIDER_ENV_VARS

# The subset of env vars that are actual provider *keys*. SIGNALOS_LLM_PROVIDER
# is a selector, not a credential, so it is excluded from the key set (but still
# counts toward app-level availability below).
PROVIDER_ENV_VARS = tuple(v for v in _PROVIDER_ENV_VARS if v != "SIGNALOS_LLM_PROVIDER")

# Highest priority first: .env.local overrides .env, which overrides the rest.
_ENV_FILE_PRIORITY = (".env.local", ".env", ".env.development", ".env.production")


def parse_env_file(path) -> dict[str, str]:
    """Parse a dotenv file into a dict.

    Tolerant by design: ignores comments, blank lines, malformed lines, and an
    optional leading ``export``. Strips a single matching pair of surrounding
    quotes. Never raises on missing files or decode errors -- a product without
    a given .env simply contributes nothing.
    """
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_workspace_env(root) -> dict[str, str]:
    """Merge a workspace's .env files into one dict; higher-priority files win."""
    merged: dict[str, str] = {}
    if root is None:
        return merged
    base = Path(root)
    # Apply lowest priority first so the highest-priority file overwrites it.
    for name in reversed(_ENV_FILE_PRIORITY):
        merged.update(parse_env_file(base / name))
    return merged


def product_provider_keys(root) -> dict[str, str]:
    """Provider keys defined at the product level (non-empty values only)."""
    env = load_workspace_env(root)
    return {k: v for k, v in env.items() if k in PROVIDER_ENV_VARS and v}


def resolve_provider_key(var: str, root=None) -> Optional[str]:
    """Resolve a single provider env var: product value wins, else process env."""
    if root is not None:
        keys = product_provider_keys(root)
        if var in keys:
            return keys[var]
    return os.environ.get(var)


def is_llm_available(root=None) -> bool:
    """Product-aware availability check.

    True when a provider key is configured at EITHER the product level (the
    workspace .env files) OR the app level (process env / keychain). Both
    satisfy availability, so callers only see "no key" when both miss.
    ``SIGNALOS_DISABLE_LLM`` still forces False.
    """
    if os.environ.get("SIGNALOS_DISABLE_LLM", "").strip().lower() in _DISABLE_VALUES:
        return False
    if root is not None and product_provider_keys(root):
        return True
    return any(os.environ.get(var) for var in _PROVIDER_ENV_VARS)


@contextmanager
def apply_product_secrets(root) -> Iterator[None]:
    """Temporarily overlay product provider keys onto os.environ (product wins).

    Restores the prior environment on exit. A no-op when ``root`` is None or the
    product defines no provider keys, so app-level resolution is unchanged.
    """
    overrides = product_provider_keys(root) if root is not None else {}
    if not overrides:
        yield
        return
    saved: dict[str, Optional[str]] = {}
    try:
        for k, v in overrides.items():
            saved[k] = os.environ.get(k)
            os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
