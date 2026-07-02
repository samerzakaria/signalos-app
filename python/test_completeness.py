"""Completeness-rubric inversion pass (Wave 1.9)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product import completeness as comp


def _pad(text: str) -> str:
    # ensure we clear the substantial-artifact threshold
    return text + ("\nlorem ipsum context. " * 30)


class CompletenessTests(unittest.TestCase):
    def test_stub_is_not_flagged(self):
        self.assertEqual(comp.completeness_findings("too short"), [])

    def test_artifact_addressing_everything_has_no_findings(self):
        rich = _pad(
            "The user signs in with an account (identity). Roles and permissions "
            "enforce isolation per tenant. Onboarding covers the empty state on "
            "first run. Data retention, deletion and export are defined. Failure "
            "handling, timeout, retry and rollback recovery are specified."
        )
        self.assertEqual(comp.completeness_findings(rich), [])

    def test_missing_data_lifecycle_is_flagged(self):
        text = _pad(
            "The user signs in with an account. Roles and permissions enforce "
            "isolation. Onboarding covers the empty state. Failures have recovery "
            "and rollback with retry on timeout."
        )
        concerns = {f["concern"] for f in comp.completeness_findings(text)}
        self.assertIn("data lifecycle", concerns)

    def test_billing_is_never_a_concern(self):
        # money/billing is intentionally out of scope so it never cries wolf
        text = _pad("A plain artifact with no billing or payment discussion at all.")
        concerns = {f["concern"] for f in comp.completeness_findings(text)}
        self.assertNotIn("money & billing", concerns)


if __name__ == "__main__":
    unittest.main()
