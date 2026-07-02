"""Budget hard-stop + 90% auto-pause (Wave 1.2).

A run budget must be a live, fail-closed control: 'halt' at/over the cap and
'warn' at/over the pause threshold (default 90%), not just a bill discovered
afterward. These tests pin the decision function and its surfacing in the cost
report that CI and the app consult.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.commands import cost


class BudgetStatusTests(unittest.TestCase):
    def test_ok_below_threshold(self):
        self.assertEqual(cost.budget_status(Decimal("0.50"), Decimal("1.00")), "ok")

    def test_warn_at_pause_threshold(self):
        self.assertEqual(cost.budget_status(Decimal("0.90"), Decimal("1.00")), "warn")
        self.assertEqual(cost.budget_status(Decimal("0.95"), Decimal("1.00")), "warn")

    def test_halt_at_or_over_cap(self):
        self.assertEqual(cost.budget_status(Decimal("1.00"), Decimal("1.00")), "halt")
        self.assertEqual(cost.budget_status(Decimal("1.50"), Decimal("1.00")), "halt")

    def test_zero_cap_permits_no_spend(self):
        self.assertEqual(cost.budget_status(Decimal("0"), Decimal("0")), "halt")

    def test_unpriced_when_no_cap(self):
        self.assertEqual(cost.budget_status(Decimal("5"), None), "unpriced")


class CostReportBudgetStateTests(unittest.TestCase):
    def _report(self, cost_usd: str, budget: str) -> dict:
        d = tempfile.mkdtemp()
        root = Path(d)
        led = root / ".signalos" / "product" / "AI_USAGE.jsonl"
        led.parent.mkdir(parents=True, exist_ok=True)
        led.write_text(
            json.dumps({"cost_usd": cost_usd, "total_tokens": 100,
                        "provider": "x", "model": "y"}) + "\n",
            encoding="utf-8",
        )
        return cost.build_cost_report(root, budget_usd=budget, write_evidence=False)

    def test_report_halts_over_cap(self):
        self.assertEqual(self._report("1.50", "1.00")["budget_state"], "halt")

    def test_report_warns_near_cap(self):
        self.assertEqual(self._report("0.95", "1.00")["budget_state"], "warn")

    def test_report_ok_under_cap(self):
        self.assertEqual(self._report("0.50", "1.00")["budget_state"], "ok")


if __name__ == "__main__":
    unittest.main()
