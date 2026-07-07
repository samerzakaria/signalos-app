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
        self.assertIn('id = "smoke-ping"', script)
        self.assertIn("Bundled sidecar ping failed after ready", script)
        self.assertIn('EnvironmentVariables.Remove("PYTHONPATH")', script)
        self.assertIn("Invoke-ProcessWithTimeout", script)
        self.assertIn("Invoke-SidecarOneShot", script)
        self.assertIn("ConvertTo-SidecarPayloadJson", script)
        self.assertIn("StandardInput.BaseStream.Write", script)
        self.assertIn("did not report ready before request", script)
        self.assertIn("StandardInput.Close()", script)
        self.assertIn("sidecar one-shot response", script)
        self.assertIn("InstallerTimeoutSeconds", script)
        self.assertIn("MSI administrative extraction", script)
        self.assertIn("NSIS silent install", script)
        self.assertIn("Frontend interactivity fallback", script)
        self.assertNotIn("[SKIP]", script)

        sidecar_index = script.rindex("Test-BundledSidecarProductValidation")
        app_launch_index = script.rindex('Test-AppLaunch $ReleaseExe "release executable"')
        self.assertLess(sidecar_index, app_launch_index)

    def test_release_ci_has_bounded_smoke_and_no_push_time_l6_skip(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        test_automation = (ROOT / ".github" / "workflows" / "test-automation.yml").read_text(encoding="utf-8")
        nightly = (ROOT / ".github" / "workflows" / "nightly-deep-validation.yml").read_text(encoding="utf-8")

        self.assertIn("timeout-minutes: 20", release)
        self.assertIn("smoke-installed-build.ps1 -InstallNsis", release)
        self.assertNotIn("l6-nightly", test_automation)
        self.assertNotIn("github.event_name == 'schedule'", test_automation)
        self.assertNotIn("github.event_name == 'push'", test_automation)
        self.assertIn("l6-nightly", nightly)
        self.assertIn("schedule:", nightly)

    def test_verify_release_manifest_channel_matches_release_workflow(self) -> None:
        script = (ROOT / "scripts" / "verify-release.ps1").read_text(encoding="utf-8")
        validator = (ROOT / "scripts" / "validate-release-urls.ps1").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        self.assertIn("Get-ReleaseManifestNameForVersion", script)
        self.assertIn('return "beta.json"', script)
        self.assertIn('return "latest.json"', script)
        self.assertNotIn('$manifestNames = @("beta.json", "latest.json")', script)
        self.assertIn('gh release create "\\$TAG"', script)
        self.assertIn('gh release upload "\\$TAG"', script)
        self.assertNotIn('tag_name:\\s*\\$\\{\\{\\s*steps\\.channel\\.outputs\\.tag\\s*\\}\\}', script)
        self.assertNotIn('Invoke-Step "Frontend build"', script)
        self.assertIn('Invoke-Step -Name "Frontend build"', script)
        self.assertIn("$ExpectedManifestName = Get-ReleaseManifestNameForVersion $ExpectedVersion", validator)
        self.assertIn("non-active release channel", validator)
        self.assertIn('echo "channel=beta"', release)
        self.assertIn('echo "channel=stable"', release)

    def test_sidecar_ready_means_ipc_loop_is_live(self) -> None:
        server = (ROOT / "python" / "signalos_ipc_server.py").read_text(encoding="utf-8")
        main_start = server.index("def main() -> None:")
        loop_start = server.index("for raw_line in sys.stdin:", main_start)
        ready_start = server.index('"id": "init"', main_start)

        self.assertLess(ready_start, loop_start)
        self.assertIn('replace("\\x00", "")', server[loop_start:])
        self.assertIn('lstrip("\\ufeff")', server[loop_start:])
        self.assertNotIn("Early diagnostic", server[:main_start])

    def test_current_release_surfaces_use_desktop_version(self) -> None:
        version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
        tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        product = tauri["productName"]
        setup_name = f"{product}_{version}_x64-setup.exe"

        current_release_docs = [
            ROOT / "distribution" / "landing" / "index.html",
            ROOT / "docs" / "RELEASE_OPERATOR_GUIDE.md",
            ROOT / "docs" / "RELEASE_GATES_RUNBOOK.md",
            ROOT / "docs" / "INTERNAL_TESTING_BUILD.md",
        ]
        for path in current_release_docs:
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8")
                self.assertIn(version, text)
                self.assertIn(setup_name, text)
                self.assertNotIn("0.0.9", text)
                self.assertNotIn("0.0.7", text)
                self.assertNotIn("v1.0", text)

        server = (ROOT / "python" / "signalos_ipc_server.py").read_text(encoding="utf-8")
        self.assertIn("SIGNALOS_APP_VERSION", server)
        self.assertNotIn('"version": "0.0.9"', server)

        build_internal_sh = (ROOT / "scripts" / "build-internal.sh").read_text(encoding="utf-8")
        self.assertIn('"product": "Foundry"', build_internal_sh)


if __name__ == "__main__":
    unittest.main()
