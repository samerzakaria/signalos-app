"""Fail-open governance hole: signatures were FORGEABLE on the gating surfaces.

The status board, wave engine `inspect`, and build preflight all treated a gate
as SIGNED on mere signature *presence* (`has_signatures`) -- never checking the
verdict, the signer's role, the artifact hash, an audit-trail link, or a durable
revocation. A crafted block with `role: HACKER`, `verdict: REJECTED` and NO
artifact_hash was reported as SIGNED.

These tests reproduce the audit's $0 probe at the surface the audit hit (the
status board), then pin the strict behaviour on every rewired surface. Each
forgery case is isolated so a single check is what flips the verdict:

  (a) forged verdict REJECTED (in-file)          -> NOT signed
  (b) signature with no artifact_hash            -> NOT signed
  (c) unauthorized / wrong role                  -> NOT signed
  (d) artifact content tampered after signing    -> NOT signed
  (e) revoked / reopened gate (durable marker)   -> NOT signed on a fresh read
  (f) genuine APPROVED + authorized role + valid
      hash + audit row                           -> signed (no false-negative)

Board-level tests (`test_board_*`) use only shipped functions so they run RED
against the pre-fix code (the board reported the forgery as signed). The
strict-validator / preflight / inspect / revoke tests pin the same guarantees on
the other rewired surfaces.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import sign  # noqa: E402
from signalos_lib import status as status_lib  # noqa: E402
from signalos_lib.product import preflight  # noqa: E402
from signalos_lib import wave_engine  # noqa: E402

# G2 is a single-artifact gate (EXPECTATION_MAP.md, role PO) -- the cleanest
# surface to isolate one forged check at a time.
_G2_ARTIFACT = "core/strategy/EXPECTATION_MAP.md"
_G2_BODY = "Expectation one.\nExpectation two.\nExpectation three.\n"


def _mkroot() -> Path:
    return Path(tempfile.mkdtemp(prefix="signalos-forgery-"))


def _audit_log(root: Path) -> Path:
    return root / ".signalos" / "AUDIT_TRAIL.jsonl"


def _genuine_sign_g2(root: Path) -> Path:
    """A fully valid G2: signature block + workspace audit row (the real path)."""
    p = root.joinpath(*_G2_ARTIFACT.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_G2_BODY, encoding="utf-8")
    sign.sign_gate(root, "G2", "PO Lead", "PO", "APPROVED", audit_log=_audit_log(root))
    return p


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"marker {old!r} not found in artifact"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _board_g2_signed(root: Path) -> bool:
    return bool(status_lib.get_wave_status(root)["gates"]["G2"])


# ---------------------------------------------------------------------------
# Board-level ($0 repro) -- RED against the pre-fix presence check.
# ---------------------------------------------------------------------------

class TestBoardRejectsForgery(unittest.TestCase):
    def test_board_signed_for_genuine_gate(self):
        """(f) A genuinely signed + audit-linked gate reads SIGNED (no
        false-negative regression)."""
        root = _mkroot()
        _genuine_sign_g2(root)
        self.assertTrue(_board_g2_signed(root))

    def test_board_rejects_forged_rejected_verdict(self):
        """(a) An APPROVED audit row but an in-file verdict of REJECTED must
        NOT read as signed -- the board must inspect the verdict, not presence."""
        root = _mkroot()
        p = _genuine_sign_g2(root)
        _edit(p, "verdict: APPROVED", "verdict: REJECTED")
        self.assertFalse(_board_g2_signed(root))

    def test_board_rejects_hashless_signature(self):
        """(b) A signature with NO artifact_hash must NOT read as signed."""
        root = _mkroot()
        p = _genuine_sign_g2(root)
        text = re.sub(r"\n\s*artifact_hash: [a-f0-9]{64}", "", p.read_text(encoding="utf-8"))
        p.write_text(text, encoding="utf-8")
        self.assertFalse(_board_g2_signed(root))

    def test_board_rejects_unauthorized_role(self):
        """(c) A signature from a role not authorized for the gate must NOT
        read as signed (forged `role: HACKER`)."""
        root = _mkroot()
        p = _genuine_sign_g2(root)
        _edit(p, "role: PO", "role: HACKER")
        self.assertFalse(_board_g2_signed(root))

    def test_board_rejects_tampered_content(self):
        """(d) Editing the artifact body after signing (hash mismatch) must
        NOT read as signed."""
        root = _mkroot()
        p = _genuine_sign_g2(root)
        _edit(p, "Expectation one.", "TAMPERED after signing.")
        self.assertFalse(_board_g2_signed(root))

    def test_board_rejects_durable_revocation_marker(self):
        """(e) A durable revocation marker on disk must make a reopened gate
        read NOT signed even though its (now-stale) signature block remains."""
        root = _mkroot()
        _genuine_sign_g2(root)
        self.assertTrue(_board_g2_signed(root))  # signed before revoke
        marker = root / ".signalos" / "gate-revocations.json"
        marker.write_text(json.dumps({"G2": {"reason": "reopened for rework"}}),
                          encoding="utf-8")
        self.assertFalse(_board_g2_signed(root))


# ---------------------------------------------------------------------------
# Strict validator (sign.is_gate_signed_strict) -- the single source of truth.
# ---------------------------------------------------------------------------

class TestStrictValidator(unittest.TestCase):
    def _strict(self, root: Path) -> bool:
        return sign.is_gate_signed_strict(root, "G2")

    def test_genuine_is_signed(self):
        root = _mkroot()
        _genuine_sign_g2(root)
        self.assertTrue(self._strict(root))
        res = sign.check_gate_signed_strict(root, "G2")
        self.assertTrue(res.signed)
        self.assertEqual(res.reasons, [])

    def test_production_outcome_evidence_requires_executed_browser_receipt(self):
        root = _mkroot()
        (root / "package.json").write_text(
            json.dumps({
                "scripts": {"dev": "vite"},
                "dependencies": {"react": "18", "vite": "5"},
            }),
            encoding="utf-8",
        )
        (root / "src").mkdir()
        evidence = {
            "security_gate": {"status": "passed"},
            "runtime_proof": {
                "status": "passed",
                "ok": True,
                "stack": "react-vite",
                "ux_required": True,
                "ux_status": "passed",
                "ux_executed": False,
                "ux_schema_version": "signalos.ux-browser-proof.v1",
            },
        }

        reasons = sign._production_release_evidence_reasons(root, evidence)

        self.assertIn(
            "production browser UX proof was not executed", reasons
        )
        evidence["runtime_proof"]["ux_executed"] = True
        self.assertEqual(
            sign._production_release_evidence_reasons(root, evidence), []
        )

    def test_rejected_verdict_not_signed(self):
        root = _mkroot()
        p = _genuine_sign_g2(root)
        _edit(p, "verdict: APPROVED", "verdict: REJECTED")
        self.assertFalse(self._strict(root))

    def test_hashless_not_signed(self):
        root = _mkroot()
        p = _genuine_sign_g2(root)
        text = re.sub(r"\n\s*artifact_hash: [a-f0-9]{64}", "", p.read_text(encoding="utf-8"))
        p.write_text(text, encoding="utf-8")
        self.assertFalse(self._strict(root))

    def test_wrong_role_not_signed(self):
        root = _mkroot()
        p = _genuine_sign_g2(root)
        _edit(p, "role: PO", "role: HACKER")
        self.assertFalse(self._strict(root))

    def test_tampered_hash_not_signed(self):
        root = _mkroot()
        p = _genuine_sign_g2(root)
        _edit(p, "Expectation one.", "TAMPERED after signing.")
        self.assertFalse(self._strict(root))

    def test_no_audit_row_not_signed(self):
        """A pure in-file signature (no audit trail) is NOT enough."""
        root = _mkroot()
        p = root.joinpath(*_G2_ARTIFACT.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_G2_BODY, encoding="utf-8")
        sign.sign_artifact(p, "PO Lead", "PO", "G2", "APPROVED")  # block only, no audit
        self.assertFalse(self._strict(root))

    def test_hashless_unchained_audit_row_cannot_authorize_gate(self):
        root = _mkroot()
        artifact = root.joinpath(*_G2_ARTIFACT.split("/"))
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(_G2_BODY, encoding="utf-8")
        sign.sign_artifact(artifact, "PO Lead", "PO", "G2", "APPROVED")
        audit = _audit_log(root)
        sign.append_audit_event(audit, {"action": "test.boundary"})
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "action": "sign",
                "gate": "Execution Plan",
                "artifact": _G2_ARTIFACT,
                "verdict": "APPROVED",
                "project_id": "default",
            }) + "\n")

        self.assertEqual(sign.verify_audit_chain(audit), [])
        self.assertFalse(self._strict(root))

    def test_revoked_gate_not_signed(self):
        root = _mkroot()
        _genuine_sign_g2(root)
        self.assertTrue(self._strict(root))
        sign.revoke_gate(root, "G2", reason="reopened")
        self.assertTrue(sign.is_gate_revoked(root, "G2"))
        self.assertFalse(self._strict(root))

    def test_resign_clears_revocation(self):
        """A legitimate re-sign after a reopen clears the durable marker."""
        root = _mkroot()
        _genuine_sign_g2(root)
        sign.revoke_gate(root, "G2", reason="reopened")
        self.assertFalse(self._strict(root))
        # rework + re-sign
        _genuine_sign_g2(root)
        self.assertFalse(sign.is_gate_revoked(root, "G2"))
        self.assertTrue(self._strict(root))

    def test_deleting_revocation_marker_does_not_resurrect_old_signature(self):
        root = _mkroot()
        _genuine_sign_g2(root)
        sign.revoke_gate(root, "G2", reason="reopened")
        marker = root / ".signalos" / "gate-revocations.json"
        self.assertTrue(marker.is_file())

        marker.unlink()

        self.assertFalse(sign.is_gate_revoked(root, "G2"))
        self.assertFalse(self._strict(root))
        # A fresh signature is later than the audit reversal and is therefore
        # the only legitimate way to restore the gate.
        _genuine_sign_g2(root)
        self.assertTrue(self._strict(root))


class TestRevocationDurableAcrossProcess(unittest.TestCase):
    def test_revocation_survives_fresh_process(self):
        """(e) The durable marker is authoritative in a brand-new interpreter."""
        root = _mkroot()
        _genuine_sign_g2(root)
        sign.revoke_gate(root, "G2", reason="reopened")
        code = (
            "import sys, json;"
            "sys.path.insert(0, r'%s');"
            "from signalos_lib import status as s;"
            "print(json.dumps(s.get_wave_status(r'%s')['gates']['G2']))"
            % (str(Path(__file__).resolve().parent), str(root))
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "false", out.stdout)


# ---------------------------------------------------------------------------
# Cross-surface: preflight + wave_engine.inspect read the SAME strict verdict.
# ---------------------------------------------------------------------------

class TestOtherSurfacesRejectForgery(unittest.TestCase):
    def _seed_g0_g1(self, root: Path) -> None:
        # preflight looks at G0..G3; seed the earlier ones genuinely so the
        # ONLY blocker is our forged G2.
        from conftest import seed_signed_gate
        seed_signed_gate(root, "G0")
        seed_signed_gate(root, "G1")
        seed_signed_gate(root, "G3")

    def test_preflight_flags_forged_g2(self):
        root = _mkroot()
        self._seed_g0_g1(root)
        p = _genuine_sign_g2(root)
        self.assertEqual(
            [x for x in preflight.validate_build_readiness(root) if x.startswith("G2")],
            [],
        )  # genuine -> no G2 problem
        _edit(p, "role: PO", "role: HACKER")
        problems = preflight.validate_build_readiness(root)
        self.assertTrue(any("G2" in x for x in problems), problems)

    def test_inspect_flags_forged_g2(self):
        root = _mkroot()
        _genuine_sign_g2(root)
        self.assertTrue(wave_engine.inspect(root)["gates"]["G2"])
        p = root.joinpath(*_G2_ARTIFACT.split("/"))
        _edit(p, "verdict: APPROVED", "verdict: REJECTED")
        self.assertFalse(wave_engine.inspect(root)["gates"]["G2"])


if __name__ == "__main__":
    unittest.main()
