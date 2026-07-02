"""Belief auto-resolution loop-closer (C-bridge / Wave 3.2)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product import belief_resolution as br

_THRESH = {"activation_rate": 0.30, "week1_retention": 0.20}


class BeliefResolutionTests(unittest.TestCase):
    def test_refuses_when_metrics_unsigned(self):
        r = br.resolve_belief({"activation_rate": 0.9}, _THRESH, thresholds_signed=False)
        self.assertEqual(r, br.UNSIGNED)

    def test_pending_when_no_signals_yet(self):
        r = br.resolve_belief({}, _THRESH, thresholds_signed=True)
        self.assertEqual(r, br.PENDING)

    def test_keep_when_all_metrics_met(self):
        signals = {"activation_rate": 0.35, "week1_retention": 0.25}
        self.assertEqual(br.resolve_belief(signals, _THRESH, thresholds_signed=True), br.KEEP)

    def test_refute_when_no_metrics_met(self):
        signals = {"activation_rate": 0.10, "week1_retention": 0.05}
        self.assertEqual(br.resolve_belief(signals, _THRESH, thresholds_signed=True), br.REFUTE)

    def test_iterate_when_partially_met(self):
        signals = {"activation_rate": 0.35, "week1_retention": 0.05}
        self.assertEqual(br.resolve_belief(signals, _THRESH, thresholds_signed=True), br.ITERATE)


if __name__ == "__main__":
    unittest.main()
