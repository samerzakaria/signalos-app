# test_review_gate.py
# #21: the Build -> Test -> REVIEW gate. Deterministic verdict on spec coverage,
# test evidence, and build correctness; governed by gate-compliance mode
# (strict blocks closure, warn records). Render-only smoke tests are accepted
# (the deterministic local path ships them) but a missing/empty test is a block.
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.review_gate import run_review_gate


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


_GOOD_TEST = (
    "import { render, screen } from '@testing-library/react';\n"
    "import { expect, test } from 'vitest';\n"
    "import Expense from './Expense';\n"
    "test('renders', () => { render(<Expense/>); expect(screen).toBeDefined(); });\n"
)
_COMPONENT = "export default function Expense(){ return null; }\n"
_INTENT = {"entities": [{"name": "Expense", "fields": ["id", "amount"]}]}
_PASS_VAL = {"results": {"build": {"status": "passed"}, "test": {"status": "passed"}}}


class TestReviewGate(unittest.TestCase):
    def test_pass_when_component_has_test_and_build_green(self) -> None:
        with TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "src/components/Expense.tsx", _COMPONENT)
            _write(root, "src/components/Expense.test.tsx", _GOOD_TEST)
            v = run_review_gate(root, _INTENT, {}, _PASS_VAL, mode="strict")
            self.assertEqual(v["status"], "pass")
            self.assertFalse(v["blocking"])
            self.assertTrue(all(v["checks"].values()))

    def test_missing_test_blocks_under_strict(self) -> None:
        with TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "src/components/Expense.tsx", _COMPONENT)  # no .test.tsx
            v = run_review_gate(root, _INTENT, {}, _PASS_VAL, mode="strict")
            self.assertEqual(v["status"], "blocked")
            self.assertTrue(v["blocking"])
            self.assertFalse(v["checks"]["test_evidence"])
            self.assertTrue(any("no test file" in f for f in v["findings"]))

    def test_missing_test_only_warns_under_warn_mode(self) -> None:
        with TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "src/components/Expense.tsx", _COMPONENT)
            v = run_review_gate(root, _INTENT, {}, _PASS_VAL, mode="warn")
            self.assertEqual(v["status"], "warn")
            self.assertFalse(v["blocking"])  # warn never fails closed

    def test_render_only_smoke_test_is_accepted(self) -> None:
        with TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "src/components/Expense.tsx", _COMPONENT)
            _write(
                root, "src/components/Expense.test.tsx",
                "import { render, screen } from '@testing-library/react';\n"
                "import { expect, test } from 'vitest';\n"
                "import Expense from './Expense';\n"
                "test('renders', () => { render(<Expense/>);"
                " expect(screen.getByRole('heading')).toBeDefined(); });\n",
            )
            v = run_review_gate(root, _INTENT, {}, _PASS_VAL, mode="strict")
            self.assertEqual(v["status"], "pass")

    def test_empty_assertion_test_blocks(self) -> None:
        with TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "src/components/Expense.tsx", _COMPONENT)
            _write(
                root, "src/components/Expense.test.tsx",
                "import Expense from './Expense';\n// TODO write tests\n",
            )
            v = run_review_gate(root, _INTENT, {}, _PASS_VAL, mode="strict")
            self.assertTrue(v["blocking"])
            self.assertFalse(v["checks"]["test_evidence"])

    def test_entities_but_zero_components_blocks(self) -> None:
        with TemporaryDirectory() as d:
            root = Path(d)  # no src/components at all
            v = run_review_gate(root, _INTENT, {}, _PASS_VAL, mode="strict")
            self.assertTrue(v["blocking"])
            self.assertFalse(v["checks"]["spec_coverage"])
            self.assertTrue(any("NO components" in f for f in v["findings"]))

    def test_build_failed_blocks(self) -> None:
        with TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "src/components/Expense.tsx", _COMPONENT)
            _write(root, "src/components/Expense.test.tsx", _GOOD_TEST)
            v = run_review_gate(
                root, _INTENT, {},
                {"results": {"build": {"status": "failed"}}}, mode="strict",
            )
            self.assertTrue(v["blocking"])
            self.assertFalse(v["checks"]["build_correctness"])

    def test_not_run_build_does_not_block(self) -> None:
        with TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "src/components/Expense.tsx", _COMPONENT)
            _write(root, "src/components/Expense.test.tsx", _GOOD_TEST)
            v = run_review_gate(
                root, _INTENT, {},
                {"results": {"build": {"status": "not_run"}}}, mode="strict",
            )
            self.assertEqual(v["status"], "pass")

    def test_per_entity_gap_is_finding_not_block(self) -> None:
        # Two entities, one component that covers only one -> a finding, but the
        # other entity may be embedded, so it does NOT fail closed.
        with TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "src/components/Expense.tsx", _COMPONENT)
            _write(root, "src/components/Expense.test.tsx", _GOOD_TEST)
            intent = {"entities": [{"name": "Expense"}, {"name": "Category"}]}
            v = run_review_gate(root, intent, {}, _PASS_VAL, mode="strict")
            self.assertEqual(v["status"], "pass")
            self.assertTrue(v["checks"]["spec_coverage"])
            self.assertTrue(any("Category" in f for f in v["findings"]))

    def test_no_entities_no_components_is_pass(self) -> None:
        with TemporaryDirectory() as d:
            v = run_review_gate(Path(d), {"entities": []}, {}, None, mode="strict")
            self.assertEqual(v["status"], "pass")


if __name__ == "__main__":
    unittest.main()
