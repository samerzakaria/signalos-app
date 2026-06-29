"""Focused coverage for `signalos validate --group layer1`."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    _write(root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md", "# Soul\n\nsecurity_surfaces:\n  - webview\n  - ipc\n")
    _write(root / "core" / "governance" / "Governance" / "CONSTITUTION.md", "# Constitution\n\nsecurity_surfaces:\n  - webview\n  - ipc\n")
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
        self.assertEqual(payload["summary"]["total"], 14)
        self.assertEqual(payload["summary"]["failed"], 0)
        security = next(
            result
            for result in payload["results"]
            if result["name"] == "security-posture-guard"
        )
        self.assertEqual(security["details"]["method"], "python")
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
                "layer1-profile",
                "layer1-path-safety",
                "detect-bypass",
                "validate-guidance-obligations",
                "agent-prompt-contracts",
                "constitution-integrity",
                "security-posture-guard",
            },
        )

    def test_layer1_security_posture_does_not_shell_out(self) -> None:
        _make_layer1_repo(self.tmp)

        with patch("signalos_lib.validate_cmd.subprocess.run") as run:
            code, payload = self._run_validate([
                "--repo-root",
                str(self.tmp),
                "--group",
                "layer1",
                "--validator",
                "security-posture-guard",
                "--json",
            ])

        self.assertEqual(code, 0, payload)
        self.assertFalse(run.called)
        self.assertEqual(payload["results"][0]["details"]["method"], "python")

    def test_layer1_agent_prompt_contracts_fail_when_installed_prompt_incomplete(self) -> None:
        _make_layer1_repo(self.tmp)
        _write(
            self.tmp / "core" / "execution" / "agents" / "build.md",
            "## Purpose\n",
        )

        code, payload = self._run_validate([
            "--repo-root",
            str(self.tmp),
            "--group",
            "layer1",
            "--validator",
            "agent-prompt-contracts",
            "--json",
        ])

        self.assertEqual(code, 2, payload)
        self.assertEqual(payload["results"][0]["name"], "agent-prompt-contracts")
        self.assertEqual(payload["results"][0]["status"], "FAIL")
        self.assertIn("Success criteria", json.dumps(payload["results"][0]["details"]))

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

    def test_top_level_detect_bypass_reports_agent_scope_violation(self) -> None:
        from signalos_lib.product.agent_packets import build_agent_packet, write_agent_packet

        _make_layer1_repo(self.tmp)
        packet = build_agent_packet(
            repo_root=self.tmp,
            intent={
                "product_name": "Task App",
                "product_type": "task-management",
                "entities": ["task"],
                "primary_workflows": ["create task"],
            },
            blueprint={"id": "task-management"},
            acceptance_matrix={"criteria": []},
            profile="generic",
            wave="1",
            tasks=[{"id": "T1", "title": "Implement task creation"}],
            allowed_paths=["src/**", "tests/**"],
        )
        run_dir = write_agent_packet(packet, self.tmp)
        _write(
            run_dir / "RESULT.json",
            json.dumps({
                "run_id": packet["run_id"],
                "status": "completed",
                "files_written": [".signalos/hack.json"],
                "actions_taken": [],
                "validation_results": {},
            }) + "\n",
        )
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "detect-bypass",
                "--repo-root",
                str(self.tmp),
                "--json",
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["status"], "FAIL")
        self.assertTrue(any("agent result" in item for item in payload["violations"]))

    def test_top_level_guidance_obligations_writes_evidence(self) -> None:
        from signalos_lib.product.generation import prepare_generation

        _make_layer1_repo(self.tmp)
        prepare_generation(
            repo_root=self.tmp,
            intent={
                "product_name": "Task App",
                "product_type": "task-management",
                "entities": ["task"],
                "primary_workflows": ["create task"],
                "ux_surfaces": ["task list"],
            },
            blueprint={
                "id": "task-management",
                "entities": [{"name": "Task", "fields": []}],
                "workflows": [{"name": "create task"}],
                "ui": ["task-list"],
            },
            profile="generic",
            acceptance_matrix={"criteria": []},
        )
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "validate-guidance-obligations",
                "--repo-root",
                str(self.tmp),
                "--json",
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue((self.tmp / payload["evidence_path"]).is_file())

    def test_top_level_guidance_obligations_resolves_staged_paths(self) -> None:
        _make_layer1_repo(self.tmp)
        subprocess.run(["git", "init"], cwd=self.tmp, check=True, capture_output=True, text=True)
        _write(self.tmp / "src" / "components" / "TaskList.tsx", "export function TaskList() { return null; }\n")
        subprocess.run(["git", "add", "src/components/TaskList.tsx"], cwd=self.tmp, check=True, capture_output=True, text=True)
        loaded = self.tmp / ".signalos" / "loaded-guidance.txt"
        _write(
            loaded,
            "\n".join([
                "test-driven-development",
                "test-generation",
                "verification-before-completion",
                "design",
                "e2e-testing",
            ]) + "\n",
        )
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "validate-guidance-obligations",
                "--repo-root",
                str(self.tmp),
                "--staged",
                "--loaded",
                str(loaded),
                "--stack",
                "react-vite",
                "--json",
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["mode"], "staged")
        self.assertIn("src/components/TaskList.tsx", payload["touched_paths"])
        self.assertTrue(payload["resolved_obligations"]["resolved"])


if __name__ == "__main__":
    unittest.main()
