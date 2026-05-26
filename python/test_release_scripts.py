"""Tests for deterministic release validation scripts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseScriptTests(unittest.TestCase):
    def test_installed_artifact_preflight_reports_ready_without_claiming_launch(self) -> None:
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell is None:
            self.skipTest("PowerShell is required for installed artifact preflight script")

        with tempfile.TemporaryDirectory(prefix="signalos-artifact-preflight-") as tmp:
            root = Path(tmp)
            release = root / "src-tauri" / "target" / "release"
            release.mkdir(parents=True)
            suffix = ".exe" if os.name == "nt" else ""
            (release / f"signalos-desktop{suffix}").write_bytes(b"fake-app")
            (release / f"signalos-python{suffix}").write_bytes(b"fake-sidecar")
            nsis = release / "bundle" / "nsis"
            msi = release / "bundle" / "msi"
            nsis.mkdir(parents=True)
            msi.mkdir(parents=True)
            (nsis / "SignalOS_2.0.0_x64-setup.exe").write_bytes(b"fake-nsis")
            (msi / "SignalOS_2.0.0_x64_en-US.msi").write_bytes(b"fake-msi")

            command = [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts" / "check-installed-artifact-preconditions.ps1"),
                "-Root",
                str(root),
                "-Json",
                "-RequireInstallers",
            ]
            proc = subprocess.run(command, capture_output=True, text=True, timeout=30)

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["schema_version"], "signalos.installed_app_preflight.v1")
        self.assertEqual(payload["status"], "READY_FOR_SMOKE")
        self.assertFalse(payload["installed_app_passed"])
        self.assertIn("smoke-installed-build.ps1", payload["smoke_command"])
        self.assertTrue(all(check["exists"] for check in payload["checks"] if check["required"]))

    def test_installed_smoke_logs_sidecar_request_progress(self) -> None:
        script = (ROOT / "scripts" / "smoke-installed-build.ps1").read_text(encoding="utf-8")

        self.assertIn("[RUN ] Sidecar request:", script)
        self.assertIn("[INFO] Sidecar progress:", script)
        self.assertIn("failed while waiting for output", script)


if __name__ == "__main__":
    unittest.main()
