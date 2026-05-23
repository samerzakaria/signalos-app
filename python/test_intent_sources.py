"""Focused coverage for source intent and PRD/spec ingestion."""

from __future__ import annotations

import contextlib
import hashlib
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
from signalos_lib.commands import intent as intent_command
from signalos_lib.commands import validate_cmd as validate_command


class IntentSourceCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-intent-source-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_intent(self, args: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = intent_command.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_prompt_source_persists_deterministic_initial_intent(self) -> None:
        phrase = "Build a task management system"
        code, stdout, stderr = self._run_intent([
            phrase,
            "--save-source",
            "--repo-root",
            str(self.tmp),
            "--json",
        ])

        self.assertIn(code, {0, 1}, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["sources"][0]["record_path"], ".signalos/sources/initial-intent.json")

        record_path = self.tmp / ".signalos" / "sources" / "initial-intent.json"
        before = record_path.read_text(encoding="utf-8")
        record = json.loads(before)
        self.assertEqual(record["schema_version"], "signalos.source-intent.v1")
        self.assertEqual(record["kind"], "prompt")
        self.assertEqual(record["source_type"], "prompt")
        self.assertEqual(record["text"], phrase)
        self.assertEqual(
            record["fingerprint"],
            {
                "algorithm": "sha256",
                "value": hashlib.sha256(phrase.encode("utf-8")).hexdigest(),
            },
        )
        self.assertNotIn("created_at", record)

        code, _stdout, stderr = self._run_intent([
            phrase,
            "--save-source",
            "--repo-root",
            str(self.tmp),
            "--json",
        ])
        self.assertIn(code, {0, 1}, stderr)
        self.assertEqual(record_path.read_text(encoding="utf-8"), before)

    def test_prd_file_import_copies_file_and_satisfies_traceability_check(self) -> None:
        source = self.tmp / "Task PRD.md"
        body = "# Task PRD\n\nUsers can create and complete tasks.\n"
        source.write_bytes(body.encode("utf-8"))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        short = digest[:16]

        code, stdout, stderr = self._run_intent([
            "--source-file",
            str(source),
            "--source-kind",
            "prd",
            "--repo-root",
            str(self.tmp),
            "--json",
        ])

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        source_record = payload["sources"][0]
        self.assertEqual(source_record["kind"], "prd")
        self.assertEqual(source_record["stored_path"], f".signalos/sources/prd-{short}.md")
        self.assertEqual(source_record["record_path"], f".signalos/sources/source-prd-{short}.json")
        self.assertEqual((self.tmp / source_record["stored_path"]).read_text(encoding="utf-8"), body)

        metadata = json.loads((self.tmp / source_record["record_path"]).read_text(encoding="utf-8"))
        self.assertEqual(metadata["original_name"], "Task PRD.md")
        self.assertEqual(metadata["fingerprint"]["value"], digest)

        validate_stdout = io.StringIO()
        with contextlib.redirect_stdout(validate_stdout):
            validate_code = validate_command.main([
                "--repo-root",
                str(self.tmp),
                "--group",
                "layer1",
                "--validator",
                "layer1-source-traceability",
                "--json",
            ])
        validate_payload = json.loads(validate_stdout.getvalue())
        self.assertEqual(validate_code, 0, validate_payload)
        self.assertEqual(validate_payload["status"], "PASS")

    def test_import_rejects_files_over_configured_limit(self) -> None:
        source = self.tmp / "large-spec.md"
        source.write_text("too large", encoding="utf-8")

        code, _stdout, stderr = self._run_intent([
            "--source-file",
            str(source),
            "--repo-root",
            str(self.tmp),
            "--max-source-bytes",
            "3",
            "--json",
        ])

        self.assertEqual(code, 2)
        self.assertIn("source file exceeds 3 bytes", stderr)
        self.assertFalse((self.tmp / ".signalos" / "sources").exists())

    def test_top_level_cli_forwards_source_file_options(self) -> None:
        source = self.tmp / "spec.txt"
        source.write_text("Spec text\n", encoding="utf-8")
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "intent",
                "--source-file",
                str(source),
                "--source-kind",
                "spec",
                "--repo-root",
                str(self.tmp),
                "--json",
            ])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["sources"][0]["kind"], "spec")


if __name__ == "__main__":
    unittest.main()
