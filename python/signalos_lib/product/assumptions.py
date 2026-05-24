# signalos_lib/product/assumptions.py
# Phase P1 - Record safe-default assumptions for empty non-critical fields

from __future__ import annotations

__all__ = ["record_assumptions", "write_assumptions"]

import json
from pathlib import Path
from typing import Any


# Non-critical fields and their safe defaults when the user opts for speed.
_SAFE_DEFAULTS: list[tuple[str, str, str]] = [
    (
        "deployment_intent",
        "none",
        "No deployment preference stated; defaulting to none until decided.",
    ),
    (
        "auth_requirements",
        "email-password",
        "No auth requirements stated; assuming basic email/password login.",
    ),
    (
        "data_sources",
        "database",
        "No data source specified; assuming a relational database.",
    ),
    (
        "integrations",
        "none",
        "No third-party integrations mentioned; assuming standalone.",
    ),
    (
        "permissions",
        "owner-only",
        "No permission model specified; assuming single-owner access.",
    ),
    (
        "audit_requirements",
        "basic-logging",
        "No audit requirements stated; assuming basic event logging.",
    ),
    (
        "security_constraints",
        "standard-web",
        "No specific security constraints; assuming standard web security practices.",
    ),
    (
        "performance_expectations",
        "standard",
        "No performance requirements stated; assuming standard web-app latency.",
    ),
]


def record_assumptions(intent: dict[str, Any]) -> list[dict[str, str]]:
    """Return a list of safe-default assumptions for empty non-critical fields.

    Each entry is ``{"field": str, "assumed_value": str, "reason": str}``.
    Only fields that are currently empty/default get an assumption recorded.
    """
    assumptions: list[dict[str, str]] = []

    for field, default_value, reason in _SAFE_DEFAULTS:
        value = intent.get(field)
        if _is_empty(value):
            assumptions.append({
                "field": field,
                "assumed_value": default_value,
                "reason": reason,
            })

    return assumptions


def write_assumptions(
    assumptions: list[dict[str, str]],
    signalos_dir: Path,
) -> Path:
    """Persist assumptions to ``.signalos/product/ASSUMPTIONS.json``."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "ASSUMPTIONS.json"
    path.write_text(
        json.dumps(assumptions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value == "none"
    if isinstance(value, list):
        return len(value) == 0
    return False
