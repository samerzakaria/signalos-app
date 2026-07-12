# test_validation_frozen_tests.py
# FIX 3 (validation half): the G4 verification contract must confirm the FROZEN
# plan tests were actually collected/executed. A model that neuters the test
# command (`"test": "exit 0"`) or excludes the frozen tests from discovery
# leaves them ABSENT from the runner output -- verification must FAIL, never
# report a green build.

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product import validation
from signalos_lib.product.validation import (
    run_validation,
    verify_frozen_tests_collected,
)

_FROZEN = [
    "core/execution/tests/skeletons/wave-1/T1.1_store.test.ts",
    "core/execution/tests/skeletons/wave-1/T1.2_view.test.ts",
]


class TestVerifyFrozenTestsCollected(unittest.TestCase):
    def test_all_collected_returns_empty(self):
        out = ("RUN v1.6\n"
               " ✓ core/execution/tests/skeletons/wave-1/T1.1_store.test.ts (3)\n"
               " ✓ core/execution/tests/skeletons/wave-1/T1.2_view.test.ts (2)\n"
               "Test Files 2 passed\n")
        self.assertEqual(verify_frozen_tests_collected(out, _FROZEN), [])

    def test_excluded_test_is_reported_missing(self):
        # Only the first frozen test ran; the second was excluded -> reported.
        out = " ✓ core/execution/tests/skeletons/wave-1/T1.1_store.test.ts (3)\n"
        self.assertEqual(
            verify_frozen_tests_collected(out, _FROZEN),
            ["core/execution/tests/skeletons/wave-1/T1.2_view.test.ts"])

    def test_neutered_command_empty_output_all_missing(self):
        # `"test": "exit 0"` -> no test files printed -> every frozen test missing.
        self.assertEqual(verify_frozen_tests_collected("", _FROZEN), _FROZEN)

    def test_basename_match_is_accepted(self):
        out = "PASS T1.1_store.test.ts\nPASS T1.2_view.test.ts\n"
        self.assertEqual(verify_frozen_tests_collected(out, _FROZEN), [])

    def test_no_frozen_tests_is_noop(self):
        self.assertEqual(verify_frozen_tests_collected("anything", []), [])


class TestRunValidationFrozenGate(unittest.TestCase):
    def _plan(self):
        return {
            "profile": "generic",
            "install": [], "build": ["__build__"], "test": ["__test__"],
            "lint": [], "qa": [], "e2e": [], "runtime_smoke": [],
            "ux_smoke": [], "security": [],
            "can_validate_build": True, "can_validate_tests": True,
        }

    def _patched(self, test_output):
        def fake_run_commands(repo_root, cmds):
            if cmds == ["__test__"]:
                return {"status": "passed", "output": test_output, "duration_s": 0.1}
            return {"status": "passed", "output": "build ok", "duration_s": 0.1}
        return patch.object(validation, "_run_commands", side_effect=fake_run_commands)

    def test_neutered_run_fails_verification_and_cannot_close(self):
        # test command "passes" but the frozen tests never appear -> FAIL.
        with tempfile.TemporaryDirectory() as d, self._patched("Tests: exit 0\n"):
            result = run_validation(Path(d), self._plan(), frozen_tests=_FROZEN)
        self.assertEqual(result["results"]["test"]["status"], "failed")
        self.assertFalse(result["can_close_delivery"])
        self.assertEqual(result["frozen_tests_uncollected"], _FROZEN)
        self.assertIn("FROZEN TEST VERIFICATION FAILED",
                      result["results"]["test"]["output"])

    def test_frozen_tests_collected_run_can_close(self):
        good = (" ✓ core/execution/tests/skeletons/wave-1/T1.1_store.test.ts\n"
                " ✓ core/execution/tests/skeletons/wave-1/T1.2_view.test.ts\n")
        with tempfile.TemporaryDirectory() as d, self._patched(good):
            result = run_validation(Path(d), self._plan(), frozen_tests=_FROZEN)
        self.assertEqual(result["results"]["test"]["status"], "passed")
        self.assertTrue(result["can_close_delivery"])
        self.assertEqual(result["frozen_tests_uncollected"], [])

    def test_without_frozen_tests_behavior_is_unchanged(self):
        # Byte-identical default: no frozen_tests -> no collection gate.
        with tempfile.TemporaryDirectory() as d, self._patched("anything"):
            result = run_validation(Path(d), self._plan())
        self.assertEqual(result["results"]["test"]["status"], "passed")
        self.assertTrue(result["can_close_delivery"])
        self.assertEqual(result["frozen_tests_uncollected"], [])


if __name__ == "__main__":
    unittest.main()
