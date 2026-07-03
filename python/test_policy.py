"""Founder policy controls (Wave 1.11)."""
from __future__ import annotations

import sys
import tempfile
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


class PolicyPersistenceTests(unittest.TestCase):
    """1.11: the founder-facing UI reads/writes policy through these functions."""

    def test_load_missing_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            loaded = pol.load_policy(Path(d))
            self.assertEqual(loaded.to_dict(), FounderPolicy().to_dict())

    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = FounderPolicy(gate_mode="fast-lane", research_depth="deep",
                              budget_cap_usd=25.0, standards_profile="strict-typescript")
            pol.save_policy(root, p)
            loaded = pol.load_policy(root)
            self.assertEqual(loaded.to_dict(), p.to_dict())

    def test_save_rejects_invalid_policy(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                pol.save_policy(Path(d), FounderPolicy(gate_mode="not-a-real-mode"))

    def test_load_corrupt_file_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / ".signalos" / "product" / "POLICY.json"
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")
            loaded = pol.load_policy(root)
            self.assertEqual(loaded.to_dict(), FounderPolicy().to_dict())


if __name__ == "__main__":
    unittest.main()
