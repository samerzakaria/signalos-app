# test_blueprint_ux_surface_leak.py
# #35: an LLM that mislabels product_type MUST NOT snap a non-finance product
# onto financial-dashboard via generic UX-surface keywords (dashboard/chart) or
# its own label. Only real domain evidence (entities/workflows/name) may match.
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.blueprints.registry import (
    _extract_intent_keywords,
    match_blueprint,
)

# The exact real repro: claude-sonnet-4-5 refined an expense tracker but
# mislabeled product_type=financial-dashboard and set ux_surfaces=[...dashboard,
# chart]. Entities/workflows are correctly expense-domain.
_EXPENSE = {
    "product_name": "expense-tracker",
    "product_type": "financial-dashboard",
    "_product_type_source": "llm",
    "_deterministic_entities": [
        "Title", "Amount", "Category", "RunningTotal",
        "PerCategoryBreakdown", "MarkReimbursed",
    ],
    "entities": ["Expense", "Category", "Reimbursement"],
    "primary_workflows": [
        "add_expense", "categorize_expense", "mark_reimbursed",
        "view_running_total", "view_category_breakdown",
    ],
    "ux_surfaces": ["form", "dashboard", "list", "chart"],
    "target_users": ["individual user", "personal finance user"],
}

_FINANCE = {
    "product_name": "saas-metrics",
    "product_type": "financial-dashboard",
    "_product_type_source": "llm",
    "_deterministic_entities": ["Revenue", "Churn", "Metric", "Subscription"],
    "entities": ["Revenue", "Churn", "Metric", "Subscription"],
    "primary_workflows": ["track_mrr", "track_churn"],
    "ux_surfaces": ["dashboard", "chart"],
    "target_users": ["founder"],
}


def test_expense_tracker_does_not_snap_to_financial_dashboard():
    assert match_blueprint(_EXPENSE) != "financial-dashboard"
    assert match_blueprint(_EXPENSE) is None


def test_real_finance_product_still_matches_via_entities():
    assert match_blueprint(_FINANCE) == "financial-dashboard"


def test_keywords_exclude_ux_surfaces_and_product_type():
    kw = _extract_intent_keywords(_EXPENSE)
    # generic UI surface words must not become domain keywords
    assert "dashboard" not in kw
    assert "chart" not in kw
    # the circular product_type label must not corroborate itself
    assert "financialdashboard" not in kw
    # real domain evidence is still present (product_name + workflow tokens)
    assert "expensetracker" in kw  # product_name "expense-tracker" (one token)
    assert "addexpense" in kw      # workflow "add_expense" (one token)
