"""Tests for `signalos constitution lock|verify` (Phase 13 hardening)."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import cli
from signalos_lib.commands import constitution
from signalos_lib.validators.constitution_integrity import check_constitution_integrity


CONSTITUTION_REL = "core/governance/Governance/CONSTITUTION.md"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_constitution(root: Path, body: str = "# Constitution\n\nWe believe in audit.\n") -> Path:
    p = root / CONSTITUTION_REL
    _write(p, body)
    return p


class ConstitutionLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-const-lock-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = constitution.main(argv)
        return code, json.loads(stdout.getvalue())

    def test_lock_writes_lock_file_with_sha256(self) -> None:
        _seed_constitution(self.tmp)

        code, payload = self._run(["lock", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["path"], CONSTITUTION_REL)
        self.assertEqual(len(payload["sha256"]), 64)
        self.assertIn("locked_at", payload)

        lock_file = self.tmp / ".signalos" / "integrity" / "constitution.lock.json"
        self.assertTrue(lock_file.is_file())
        data = json.loads(lock_file.read_text(encoding="utf-8"))
        self.assertEqual(data["sha256"], payload["sha256"])
        self.assertEqual(data["path"], CONSTITUTION_REL)

    def test_lock_fails_when_constitution_missing(self) -> None:
        code, payload = self._run(["lock", "--repo-root", str(self.tmp), "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("not found", payload["error"])

    def test_lock_is_idempotent_for_unchanged_content(self) -> None:
        _seed_constitution(self.tmp)

        code1, payload1 = self._run(["lock", "--repo-root", str(self.tmp), "--json"])
        code2, payload2 = self._run(["lock", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        self.assertEqual(payload1["sha256"], payload2["sha256"])


class ConstitutionVerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-const-verify-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = constitution.main(argv)
        return code, json.loads(stdout.getvalue())

    def test_verify_passes_when_hash_matches(self) -> None:
        _seed_constitution(self.tmp)
        self._run(["lock", "--repo-root", str(self.tmp), "--json"])

        code, payload = self._run(["verify", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["matches"])
        self.assertEqual(payload["locked_sha256"], payload["current_sha256"])

    def test_verify_fails_when_constitution_tampered(self) -> None:
        const = _seed_constitution(self.tmp)
        self._run(["lock", "--repo-root", str(self.tmp), "--json"])
        const.write_text(const.read_text(encoding="utf-8") + "\nTAMPERED\n", encoding="utf-8")

        code, payload = self._run(["verify", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "mismatch")
        self.assertFalse(payload["matches"])
        self.assertNotEqual(payload["locked_sha256"], payload["current_sha256"])

    def test_verify_fails_when_lock_missing(self) -> None:
        _seed_constitution(self.tmp)
        code, payload = self._run(["verify", "--repo-root", str(self.tmp), "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("lock file not found", payload["error"])


class ConstitutionIntegrityValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-const-val-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_validator_passes_after_lock(self) -> None:
        _seed_constitution(self.tmp)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            constitution.main(["lock", "--repo-root", str(self.tmp), "--json"])

        passed, message, details = check_constitution_integrity(self.tmp)
        self.assertTrue(passed, message)
        self.assertEqual(details["locked_sha256"], details["current_sha256"])

    def test_validator_fails_when_lock_missing(self) -> None:
        _seed_constitution(self.tmp)
        passed, message, _details = check_constitution_integrity(self.tmp)
        self.assertFalse(passed)
        self.assertIn("lock is missing", message)

    def test_validator_skips_when_constitution_absent(self) -> None:
        passed, message, _details = check_constitution_integrity(self.tmp)
        self.assertTrue(passed)
        self.assertIn("nothing to verify", message)

    def test_validator_registered_in_layer1_group(self) -> None:
        from signalos_lib.validate_cmd import _layer1_checks
        names = {name for name, _sev, _fn in _layer1_checks()}
        self.assertIn("constitution-integrity", names)


class ConstitutionCliRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-const-cli-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_top_level_cli_forwards_lock(self) -> None:
        _seed_constitution(self.tmp)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos", "constitution", "lock",
                "--repo-root", str(self.tmp),
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
