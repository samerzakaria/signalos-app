# signalos_lib/product/assumptions.py
# Phase P1 - Record safe-default assumptions for empty non-critical fields
#
# LLM agent is FIRST choice; static defaults are FALLBACK.

from __future__ import annotations

__all__ = ["record_assumptions", "record_assumptions_with_llm", "write_assumptions"]

import json
import os
from pathlib import Path
from typing import Any
from .llm_provider import is_llm_available


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


# ---------------------------------------------------------------------------
# LLM-driven assumption generation
# ---------------------------------------------------------------------------

_ASSUMPTIONS_SYSTEM_PROMPT = """\
You are the highest-level domain analyst ever for this product's domain, the
greatest product analyst ever for this product's domain, with very deep domain
knowledge and hands-on operating experience, acting in a SignalOS-governed
software house.

Your job: fill reasonable assumptions for empty/missing fields in a product
intent, based on the product domain and what IS known. You reason from
hands-on domain reality rather than using generic defaults.

For example, if the product is a "medical records app", you would assume
HIPAA-grade security rather than "standard-web". If it's a real-time
dashboard, you'd assume WebSocket data sources rather than plain database.

Rules:
- Only fill fields that are currently empty/missing
- Base assumptions on what IS known about the product (name, type, entities)
- Provide domain-appropriate defaults, not generic ones
- Consider users, workflows, data sensitivity, regulations, incentives,
  operational constraints, and failure modes in the domain
- Each assumption must have a clear reason

## Fields to consider

- deployment_intent: How the product will be deployed
- auth_requirements: Authentication/authorization approach
- data_sources: Where data comes from
- integrations: Third-party services needed
- permissions: Access control model
- audit_requirements: Logging/audit needs
- security_constraints: Security posture
- performance_expectations: Latency/throughput needs

## Output Format

Return ONLY valid JSON (no markdown fencing, no explanation outside the JSON):
[
  {
    "field": "<field name>",
    "assumed_value": "<the assumed value>",
    "reason": "<domain-specific reason for this assumption>"
  }
]
"""


def record_assumptions_with_llm(
    intent: dict[str, Any],
    provider_name: str | None = None,
    model: str | None = None,
) -> list[dict[str, str]] | None:
    """Use LLM to fill reasonable assumptions for empty fields.

    The agent reasons about what's sensible given the product domain,
    rather than using static defaults.

    Returns a list of assumptions or None if LLM unavailable.
    """
    try:
        from signalos_lib.harness import _resolve_provider, resolve_model
    except Exception:
        return None

    try:
        provider = _resolve_provider(provider_name)
    except Exception:
        return None

    # Identify empty fields
    empty_fields = [f for f, _, _ in _SAFE_DEFAULTS if _is_empty(intent.get(f))]
    if not empty_fields:
        return []

    parts: list[str] = [
        "## Current Product Intent\n",
        json.dumps(intent, indent=2, default=str),
        f"\n## Empty Fields Needing Assumptions: {', '.join(empty_fields)}\n",
        "\nFill reasonable, domain-appropriate assumptions for these fields. "
        "Return ONLY the JSON array described in the output format.",
    ]

    user_prompt = "\n".join(parts)

    try:
        # No hardcoded default: explicit model → SIGNALOS_LLM_MODEL → discovery.
        use_model = resolve_model(model, provider_name)
        response_text, _, _ = provider.call(
            f"{_ASSUMPTIONS_SYSTEM_PROMPT}\n\n{user_prompt}",
            use_model,
        )
    except Exception:
        return None

    return _parse_assumptions_response(response_text, empty_fields)


def _parse_assumptions_response(
    response: str,
    valid_fields: list[str],
) -> list[dict[str, str]] | None:
    """Parse LLM response into a valid assumptions list, or None on failure."""
    if not response or not response.strip():
        return None

    text = response.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        import re
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, list):
        return None

    # Validate each assumption
    valid_assumptions: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        field = item.get("field", "")
        if field in valid_fields and "assumed_value" in item and "reason" in item:
            valid_assumptions.append({
                "field": str(field),
                "assumed_value": str(item["assumed_value"]),
                "reason": str(item["reason"]),
            })

    return valid_assumptions if valid_assumptions else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def record_assumptions(intent: dict[str, Any]) -> list[dict[str, str]]:
    """Return a list of safe-default assumptions for empty non-critical fields.

    Tries LLM agent first for domain-appropriate assumptions; falls back
    to static defaults if LLM is unavailable.

    Each entry is ``{"field": str, "assumed_value": str, "reason": str}``.
    Only fields that are currently empty/default get an assumption recorded.
    """
    # LLM agent reasons about domain-appropriate defaults
    if is_llm_available():
        llm_result = record_assumptions_with_llm(intent)
        if llm_result is not None:
            return llm_result

    # No LLM — return empty, not fake defaults that ignore the prompt.
    # "Assuming single-owner" for a team tool is wrong, not safe.
    return []


def _deterministic_assumptions(intent: dict[str, Any]) -> list[dict[str, str]]:
    """Static default assumptions -- no LLM, no network."""
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
