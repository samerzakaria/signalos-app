"""Gate provisioning: fill any MISSING prior gate (G0-G3) present-and-signed
under an EXPLICIT provenance tier, so a delivery always has one governed build
path -- never fail-open (ungoverned product) and never dead-end. The guardrail:
provisioned gates are signed as a clearly-labelled system identity, NEVER as
the founder, and the delivery reports the tier so nothing is silently
rubber-stamped.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import sign  # noqa: E402
from signalos_lib.product.delivery import _signed_prior_gates_for_g4  # noqa: E402
from signalos_lib.product.provision_gates import (  # noqa: E402
    PROVENANCE_SIGNERS,
    governance_tier_summary,
    provision_gates,
)


def _bare_repo() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / ".signalos").mkdir(parents=True, exist_ok=True)
    return d


class TestProvisionGates(unittest.TestCase):
    def test_provisioning_satisfies_the_build_precondition(self):
        d = _bare_repo()
        signed, blockers = _signed_prior_gates_for_g4(d)
        self.assertEqual(signed, [])              # bare repo: build blocked
        self.assertEqual(len(blockers), 4)

        provision_gates(d, tier="assumed")

        signed, blockers = _signed_prior_gates_for_g4(d)
        self.assertEqual(signed, [0, 1, 2, 3])    # all prior gates now signed
        self.assertEqual(blockers, [])            # build may proceed

    def test_mixed_role_gate_signs_every_required_role(self):
        """G3 mixes roles (Design Note=PO, Plan/Acceptance=PE); every artifact
        must end up signed despite separation-of-duties."""
        d = _bare_repo()
        provision_gates(d, tier="assumed")
        for st in sign.check_gate(d, "G3"):
            self.assertTrue(st.exists and st.has_signatures and not st.is_draft,
                            f"{st.rel_path} not signed")

    def test_signer_is_never_the_founder_and_tier_is_visible(self):
        d = _bare_repo()
        provision_gates(d, tier="assumed")
        for st in sign.check_gate(d, "G0"):
            for s in st.signers:
                self.assertIn("SignalOS", s)           # system identity
                self.assertIn("NOT founder-reviewed", s)  # honest marking
        self.assertEqual(governance_tier_summary(d),
                         {"G0": "assumed", "G1": "assumed",
                          "G2": "assumed", "G3": "assumed"})

    def test_reconstructed_tier_marks_correct_me(self):
        d = _bare_repo()
        provision_gates(d, tier="reconstructed")
        note = (d / "core" / "strategy" / "DESIGN_NOTE.md").read_text(encoding="utf-8")
        self.assertIn("reconstructed", note.lower())
        self.assertIn("CORRECT", note)
        self.assertEqual(governance_tier_summary(d)["G3"], "reconstructed")

    def test_idempotent_and_preserves_existing_signatures(self):
        d = _bare_repo()
        provision_gates(d, tier="assumed")
        # a real founder signs G2 on top
        for st in sign.check_gate(d, "G2"):
            if st.exists:
                sign.sign_gate(d, "G2", "Jane Founder <jane@x>", "PO", "APPROVED",
                               audit_log=d / ".signalos" / "AUDIT_TRAIL.jsonl")
        again = provision_gates(d, tier="assumed")
        self.assertEqual(again, {})   # nothing missing -> no action, no double-sign
        # founder signature preserved
        g2_signers = [s for st in sign.check_gate(d, "G2") for s in st.signers]
        self.assertTrue(any("Jane Founder" in s for s in g2_signers))

    def test_never_cosigns_a_founder_artifact_in_a_multi_artifact_gate(self):
        """G0 holds several PO/PE artifacts. If a founder signs ONE of them and
        provision fills the rest, provision must NOT append a system
        co-signature onto the founder's artifact -- doing so both pollutes the
        founder artifact and (since an 'assumed' signer outranks a founder one in
        the tier summary) mislabels a founder-reviewed artifact as auto-provisioned."""
        d = _bare_repo()
        soul = d / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md"
        soul.parent.mkdir(parents=True, exist_ok=True)
        soul.write_text("# Soul Document\n\nReal founder purpose statement.\n",
                        encoding="utf-8")
        audit = d / ".signalos" / "AUDIT_TRAIL.jsonl"
        # Founder signs only the Soul Document (both its required roles).
        sign.sign_gate(d, "G0", "Jane Founder <jane@x>", "PO", "APPROVED", audit_log=audit)
        sign.sign_gate(d, "G0", "Jane Founder <jane@x>", "PE", "APPROVED", audit_log=audit)

        provision_gates(d, tier="assumed")   # fills Constitution/Surface/T3

        for st in sign.check_gate(d, "G0"):
            if st.rel_path.endswith("SOUL-DOCUMENT.md"):
                self.assertTrue(st.signers, "founder artifact lost its signature")
                self.assertTrue(
                    all("Jane Founder" in s for s in st.signers),
                    f"founder artifact was co-signed by the system: {st.signers}")
                self.assertFalse(
                    any("SignalOS" in s for s in st.signers),
                    "system signature polluted a founder-signed artifact")

    def test_content_fn_supplies_real_content(self):
        d = _bare_repo()

        def content(gate, art):
            if art.label == "Plan":
                return "# Plan\n\nReal reconstructed plan content.\n"
            return None
        provision_gates(d, tier="reconstructed", content_fn=content)
        plan = (d / "core" / "execution" / "PLAN.md").read_text(encoding="utf-8")
        self.assertIn("Real reconstructed plan content", plan)

    def test_rejects_unknown_tier(self):
        with self.assertRaises(ValueError):
            provision_gates(_bare_repo(), tier="totally-approved")
        self.assertNotIn("totally-approved", PROVENANCE_SIGNERS)


if __name__ == "__main__":
    unittest.main()
