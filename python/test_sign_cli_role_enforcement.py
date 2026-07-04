# test_sign_cli_role_enforcement.py
# #17 Edit 3.4 — the CLI sign path routes through sign.sign_gate, which enforces
# segregation of duties from artifact required_roles. End-to-end proof that a PO
# cannot sign a QA gate (G5) on the REAL path, and a QA can.

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.artifacts import GATE_ARTIFACTS  # noqa: E402
from signalos_lib.commands import sign as sign_cmd  # noqa: E402


def _run_main(argv: list[str]) -> int:
    # Capture stdout/stderr so the CLI's unicode glyphs don't hit a cp1252
    # console under Windows CI (the real sidecar captures CLI output the same way).
    out, errbuf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errbuf):
        return sign_cmd.main(argv)


def _seed_gate_artifacts(root: Path, gate: str) -> None:
    """Create every artifact file a gate signs, with a signable body."""
    for artifact in GATE_ARTIFACTS[gate]:
        p = root / artifact.rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {artifact.label}\n\nContent for {gate}.\n", encoding="utf-8")


def _signatures_present(root: Path, gate: str) -> bool:
    for artifact in GATE_ARTIFACTS[gate]:
        text = (root / artifact.rel_path).read_text(encoding="utf-8")
        if "## Signatures" in text and "signer:" in text:
            return True
    return False


class TestSignCliRoleEnforcement(unittest.TestCase):
    def test_po_cannot_sign_g5_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_gate_artifacts(root, "G5")
            rc = _run_main(
                [
                    "G5",
                    "--signer",
                    "Pat Owner",
                    "--role",
                    "PO",
                    "--repo-root",
                    str(root),
                ]
            )
            self.assertNotEqual(rc, 0, "PO signing a QA gate must fail")
            self.assertFalse(
                _signatures_present(root, "G5"),
                "no signature should be written on an unauthorised sign",
            )

    def test_qa_can_sign_g5(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_gate_artifacts(root, "G5")
            rc = _run_main(
                [
                    "G5",
                    "--signer",
                    "Quinn Assurance",
                    "--role",
                    "QA",
                    "--repo-root",
                    str(root),
                ]
            )
            self.assertEqual(rc, 0, "QA signing the QA gate must succeed")
            self.assertTrue(_signatures_present(root, "G5"))

    def test_pe_cannot_sign_g1(self):
        # G1 requires PO; a PE must be rejected on the real path too.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_gate_artifacts(root, "G1")
            rc = _run_main(
                [
                    "G1",
                    "--signer",
                    "Erin Engineer",
                    "--role",
                    "PE",
                    "--repo-root",
                    str(root),
                ]
            )
            self.assertNotEqual(rc, 0)
            self.assertFalse(_signatures_present(root, "G1"))


if __name__ == "__main__":
    unittest.main()
