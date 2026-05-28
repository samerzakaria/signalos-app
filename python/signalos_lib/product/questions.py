# signalos_lib/product/questions.py
# Phase P1 - Blocking clarifying questions for ambiguous intent
#
# LLM account-manager agent is FIRST choice; deterministic logic is FALLBACK.

from __future__ import annotations

__all__ = ["generate_questions", "generate_questions_with_llm"]

import json
import os
from typing import Any


# Fields that MUST have values before proceeding - blocking questions.
_CRITICAL_FIELDS: list[tuple[str, str]] = [
    ("product_name", "What is the name of the product you want to build?"),
    ("product_type", "What kind of product is this? (e.g. task manager, dashboard, e-commerce store)"),
    ("primary_workflows", "What are the main things users need to do in this product?"),
]

# Fields that are nice to know - non-blocking questions.
_OPTIONAL_FIELDS: list[tuple[str, str]] = [
    ("target_users", "Who are the primary users of this product?"),
    ("entities", "What information should users save, see, or manage?"),
    ("ux_surfaces", "Which main screens should users have? (for example: overview, detail page, form, board, or report)"),
    ("api_surfaces", "Should anything else need to connect to this product later?"),
    ("data_sources", "Where should the product get its information from?"),
    ("auth_requirements", "Who should be allowed to access or change things?"),
    ("deployment_intent", "Where do you expect people to use this product? (personal computer, team workspace, public website, or internal company app)"),
    ("stack_preferences", "Are there any company rules or existing tools SignalOS must respect?"),
]


# ---------------------------------------------------------------------------
# LLM-driven question generation (Account Manager agent)
# ---------------------------------------------------------------------------

_ACCOUNT_MANAGER_SYSTEM_PROMPT = """\
You are the highest-level domain analyst ever for this product's domain, the
greatest product analyst ever for this product's domain, with very deep domain
knowledge and hands-on operating experience, acting as the SignalOS Account
Manager agent in a SignalOS-governed software house.

Your job: look at the product intent extracted so far and generate smart,
domain-specific clarifying questions. You ask questions that a real account
manager would ask a client before kicking off development.

Apply world-class analyst judgment for product risk, implementation blockers,
user workflows, data ownership, domain regulations, incentives, operational
constraints, security, deployment, and acceptance evidence. SignalOS owns the
governance flow; you own the quality of discovery questions. Ask only questions
that improve delivery confidence or remove real ambiguity in this product
domain.

Rules:
- Look at what fields are empty or ambiguous in the intent
- Ask domain-specific questions, not generic ones
- The user may be non-technical. Ask in product and business language.
- Do NOT ask the user to choose frameworks, libraries, databases, hosting providers, CI tools, or implementation architecture.
- If a technical choice is needed, decide it yourself and phrase any approval in outcome terms.
- Classify each question as "blocking" (cannot proceed without answer) or non-blocking
- Blocking: product_name, product_type, primary_workflows
- Non-blocking: target_users, entities, ux_surfaces, api_surfaces, data_sources, etc.
- Do NOT ask about fields that already have good values
- Keep questions conversational and helpful

## Output Format

Return ONLY valid JSON (no markdown fencing, no explanation outside the JSON):
[
  {
    "field": "<intent field this question fills>",
    "question": "<the question to ask the user>",
    "blocking": true/false,
    "reason": "<why this question matters>"
  }
]
"""


def generate_questions_with_llm(
    intent: dict[str, Any],
    provider_name: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]] | None:
    """Use an LLM account-manager agent to generate clarifying questions.

    The agent receives:
    - Product intent (what was extracted so far)
    - Which fields are empty/ambiguous
    - The product constitution (what needs to be clear before building)

    Returns a list of questions with blocking/non-blocking classification,
    or None if LLM unavailable.
    """
    try:
        from signalos_lib.harness import _resolve_provider, DEFAULT_MODEL
    except Exception:
        return None

    try:
        provider = _resolve_provider(provider_name)
    except Exception:
        return None

    # Build the user prompt
    empty_fields = [f for f, _ in _CRITICAL_FIELDS + _OPTIONAL_FIELDS if _is_empty(intent.get(f))]

    parts: list[str] = [
        "## Current Product Intent\n",
        json.dumps(intent, indent=2, default=str),
        f"\n## Empty/Missing Fields: {', '.join(empty_fields)}\n" if empty_fields else "",
        "\nGenerate clarifying questions for this product. "
        "Return ONLY the JSON array described in the output format.",
    ]

    user_prompt = "\n".join(parts)
    use_model = model or DEFAULT_MODEL

    try:
        response_text, _, _ = provider.call(
            f"{_ACCOUNT_MANAGER_SYSTEM_PROMPT}\n\n{user_prompt}",
            use_model,
        )
    except Exception:
        return None

    return _parse_questions_response(response_text)


def _parse_questions_response(response: str) -> list[dict[str, Any]] | None:
    """Parse LLM response into a valid questions list, or None on failure."""
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
        # Try to find JSON array in the response
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

    # Validate each question has required keys
    valid_questions: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "field" in item and "question" in item and "blocking" in item:
            valid_questions.append({
                "field": str(item["field"]),
                "question": str(item["question"]),
                "blocking": bool(item["blocking"]),
                "reason": str(item.get("reason", "")),
            })

    return valid_questions if valid_questions else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_questions(intent: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate clarifying questions for empty or missing intent fields.

    Tries LLM account-manager agent first; falls back to deterministic
    pattern-based generation if LLM is unavailable.

    Returns a list of dicts with keys: field, question, blocking.
    Blocking questions target critical fields that should be answered
    before proceeding.  Non-blocking questions cover nice-to-have fields.
    """
    # Try LLM account-manager agent first
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("SIGNALOS_LLM_PROVIDER"):
        llm_result = generate_questions_with_llm(intent)
        if llm_result is not None:
            return llm_result

    # Fallback: deterministic (existing logic)
    return _deterministic_questions(intent)


def _deterministic_questions(intent: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic question generation -- no LLM, no network."""
    questions: list[dict[str, Any]] = []

    for field, question in _CRITICAL_FIELDS:
        value = intent.get(field)
        if _is_empty(value):
            questions.append({
                "field": field,
                "question": question,
                "blocking": True,
            })

    for field, question in _OPTIONAL_FIELDS:
        value = intent.get(field)
        if _is_empty(value):
            questions.append({
                "field": field,
                "question": question,
                "blocking": False,
            })

    return questions


def _is_empty(value: Any) -> bool:
    """Return True if a value is empty / missing / default."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value == "none"
    if isinstance(value, list):
        return len(value) == 0
    return False
