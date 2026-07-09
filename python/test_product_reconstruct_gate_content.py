"""Reconstructed gate content: a provisioned gate must be grounded in real
material -- the existing codebase (adopt) or the delivery's own generated brief
(greenfield) -- not a generic placeholder, and must still sign cleanly."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import sign  # noqa: E402
from signalos_lib.product.delivery import _signed_prior_gates_for_g4  # noqa: E402
from signalos_lib.product.provision_gates import (  # noqa: E402
    governance_tier_summary,
    provision_gates,
)
from signalos_lib.product.reconstruct_gate_content import (  # noqa: E402
    code_content_fn,
    evidence_content_fn,
)

_BLOCKING = ("TODO", "TBD", "FIXME", "{{", "}}", "[DATE]")


def _all_gate_text(root: Path) -> str:
    text = []
    for gate in ("G0", "G1", "G2", "G3"):
        for st in sign.check_gate(root, gate):
            if st.exists:
                text.append(st.path.read_text(encoding="utf-8"))
    return "\n".join(text)


class TestAdoptReconstruction(unittest.TestCase):
    def _existing_repo(self) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / ".signalos").mkdir(parents=True, exist_ok=True)
        (d / "package.json").write_text(json.dumps({
            "name": "acme-ledger",
            "description": "A double-entry ledger service",
            "scripts": {"build": "tsc", "test": "vitest run"},
            "dependencies": {"express": "^4", "zod": "^3"},
            "license": "MIT",
        }), encoding="utf-8")
        (d / "README.md").write_text(
            "# Acme Ledger\n\nReconciles accounts nightly.\n", encoding="utf-8")
        (d / "src").mkdir()
        (d / "src" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
        (d / "src" / "ledger.test.ts").write_text("test('x', () => {});\n", encoding="utf-8")
        return d

    def test_reconstructs_from_code_and_signs(self):
        d = self._existing_repo()
        provision_gates(d, tier="reconstructed", content_fn=code_content_fn(d))

        signed, blockers = _signed_prior_gates_for_g4(d)
        self.assertEqual(signed, [0, 1, 2, 3], f"gates not all signed: {blockers}")
        self.assertEqual(governance_tier_summary(d),
                         {"G0": "reconstructed", "G1": "reconstructed",
                          "G2": "reconstructed", "G3": "reconstructed"})

    def test_content_is_grounded_in_the_actual_repo(self):
        d = self._existing_repo()
        provision_gates(d, tier="reconstructed", content_fn=code_content_fn(d))
        soul = (d / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md").read_text("utf-8")
        self.assertIn("acme-ledger", soul)
        self.assertIn("double-entry ledger", soul)
        # Surface inventory reflects the real source dir.
        surface = (d / "core" / "governance" / "Governance" / "SURFACE_INVENTORY.md").read_text("utf-8")
        self.assertIn("src", surface)
        self.assertIn("express", surface)         # detected stack
        # Expectations reference the real test file.
        expect = (d / "core" / "strategy" / "EXPECTATION_MAP.md").read_text("utf-8")
        self.assertIn("ledger.test.ts", expect)
        # Honest reconstruction framing preserved.
        self.assertIn("CORRECT", (d / "core" / "strategy" / "DESIGN_NOTE.md").read_text("utf-8"))

    def test_no_blocking_tokens_survive_from_repo_text(self):
        d = self._existing_repo()
        # A README carrying a TODO must not leak a blocking token into a signed gate.
        (d / "README.md").write_text(
            "# Acme Ledger\n\nTODO: document the reconciliation flow.\n", encoding="utf-8")
        provision_gates(d, tier="reconstructed", content_fn=code_content_fn(d))
        blob = _all_gate_text(d)
        for tok in _BLOCKING:
            self.assertNotIn(tok, blob, f"blocking token {tok!r} leaked into a signed gate")


class TestGreenfieldEvidence(unittest.TestCase):
    def _repo_with_evidence(self) -> Path:
        d = Path(tempfile.mkdtemp())
        prod = d / ".signalos" / "product"
        prod.mkdir(parents=True, exist_ok=True)
        (prod / "INTENT.json").write_text(json.dumps({
            "api_surfaces": ["POST /expenses", "GET /expenses"],
            "entities": ["Expense", "Category"],
            "entity_fields": {"Expense": ["amount", "date"]},
            "out_of_scope": ["multi-currency"],
            "deployment_intent": "desktop",
        }), encoding="utf-8")
        (prod / "ACCEPTANCE_MATRIX.json").write_text(json.dumps({
            "product_name": "Expenses",
            "profile": "generic",
            "blueprint_id": "auto",
            "summary": "add, list and reconcile expenses",
            "criteria": ["can add an expense", "can reconcile the ledger"],
            "test_scenarios": ["add then list shows the row"],
        }), encoding="utf-8")
        return d

    def test_evidence_grounds_gates_and_signs(self):
        d = self._repo_with_evidence()
        provision_gates(d, tier="assumed", content_fn=evidence_content_fn(d))

        signed, blockers = _signed_prior_gates_for_g4(d)
        self.assertEqual(signed, [0, 1, 2, 3], f"gates not all signed: {blockers}")
        surface = (d / "core" / "governance" / "Governance" / "SURFACE_INVENTORY.md").read_text("utf-8")
        self.assertIn("POST /expenses", surface)
        self.assertIn("Expense", surface)
        crit = (d / "core" / "execution" / "ACCEPTANCE_CRITERIA.md").read_text("utf-8")
        self.assertIn("reconcile the ledger", crit)
        self.assertEqual(governance_tier_summary(d)["G0"], "assumed")

    def test_falls_back_to_default_when_evidence_absent(self):
        # No .signalos/product evidence -> content_fn returns None -> provision
        # uses its honest default and still signs.
        d = Path(tempfile.mkdtemp())
        (d / ".signalos").mkdir(parents=True, exist_ok=True)
        provision_gates(d, tier="assumed", content_fn=evidence_content_fn(d))
        signed, _ = _signed_prior_gates_for_g4(d)
        self.assertEqual(signed, [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
