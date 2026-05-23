"""Focused tests for `signalos release-readiness`."""

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
from signalos_lib.artifacts import expected_gate_artifacts
from signalos_lib.commands import release_readiness
from signalos_lib.sign import sign_artifact
from signalos_ipc_server import map_slash_command


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_ready_repo(root: Path) -> None:
    (root / ".signalos" / "sessions").mkdir(parents=True)
    _write(root / ".signalos" / "worktree-state.json", "{}\n")
    _write(root / ".signalos" / "AUDIT_TRAIL.jsonl", "")
    _write(
        root / ".signalos" / "sources" / "initial-intent.json",
        json.dumps({"kind": "prompt", "text": "Build a task management system"}) + "\n",
    )
    _write(root / ".signalos" / "unknowns.json", json.dumps({"unknowns": []}) + "\n")
    _write(root / ".signalos" / "adoption" / "unknowns.json", json.dumps({"unknowns": []}) + "\n")
    _write(
        root / ".signalos" / "deployment-path.json",
        json.dumps({"mode": "manual", "target": "not deployed by default"}) + "\n",
    )
    _write(root / ".signalos" / "release-blockers.json", "[]\n")

    for rel in (
        "core/governance/Templates/plan-template.md",
        "core/governance/Templates/quality-check-template.md",
        "core/governance/Templates/soul-document-template.md",
        "core/governance/Templates/trust-tier-scoring.md",
    ):
        _write(root / rel, f"# {Path(rel).stem}\n")
    _write(root / "core" / "governance" / "Governance" / "DECISION-DNA.md", "# Decision DNA\n")

    for artifact in expected_gate_artifacts():
        target = root / artifact.rel_path
        _write(target, f"# {artifact.label}\n\nRelease-ready fixture.\n")
        sign_artifact(
            target,
            signer="Fixture User",
            role=artifact.required_roles[0],
            gate=artifact.gate,
            verdict="APPROVED",
        )

    evidence_dir = root / ".signalos" / "evidence" / "W1"
    evidence_dir.mkdir(parents=True)
    verify_payload = {
        "schema_version": "signalos.verify_product.v1",
        "repo_root": str(root),
        "wave": "W1",
        "status": "PASS",
        "summary": {"total": 4, "passed": 4, "failed": 0, "skipped": 0},
        "checks": [
            {"name": "workspace", "status": "PASS", "required": True},
            {"name": "build", "status": "PASS", "required": True},
            {"name": "test", "status": "PASS", "required": True},
            {"name": "qa", "status": "PASS", "required": True},
        ],
    }
    _write(evidence_dir / "verify-product.json", json.dumps(verify_payload) + "\n")


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-release-ready-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_repo_blocks_without_creating_it(self) -> None:
        missing = self.tmp / "missing"

        payload = release_readiness.release_readiness(missing)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "blocked")
        self.assertIsNone(payload["evidence_path"])
        self.assertFalse(missing.exists())
        blockers = {blocker["id"] for blocker in payload["blockers"]}
        self.assertIn("active-workspace", blockers)

    def test_missing_required_pieces_report_blockers(self) -> None:
        payload = release_readiness.release_readiness(self.tmp)

        self.assertFalse(payload["ok"])
        blockers = {blocker["id"] for blocker in payload["blockers"]}
        self.assertIn("layer1-valid", blockers)
        self.assertIn("build-test-evidence", blockers)
        self.assertIn("deployment-path-known", blockers)
        self.assertTrue((self.tmp / ".signalos" / "evidence" / "release-readiness" / "release-readiness.json").is_file())
        self.assertTrue((self.tmp / ".signalos" / "evidence" / "layer1" / "validate-layer1.json").is_file())

    def test_ready_fixture_returns_ready_to_publish(self) -> None:
        _make_ready_repo(self.tmp)

        payload = release_readiness.release_readiness(self.tmp, wave="W9")

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["status"], "ready-to-publish")
        self.assertEqual(payload["publish_relationship"], "ready-to-publish")
        self.assertEqual(payload["blockers"], [])
        self.assertIn(".signalos/evidence/W9/release-readiness.json", payload["evidence"])
        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(checks["layer1-valid"]["status"], "PASS")
        self.assertEqual(checks["required-gates-signed"]["status"], "PASS")
        self.assertEqual(checks["build-test-evidence"]["status"], "PASS")

    def test_failed_verification_evidence_blocks_release(self) -> None:
        _make_ready_repo(self.tmp)
        _write(
            self.tmp / ".signalos" / "evidence" / "W2" / "verify-product.json",
            json.dumps({
                "schema_version": "signalos.verify_product.v1",
                "status": "FAIL",
                "checks": [{"name": "test", "status": "FAIL"}],
            }) + "\n",
        )

        payload = release_readiness.release_readiness(self.tmp)

        blockers = {blocker["id"] for blocker in payload["blockers"]}
        self.assertIn("build-test-evidence", blockers)

    def test_top_level_cli_emits_json(self) -> None:
        _make_ready_repo(self.tmp)
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "release-readiness",
                "--repo-root",
                str(self.tmp),
                "--json",
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["schema_version"], "signalos.release_readiness.v1")
        self.assertEqual(payload["publish_relationship"], "ready-to-publish")

    def test_sidecar_routes_signal_release_readiness_to_cli(self) -> None:
        argv = map_slash_command("signal-release-readiness", ["--json"], str(self.tmp))

        self.assertEqual(argv, ["release-readiness", "--repo-root", str(self.tmp), "--json"])


if __name__ == "__main__":
    unittest.main()
