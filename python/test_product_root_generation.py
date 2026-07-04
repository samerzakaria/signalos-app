# Tests for the root generation defect (#9) + Mantine dep consistency (#27).
#
# #9 ROOT: correctly-extracted domain entities/workflows must WIN over a
# mis-classified product_type.  The prior confidence-floor fix left two escape
# hatches (see test_blueprint_match_threshold.py) that the LIVE pipeline hit:
#
#   Escape A -- the LLM refinement pass overwrites the extracted entities AND
#     the product_type in the SAME call.  The "corroboration" check then
#     compared the LLM's finance entities against the finance blueprint and
#     declared the LLM label "trustworthy" (self-fulfilling).  The corroboration
#     must come from an INDEPENDENT (deterministic) source, not from the same
#     untrusted LLM step.
#
#   Escape B -- Pass-1 exact match bypasses the floor entirely whenever the
#     `_product_type_source == "llm"` marker is absent (e.g. persisted/reloaded
#     intent, injected type).  An exact type match with concrete domain evidence
#     that DISAGREES with the blueprint must still require corroboration.
#
# The bar (from the task): prompt "personal expense tracker ... add expenses
# ... running total ... mark reimbursed" must generate components for
# Expense/Category with add/list/total/reimburse -- NOT RevenueChart /
# ChurnChart / RunwayGauge.
#
# #27: the generated react-vite package.json must pin ALL @mantine/* on ONE
# consistent version, and @mantine/charts must have its recharts peer satisfied.

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.blueprints.registry import (
    apply_blueprint_intent_defaults,
    load_blueprint,
    match_blueprint,
)
from signalos_lib.product.design import (
    build_design_system,
    get_design_dependencies,
)
from signalos_lib.product.generation import build_generation_packet
from signalos_lib.product.intent import (
    extract_product_intent,
    refine_intent_with_llm,
)


EXPENSE_PROMPT = (
    "A personal expense tracker: add expenses with title/amount/category, "
    "running total, per-category breakdown, mark reimbursed"
)

_FINANCE_ENTITIES = ["Metric", "Revenue", "Churn", "CashRunway", "Subscription"]


# ---------------------------------------------------------------------------
# #9 -- Escape A: LLM co-mislabels product_type AND entities in one call
# ---------------------------------------------------------------------------

class TestEscapeALlmCoMislabel:
    """The LLM must not be its own corroboration for a wrong type label."""

    def test_llm_finance_type_and_finance_entities_do_not_match_when_deterministic_domain_is_expense(self):
        # This is the LIVE pipeline state: the deterministic extractor found
        # Expense/Category; the LLM rewrote entities to the finance domain AND
        # set product_type=financial-dashboard in the SAME response.  The
        # deterministic entities are carried as independent evidence.
        intent = {
            "product_type": "financial-dashboard",
            "_product_type_source": "llm",
            "entities": list(_FINANCE_ENTITIES),
            "_deterministic_entities": ["Expense", "Category"],
            "primary_workflows": ["add expense", "mark reimbursed"],
            "ux_surfaces": ["dashboard"],
        }
        assert match_blueprint(intent) != "financial-dashboard", (
            "the LLM's own rewritten entities were used to corroborate the "
            "LLM's own wrong type label (self-fulfilling)"
        )

    def test_llm_finance_type_still_matches_when_deterministic_domain_agrees(self):
        # Genuine revenue dashboard: deterministic evidence also finance ->
        # the LLM label is corroborated by an independent source -> match.
        intent = {
            "product_type": "financial-dashboard",
            "_product_type_source": "llm",
            "entities": ["Revenue", "Churn"],
            "_deterministic_entities": ["Revenue", "Churn"],
            "primary_workflows": ["record revenue", "compute churn"],
        }
        assert match_blueprint(intent) == "financial-dashboard"


# ---------------------------------------------------------------------------
# #9 -- Escape B: missing _product_type_source marker
# ---------------------------------------------------------------------------

