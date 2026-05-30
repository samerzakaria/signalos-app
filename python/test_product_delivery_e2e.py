"""End-to-end tests for the product delivery pipeline.

These tests run the full delivery state machine in temporary directories
with dry_run=True to avoid needing npm/node installed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from signalos_lib.product.delivery import run_delivery
from signalos_lib.product.lifecycle import load_delivery_state
from signalos_lib.orchestrator import _SKILL_KEY_TO_PATH


class TestDeliveryE2E(unittest.TestCase):
    """Full delivery pipeline tests."""

    def test_greenfield_generic_full_pipeline(self):
        """Full prompt -> packet -> proof -> handoff flow (generic profile)."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "test-product"
            closeout = run_delivery(
                prompt="Build me a task management app with projects and tasks",
                name="test-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )

            # Closeout exists and has required fields
            self.assertEqual(closeout["product_name"], "test-product")
            self.assertEqual(closeout["profile"], "generic")
            self.assertEqual(closeout["repo_path"], str(repo_root))
            self.assertIsNotNone(closeout["deploy_status"])
            self.assertGreater(len(closeout["how_to_run"]), 0)
            # dry_run cannot be "ready"
            self.assertNotEqual(closeout["closure_level"], "ready")

            # Evidence files exist
            signalos = repo_root / ".signalos"
            self.assertTrue((signalos / "product" / "INTENT.json").exists())
            self.assertTrue((signalos / "product" / "DELIVERY_STATE.json").exists())
            self.assertTrue((signalos / "product" / "ACCEPTANCE_MATRIX.json").exists())
            self.assertTrue((signalos / "product" / "STRATEGY_REVIEW.yaml").exists())
            self.assertTrue((signalos / "product" / "SCOPE_DECISIONS.yaml").exists())
            self.assertTrue((signalos / "product" / "ARCH_REVIEW.yaml").exists())
            self.assertTrue((signalos / "product" / "REVIEW_READINESS.yaml").exists())
            self.assertTrue(
                (signalos / "designs" / "1" / "DESIGN_DECISIONS.yaml").exists()
            )
            self.assertTrue((signalos / "product" / "CLOSEOUT.json").exists())
            self.assertTrue((signalos / "product" / "CLOSEOUT.md").exists())
            self.assertTrue((signalos / "handoffs").exists())

    def test_minimum_prompt_writes_enterprise_ownership_contract(self):
        """Minimum non-technical prompt expands into enterprise scope + ownership."""
        prompt = (
            "I want to do a task management system to manage my team's tasks, "
            "utulization, workload and their KPIs"
        )
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "team-ops"
            closeout = run_delivery(
                prompt=prompt,
                name=None,
                repo_root=repo_root,
                mode="greenfield",
                profile="auto",
                blueprint="auto",
                deploy="prepare",
                dry_run=True,
            )

            self.assertTrue((repo_root / ".git").exists())
            self.assertTrue((repo_root / "package.json").exists())
            self.assertTrue((repo_root / "vite.config.ts").exists())
            self.assertTrue((repo_root / "src" / "App.tsx").exists())
            self.assertTrue((repo_root / "src" / "App.test.tsx").exists())
            self.assertEqual(closeout["profile"], "react-vite")

            signalos = repo_root / ".signalos"
            intent = json.loads(
                (signalos / "product" / "INTENT.json").read_text(encoding="utf-8")
            )
            self.assertEqual(intent["product_type"], "task-management")
            self.assertEqual(intent["product_name"], "Team Task Operations")
            self.assertIn("WorkloadSnapshot", intent["entities"])
            self.assertIn("KpiMetric", intent["entities"])
            self.assertIn("rbac", intent["auth_requirements"])
            self.assertIn("audit-trail", intent["audit_requirements"])

            questions = json.loads(
                (signalos / "product" / "QUESTIONS.json").read_text(encoding="utf-8")
            )
            self.assertEqual(questions["blocking"], [])

            acceptance = json.loads(
                (signalos / "product" / "ACCEPTANCE_MATRIX.json").read_text(encoding="utf-8")
            )
            descriptions = " ".join(c["description"] for c in acceptance["criteria"]).lower()
            self.assertIn("workload", descriptions)
            self.assertIn("utilization", descriptions)
            self.assertIn("kpi", descriptions)
            self.assertIn("role-based access", descriptions)

            ownership = json.loads(
                (signalos / "product" / "DELIVERY_OWNERSHIP.json").read_text(encoding="utf-8")
            )
            owners = {step["owner"] for step in ownership["ownership"]}
            self.assertIn("signalos-system", owners)
            self.assertIn("signalos-agent-team", owners)
            self.assertTrue(ownership["minimum_prompt_contract"]["accepted_minimum_prompt"])
            self.assertEqual(
                ownership["minimum_prompt_contract"]["technical_choices_owned_by"],
                "signalos-system",
            )

            runs_dir = signalos / "product" / "agent-runs"
            run_dirs = [path for path in runs_dir.iterdir() if path.is_dir()]
            self.assertEqual(len(run_dirs), 1)
            scope = json.loads((run_dirs[0] / "scope.json").read_text(encoding="utf-8"))
            self.assertIn("delivery_ownership", scope)
            self.assertEqual(scope["delivery_ownership"]["product_type"], "task-management")
            self.assertIn(".signalos/", scope["forbidden_paths"])
            self.assertIn(".git/", scope["forbidden_paths"])
            self.assertTrue(scope["allowed_paths"])
            self.assertFalse(
                any(path.startswith(".signalos") for path in scope["allowed_paths"])
            )
            self.assertTrue((run_dirs[0] / "PACKET.md").exists())
            self.assertTrue((run_dirs[0] / "validation-plan.json").exists())
            self.assertEqual(closeout["delivery_ownership"]["product_type"], "task-management")
            self.assertEqual(closeout["deploy_status"], "prepare")
            self.assertNotEqual(closeout["closure_level"], "ready")

    def test_greenfield_generic_known_limitations(self):
        """Generic profile reports known limitations."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "generic-product"
            closeout = run_delivery(
                prompt="Build me a simple utility",
                name="generic-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            self.assertNotEqual(closeout["closure_level"], "ready")
            self.assertGreater(len(closeout["known_limitations"]), 0)

    def test_delivery_with_prepare_deploy(self):
        """Prepare mode creates evidence but does not deploy."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "deploy-test"
            closeout = run_delivery(
                prompt="Build a dashboard",
                name="deploy-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="prepare",
                dry_run=True,
            )
            signalos = repo_root / ".signalos"
            self.assertTrue((signalos / "product" / "DEPLOY_DECISION.json").exists())
            self.assertTrue((signalos / "product" / "DEPLOY_EVIDENCE.json").exists())

    def test_delivery_json_serializable(self):
        """Closeout dict is JSON-serializable."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "json-test"
            closeout = run_delivery(
                prompt="Build a tool",
                name="json-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
                json_output=False,
            )
            # Must not raise
            serialized = json.dumps(closeout)
            self.assertIsInstance(json.loads(serialized), dict)

    def test_delivery_state_machine_phases(self):
        """Delivery state progresses through all phases to closed."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "state-test"
            run_delivery(
                prompt="Build something",
                name="state-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            state = load_delivery_state(repo_root)
            self.assertIsNotNone(state)
            self.assertEqual(state["phase"], "closed")

    def test_cli_command_registered(self):
        """deliver command is registered in CLI parser."""
        from signalos_lib.cli import _build_parser

        parser = _build_parser()
        # Parse deliver with required --prompt
        args, _ = parser.parse_known_args(
            ["deliver", "--prompt", "Build a thing"]
        )
        self.assertEqual(args.command, "deliver")
        self.assertEqual(args.prompt, "Build a thing")

    def test_cli_deliver_defaults(self):
        """deliver command has expected default values."""
        from signalos_lib.cli import _build_parser

        parser = _build_parser()
        args, _ = parser.parse_known_args(
            ["deliver", "--prompt", "test"]
        )
        self.assertEqual(args.mode, "auto")
        self.assertEqual(args.profile, "auto")
        self.assertEqual(args.blueprint, "auto")
        self.assertEqual(args.deploy, "none")
        self.assertFalse(args.yes)
        self.assertFalse(args.dry_run)
        self.assertEqual(args.max_repair_cycles, 3)
        self.assertEqual(args.agent, "none")
        self.assertFalse(args.as_json)

    def test_adopt_mode_preserves_files(self):
        """Adopt mode does not overwrite existing source files."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "existing-repo"
            repo_root.mkdir()
            (repo_root / "existing.txt").write_text("precious content")
            closeout = run_delivery(
                prompt="Add features to this repo",
                name="existing-repo",
                repo_root=repo_root,
                mode="adopt",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            self.assertEqual(
                (repo_root / "existing.txt").read_text(), "precious content"
            )

    def test_intent_file_written(self):
        """INTENT.json is written with extracted fields."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "intent-test"
            run_delivery(
                prompt="Build a task management app with projects and tasks",
                name="intent-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            intent_path = repo_root / ".signalos" / "product" / "INTENT.json"
            self.assertTrue(intent_path.exists())
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            self.assertEqual(intent["product_name"], "intent-test")

    def test_acceptance_matrix_written(self):
        """ACCEPTANCE_MATRIX.json is written with criteria."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "accept-test"
            run_delivery(
                prompt="Build a task management app with projects and tasks",
                name="accept-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            matrix_path = (
                repo_root / ".signalos" / "product" / "ACCEPTANCE_MATRIX.json"
            )
            self.assertTrue(matrix_path.exists())
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
            self.assertIn("criteria", matrix)
            self.assertIn("test_scenarios", matrix)

    def test_handoff_files_created(self):
        """Handoff directory contains all three files."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "handoff-test"
            run_delivery(
                prompt="Build something",
                name="handoff-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            handoffs = repo_root / ".signalos" / "handoffs"
            self.assertTrue(handoffs.exists())
            self.assertTrue((handoffs / "product-summary.md").exists())
            self.assertTrue((handoffs / "test-evidence.md").exists())
            self.assertTrue((handoffs / "operator-runbook.md").exists())

    def test_proof_artifacts_created(self):
        """Proof artifacts directory is created with smoke files."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "proof-test"
            run_delivery(
                prompt="Build something",
                name="proof-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            proof_dir = (
                repo_root / ".signalos" / "product" / "proof" / "runtime"
            )
            self.assertTrue(proof_dir.exists())
            self.assertTrue((proof_dir / "smoke.json").exists())
            self.assertTrue((proof_dir / "ux-smoke.json").exists())

    def test_deploy_none_no_evidence(self):
        """deploy=none does not create DEPLOY_EVIDENCE.json."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "no-deploy"
            run_delivery(
                prompt="Build something",
                name="no-deploy",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            evidence_path = (
                repo_root / ".signalos" / "product" / "DEPLOY_EVIDENCE.json"
            )
            self.assertFalse(evidence_path.exists())

    def test_delivery_with_no_name_uses_dir_name(self):
        """When no name is provided, repo dir name is used."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "auto-named"
            closeout = run_delivery(
                prompt="Build something",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            self.assertEqual(closeout["product_name"], "auto-named")

    def test_generation_packet_written(self):
        """GENERATION_PACKET.json is written during delivery."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "packet-test"
            run_delivery(
                prompt="Build a task management app with projects and tasks",
                name="packet-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )
            packet_path = (
                repo_root / ".signalos" / "product" / "GENERATION_PACKET.json"
            )
            self.assertTrue(packet_path.exists())
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            self.assertEqual(
                packet["schema_version"], "signalos.generation_packet.v1"
            )
            self.assertGreater(len(packet["file_specs"]), 0)

    def test_delivery_writes_agent_packet_with_skills(self):
        """Generated agent packet exposes full and applicable skill contracts."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "agent-skill-test"
            run_delivery(
                prompt="Build a task management app with projects and tasks",
                name="agent-skill-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )
            runs_dir = repo_root / ".signalos" / "product" / "agent-runs"
            run_dirs = [path for path in runs_dir.iterdir() if path.is_dir()]
            self.assertEqual(len(run_dirs), 1)
            scope = json.loads((run_dirs[0] / "scope.json").read_text(encoding="utf-8"))
            self.assertEqual(scope["schema_version"], "signalos.agent_packet.v1")
            self.assertEqual(len(scope["skills_catalog"]), len(_SKILL_KEY_TO_PATH))
            self.assertGreaterEqual(len(scope["applicable_skills"]), 3)
            self.assertTrue((run_dirs[0] / "skills-catalog.json").exists())
            self.assertTrue((run_dirs[0] / "applicable-skills.md").exists())

    def test_no_application_code_written(self):
        """Delivery does NOT write application source code to disk."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "no-code-test"
            run_delivery(
                prompt="Build a task management app with projects and tasks",
                name="no-code-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )
            # No Python source files should exist (only .signalos governance)
            for child in repo_root.rglob("*.py"):
                rel = child.relative_to(repo_root).as_posix()
                if not rel.startswith(".signalos"):
                    self.fail(f"Application code written to disk: {rel}")


class TestDeliveryRepairAndWiring(unittest.TestCase):
    """Tests for repair loop wiring, task IDs, and workspace metadata."""

    def test_repair_loop_invoked_when_validation_fails(self):
        """When validation fails and agent_mode is set, repair loop runs."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repair-test"

            # Monkeypatch run_validation to return can_close_delivery=False
            fake_val_result = {
                "schema_version": "signalos.validation_result.v1",
                "profile": "generic",
                "dry_run": False,
                "results": {},
                "summary": {"total_checks": 1, "passed": 0, "failed": 1,
                            "skipped": 0, "blocked": 0},
                "can_close_delivery": False,
                "blockers": ["build failed"],
            }

            with patch(
                "signalos_lib.product.delivery.run_validation",
                return_value=fake_val_result,
            ), patch(
                "signalos_lib.product.delivery.run_repair_loop",
            ) as mock_repair:
                mock_repair.return_value = {
                    "status": "awaiting_agent",
                    "cycles_used": 1,
                    "max_cycles": 3,
                    "repairs": [],
                    "final_validation": fake_val_result,
                }
                run_delivery(
                    prompt="Build a task app",
                    name="repair-test",
                    repo_root=repo_root,
                    mode="greenfield",
                    profile="generic",
                    deploy="none",
                    dry_run=False,
                    agent_mode="packet-only",
                )
                mock_repair.assert_called_once()
                call_kwargs = mock_repair.call_args[1]
                self.assertEqual(call_kwargs["agent_mode"], "packet-only")
                self.assertEqual(call_kwargs["max_cycles"], 3)

    def test_repair_loop_not_invoked_when_agent_mode_none(self):
        """Repair loop is NOT called when agent_mode is 'none'."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "no-repair"

            fake_val_result = {
                "schema_version": "signalos.validation_result.v1",
                "profile": "generic",
                "dry_run": False,
                "results": {},
                "summary": {"total_checks": 1, "passed": 0, "failed": 1,
                            "skipped": 0, "blocked": 0},
                "can_close_delivery": False,
                "blockers": ["build failed"],
            }

            with patch(
                "signalos_lib.product.delivery.run_validation",
                return_value=fake_val_result,
            ), patch(
                "signalos_lib.product.delivery.run_repair_loop",
            ) as mock_repair:
                run_delivery(
                    prompt="Build a task app",
                    name="no-repair",
                    repo_root=repo_root,
                    mode="greenfield",
                    profile="generic",
                    deploy="none",
                    dry_run=False,
                    agent_mode="none",
                )
                mock_repair.assert_not_called()

    def test_task_ids_passed_to_generation(self):
        """Generation manifest contains non-None task_ids on file records."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "taskid-test"
            run_delivery(
                prompt="Build a task management app with projects and tasks",
                name="taskid-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )
            manifest_path = (
                repo_root / ".signalos" / "product" / "GENERATION_MANIFEST.json"
            )
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # task_ids list should be non-empty
            self.assertGreater(len(manifest.get("task_ids", [])), 0)
            # At least some files should have task_id set
            files_with_task = [
                f for f in manifest.get("files", [])
                if f.get("task_id") is not None
            ]
            self.assertGreater(len(files_with_task), 0)

    def test_acceptance_matrix_linked_to_generation(self):
        """Generation manifest files have acceptance_ids linked."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "accept-link-test"
            run_delivery(
                prompt="Build a task management app with projects and tasks",
                name="accept-link-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )
            manifest_path = (
                repo_root / ".signalos" / "product" / "GENERATION_MANIFEST.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files_with_acceptance = [
                f for f in manifest.get("files", [])
                if f.get("acceptance_id") is not None
            ]
            self.assertGreater(len(files_with_acceptance), 0)

    def test_workspace_json_written(self):
        """WORKSPACE.json is written with correct content."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "ws-test"
            run_delivery(
                prompt="Build a tool",
                name="ws-test",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            ws_path = repo_root / ".signalos" / "product" / "WORKSPACE.json"
            self.assertTrue(ws_path.exists())
            ws = json.loads(ws_path.read_text(encoding="utf-8"))
            self.assertEqual(ws["product_name"], "ws-test")
            self.assertEqual(ws["profile"], "generic")
            self.assertEqual(ws["repo_root"], str(repo_root))
            self.assertIn("created_at", ws)

    def test_closeout_has_workspace_info(self):
        """Closeout dict includes workspace switch metadata."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "ws-closeout"
            closeout = run_delivery(
                prompt="Build a tool",
                name="ws-closeout",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
            )
            self.assertIn("workspace", closeout)
            ws = closeout["workspace"]
            self.assertEqual(ws["product_name"], "ws-closeout")
            self.assertEqual(ws["profile"], "generic")
            self.assertEqual(ws["repo_root"], str(repo_root))
            self.assertTrue(ws["switch_recommended"])


class TestDeliveryHITL(unittest.TestCase):
    """Tests for HITL questions/assumptions wiring."""

    def test_delivery_writes_assumptions(self):
        """Delivery with vague prompt writes ASSUMPTIONS.json."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "vague-product"
            run_delivery(
                prompt="Build me something",
                name="vague-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
                yes=True,
            )
            assumptions_path = (
                repo_root / ".signalos" / "product" / "ASSUMPTIONS.json"
            )
            self.assertTrue(assumptions_path.exists())
            assumptions = json.loads(
                assumptions_path.read_text(encoding="utf-8")
            )
            self.assertIsInstance(assumptions, list)
            self.assertGreater(len(assumptions), 0)
            # Each assumption has required keys
            for a in assumptions:
                self.assertIn("field", a)
                self.assertIn("assumed_value", a)
                self.assertIn("reason", a)

    def test_delivery_writes_questions_for_ui(self):
        """Delivery with vague prompt (no --yes) writes QUESTIONS.json with blocking."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "questions-product"
            run_delivery(
                prompt="Build me something",
                name="questions-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
                yes=False,
            )
            questions_path = (
                repo_root / ".signalos" / "product" / "QUESTIONS.json"
            )
            self.assertTrue(questions_path.exists())
            data = json.loads(questions_path.read_text(encoding="utf-8"))
            self.assertIn("questions", data)
            self.assertIn("blocking", data)
            self.assertIn("answered", data)
            self.assertIn("assumptions_used", data)
            self.assertFalse(data["answered"])
            self.assertTrue(data["assumptions_used"])
            # Vague prompt should have blocking questions
            self.assertGreater(len(data["blocking"]), 0)

    def test_delivery_with_yes_skips_blocking(self):
        """Delivery with yes=True completes even with blocking questions."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "yes-product"
            closeout = run_delivery(
                prompt="Build me something",
                name="yes-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                deploy="none",
                dry_run=True,
                yes=True,
            )
            # Pipeline completes successfully
            self.assertIn("closure_level", closeout)
            self.assertEqual(closeout["product_name"], "yes-product")
            # Questions are still written for UI reference
            questions_path = (
                repo_root / ".signalos" / "product" / "QUESTIONS.json"
            )
            self.assertTrue(questions_path.exists())
            # Assumptions are also written
            assumptions_path = (
                repo_root / ".signalos" / "product" / "ASSUMPTIONS.json"
            )
            self.assertTrue(assumptions_path.exists())


if __name__ == "__main__":
    unittest.main()
