# SignalOS Core v1.1 — Python CLI support library.
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
#
# Module map:
#   session.py  — W1.1: session journal read/write, resume, archive
#   pause.py    — W1.1: step-pause controller (opt-in, per-step)
#   harness.py  — W1.2: headless emitter; Anthropic SDK-based
#   context.py  — W1.3: rule-based context compressor (4 layers)
#   registry.py — W1.3: plugin registry (cosign-verified tarballs)
#
#   commands/   — thin argparse wrappers for each CLI verb; modules above
#                 hold the business logic so they can be unit-tested in
#                 isolation.

from __future__ import annotations

import sys as _sys

__version__ = "2.17.0b3"

__all__: list[str] = ["deprecated_warning"]  # W-2: explicit public API


def deprecated_warning(
    name: str,
    removal_version: str,
    alternative: str | None = None,
) -> None:
    """Emit a deprecation notice to stderr (AMD-CORE-024).

    Parameters
    ----------
    name:
        The item being deprecated (e.g. ``"--turn"``).
    removal_version:
        Earliest version at which the item may be removed (e.g. ``"3.0"``).
    alternative:
        Suggested replacement, or ``None`` if none exists.
    """
    msg = f"{name!r} is deprecated and will be removed in {removal_version}."
    if alternative is not None:
        msg += f" Use {alternative!r} instead."
    _sys.stderr.write(f"DeprecationWarning: {msg}\n")
