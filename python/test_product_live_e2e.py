"""Real end-to-end tests that run delivery with dry_run=False.

These tests execute the generated react-vite validation/proof path without
requiring package registries to be reachable.  External-tool failures are
acceptable only when recorded as explicit failed/blocked evidence; skipped
or hanging checks are not acceptable.

Run with:
    python -m pytest python/test_product_live_e2e.py -v --timeout=180
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module-level skip if Node.js is not available
# ---------------------------------------------------------------------------

def _node_available() -> bool:
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not _node_available(), reason="Node.js not available - skipping live E2E",
)


@pytest.fixture(autouse=True)
def _bound_live_product_commands(monkeypatch: pytest.MonkeyPatch):
    """Keep live product proof bounded on machines without registry access."""
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    monkeypatch.setenv("SIGNALOS_VALIDATION_COMMAND_TIMEOUT_S", "10")
    monkeypatch.setenv("SIGNALOS_PROOF_TIMEOUT_S", "5")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _TempDir:
    """TemporaryDirectory that tolerates Windows file locks on cleanup."""

    def __init__(self):
        self.path = tempfile.mkdtemp()

    def __enter__(self) -> str:
        return self.path

    def __exit__(self, *_exc):
        # Best-effort cleanup; Windows may lock .exe files inside
        # node_modules even after the process is terminated.
        shutil.rmtree(self.path, ignore_errors=True)


def _load_validation_result(repo: Path) -> dict:
    path = repo / ".signalos" / "product" / "VALIDATION_RESULT.json"
    assert path.exists(), f"VALIDATION_RESULT.json missing at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLiveE2E:
    """Real E2E tests requiring Node.js."""

    def test_react_vite_builds_successfully(self):
        """Generated react-vite product installs and builds."""
        from signalos_lib.product.delivery import run_delivery

        with _TempDir() as td:
            repo = Path(td) / "live-test"
            closeout = run_delivery(
                prompt="Build a task management app with projects and tasks",
                name="live-test",
                repo_root=repo,
                mode="greenfield",
                profile="react-vite",
                deploy="none",
                dry_run=False,
                max_repair_cycles=0,
                agent_mode="none",
            )

            val = _load_validation_result(repo)
            results = val.get("results", {})

            # Install should have run (not skipped - that would mean dry-run)
            install_status = results.get("install", {}).get("status")
            assert install_status in (
                "passed", "failed", "blocked",
            ), f"Install was: {install_status}"

            # If install passed, verify real artifacts exist
            if install_status == "passed":
                assert (repo / "node_modules").exists(), "node_modules missing after install"

                # Build should have been attempted
                build_status = results.get("build", {}).get("status")
                assert build_status in (
                    "passed", "failed",
                ), f"Build was: {build_status}"

                # If build passed, dist directory should exist
                if build_status == "passed":
                    assert (repo / "dist").exists(), "dist missing after successful build"

    def test_react_vite_tests_execute(self):
        """Generated tests can be executed with vitest."""
        from signalos_lib.product.delivery import run_delivery

        with _TempDir() as td:
            repo = Path(td) / "test-runner"
            run_delivery(
                prompt="Build a simple dashboard with metrics",
                name="test-runner",
                repo_root=repo,
                mode="greenfield",
                profile="react-vite",
                deploy="none",
                dry_run=False,
                max_repair_cycles=0,
                agent_mode="none",
            )

            val = _load_validation_result(repo)
            install_status = val.get("results", {}).get("install", {}).get("status")
            assert install_status in (
                "passed", "failed", "blocked",
            ), (
                f"Install was not executed honestly (got {install_status}): "
                f"{val.get('results', {}).get('install', {}).get('output', '')[:300]}"
            )

            test_status = val.get("results", {}).get("test", {}).get("status")
            # Tests may fail or block when dependencies are unavailable, but
            # they must be attempted and recorded, never silently skipped.
            assert test_status in (
                "passed", "failed", "blocked",
            ), f"Tests were: {test_status}"

    def test_dev_server_proof_is_bounded_and_recorded(self):
        """Runtime proof starts the dev command or records a bounded failure."""
        from signalos_lib.product.scaffold import run_scaffold
        from signalos_lib.product.proof import run_runtime_proof

        with _TempDir() as td:
            repo = Path(td) / "server-test"
            run_scaffold(
                repo_root=repo,
                profile="react-vite",
                product_name="server-test",
                prompt="Build a dashboard",
                mode="greenfield",
            )

            proof = run_runtime_proof(repo, "react-vite")

            assert proof.get("preview_command") == "npm run dev"
            assert proof.get("status") in ("passed", "failed", "blocked")
            assert proof.get("duration_s", 999) < 20
            if proof.get("status") == "failed":
                assert proof.get("health_check", {}).get("responded") is False

    def test_closure_level_with_real_validation(self):
        """With real validation, closure level reflects actual results."""
        from signalos_lib.product.delivery import run_delivery

        with _TempDir() as td:
            repo = Path(td) / "closure-test"
            run_delivery(
                prompt="Build a simple task tracker",
                name="closure-test",
                repo_root=repo,
                mode="greenfield",
                profile="react-vite",
                deploy="none",
                dry_run=False,
                max_repair_cycles=0,
                agent_mode="none",
            )

            val = _load_validation_result(repo)

            # Should NOT be a dry-run result
            assert val.get("dry_run") is False, "Expected real validation, got dry-run"

            summary = val.get("summary", {})
            # At least install should have been attempted
            assert summary.get("total_checks", 0) > 0, "No checks were recorded"

            # At least one check should not be skipped (proving real execution)
            results = val.get("results", {})
            non_skipped = [
                cat for cat, r in results.items()
                if r.get("status") != "skipped"
            ]
            assert len(non_skipped) > 0, (
                "All checks were skipped - pipeline did not execute real commands"
            )
