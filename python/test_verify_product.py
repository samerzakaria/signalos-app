"""Focused tests for `signalos verify-product`."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import cli
from signalos_lib.commands import verify_product


def _write_profile(profile_dir: Path, profile_id: str, commands: dict, preview: dict | None = None) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "id": profile_id,
        "name": profile_id,
        "description": "test profile",
        "required_templates": [],
        "ci": {
            "enabled": False,
            "files": [],
            "templates": [],
            "disabled_reason": "test profile has no CI",
        },
        "commands": {
            "install": None,
            "build": None,
            "test": None,
            "lint": None,
            "preview": None,
            **commands,
        },
        "preview": preview
        or {
            "mode": "none",
            "command": None,
            "url": None,
            "requires_install": False,
            "disabled_reason": "no preview in test profile",
        },
        "validator_groups": [],
    }
    (profile_dir / f"{profile_id}.json").write_text(json.dumps(payload), encoding="utf-8")


class VerifyProductTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-verify-product-"))
        (self.tmp / ".signalos").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generic_profile_reports_disabled_commands_as_skips(self) -> None:
        payload = verify_product.verify_product(
            self.tmp,
            profile_id="generic",
            wave="W9",
            include_qa=False,
            include_e2e=False,
        )

        self.assertEqual(payload["schema_version"], "signalos.verify_product.v1")
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["evidence_dir"], ".signalos/evidence/W9")
        self.assertTrue((self.tmp / ".signalos" / "evidence" / "W9" / "verify-product.json").is_file())
        checks = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(checks["build"]["status"], "SKIP")
        self.assertIn("does not declare", checks["build"]["reason"])
        self.assertEqual(checks["test"]["status"], "SKIP")
        self.assertEqual(checks["lint"]["status"], "SKIP")

    def test_missing_repo_root_fails_without_creating_directory(self) -> None:
        missing = self.tmp / "does-not-exist"

        payload = verify_product.verify_product(
            missing,
            profile_id="generic",
            wave="bad-root",
            include_qa=False,
            include_e2e=False,
        )

        self.assertEqual(payload["status"], "FAIL")
        self.assertIsNone(payload["evidence_path"])
        self.assertFalse(missing.exists())
        checks = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(checks["workspace"]["status"], "FAIL")

    def test_profile_commands_capture_pass_and_fail_logs(self) -> None:
        profile_dir = self.tmp / "profiles"
        _write_profile(
            profile_dir,
            "cmds",
            {
                "build": {
                    "name": "build pass",
                    "argv": [sys.executable, "-c", "print('build ok')"],
                    "required": True,
                },
                "test": {
                    "name": "test fail",
                    "argv": [sys.executable, "-c", "import sys; print('test bad'); sys.exit(3)"],
                    "required": False,
                },
            },
        )

        payload = verify_product.verify_product(
            self.tmp,
            profile_id="cmds",
            profile_dir=profile_dir,
            wave="W10",
            include_qa=False,
            include_e2e=False,
            include_lint=False,
        )

        self.assertEqual(payload["status"], "FAIL")
        checks = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(checks["build"]["status"], "PASS")
        self.assertEqual(checks["test"]["status"], "FAIL")
        self.assertEqual(checks["test"]["exit_code"], 3)
        self.assertTrue((self.tmp / checks["build"]["evidence_path"]).is_file())
        self.assertIn("test bad", (self.tmp / checks["test"]["evidence_path"]).read_text(encoding="utf-8"))

    def test_qa_runner_is_composed_when_scenarios_exist(self) -> None:
        scenario = self.tmp / "core" / "governance" / "QA" / "scenarios" / "smoke.yaml"
        scenario.parent.mkdir(parents=True)
        scenario.write_text("id: qa-1\nname: smoke\nurl: http://example.test\n", encoding="utf-8")
        fake_pack = SimpleNamespace(
            fail_count=0,
            as_dict=lambda: {
                "scenario_count": 1,
                "fail": 0,
                "qa_evidence_path": str(self.tmp / ".signalos" / "evidence" / "W11" / "qa-evidence.json"),
            },
        )

        with mock.patch("signalos_lib.qa_runner.run_scenario_suite", return_value=fake_pack) as runner:
            payload = verify_product.verify_product(
                self.tmp,
                profile_id="generic",
                wave="W11",
                include_build=False,
                include_test=False,
                include_lint=False,
                include_e2e=False,
            )

        self.assertTrue(runner.called)
        checks = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(checks["qa"]["status"], "PASS")
        self.assertEqual(checks["qa"]["evidence_path"], ".signalos/evidence/W11/qa-evidence.json")

    def test_e2e_runner_is_composed_when_profile_preview_is_enabled(self) -> None:
        profile_dir = self.tmp / "profiles"
        _write_profile(
            profile_dir,
            "web",
            {
                "preview": {
                    "name": "dev",
                    "argv": [sys.executable, "-c", "print('dev')"],
                    "required": True,
                }
            },
            preview={
                "mode": "command",
                "command": "preview",
                "url": "http://127.0.0.1:5173",
                "requires_install": False,
                "disabled_reason": None,
            },
        )

        with mock.patch(
            "signalos_lib.e2e_runner.run_e2e_task",
            return_value={"ok": True, "failure": None, "url": "http://127.0.0.1:5173/"},
        ) as runner:
            payload = verify_product.verify_product(
                self.tmp,
                profile_id="web",
                profile_dir=profile_dir,
                wave="W12",
                include_build=False,
                include_test=False,
                include_lint=False,
                include_qa=False,
            )

        self.assertTrue(runner.called)
        checks = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(checks["e2e"]["status"], "PASS")
        self.assertTrue((self.tmp / checks["e2e"]["evidence_path"]).is_file())

    def test_top_level_cli_emits_json(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "verify-product",
                "--repo-root",
                str(self.tmp),
                "--profile",
                "generic",
                "--wave",
                "cli",
                "--json",
                "--no-qa",
                "--no-e2e",
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["schema_version"], "signalos.verify_product.v1")
        self.assertEqual(payload["wave"], "cli")
        self.assertEqual(payload["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