class TestEscapeBMissingMarker:
    """An exact type match whose domain evidence disagrees still needs corroboration."""

    def test_finance_type_without_marker_does_not_override_expense_entities(self):
        # No provenance marker (e.g. reloaded from INTENT.json).  The concrete
        # entities are the expense domain and disagree with the finance
        # blueprint -> must not snap onto financial-dashboard.
        intent = {
            "product_type": "financial-dashboard",
            "entities": ["Expense", "Category", "RunningTotal", "Reimbursement"],
            "primary_workflows": ["add expense", "mark reimbursed"],
            "ux_surfaces": ["dashboard"],
        }
        assert match_blueprint(intent) != "financial-dashboard"

    def test_finance_type_without_marker_still_matches_with_agreeing_entities(self):
        intent = {
            "product_type": "financial-dashboard",
            "entities": ["Revenue", "Churn", "Metric"],
        }
        assert match_blueprint(intent) == "financial-dashboard"

    def test_bare_finance_type_no_entities_still_matches(self):
        # No concrete domain evidence at all -> the deterministic/caller label
        # is trusted (backwards-compatible with the existing exact-type tests).
        intent = {"product_type": "financial-dashboard", "entities": []}
        assert match_blueprint(intent) == "financial-dashboard"


# ---------------------------------------------------------------------------
# #9 -- refine_intent_with_llm preserves deterministic entities
# ---------------------------------------------------------------------------

class TestRefinePreservesDeterministicEntities:
    def test_refine_records_deterministic_entities_when_llm_changes_type(self, monkeypatch=None):
        # Manual monkeypatch (unittest-compatible: no pytest fixture needed).
        import signalos_lib.harness as harness

        class FakeProvider:
            def call(self, _prompt, _model):
                return (
                    json.dumps({
                        "entities": _FINANCE_ENTITIES,
                        "product_type": "financial-dashboard",
                    }),
                    1,
                    1,
                )

        orig_provider = harness._resolve_provider
        orig_model = harness.resolve_model
        harness._resolve_provider = lambda _name=None: FakeProvider()
        harness.resolve_model = lambda _model=None, _provider=None: "test-model"
        try:
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
        finally:
            harness._resolve_provider = orig_provider
            harness.resolve_model = orig_model

        assert refined["_product_type_source"] == "llm"
        # The independent deterministic entities are preserved so a later
        # corroboration check has a source that is NOT the LLM output.
        assert "_deterministic_entities" in refined
        assert [e.lower() for e in refined["_deterministic_entities"]] == [
            "expense", "category",
        ]

    def test_refined_expense_intent_does_not_match_financial_dashboard_end_to_end(self):
        import signalos_lib.harness as harness

        class FakeProvider:
            def call(self, _prompt, _model):
                return (
                    json.dumps({
                        "entities": _FINANCE_ENTITIES,
                        "product_type": "financial-dashboard",
                    }),
                    1,
                    1,
                )

        orig_provider = harness._resolve_provider
        orig_model = harness.resolve_model
        harness._resolve_provider = lambda _name=None: FakeProvider()
        harness.resolve_model = lambda _model=None, _provider=None: "test-model"
        try:
            intent = extract_product_intent(EXPENSE_PROMPT)
            refined = refine_intent_with_llm(intent, EXPENSE_PROMPT)
        finally:
            harness._resolve_provider = orig_provider
            harness.resolve_model = orig_model

        # Even though the LLM co-mislabelled type AND entities, matching must
        # fall back to a non-finance blueprint (or None) so generation builds
        # the founder's actual expense domain.
        assert match_blueprint(refined) != "financial-dashboard"


# ---------------------------------------------------------------------------
# #9 -- generation builds the RIGHT product (the headline bar)
# ---------------------------------------------------------------------------

