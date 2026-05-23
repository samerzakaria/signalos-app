"""Focused coverage for `signalos validate --group layer1`."""

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
from signalos_lib.commands import validate_cmd as validate_command
from signalos_lib.validate_cmd import VALIDATOR_SEVERITY, run_validators


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_layer1_repo(root: Path) -> None:
    (root / ".signalos" / "sessions").mkdir(parents=True)
    _write(root / ".signalos" / "worktree-state.json", "{}\n")
    _write(root / ".signalos" / "AUDIT_TRAIL.jsonl", "")
    _write(
        root / ".signalos" / "sources" / "initial-intent.json",
        json.dumps({"kind": "prompt", "text": "Build a task management system"}) + "\n",
    )
    _write(root / ".signalos" / "unknowns.json", "[]\n")
    _write(root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md", "# Soul\n")
    _write(root / "core" / "governance" / "Governance" / "CONSTITUTION.md", "# Constitution\n")
    _write(root / "core" / "governance" / "Governance" / "DECISION-DNA.md", "# Decision DNA\n")


class ValidateLayer1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-validate-layer1-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_validate(self, args: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = validate_command.main(args)
        payload = json.loads(stdout.getvalue())
        return code, payload

    def test_default_validator_behavior_stays_core_scripts(self) -> None:
        results = run_validators(repo_root=self.tmp)

        self.assertEqual(len(results), len(VALIDATOR_SEVERITY))
        self.assertTrue(all(result.group == "core" for result in results))
        self.assertTrue(all(result.skipped for result in results))
        self.assertFalse(any(result.name.startswith("layer1-") for result in results))

    def test_layer1_group_passes_for_minimal_valid_repo(self) -> None:
        _make_layer1_repo(self.tmp)

        code, payload = self._run_validate([
            "--repo-root",
            str(self.tmp),
            "--group",
            "layer1",
            "--json",
        ])

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["schema_version"], "signalos.validate.v1")
        self.assertEqual(payload["group"], "layer1")
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["summary"]["total"], 8)
        self.assertEqual(payload["summary"]["failed"], 0)
        self.assertEqual(
            {result["name"] for result in payload["results"]},
            {
                "layer1-workspace-root",
                "layer1-runtime-state",
                "layer1-audit-trail",
                "layer1-governance-docs",
                "layer1-gates-readable",
                "layer1-source-traceability",
                "layer1-unknowns",
                "layer1-path-safety",
            },
        )

    def test_layer1_group_reports_halt_failure_for_missing_runtime(self) -> None:
        code, payload = self._run_validate([
            "--repo-root",
            str(self.tmp),
            "--group",
            "layer1",
            "--json",
        ])

        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["status"], "FAIL")
        self.assertGreaterEqual(payload["summary"]["halt_failures"], 1)
        failures = {
            result["name"]: result
            for result in payload["results"]
            if result["status"] == "FAIL"
        }
        self.assertIn("layer1-runtime-state", failures)
        self.assertIn(".signalos runtime state is incomplete", failures["layer1-runtime-state"]["message"])

    def test_layer1_validator_filter_runs_one_check(self) -> None:
        _make_layer1_repo(self.tmp)

        code, payload = self._run_validate([
            "--repo-root",
            str(self.tmp),
            "--group",
            "layer1",
            "--validator",
            "layer1-audit-trail",
            "--json",
        ])

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["results"][0]["name"], "layer1-audit-trail")

    def test_top_level_cli_forwards_group_argument(self) -> None:
        _make_layer1_repo(self.tmp)
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "validate",
                "--repo-root",
                str(self.tmp),
                "--group",
                "layer1",
                "--json",
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["group"], "layer1")


if __name__ == "__main__":
    unittest.main()
