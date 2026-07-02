"""Founder policy controls (Wave 1.11)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product import policy as pol
from signalos_lib.product.policy import FounderPolicy

_ALL_GATES = ["qualification", "business-case", "go-no-go", "design", "build", "deploy", "launch"]


class PolicyTests(unittest.TestCase):
    def test_default_policy_is_valid(self):
        self.assertEqual(pol.validate_policy(FounderPolicy()), [])

    def test_unknown_gate_mode_flagged(self):
        p = FounderPolicy(gate_mode="whatever")
        self.assertTrue(pol.validate_policy(p))

    def test_negative_budget_flagged(self):
        self.assertTrue(pol.validate_policy(FounderPolicy(budget_cap_usd=-1)))

    def test_no_mode_ever_removes_a_floor_gate(self):
        for mode in ("strict", "standard", "fast-lane", "some-unknown-mode"):
            active = pol.gates_for_mode(mode, _ALL_GATES)
            for floor in pol.FLOOR_GATES:
                self.assertIn(floor, active, f"{mode} dropped floor gate {floor}")

    def test_fast_lane_keeps_only_floor_gates(self):
        active = pol.gates_for_mode("fast-lane", _ALL_GATES)
        self.assertNotIn("business-case", active)  # non-floor trimmed
        self.assertNotIn("build", active)
        self.assertIn("go-no-go", active)          # floor kept

    def test_unknown_mode_fails_closed_to_full_set(self):
        self.assertEqual(pol.gates_for_mode("bogus", _ALL_GATES), _ALL_GATES)


if __name__ == "__main__":
    unittest.main()
