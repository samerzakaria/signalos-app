"""Tests for shared gate artifact definitions and safe path resolution."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import artifacts
from signalos_lib import sign


class ArtifactMapTests(unittest.TestCase):
    def test_sign_reexports_shared_gate_map(self) -> None:
        self.assertIs(sign.GATE_MAP, artifacts.GATE_MAP)
        self.assertEqual(artifacts.list_gates(), ["G0", "G1", "G2", "G3", "G4", "G5"])
        self.assertEqual(sign.GATE_MAP["G5"][0][0], "core/governance/QUALITY_CHECK.md")

    def test_expected_gate_artifacts_keeps_labels_and_roles(self) -> None:
        g0 = artifacts.expected_gate_artifacts("g0")
        self.assertEqual(g0[0].rel_path, "core/governance/Governance/SOUL-DOCUMENT.md")
        self.assertEqual(g0[0].required_roles, ("PO", "PE"))
        self.assertEqual(g0[0].label, "Soul Document")

    def test_resolve_gate_artifacts_stays_under_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-artifacts-") as tmp:
            root = Path(tmp)
            resolved = artifacts.resolve_gate_artifacts(root, "G2")
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0].rel_path, "core/strategy/EXPECTATION_MAP.md")
            self.assertEqual(resolved[0].path, root / "core" / "strategy" / "EXPECTATION_MAP.md")

    def test_resolve_workspace_path_rejects_escape_segments(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-artifacts-") as tmp:
            root = Path(tmp)
            unsafe = [
                "../outside.md",
                "/absolute/path.md",
                "core\\strategy\\BELIEF.md",
                "C:/outside.md",
                "",
            ]
            for rel_path in unsafe:
                with self.subTest(rel_path=rel_path):
                    with self.assertRaises(ValueError):
                        artifacts.resolve_workspace_path(root, rel_path)

    def test_resolve_workspace_path_rejects_symlink_escape_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-artifacts-") as tmp:
            root = Path(tmp) / "root"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is unavailable in this environment")

            with self.assertRaises(ValueError):
                artifacts.resolve_workspace_path(root, "link/escape.md")

    def test_check_gate_uses_shared_resolved_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-artifacts-") as tmp:
            root = Path(tmp)
            quality = root / "core" / "governance" / "QUALITY_CHECK.md"
            quality.parent.mkdir(parents=True)
            quality.write_text("Quality\n\n## Signatures\n\n```yaml\n- signer: QA User\n```\n", encoding="utf-8")

            statuses = sign.check_gate(root, "G5")
            self.assertEqual(len(statuses), 1)
            self.assertEqual(statuses[0].rel_path, "core/governance/QUALITY_CHECK.md")
            self.assertTrue(statuses[0].exists)


if __name__ == "__main__":
    unittest.main()
