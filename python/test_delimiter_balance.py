"""#38: deterministic delimiter-balance pass that sharpens a misleading tsc
syntax error so the repair loop converges on the "closed a `describe(...)`
call with `}` instead of `});`" class it otherwise loops on forever.

Two tiers:
  * Pure analyzer tests -- feed source text, assert the (){}/[] imbalance and
    the human hint. No toolchain, fast, hermetic.
  * Enrichment tests -- assert the repair packet gains a crisp `BALANCE`
    diagnostic ONLY when a tsc syntax-class code is present AND the on-disk
    file is unbalanced (corroboration gate), never otherwise.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.validation import analyze_delimiter_balance
from signalos_lib.product.repair_loop import build_repair_packet


_BALANCED_TEST = """\
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ExpenseManager from './ExpenseManager';

describe('ExpenseManager', () => {
  it('adds an expense', () => {
    render(<ExpenseManager />);
    fireEvent.click(screen.getByText(/Add/i));
    expect(screen.getByText('New')).toBeDefined();
  });
});
"""

# Same file, but the describe(...) is closed with `}` instead of `});`
# (the exact 1-char nit from the real e2e): one unclosed '('.
_UNBALANCED_TEST = """\
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ExpenseManager from './ExpenseManager';

describe('ExpenseManager', () => {
  it('adds an expense', () => {
    render(<ExpenseManager />);
    fireEvent.click(screen.getByText(/Add/i));
    expect(screen.getByText('New')).toBeDefined();
  });
}
"""


class AnalyzeDelimiterBalance(unittest.TestCase):
    def test_balanced_file_is_balanced(self):
        r = analyze_delimiter_balance(_BALANCED_TEST)
        self.assertTrue(r["balanced"], r)
        self.assertEqual((r["paren"], r["brace"], r["bracket"]), (0, 0, 0))
        self.assertEqual(r["hint"], "")

    def test_describe_closed_with_brace_not_paren(self):
        r = analyze_delimiter_balance(_UNBALANCED_TEST)
        self.assertFalse(r["balanced"])
        self.assertEqual(r["paren"], 1)  # one unclosed '('
        self.assertEqual(r["brace"], 0)
        self.assertIn("unclosed '('", r["hint"])
        self.assertIn("});", r["hint"])  # names the exact fix shape

    def test_extra_closer_is_negative(self):
        r = analyze_delimiter_balance("const x = foo());\n")
        self.assertFalse(r["balanced"])
        self.assertEqual(r["paren"], -1)
        self.assertIn("extra ')'", r["hint"])

    def test_delimiters_in_strings_are_ignored(self):
        # Parens/braces inside string + template literals must not count.
        src = "const a = '(oops';\nconst b = \"} not real\";\nconst c = `x ${1 + (2)} y`;\n"
        r = analyze_delimiter_balance(src)
        self.assertTrue(r["balanced"], r)

    def test_delimiters_in_comments_are_ignored(self):
        src = "// a ) } ] stray\n/* block ( { [ */\nconst ok = 1;\n"
        r = analyze_delimiter_balance(src)
        self.assertTrue(r["balanced"], r)

    def test_template_interpolation_braces_balance(self):
        # Nested object literal inside ${...} must round-trip cleanly.
        src = "const s = `val: ${ { a: 1, b: [2, 3] } }`;\n"
        r = analyze_delimiter_balance(src)
        self.assertTrue(r["balanced"], r)

    def test_unterminated_string_flagged(self):
        r = analyze_delimiter_balance("const a = 'oops;\n")
        self.assertFalse(r["balanced"])
        self.assertIn("unterminated", r["hint"])


def _packet_with_spec(path: str) -> dict:
    return {
        "run_id": "balance-run",
        "generation": {
            "profile": "react-vite",
            "file_specs": [
                {"path": path, "kind": "test", "entity": "Expense",
                 "description": "test"},
            ],
        },
    }


class BalanceEnrichment(unittest.TestCase):
    def _write(self, root: Path, rel: str, text: str) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_syntax_error_on_unbalanced_file_adds_balance_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "src/components/ExpenseManager.test.tsx"
            self._write(root, rel, _UNBALANCED_TEST)
            failures = [{
                "file": rel, "line": 12, "col": 1, "code": "TS1005",
                "message": "')' expected.", "source": "tsc",
            }]
            packet = build_repair_packet(
                root, 1, failures, "tsc failed", _packet_with_spec(rel),
            )
            specs = packet["generation"]["file_specs"]
            self.assertEqual(len(specs), 1)
            ec = specs[0]["error_context"]
            codes = [e.get("code") for e in ec]
            self.assertIn("TS1005", codes)
            self.assertIn("BALANCE", codes)  # enrichment added
            bal = next(e for e in ec if e.get("code") == "BALANCE")
            self.assertIn("unclosed '('", bal["message"])

    def test_no_enrichment_when_file_is_balanced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "src/components/ExpenseManager.test.tsx"
            self._write(root, rel, _BALANCED_TEST)  # balanced on disk
            failures = [{
                "file": rel, "line": 9, "col": 5, "code": "TS1005",
                "message": "',' expected.", "source": "tsc",
            }]
            packet = build_repair_packet(
                root, 1, failures, "tsc failed", _packet_with_spec(rel),
            )
            ec = packet["generation"]["file_specs"][0]["error_context"]
            self.assertNotIn("BALANCE", [e.get("code") for e in ec])

    def test_no_enrichment_for_non_syntax_code(self):
        # An unbalanced file but a TYPE error (not syntax-class) -> no balance
        # diagnostic (corroboration gate: only sharpen a real syntax error).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "src/components/ExpenseManager.test.tsx"
            self._write(root, rel, _UNBALANCED_TEST)
            failures = [{
                "file": rel, "line": 5, "col": 3, "code": "TS2339",
                "message": "Property 'x' does not exist.", "source": "tsc",
            }]
            packet = build_repair_packet(
                root, 1, failures, "tsc failed", _packet_with_spec(rel),
            )
            ec = packet["generation"]["file_specs"][0]["error_context"]
            self.assertNotIn("BALANCE", [e.get("code") for e in ec])


if __name__ == "__main__":
    unittest.main()
