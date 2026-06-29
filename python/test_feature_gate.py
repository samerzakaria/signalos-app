"""Tests for `signalos feature-gate` scope reconciliation."""

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
from signalos_lib.commands import feature_gate
from signalos_lib.product.delivery import _run_delivery_feature_gate, run_delivery


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class FeatureGateCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-feature-gate-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = feature_gate.main(argv)
        return code, json.loads(stdout.getvalue())

    def _active_wave(self, wave: str = "W01") -> None:
        _write(
            self.tmp / ".signalos" / "wave.json",
            json.dumps({"wave": wave, "status": "ACTIVE"}) + "\n",
        )

    def test_missing_wave_pointer_refuses_gate(self) -> None:
        code, payload = self._run([
            "Add reporting",
            "--repo-root",
            str(self.tmp),
            "--json",
            "--no-evidence",
        ])

        self.assertEqual(code, feature_gate.EXIT_WAVE_NOT_ACTIVE)
        self.assertEqual(payload["verdict"], "WAVE_NOT_ACTIVE")

    def test_in_scope_request_matches_backlog_expectation_and_prd_build_rows(self) -> None:
        self._active_wave()
        _write(
            self.tmp / ".signalos" / "waves" / "W01" / "BACKLOG.yaml",
            "backlog:\n  - title: Add invoice export workflow\n",
        )
        _write(
            self.tmp / ".signalos" / "waves" / "W01" / "EXPECTATION_MAP.md",
            "| 1 | Export invoices | billing | QA |\n",
        )
        _write(
            self.tmp / ".signalos" / "PRD_TRACEABILITY.md",
            "| Claim | Destination |\n| C-1 | BUILD invoice export |\n",
        )

        code, payload = self._run([
            "Add invoice export",
            "--repo-root",
            str(self.tmp),
            "--json",
            "--no-evidence",
        ])

        self.assertEqual(code, 0)
        self.assertEqual(payload["verdict"], "BUILD")
        self.assertGreaterEqual(payload["backlog_matches"], 1)
        self.assertGreaterEqual(payload["expectation_matches"], 1)
        self.assertGreaterEqual(payload["prd_matches"], 1)

    def test_out_of_scope_request_needs_answers(self) -> None:
        self._active_wave()

        code, payload = self._run([
            "Add analytics dashboard",
            "--repo-root",
            str(self.tmp),
            "--json",
            "--no-evidence",
        ])

        self.assertEqual(code, feature_gate.EXIT_NEEDS_ANSWERS)
        self.assertEqual(payload["verdict"], "NEEDS_ANSWERS")
        self.assertEqual(payload["total_matches"], 0)

    def test_q1_q2_no_defers_out_of_scope_request(self) -> None:
        self._active_wave()

        code, payload = self._run([
            "Add analytics dashboard",
            "--q1",
            "no",
            "--q2",
            "no",
            "--repo-root",
            str(self.tmp),
            "--json",
            "--no-evidence",
        ])

        self.assertEqual(code, feature_gate.EXIT_DEFER)
        self.assertEqual(payload["verdict"], "DEFER")

    def test_top_level_cli_forwards_feature_gate(self) -> None:
        self._active_wave()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "feature-gate",
                "Add analytics dashboard",
                "--q1",
                "yes",
                "--q2",
                "no",
                "--repo-root",
                str(self.tmp),
                "--json",
                "--no-evidence",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["verdict"], "BUILD")


class FeatureGateDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-feature-gate-delivery-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_delivery_records_skip_when_no_active_wave_pointer_exists(self) -> None:
        payload = _run_delivery_feature_gate(self.tmp, "Build support API")

        self.assertFalse(payload["executed"])
        self.assertFalse(payload["blocked"])
        evidence = self.tmp / ".signalos" / "product" / "FEATURE_GATE.json"
        self.assertTrue(evidence.is_file())
        saved = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertFalse(saved["executed"])

    def test_delivery_blocks_when_wave_pointer_is_not_active(self) -> None:
        _write(
            self.tmp / ".signalos" / "wave.json",
            json.dumps({"wave": "W01", "status": "PLANNED"}) + "\n",
        )

        payload = _run_delivery_feature_gate(self.tmp, "Build support API")

        self.assertTrue(payload["executed"])
        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["exit_code"], feature_gate.EXIT_WAVE_NOT_ACTIVE)
        evidence = self.tmp / ".signalos" / "product" / "FEATURE_GATE.json"
        self.assertTrue(evidence.is_file())

    def test_delivery_blocks_out_of_scope_request_without_operator_answers(self) -> None:
        _write(
            self.tmp / ".signalos" / "wave.json",
            json.dumps({"wave": "W01", "status": "ACTIVE"}) + "\n",
        )

        payload = _run_delivery_feature_gate(self.tmp, "Build unrelated billing portal")

        self.assertTrue(payload["executed"])
        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["exit_code"], feature_gate.EXIT_NEEDS_ANSWERS)
        self.assertEqual(payload["verdict"], "NEEDS_ANSWERS")
        evidence = self.tmp / ".signalos" / "product" / "FEATURE_GATE.json"
        self.assertTrue(evidence.is_file())

    def test_blocked_feature_gate_halts_generation_no_product_files(self) -> None:
        """An ACTIVE wave + out-of-scope request with no answers HALTS generation.

        Not merely flagged: the generation packet must never be written and no
        product source files (src/) may appear, proving the pipeline actually
        stopped before the generation phase.
        """
        repo_root = self.tmp / "blocked-product"
        repo_root.mkdir(parents=True, exist_ok=True)
        # Active wave with NO backlog/expectation/PRD scope entries, so the
        # out-of-scope request cannot match and -- with no Q1/Q2 answers --
        # the feature gate returns NEEDS_ANSWERS and blocks generation.
        _write(
            repo_root / ".signalos" / "wave.json",
            json.dumps({"wave": "W01", "status": "ACTIVE"}) + "\n",
        )

        closeout = run_delivery(
            prompt="Build a completely unrelated billing analytics portal",
            name="blocked-product",
            repo_root=repo_root,
            mode="adopt",
            profile="generic",
            blueprint="none",
            deploy="none",
            dry_run=True,
            agent_mode="none",
        )

        # The feature gate evidence proves it ran and blocked.
        gate_evidence = repo_root / ".signalos" / "product" / "FEATURE_GATE.json"
        self.assertTrue(gate_evidence.is_file())
        gate = json.loads(gate_evidence.read_text(encoding="utf-8"))
        self.assertTrue(gate["blocked"])
        self.assertEqual(gate["verdict"], "NEEDS_ANSWERS")

        # Generation was HALTED, not flagged: the packet was never written.
        packet = repo_root / ".signalos" / "product" / "GENERATION_PACKET.json"
        self.assertFalse(
            packet.exists(),
            "GENERATION_PACKET.json must be absent when the gate blocks",
        )
        manifest = repo_root / ".signalos" / "product" / "GENERATION_MANIFEST.json"
        self.assertFalse(
            manifest.exists(),
            "GENERATION_MANIFEST.json must be absent when the gate blocks",
        )

        # No product source files were generated.
        src_dir = repo_root / "src"
        if src_dir.exists():
            source_files = [
                child for child in src_dir.rglob("*")
                if child.is_file()
            ]
            self.assertEqual(
                source_files, [],
                f"no src/ product files should be written; found {source_files}",
            )

        # And the failure surfaces honestly in the closeout.
        limitations = " ".join(closeout.get("known_limitations", []))
        self.assertIn("generation phase failed", limitations)


if __name__ == "__main__":
    unittest.main()
