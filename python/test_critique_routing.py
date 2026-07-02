"""Cross-vendor critique routing (Wave 1.4).

A critique/second-opinion must run on a different model *vendor* than the
artifact's author whenever a second vendor is configured -- a same-vendor "fresh
session" still shares the author's blind spots. Vendor is resolved from a
structural prefix or an explicit provider-config map, never guessed from the
model name (families go stale).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import second_opinion as so


class VendorOfTests(unittest.TestCase):
    def test_structural_prefix_wins(self):
        self.assertEqual(so.vendor_of("gemini/gemini-2.5-pro"), "gemini")
        self.assertEqual(so.vendor_of("ollama/llama3.1"), "ollama")
        self.assertEqual(so.vendor_of("openai/gpt-4o"), "openai")

    def test_default_used_when_no_prefix(self):
        # No name guessing: an unprefixed model resolves via the configured
        # provider, not by inspecting the model string.
        self.assertEqual(so.vendor_of("gpt-4o", default="openai"), "openai")
        self.assertEqual(so.vendor_of("some-model"), "unknown")


class ChooseReviewerTests(unittest.TestCase):
    def test_picks_different_vendor_via_config_map(self):
        r = so.choose_cross_vendor_reviewer(
            "model-a", ["model-b", "model-c"],
            vendors={"model-a": "anthropic", "model-b": "anthropic", "model-c": "openai"},
        )
        self.assertEqual(r, "model-c")

    def test_none_when_only_same_vendor(self):
        # FR-10.3: no second vendor configured -> no cross-vendor reviewer
        r = so.choose_cross_vendor_reviewer(
            "model-a", ["model-b"],
            vendors={"model-a": "anthropic", "model-b": "anthropic"},
        )
        self.assertIsNone(r)

    def test_prefix_based_when_no_config_map(self):
        r = so.choose_cross_vendor_reviewer(
            "anthropic/claude", ["anthropic/claude-2", "openai/gpt-4o"])
        self.assertEqual(r, "openai/gpt-4o")


class CheckWiredCapabilityTests(unittest.TestCase):
    """0.4: 'wired' must mean the capability is importable + callable, not that
    files sit at some (here, wrong) path."""

    def test_reports_capability_present(self):
        ok, msg = so.check_second_opinion_wired(Path("."))
        self.assertTrue(ok, msg)
        self.assertIn("callable", msg)


if __name__ == "__main__":
    unittest.main()
