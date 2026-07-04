# test_sidecar_sign_role.py
# #17 Edit 3.3 — the sidecar sign path uses the REAL role, never a hardcoded PO.
#
# Monkeypatches run_core_cli to capture argv (no real CLI invocation), then
# asserts the --role flag reflects the role passed through from Rust and that
# "PO" is never injected when the role is something else.

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_ipc_server as srv  # noqa: E402


class TestSidecarSignRole(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self._captured: list[list[str]] = []
        self._real_run = srv.run_core_cli

        def fake_run(argv, req_id=""):
            self._captured.append(list(argv))
            return (0, "signed", "")

        srv.run_core_cli = fake_run

    def tearDown(self) -> None:
        srv.run_core_cli = self._real_run
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    def _argv(self) -> list[str]:
        self.assertTrue(self._captured, "run_core_cli was not called")
        return self._captured[-1]

    def test_sidecar_sign_gate_uses_real_role(self):
        srv.sign_gate(3, "Sam", "PE")
        argv = self._argv()
        self.assertIn("--role", argv)
        self.assertEqual(argv[argv.index("--role") + 1], "PE")

    def test_sidecar_sign_gate_no_hardcoded_po(self):
        srv.sign_gate(5, "Sam", "QA")
        argv = self._argv()
        self.assertEqual(argv[argv.index("--role") + 1], "QA")
        self.assertNotIn("PO", argv)

    def test_sidecar_sign_gate_falls_back_to_identity_role(self):
        # No explicit role → read the workspace identity, not a PO default.
        sig = Path(os.getcwd()) / ".signalos"
        sig.mkdir(parents=True, exist_ok=True)
        (sig / "identity.json").write_text(
            '{"name": "Sam", "role": "QA"}', encoding="utf-8"
        )
        srv.sign_gate(5, "Sam", None)
        argv = self._argv()
        self.assertEqual(argv[argv.index("--role") + 1], "QA")

    def test_sidecar_sign_gate_no_role_no_identity_raises(self):
        with self.assertRaises(RuntimeError):
            srv.sign_gate(0, "Sam", None)

    def test_gate_sign_handler_parses_role_at_position_2(self):
        resp = srv.handle(
            {"command": "gate:sign", "id": "s1", "args": ["3", "Sam", "PE"]}
        )
        self.assertTrue(resp["ok"], msg=resp)
        argv = self._argv()
        self.assertEqual(argv[argv.index("--role") + 1], "PE")

    def test_gate_sign_handler_g1_still_requires_test_refs(self):
        # Regression: G1 refuses with no test refs (role at args[2] does not
        # satisfy the test-first requirement).
        resp = srv.handle(
            {"command": "gate:sign", "id": "s2", "args": ["1", "Sam", "PO"]}
        )
        self.assertFalse(resp["ok"], msg=resp)
        self.assertIn("test reference", resp.get("error", "").lower())

    def test_gate_sign_handler_g1_accepts_test_refs_at_position_3(self):
        resp = srv.handle(
            {
                "command": "gate:sign",
                "id": "s3",
                "args": ["1", "Sam", "PO", "tests/belief.py"],
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        argv = self._argv()
        self.assertEqual(argv[argv.index("--role") + 1], "PO")


if __name__ == "__main__":
    unittest.main()
