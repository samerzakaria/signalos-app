"""Tests for `signalos handoff`."""

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
from signalos_lib.commands import handoff


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class HandoffCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-handoff-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_closeout(self) -> None:
        _write_json(
            self.tmp / ".signalos" / "product" / "CLOSEOUT.json",
            {
                "schema_version": "signalos.product_closeout.v1",
                "product_name": "SupportApi",
                "repo_path": str(self.tmp),
                "profile": "node-api",
                "how_to_run": ["npm install", "npm test", "npm start"],
                "known_limitations": ["Acceptance criterion AC-002 is pending"],
                "tests_executed": [
                    {"category": "build", "status": "passed", "duration_s": 1.2},
                    {"category": "test", "status": "passed", "duration_s": 2.4},
                ],
            },
        )
        _write(
            self.tmp / ".signalos" / "AUDIT_TRAIL.jsonl",
            json.dumps({"action": "test.unit", "status": "passed"}) + "\n"
            + json.dumps({"action": "release-readiness", "status": "ready"}) + "\n",
        )
        _write(self.tmp / ".env.example", "DATABASE_URL=\nREDIS_URL=\n")

    def test_handoff_writes_operator_package_from_closeout(self) -> None:
        self._seed_closeout()

        payload = handoff.prepare_handoff(
            self.tmp,
            live_url="https://example.test",
            release_tag="v1.2.3",
            actor="qa-owner",
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "handoff-packaged")
        self.assertEqual(payload["product_name"], "SupportApi")
        self.assertEqual(payload["profile"], "node-api")
        self.assertEqual(payload["release_tag"], "v1.2.3")
        self.assertEqual(payload["live_url"], "https://example.test")

        handoff_dir = self.tmp / ".signalos" / "handoff"
        expected = {
            "HANDOFF.md",
            "live-url.md",
            "local-run.md",
            "env-requirements.md",
            "seeded-demo-data.md",
            "test-evidence.md",
            "known-limitations.md",
            "audit-gate-summary.md",
            "operator-runbook.md",
            "handoff-manifest.json",
        }
        self.assertTrue(expected.issubset({path.name for path in handoff_dir.iterdir()}))

        local_run = (handoff_dir / "local-run.md").read_text(encoding="utf-8")
        self.assertIn("npm test", local_run)
        env = (handoff_dir / "env-requirements.md").read_text(encoding="utf-8")
        self.assertIn("DATABASE_URL", env)
        limitations = (handoff_dir / "known-limitations.md").read_text(encoding="utf-8")
        self.assertIn("AC-002", limitations)
        manifest = json.loads((handoff_dir / "handoff-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "signalos.handoff.v1")

        audit_lines = [
            json.loads(line)
            for line in (self.tmp / ".signalos" / "AUDIT_TRAIL.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        self.assertEqual(audit_lines[-1]["action"], "handoff-packaged")
        self.assertEqual(audit_lines[-1]["release_tag"], "v1.2.3")

    def test_handoff_json_cli_outputs_manifest_summary(self) -> None:
        self._seed_closeout()
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = handoff.main([
                "--repo-root",
                str(self.tmp),
                "--release-tag",
                "v1",
                "--actor",
                "operator",
                "--json",
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "handoff-packaged")
        self.assertEqual(payload["actor"], "operator")

    def test_handoff_rejects_missing_repo_root(self) -> None:
        missing = self.tmp / "missing"
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            code = handoff.main(["--repo-root", str(missing)])

        self.assertEqual(code, handoff.EXIT_BAD_ARGS)
        self.assertIn("repo-root not found", stderr.getvalue())

    def test_top_level_cli_forwards_handoff(self) -> None:
        self._seed_closeout()
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "handoff",
                "--repo-root",
                str(self.tmp),
                "--release-tag",
                "v2",
                "--actor",
                "operator",
                "--json",
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["release_tag"], "v2")


if __name__ == "__main__":
    unittest.main()
