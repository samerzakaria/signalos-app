"""Failure-state incident cards (Wave 1.10)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product import incidents as inc


class IncidentCardTests(unittest.TestCase):
    def test_every_known_scenario_has_recovery_options(self):
        for scenario in inc.KNOWN_SCENARIOS:
            card = inc.build_incident_card(scenario)
            self.assertTrue(card.title)
            self.assertTrue(card.what_failed)
            self.assertTrue(card.recovery_options, scenario)

    def test_unknown_scenario_still_yields_a_card(self):
        # never a stack trace or silent stall
        card = inc.build_incident_card("meltdown-9000", detail="disk full")
        self.assertTrue(card.recovery_options)
        self.assertIn("disk full", card.what_failed)

    def test_detail_and_cost_are_carried(self):
        card = inc.build_incident_card("deploy-failure", detail="v3 partial", cost_so_far="$2.10")
        self.assertIn("v3 partial", card.what_failed)
        self.assertEqual(card.cost_so_far, "$2.10")
        self.assertEqual(card.to_dict()["type"], "incident")

    def test_present_day_scenarios_are_covered(self):
        for s in ("gate-deadlock", "integration-outage", "credential-revoked", "deploy-failure"):
            self.assertIn(s, inc.KNOWN_SCENARIOS)


if __name__ == "__main__":
    unittest.main()
