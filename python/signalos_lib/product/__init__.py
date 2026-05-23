"""SignalOS product stack adapters.

Profile-aware adapters that drive scaffold, validation, and preview
through a typed contract so every profile gets correct behaviour
without hard-coded branches in higher-level code.
"""

from __future__ import annotations

from .stacks import (
    GenericAdapter,
    ExistingRepoAdapter,
    ReactViteAdapter,
    StackAdapter,
    detect_profile,
    get_adapter,
    list_adapters,
)

__all__ = [
    "GenericAdapter",
    "ExistingRepoAdapter",
    "ReactViteAdapter",
    "StackAdapter",
    "detect_profile",
    "get_adapter",
    "list_adapters",
]
