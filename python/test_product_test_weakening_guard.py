"""Tests for the test-weakening guard (test-automation anti-cheat layer).

The guard compares OLD vs NEW content of a JS/TS test file and decides whether
the edit weakened it. These tests pin every weakening signal (must flag) and,
just as importantly, the legitimate edits that must NOT flag -- a false
"weakened" verdict would block honest build work.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.test_weakening_guard import (  # noqa: E402
    detect_weakening,
    summarize,
)


class TestNonWeakeningEdits(unittest.TestCase):
    """Legitimate edits that must NOT be flagged."""

    def test_identical_source_is_not_weakened(self):
        src = (
            "it('adds', () => {\n"
            "  expect(add(2, 3)).toBe(5);\n"
            "});\n"
        )
        result = detect_weakening(src, src)
        self.assertFalse(result["weakened"])
        self.assertEqual(result["reasons"], [])

    def test_strengthening_weak_to_strong_is_not_flagged(self):
        old = (
            "it('returns a user', () => {\n"
            "  expect(getUser(1)).toBeDefined();\n"
            "});\n"
        )
        new = (
            "it('returns a user', () => {\n"
            "  expect(getUser(1)).toBe(expectedUser);\n"
            "});\n"
        )
        result = detect_weakening(old, new)
        self.assertFalse(result["weakened"], result["reasons"])

    def test_adding_tests_is_not_flagged(self):
        old = (
            "it('adds', () => {\n"
            "  expect(add(2, 3)).toBe(5);\n"
            "});\n"
        )
        new = (
            "it('adds', () => {\n"
            "  expect(add(2, 3)).toBe(5);\n"
            "});\n"
            "it('subtracts', () => {\n"
            "  expect(sub(5, 3)).toBe(2);\n"
            "});\n"
            "it('multiplies', () => {\n"
            "  expect(mul(2, 3)).toBe(6);\n"
            "});\n"
        )
        result = detect_weakening(old, new)
        self.assertFalse(result["weakened"], result["reasons"])

    def test_refactor_same_assertion_count_is_not_flagged(self):
        old = (
            "it('computes the total', () => {\n"
            "  const cart = buildCart();\n"
            "  expect(total(cart)).toBe(42);\n"
            "});\n"
        )
        new = (
            "it('computes the order total', () => {\n"
            "  const order = makeOrder();\n"
            "  const result = total(order);\n"
            "  expect(result).toBe(42);\n"
            "});\n"
        )
        result = detect_weakening(old, new)
        self.assertFalse(result["weakened"], result["reasons"])


class TestWeakeningSignals(unittest.TestCase):
    """Each weakening signal must flag with a clear reason."""

    def _reasons(self, old, new):
        result = detect_weakening(old, new)
        self.assertTrue(result["weakened"], "expected weakened=True")
        return " | ".join(result["reasons"]).lower()

    def test_test_count_dropped(self):
        old = (
            "it('a', () => { expect(a()).toBe(1); });\n"
            "it('b', () => { expect(b()).toBe(2); });\n"
            "it('c', () => { expect(c()).toBe(3); });\n"
        )
        new = (
            "it('a', () => { expect(a()).toBe(1); });\n"
            "it('b', () => { expect(b()).toBe(2); });\n"
        )
        self.assertIn("test count", self._reasons(old, new))

    def test_assertion_count_dropped(self):
        old = (
            "it('checks all fields', () => {\n"
            "  expect(user.name).toBe('Sam');\n"
            "  expect(user.age).toBe(30);\n"
            "  expect(user.active).toBe(true);\n"
            "});\n"
        )
        new = (
            "it('checks all fields', () => {\n"
            "  expect(user.name).toBe('Sam');\n"
            "});\n"
        )
        self.assertIn("assertion count", self._reasons(old, new))

    def test_skip_added(self):
        old = "it('validates input', () => { expect(validate(x)).toBe(true); });\n"
        new = "it.skip('validates input', () => { expect(validate(x)).toBe(true); });\n"
        self.assertIn("skip", self._reasons(old, new))

    def test_only_added(self):
        old = (
            "it('a', () => { expect(a()).toBe(1); });\n"
            "it('b', () => { expect(b()).toBe(2); });\n"
        )
        new = (
            "it.only('a', () => { expect(a()).toBe(1); });\n"
            "it('b', () => { expect(b()).toBe(2); });\n"
        )
        self.assertIn("skip/exclusion", self._reasons(old, new))

    def test_xit_added(self):
        old = "it('validates input', () => { expect(validate(x)).toBe(true); });\n"
        new = "xit('validates input', () => { expect(validate(x)).toBe(true); });\n"
        result = detect_weakening(old, new)
        self.assertTrue(result["weakened"])
        # Converting it( -> xit( keeps the declaration count but adds a skip.
        self.assertEqual(result["metrics"]["new"]["skips"], 1)

    def test_todo_added(self):
        old = "it('validates input', () => { expect(validate(x)).toBe(true); });\n"
        new = "it.todo('validates input');\n"
        self.assertIn("skip", self._reasons(old, new))

    def test_tautology_true_added(self):
        old = "it('does real work', () => { expect(compute()).toBe(42); });\n"
        new = "it('does real work', () => { expect(true).toBe(true); });\n"
        self.assertIn("tautolog", self._reasons(old, new))

    def test_tautology_number_added(self):
        old = "it('does real work', () => { expect(compute()).toBe(42); });\n"
        new = "it('does real work', () => { expect(1).toBe(1); });\n"
        self.assertIn("tautolog", self._reasons(old, new))

    def test_strong_replaced_by_weak_definedness(self):
        old = "it('sums', () => { expect(sum(2, 3)).toBe(5); });\n"
        new = "it('sums', () => { expect(sum(2, 3)).toBeDefined(); });\n"
        self.assertIn("weaker", self._reasons(old, new))

    def test_strong_replaced_by_not_throw(self):
        old = "it('parses', () => { expect(parse(input)).toEqual(expected); });\n"
        new = "it('parses', () => { expect(() => parse(input)).not.toThrow(); });\n"
        self.assertIn("weaker", self._reasons(old, new))

    def test_assertion_commented_out_line(self):
        old = (
            "it('validates the total', () => {\n"
            "  expect(total(cart)).toBe(42);\n"
            "});\n"
        )
        new = (
            "it('validates the total', () => {\n"
            "  // expect(total(cart)).toBe(42);\n"
            "});\n"
        )
        self.assertIn("comment", self._reasons(old, new))

    def test_assertion_commented_out_block(self):
        old = (
            "it('validates the total', () => {\n"
            "  expect(total(cart)).toBe(42);\n"
            "});\n"
        )
        new = (
            "it('validates the total', () => {\n"
            "  /* expect(total(cart)).toBe(42); */\n"
            "});\n"
        )
        self.assertIn("comment", self._reasons(old, new))

    def test_coverage_threshold_lowered(self):
        old = (
            "export default {\n"
            "  coverage: {\n"
            "    statements: 90,\n"
            "    branches: 85,\n"
            "  },\n"
            "};\n"
        )
        new = (
            "export default {\n"
            "  coverage: {\n"
            "    statements: 70,\n"
            "    branches: 85,\n"
            "  },\n"
            "};\n"
        )
        reasons = self._reasons(old, new)
        self.assertIn("threshold", reasons)
        self.assertIn("statements", reasons)

    def test_coverage_threshold_removed(self):
        old = "export default { coverage: { lines: 80 } };\n"
        new = "export default { coverage: {} };\n"
        reasons = self._reasons(old, new)
        self.assertIn("removed", reasons)


class TestRealFailingStubCase(unittest.TestCase):
    """The exact real cases called out in the task."""

    def test_failing_stub_replaced_by_real_assertions_is_strengthening(self):
        # expect(true).toBe(false) is a deliberately-failing stub. Replacing it
        # with real assertions is STRENGTHENING and must be allowed.
        old = (
            "it('computes the invoice total', () => {\n"
            "  expect(true).toBe(false); // TODO: implement\n"
            "});\n"
        )
        new = (
            "it('computes the invoice total', () => {\n"
            "  const invoice = buildInvoice(lineItems);\n"
            "  expect(invoice.subtotal).toBe(100);\n"
            "  expect(invoice.tax).toBe(8);\n"
            "  expect(invoice.total).toBe(108);\n"
            "});\n"
        )
        result = detect_weakening(old, new)
        self.assertFalse(result["weakened"], result["reasons"])
        # The stub was not counted as a tautology.
        self.assertEqual(result["metrics"]["old"]["tautologies"], 0)

    def test_real_assertion_replaced_by_tautology_is_flagged(self):
        old = (
            "it('computes the invoice total', () => {\n"
            "  const invoice = buildInvoice(lineItems);\n"
            "  expect(invoice.total).toBe(108);\n"
            "});\n"
        )
        new = (
            "it('computes the invoice total', () => {\n"
            "  expect(true).toBe(true);\n"
            "});\n"
        )
        result = detect_weakening(old, new)
        self.assertTrue(result["weakened"])
        self.assertTrue(
            any("tautolog" in reason.lower() for reason in result["reasons"]),
            result["reasons"],
        )

    def test_failing_stub_to_real_does_not_regress_any_metric(self):
        # Guard against a tautology-count off-by-one: true->false must never be
        # read as a tautology in either direction.
        stub = "it('x', () => { expect(true).toBe(false); });\n"
        result = detect_weakening(stub, stub)
        self.assertEqual(result["metrics"]["old"]["tautologies"], 0)
        self.assertFalse(result["weakened"])


class TestApiShapeAndSummary(unittest.TestCase):
    def test_return_shape(self):
        result = detect_weakening("it('a', () => { expect(a).toBe(1); });", "")
        self.assertIn("weakened", result)
        self.assertIn("reasons", result)
        self.assertIn("metrics", result)
        self.assertIsInstance(result["weakened"], bool)
        self.assertIsInstance(result["reasons"], list)
        self.assertIn("old", result["metrics"])
        self.assertIn("new", result["metrics"])

    def test_summarize_reports_ok(self):
        src = "it('a', () => { expect(a()).toBe(1); });\n"
        text = summarize(src, src)
        self.assertTrue(text.startswith("OK:"), text)

    def test_summarize_reports_weakened(self):
        old = "it('a', () => { expect(compute()).toBe(42); });\n"
        new = "it('a', () => { expect(true).toBe(true); });\n"
        text = summarize(old, new)
        self.assertTrue(text.startswith("WEAKENED:"), text)
        self.assertIn("tautolog", text.lower())


if __name__ == "__main__":
    unittest.main()
