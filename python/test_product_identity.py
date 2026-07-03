# python/test_product_identity.py
# 3.6 (C-bridge): identity continuity, local-only scope. Proves the founder's
# real name/role (collected once via the onboarding wizard, stored at
# .signalos/identity.json) actually reaches the signer string recorded in
# the audit trail, and survives into an isolated launch mini-build's own
# repo_root instead of being silently dropped.

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.identity import copy_identity_to, format_signer, load_identity


def _write_identity(repo_root: Path, name: str, role: str) -> None:
    d = repo_root / ".signalos"
    d.mkdir(parents=True, exist_ok=True)
    (d / "identity.json").write_text(json.dumps({"name": name, "role": role}), encoding="utf-8")


class TestLoadIdentity(unittest.TestCase):
    def test_returns_none_when_unset(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(load_identity(Path(d)))

    def test_loads_a_real_identity(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_identity(root, "Samer Zakaria", "PO")
            identity = load_identity(root)
            self.assertEqual(identity["name"], "Samer Zakaria")
            self.assertEqual(identity["role"], "PO")

    def test_corrupt_file_yields_none_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir(parents=True, exist_ok=True)
            (root / ".signalos" / "identity.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_identity(root))

    def test_empty_name_treated_as_unset(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_identity(root, "", "PO")
            self.assertIsNone(load_identity(root))


class TestFormatSigner(unittest.TestCase):
    def test_falls_back_when_no_identity(self) -> None:
        self.assertEqual(format_signer(None), "foundry-agent")

    def test_renders_name_and_role(self) -> None:
        self.assertEqual(format_signer({"name": "Samer Zakaria", "role": "PO"}), "Samer Zakaria (PO)")

    def test_renders_name_only_when_role_missing(self) -> None:
        self.assertEqual(format_signer({"name": "Samer Zakaria", "role": ""}), "Samer Zakaria")

    def test_custom_fallback(self) -> None:
        self.assertEqual(format_signer(None, fallback="unknown-signer"), "unknown-signer")


class TestCopyIdentityTo(unittest.TestCase):
    def test_no_op_when_parent_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            parent = Path(d) / "parent"
            child = Path(d) / "child"
            parent.mkdir()
            self.assertFalse(copy_identity_to(parent, child))
            self.assertIsNone(load_identity(child))

    def test_copies_real_identity_into_isolated_child(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            parent = Path(d) / "parent"
            child = Path(d) / "child" / "launch" / "run-1"
            _write_identity(parent, "Samer Zakaria", "PO")
            self.assertTrue(copy_identity_to(parent, child))
            child_identity = load_identity(child)
            self.assertEqual(child_identity["name"], "Samer Zakaria")
            self.assertEqual(child_identity["role"], "PO")


if __name__ == "__main__":
    unittest.main()
