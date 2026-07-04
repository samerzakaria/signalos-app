# Tests for match_blueprint confidence floor (Foundry domain-accuracy fix).
#
# Root bug: a single incidental keyword coincidence ("dashboard") snapped a
# personal-expense-tracker prompt onto the financial/revenue-metrics
# blueprint. The fuzzy passes (entity overlap, keyword overlap) must clear a
# minimum-confidence floor before returning a match; below the floor they
# return None so generation builds for the founder's actual stated domain
# rather than the nearest wrong-domain blueprint.
#
# Discipline: the expense-tracker prompt must NOT match a revenue/metrics
# dashboard blueprint. It should match a generic/task blueprint or None.

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.blueprints.registry import match_blueprint
from signalos_lib.product.intent import extract_product_intent, refine_intent_with_llm


# The founder's actual prompt from the task.
EXPENSE_PROMPT = (
    "A personal expense tracker: add expenses with title/amount/category, "
    "running total, per-category breakdown, mark reimbursed"
)


class TestExpenseTrackerNotFinancialDashboard:
    """The headline discipline case: expense tracker != revenue dashboard."""

    def _refined_intent(self) -> dict:
        # Realistic LLM-refined intent: clean entities plus a ux_surface whose
        # name happens to contain the word "dashboard" (the single incidental
        # keyword that used to force the financial-dashboard blueprint).
        return {
            "product_name": "Personal Expense Tracker",
            "product_type": "custom",
            "entities": ["Expense", "Category"],
            "primary_workflows": [
                "add expense",
                "view running total",
                "per-category breakdown",
                "mark reimbursed",
            ],
            "ux_surfaces": ["expense list", "category breakdown dashboard"],
            "_prompt": EXPENSE_PROMPT,
        }

    def test_refined_expense_intent_does_not_match_financial_dashboard(self):
        matched = match_blueprint(self._refined_intent())
        assert matched != "financial-dashboard", (
            "expense tracker was snapped onto the revenue/metrics dashboard "
            "blueprint on a single incidental keyword overlap"
        )

    def test_refined_expense_intent_matches_generic_or_none(self):
        # A generic/task blueprint or None is acceptable; a wrong-domain
        # (revenue/metrics) blueprint is not.
        matched = match_blueprint(self._refined_intent())
        assert matched in (None, "task-management"), (
            f"expected None or a generic blueprint, got {matched!r}"
        )

    def test_deterministic_expense_intent_does_not_match_financial_dashboard(self):
        # Even the raw deterministic extraction (before any LLM refinement)
        # must not resolve to the financial dashboard.
        intent = extract_product_intent(EXPENSE_PROMPT)
        assert match_blueprint(intent) != "financial-dashboard"


class TestConfidenceFloor:
    """The floor rejects weak (single-token) fuzzy overlaps."""

    def test_single_keyword_overlap_returns_none(self):
        # Only "dashboard" overlaps financial-dashboard's keywords -> below the
        # floor -> no match.
        intent = {
            "product_type": "custom",
            "entities": [],
            "ux_surfaces": ["some dashboard"],
        }
        assert match_blueprint(intent) is None

    def test_single_entity_overlap_returns_none(self):
        # Only "subscription" overlaps -> a single fuzzy entity hit is not
        # enough to commit to a blueprint's whole domain.
        intent = {
            "product_type": "custom",
            "entities": ["subscription"],
            "primary_workflows": [],
        }
        assert match_blueprint(intent) is None


class TestStrongOverlapStillMatches:
    """Regression guard: genuine multi-token matches must still resolve."""

    def test_strong_entity_overlap_matches_task(self):
        intent = {"product_type": "custom", "entities": ["task", "project"]}
        assert match_blueprint(intent) == "task-management"

    def test_strong_entity_overlap_matches_financial(self):
        intent = {"product_type": "custom", "entities": ["revenue", "churn"]}
        assert match_blueprint(intent) == "financial-dashboard"

    def test_strong_keyword_overlap_matches_task(self):
        intent = {
            "product_type": "custom",
            "entities": [],
            "primary_workflows": ["kanban board"],
        }
        assert match_blueprint(intent) == "task-management"

    def test_strong_keyword_overlap_matches_financial(self):
        intent = {
            "product_type": "custom",
            "entities": [],
            "primary_workflows": ["revenue dashboard"],
        }
        assert match_blueprint(intent) == "financial-dashboard"


class TestExactProductTypeUnaffected:
    """Pass 1 (exact product_type) must win immediately, floor notwithstanding."""

    def test_exact_task_type_still_wins(self):
        intent = {"product_type": "task-management", "entities": []}
        assert match_blueprint(intent) == "task-management"

    def test_exact_financial_type_still_wins(self):
        intent = {"product_type": "financial-dashboard", "entities": []}
        assert match_blueprint(intent) == "financial-dashboard"


class TestLlmProductTypeNeedsDomainEvidence:
    """LLM-refined product_type cannot override concrete extracted objects."""

    def test_llm_financial_type_does_not_override_expense_entities(self):
        intent = {
            "product_type": "financial-dashboard",
            "_product_type_source": "llm",
            "entities": [
                "Expense",
                "Category",
                "RunningTotal",
                "Reimbursement",
            ],
            "primary_workflows": [
                "add expense",
                "view running total",
                "mark reimbursed",
            ],
            "ux_surfaces": ["dashboard"],
        }

        assert match_blueprint(intent) != "financial-dashboard"

    def test_llm_financial_type_still_matches_when_entities_corroborate(self):
        intent = {
            "product_type": "financial-dashboard",
            "_product_type_source": "llm",
            "entities": ["Revenue", "Churn"],
            "primary_workflows": ["record revenue", "compute churn"],
        }

        assert match_blueprint(intent) == "financial-dashboard"

    def test_refinement_marks_product_type_changed_by_llm(self, monkeypatch):
        class FakeProvider:
            def call(self, _prompt, _model):
                return (
                    '{"entities":["Expense","Category"],'
                    '"product_type":"financial-dashboard"}',
                    1,
                    1,
                )

        import signalos_lib.harness as harness

        monkeypatch.setattr(harness, "_resolve_provider", lambda _name=None: FakeProvider())
        monkeypatch.setattr(harness, "resolve_model", lambda _model=None, _provider=None: "test-model")

        intent = {
            "product_type": "custom",
            "entities": ["Expense", "Category"],
            "target_users": [],
            "primary_workflows": ["add expense"],
            "ux_surfaces": ["dashboard"],
            "security_constraints": [],
            "audit_requirements": [],
        }

        refined = refine_intent_with_llm(intent, EXPENSE_PROMPT)

        assert refined["product_type"] == "financial-dashboard"
        assert refined["_product_type_source"] == "llm"
