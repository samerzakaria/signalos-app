# signalos_lib/product/questions.py
# Phase P1 - Blocking clarifying questions for ambiguous intent

from __future__ import annotations

__all__ = ["generate_questions"]

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
    ("entities", "What are the main data objects? (e.g. projects, tasks, users, invoices)"),
    ("ux_surfaces", "What UI views do you envision? (e.g. dashboard, forms, tables, kanban board)"),
    ("api_surfaces", "Does this product need an API? If so, what kind? (REST, GraphQL, WebSocket)"),
    ("data_sources", "Where does the data come from? (database, CSV, external API)"),
    ("auth_requirements", "Does this product require authentication or authorization?"),
    ("deployment_intent", "How will this product be deployed? (Docker, cloud, self-hosted)"),
    ("stack_preferences", "Do you have any technology preferences? (React, Python, etc.)"),
]


def generate_questions(intent: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate clarifying questions for empty or missing intent fields.

    Returns a list of dicts with keys: field, question, blocking.
    Blocking questions target critical fields that should be answered
    before proceeding.  Non-blocking questions cover nice-to-have fields.
    """
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
