"""Tests for shared gate artifact definitions and safe path resolution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import artifacts
from signalos_lib import sign


def _create_windows_junction(link: Path, target: Path) -> bool:
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        shell=False,
    )
    return proc.returncode == 0


class ArtifactMapTests(unittest.TestCase):
    def test_gate_artifacts_load_from_shared_manifest(self) -> None:
        manifest = json.loads((HERE / "signalos_lib" / "gate_artifacts.json").read_text(encoding="utf-8"))
        expected_paths = {
            entry["rel_path"]
            for entries in manifest["gates"].values()
            for entry in entries
        }
        actual_paths = {artifact.rel_path for artifact in artifacts.expected_gate_artifacts()}

        self.assertEqual(actual_paths, expected_paths)
        self.assertIn("core/governance/Governance/SOUL-DOCUMENT.md", actual_paths)

    def test_sign_reexports_shared_gate_map(self) -> None:
        self.assertIs(sign.GATE_MAP, artifacts.GATE_MAP)
        self.assertEqual(artifacts.list_gates(), ["G0", "G1", "G2", "G3", "G4", "G5"])
        self.assertEqual(
            sign.GATE_MAP["G4"],
            [
                ("core/execution/TRUST_TIER.md", ["PE", "PO"], "Trust Tier"),
                ("core/execution/BUILD_EVIDENCE.md", ["PE"], "Build Evidence"),
            ],
        )
        self.assertEqual(sign.GATE_MAP["G5"][0][0], "core/governance/QUALITY_CHECK.md")

    def test_expected_gate_artifacts_keeps_labels_and_roles(self) -> None:
        g0 = artifacts.expected_gate_artifacts("g0")
        self.assertEqual(g0[0].rel_path, "core/governance/Governance/SOUL-DOCUMENT.md")
        self.assertEqual(g0[0].required_roles, ("PO", "PE"))
        self.assertEqual(g0[0].label, "Soul Document")
        g4 = artifacts.expected_gate_artifacts("g4")
        self.assertEqual(
            [(artifact.rel_path, artifact.required_roles, artifact.label) for artifact in g4],
            [
                ("core/execution/TRUST_TIER.md", ("PE", "PO"), "Trust Tier"),
                ("core/execution/BUILD_EVIDENCE.md", ("PE",), "Build Evidence"),
            ],
        )

    def test_gate_detection_paths_require_build_evidence_for_g4_status(self) -> None:
        detection_paths = artifacts.gate_detection_paths()

        self.assertEqual(detection_paths["G4"], ("core/execution/BUILD_EVIDENCE.md",))
        self.assertNotIn("core/execution/TRUST_TIER.md", detection_paths["G4"])

    def test_resolve_gate_artifacts_stays_under_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-artifacts-") as tmp:
            root = Path(tmp).resolve()  # resolve symlinks (macOS /var → /private/var)
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

    def test_resolve_workspace_path_rejects_link_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-artifacts-") as tmp:
            root = Path(tmp) / "root"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "link"
            created_junction = False
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                if os.name != "nt" or not _create_windows_junction(link, outside):
                    self.skipTest("filesystem link creation is unavailable in this environment")
                created_junction = True

            try:
                with self.assertRaises(ValueError):
                    artifacts.resolve_workspace_path(root, "link/escape.md")
            finally:
                if created_junction and link.exists():
                    link.rmdir()

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

    def test_check_gate_uses_full_g4_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-artifacts-") as tmp:
            root = Path(tmp)
            trust_tier = root / "core" / "execution" / "TRUST_TIER.md"
            trust_tier.parent.mkdir(parents=True)
            trust_tier.write_text("# Trust Tier\n\nT2.\n", encoding="utf-8")

            statuses = sign.check_gate(root, "G4")

            self.assertEqual(
                [(status.rel_path, status.label, status.required_roles, status.exists) for status in statuses],
                [
                    ("core/execution/TRUST_TIER.md", "Trust Tier", ["PE", "PO"], True),
                    ("core/execution/BUILD_EVIDENCE.md", "Build Evidence", ["PE"], False),
                ],
            )

    def test_sign_gate_g4_signs_trust_tier_and_build_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-artifacts-") as tmp:
            root = Path(tmp)
            for rel_path, title in (
                ("core/execution/TRUST_TIER.md", "Trust Tier"),
                ("core/execution/BUILD_EVIDENCE.md", "Build Evidence"),
            ):
                path = root / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {title}\n\nReady.\n", encoding="utf-8")

            signed = sign.sign_gate(root, "G4", "Pat Engineer", "PE", "APPROVED")
            statuses = sign.check_gate(root, "G4")

            self.assertEqual(
                signed,
                ["core/execution/TRUST_TIER.md", "core/execution/BUILD_EVIDENCE.md"],
            )
            self.assertTrue(all(status.exists for status in statuses))
            self.assertTrue(all(status.has_signatures for status in statuses))
            self.assertEqual([status.signers for status in statuses], [["Pat Engineer"], ["Pat Engineer"]])


if __name__ == "__main__":
    unittest.main()
