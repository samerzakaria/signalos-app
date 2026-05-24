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


class TestDeliveryE2E(unittest.TestCase):
    """Full delivery pipeline tests."""

    def test_greenfield_generic_full_pipeline(self):
        """Full prompt -> product -> proof -> handoff flow (generic profile)."""
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
            self.assertTrue((signalos / "product" / "CLOSEOUT.json").exists())
            self.assertTrue((signalos / "product" / "CLOSEOUT.md").exists())
            self.assertTrue((signalos / "handoffs").exists())

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


if __name__ == "__main__":
    unittest.main()
