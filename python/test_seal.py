"""Tests for `signalos seal create|verify` and G5 sign hook (Phase 13)."""

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

from signalos_lib import cli, sign as sign_lib
from signalos_lib.artifacts import GATE_ARTIFACTS
from signalos_lib.commands import seal


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_some_artifacts(root: Path) -> list[str]:
    """Materialize a couple of real artifact paths so seal has work to do."""

    seeded: list[str] = []
    for entries in GATE_ARTIFACTS.values():
        for artifact in entries[:1]:  # one per gate for speed
            p = root / artifact.rel_path
            _write(p, f"# {artifact.label}\n")
            seeded.append(artifact.rel_path)
    return seeded


class SealCreateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-seal-create-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = seal.main(argv)
        return code, json.loads(stdout.getvalue())

    def test_create_writes_seal_with_per_artifact_entries(self) -> None:
        seeded = _seed_some_artifacts(self.tmp)

        code, payload = self._run(["create", "--wave", "5", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["wave"], "5")
        self.assertGreaterEqual(payload["sealed"], len(seeded))

        seal_file = self.tmp / ".signalos" / "integrity" / "seal-5.json"
        self.assertTrue(seal_file.is_file())
        bundle = json.loads(seal_file.read_text(encoding="utf-8"))
        self.assertEqual(bundle["schema_version"], "signalos.seal.v1")
        self.assertEqual(bundle["wave"], "5")
        for entry in bundle["artifacts"]:
            self.assertIn("artifact_path", entry)
            self.assertIn("sha256", entry)
            self.assertIn("exists", entry)
            self.assertIn("sealed_at", entry)
            if entry["exists"]:
                self.assertEqual(len(entry["sha256"]), 64)
            else:
                self.assertEqual(entry["sha256"], "")

    def test_create_records_audit_trail(self) -> None:
        _seed_some_artifacts(self.tmp)
        self._run(["create", "--wave", "2", "--repo-root", str(self.tmp), "--json"])

        trail = self.tmp / ".signalos" / "AUDIT_TRAIL.jsonl"
        rows = [json.loads(line) for line in trail.read_text(encoding="utf-8").splitlines() if line]
        actions = [r["action"] for r in rows]
        self.assertIn("seal-create", actions)


class SealVerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-seal-verify-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = seal.main(argv)
        return code, json.loads(stdout.getvalue())

    def test_verify_passes_for_unchanged_artifacts(self) -> None:
        _seed_some_artifacts(self.tmp)
        self._run(["create", "--wave", "1", "--repo-root", str(self.tmp), "--json"])

        code, payload = self._run(["verify", "--wave", "1", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["mismatches"], [])

    def test_verify_detects_tampered_artifact(self) -> None:
        seeded = _seed_some_artifacts(self.tmp)
        self._run(["create", "--wave", "1", "--repo-root", str(self.tmp), "--json"])
        # Mutate the first seeded artifact.
        victim = self.tmp / seeded[0]
        victim.write_text(victim.read_text(encoding="utf-8") + "\nTAMPER\n", encoding="utf-8")

        code, payload = self._run(["verify", "--wave", "1", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "mismatch")
        reasons = {m["reason"] for m in payload["mismatches"]}
        self.assertIn("hash-changed", reasons)
        changed_paths = [m["artifact_path"] for m in payload["mismatches"]]
        self.assertIn(seeded[0], changed_paths)

    def test_verify_errors_when_seal_missing(self) -> None:
        code, payload = self._run(["verify", "--wave", "99", "--repo-root", str(self.tmp), "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("seal file not found", payload["error"])

    def _rewrite_bundle(self, mutate) -> None:
        path = self.tmp / ".signalos" / "integrity" / "seal-1.json"
        bundle = json.loads(path.read_text(encoding="utf-8"))
        mutate(bundle)
        path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")

    def test_verify_rejects_empty_artifact_rows(self) -> None:
        _seed_some_artifacts(self.tmp)
        seal.create_seal(self.tmp, "1")
        self._rewrite_bundle(lambda bundle: bundle.__setitem__("artifacts", []))

        result = seal.verify_seal(self.tmp, "1")

        self.assertEqual(result["status"], "error")
        self.assertTrue(any("empty" in error for error in result["errors"]))

    def test_verify_rejects_an_omitted_canonical_row(self) -> None:
        _seed_some_artifacts(self.tmp)
        seal.create_seal(self.tmp, "1")
        self._rewrite_bundle(lambda bundle: bundle["artifacts"].pop())

        result = seal.verify_seal(self.tmp, "1")

        self.assertEqual(result["status"], "error")
        self.assertTrue(any("omitted" in error for error in result["errors"]))

    def test_verify_rejects_a_duplicate_canonical_row(self) -> None:
        _seed_some_artifacts(self.tmp)
        seal.create_seal(self.tmp, "1")

        def duplicate(bundle) -> None:
            bundle["artifacts"].append(dict(bundle["artifacts"][0]))

        self._rewrite_bundle(duplicate)
        result = seal.verify_seal(self.tmp, "1")

        self.assertEqual(result["status"], "error")
        self.assertTrue(any("duplicate" in error for error in result["errors"]))

    def test_verify_rejects_bundle_project_replay(self) -> None:
        _seed_some_artifacts(self.tmp)
        seal.create_seal(self.tmp, "1")
        self._rewrite_bundle(
            lambda bundle: bundle.__setitem__("project_id", "another-project")
        )

        result = seal.verify_seal(self.tmp, "1")

        self.assertEqual(result["status"], "error")
        self.assertTrue(any("project_id" in error for error in result["errors"]))


class SealG5HookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-seal-g5-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_auto_seal_on_g5_writes_seal_and_audit(self) -> None:
        # Seed at least one artifact so something gets sealed.
        _seed_some_artifacts(self.tmp)

        sign_lib._auto_seal_on_g5(self.tmp)

        trail = self.tmp / ".signalos" / "AUDIT_TRAIL.jsonl"
        rows = [json.loads(line) for line in trail.read_text(encoding="utf-8").splitlines() if line]
        seal_rows = [r for r in rows if r["action"] == "g5-seal-result"]
        self.assertEqual(len(seal_rows), 1)
        self.assertEqual(seal_rows[0]["status"], "ok")

        wave = seal_rows[0]["wave"]
        seal_file = self.tmp / ".signalos" / "integrity" / f"seal-{wave}.json"
        self.assertTrue(seal_file.is_file())

    def test_auto_seal_never_raises_when_internals_break(self) -> None:
        # Point the seal module at a non-existent repo subdir; create_seal
        # still succeeds (it tolerates missing files) so this just confirms
        # the hook is genuinely best-effort and records an outcome.
        bogus = self.tmp / "does-not-exist"
        try:
            sign_lib._auto_seal_on_g5(bogus)
        except Exception as exc:
            self.fail(f"_auto_seal_on_g5 raised: {exc}")


class SealCliRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-seal-cli-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_top_level_cli_forwards_create(self) -> None:
        _seed_some_artifacts(self.tmp)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos", "seal", "create",
                "--wave", "9",
                "--repo-root", str(self.tmp),
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["wave"], "9")
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
