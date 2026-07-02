"""Task-class model routing (Wave 1.3)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import model_router as mr
from signalos_lib import second_opinion as so


class RouteTests(unittest.TestCase):
    def test_auto_defaults_to_primary(self):
        self.assertEqual(mr.route("research", "primary-model"), "primary-model")

    def test_pin_overrides_primary(self):
        self.assertEqual(
            mr.route("coding", "primary-model", pins={"coding": "coder-model"}),
            "coder-model",
        )

    def test_critique_routes_to_a_different_vendor(self):
        r = mr.route(
            "critique", "anthropic/claude",
            available=["anthropic/claude-2", "openai/gpt-4o"],
            author_model="anthropic/claude",
        )
        self.assertEqual(so.vendor_of(r), "openai")

    def test_critique_falls_back_to_primary_without_a_second_vendor(self):
        r = mr.route(
            "critique", "anthropic/claude",
            available=["anthropic/claude-2"],
            author_model="anthropic/claude",
        )
        self.assertEqual(r, "anthropic/claude")

    def test_pin_beats_cross_vendor_for_critique(self):
        r = mr.route(
            "critique", "anthropic/claude",
            pins={"critique": "pinned/model"},
            available=["openai/gpt-4o"],
            author_model="anthropic/claude",
        )
        self.assertEqual(r, "pinned/model")

    def test_task_classes_cover_the_roster(self):
        for cls in ("triage", "research", "strategy", "narrative", "coding", "critique"):
            self.assertIn(cls, mr.TASK_CLASSES)


if __name__ == "__main__":
    unittest.main()