class TestGenerationBuildsExpenseNotFinance:
    def _expense_intent(self) -> dict:
        return {
            "product_name": "Personal Expense Tracker",
            "product_type": "custom",
            "entities": ["Expense", "Category"],
            "primary_workflows": [
                "add_expense",
                "view_running_total",
                "mark_reimbursed",
            ],
            "ux_surfaces": ["list", "form", "table"],
        }

    def test_expense_intent_matched_blueprint_is_not_financial_dashboard(self):
        intent = self._expense_intent()
        matched = match_blueprint(intent)
        assert matched != "financial-dashboard"

    def test_generation_packet_has_expense_entities_not_finance(self):
        intent = self._expense_intent()
        matched = match_blueprint(intent)
        bp = load_blueprint(matched) if matched else None
        # Merge (extracted entities take precedence) rather than override.
        intent = apply_blueprint_intent_defaults(intent, bp)

        with tempfile.TemporaryDirectory() as tmp:
            packet = build_generation_packet(
                Path(tmp), intent, bp, "react-vite",
                design=build_design_system(intent, "react-vite"),
            )

        entity_names = {e.get("name") for e in packet.get("entities", [])}
        assert "Expense" in entity_names
        assert "Category" in entity_names
        # The finance domain must not have replaced the founder's entities.
        for finance in ("Revenue", "Churn", "CashRunway", "Metric"):
            assert finance not in entity_names, (
                f"blueprint replaced founder entity with finance {finance!r}"
            )

        # Component file specs must be for the expense domain, not finance UI.
        comp_paths = [
            s["path"] for s in packet.get("file_specs", [])
            if s.get("kind") in ("source", "test")
            and "/components/" in s.get("path", "")
        ]
        joined = " ".join(comp_paths)
        for finance_comp in ("RevenueChart", "ChurnChart", "RunwayGauge"):
            assert finance_comp not in joined, (
                f"generation emitted finance component {finance_comp!r} for an "
                f"expense tracker"
            )


# ---------------------------------------------------------------------------
# #27 -- Mantine dependency consistency in the generated package.json
# ---------------------------------------------------------------------------

def _mantine_intent() -> dict:
    # Entity-rich, form/table surfaces -> deterministic design selects Mantine.
    return {
        "product_name": "Records App",
        "product_type": "custom",
        "entities": ["Patient", "Note", "LabResult", "Prescription"],
        "primary_workflows": ["intake", "schedule"],
        "ux_surfaces": ["table", "form", "detail"],
    }


class TestMantineDependencyConsistency:
    def test_all_mantine_packages_pinned_to_one_version(self):
        design = build_design_system(_mantine_intent(), "react-vite")
        assert design["ui_library"]["name"] == "@mantine/core"
        deps = get_design_dependencies(design)
        mantine = {
            name: ver for name, ver in deps.items()
            if name.startswith("@mantine/")
        }
        # All five sub-packages present.
        for sub in ("core", "hooks", "form", "dates", "charts"):
            assert f"@mantine/{sub}" in mantine, f"missing @mantine/{sub}"
        # Exactly one version across ALL @mantine/* (no core-newer-than-hooks skew).
        assert len(set(mantine.values())) == 1, (
            f"skewed @mantine/* versions: {mantine}"
        )
        # Exact pin, no caret range.
        assert not next(iter(mantine.values())).startswith("^")

    def test_mantine_charts_has_recharts_peer_satisfied(self):
        design = build_design_system(_mantine_intent(), "react-vite")
        deps = get_design_dependencies(design)
        assert "@mantine/charts" in deps
        # @mantine/charts requires recharts as a peer.  A Mantine dashboard must
        # ship recharts so the peer resolves.
        assert "recharts" in deps, (
            "@mantine/charts shipped without its required recharts peer"
        )

    def test_generated_package_json_has_consistent_mantine(self):
        # Full generator path: scaffold merges design deps into package.json.
        from signalos_lib.product.stacks import get_adapter

        design = build_design_system(_mantine_intent(), "react-vite")
        deps = get_design_dependencies(design)
        adapter = get_adapter("react-vite")

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            adapter.scaffold(repo_root, _mantine_intent(), dependencies=deps)
            pkg = json.loads((repo_root / "package.json").read_text(encoding="utf-8"))

        all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        mantine = {
            name: ver for name, ver in all_deps.items()
            if name.startswith("@mantine/")
        }
        assert mantine, "no @mantine/* deps landed in package.json"
        assert len(set(mantine.values())) == 1, (
            f"package.json has skewed @mantine/* versions: {mantine}"
        )
        # @mantine/charts peer present in the shipped package.json.
        assert "recharts" in all_deps
