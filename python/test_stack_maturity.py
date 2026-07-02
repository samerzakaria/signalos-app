"""Target-platform maturity tiers (Wave 1.5).

Every stack adapter declares a maturity tier (proven/supported/experimental) so
the founder is told, before committing, how production-ready a platform is.
Unknown adapters default to experimental -- honest under-promising, not silence.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product import stacks


class MaturityTierTests(unittest.TestCase):
    def test_every_registered_adapter_has_a_valid_tier(self):
        for adapter in stacks.list_adapters():
            self.assertIn("maturity", adapter, adapter)
            self.assertIn(adapter["maturity"], stacks.MATURITY_TIERS, adapter)

    def test_unknown_adapter_defaults_to_experimental(self):
        self.assertEqual(stacks.maturity_of("no-such-stack"), "experimental")

    def test_mainstream_stack_is_proven(self):
        self.assertEqual(stacks.maturity_of("react-vite"), "proven")

    def test_less_common_stack_is_not_overpromised(self):
        # rust-api is niche here -> must not claim "proven"
        self.assertNotEqual(stacks.maturity_of("rust-api"), "proven")


if __name__ == "__main__":
    unittest.main()
