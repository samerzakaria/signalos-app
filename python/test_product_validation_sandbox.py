"""Option 2 -- the product validation path builds in a container when needed.

``_run_commands`` runs each build/test command against the host toolchain when
it's present (the fast path), but transparently falls back to a per-stack
container when the host LACKS the tool (or the workspace forces sandboxed
validation) -- so a Go/.NET/react product validates with ZERO language
toolchain on the operator's machine, only a container runtime. With neither the
host tool nor a runtime, it blocks honestly.

Runtime-free: we patch the sandbox helpers + the shell runner, so no real
Docker/Podman is needed to prove the wiring.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalos_lib.product import validation as V
from signalos_lib.product.sandbox import CommandOutput, ContainerRunner

_CONTAINER_ARGV = ["docker", "run", "--rm", "-i", "node:lts", "npm", "test"]


def _ok_proc(cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")


class TestValidationContainerFallback(unittest.TestCase):
    def _run(self, *, which, docker, forced):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(V.shutil, "which", return_value=which), \
                mock.patch("signalos_lib.sandbox.is_sandbox_enabled", return_value=forced), \
                mock.patch("signalos_lib.sandbox.docker_available", return_value=docker), \
                mock.patch("signalos_lib.sandbox.build_docker_run_argv", return_value=list(_CONTAINER_ARGV)), \
                mock.patch.object(V, "_run_shell_command") as run:
                run.return_value = _ok_proc("npm test")
                result = V._run_commands(root, ["npm test"])
        return result, run

    def test_host_tool_present_uses_host_fast_path(self):
        result, run = self._run(which="/usr/bin/npm", docker=True, forced=False)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(run.call_args.args[1], ["/usr/bin/npm", "test"])

    def test_host_tool_missing_but_runtime_present_builds_in_container(self):
        # the "never one language short" case -- no host npm, but a runtime.
        result, run = self._run(which=None, docker=True, forced=False)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(run.call_args.args[1], _CONTAINER_ARGV)

    def test_forced_sandbox_containerizes_even_when_host_tool_present(self):
        result, run = self._run(which="/usr/bin/npm", docker=True, forced=True)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(run.call_args.args[1], _CONTAINER_ARGV)

    def test_forced_but_no_runtime_falls_back_to_host(self):
        result, run = self._run(which="/usr/bin/npm", docker=False, forced=True)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(run.call_args.args[1], ["/usr/bin/npm", "test"])

    def test_no_host_tool_and_no_runtime_blocks_honestly(self):
        result, _ = self._run(which=None, docker=False, forced=False)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("not installed on the host", result["output"])
        self.assertIn("container runtime", result["output"])


class _RecordingRunner:
    """Duck-typed SandboxRunner: records each run() and returns a canned result."""

    name = "container:fake"

    def __init__(self, exit_code=0, stdout="ok", stderr="", timed_out=False):
        self._exit = exit_code
        self._out = CommandOutput(stdout, stderr, timed_out)
        self.calls: list[dict] = []

    def run(self, cmd, cwd, timeout, env):
        self.calls.append(
            {"cmd": cmd, "cwd": str(cwd), "timeout": timeout, "env": dict(env)}
        )
        return self._exit, self._out


class TestVerifierContainerRouting(unittest.TestCase):
    """STEP 2 -- the gate's INDEPENDENT verification routes through the selected
    hardened SandboxRunner when SIGNALOS_SANDBOX opts in, and falls back to the
    host path (byte-identical) when it does not. Runtime-free: the runner is a
    duck-typed fake, so no real Docker is needed to prove the wiring."""

    def test_sandbox_unset_runs_on_host(self):
        # No SIGNALOS_SANDBOX -> no verifier container; host path is used.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            env = {k: v for k, v in os.environ.items() if k != "SIGNALOS_SANDBOX"}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertIsNone(V._select_verifier_runner(root))

    def test_sandbox_docker_selects_hardened_container_runner(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.dict(os.environ, {"SIGNALOS_SANDBOX": "docker"}), \
                mock.patch(
                    "signalos_lib.product.sandbox.container_engine_available",
                    return_value=True,
                ):
                runner = V._select_verifier_runner(root)
            self.assertIsInstance(runner, ContainerRunner)
            self.assertEqual(runner.engine, "docker")
            # digest-pin / offline: the verifier never pulls.
            self.assertEqual(runner.pull, "never")

    def test_passing_build_routes_through_runner_not_host(self):
        rec = _RecordingRunner(exit_code=0, stdout="built ok")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(V, "_select_verifier_runner", return_value=rec), \
                mock.patch.object(V, "_run_shell_command") as host:
                result = V._run_commands(root, ["npm run build", "npm test"])
        host.assert_not_called()  # the raw host subprocess is never touched
        # behavior-identical result shape for a passing build
        self.assertEqual(result["status"], "passed")
        self.assertIn("built ok", result["output"])
        self.assertIn("duration_s", result)
        self.assertEqual(
            [c["cmd"] for c in rec.calls], ["npm run build", "npm test"]
        )
        # cwd handed to the runner is the workspace root (mount derives -w).
        self.assertEqual(rec.calls[0]["cwd"], str(root))

    def test_container_nonzero_exit_is_failed(self):
        rec = _RecordingRunner(exit_code=1, stdout="", stderr="tsc error TS1005")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(V, "_select_verifier_runner", return_value=rec):
                result = V._run_commands(root, ["npm run build"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("tsc error TS1005", result["output"])

    def test_container_timeout_is_blocked(self):
        rec = _RecordingRunner(exit_code=124, timed_out=True)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(V, "_select_verifier_runner", return_value=rec):
                result = V._run_commands(root, ["npm test"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("timed out", result["output"])

    def test_install_command_gets_the_larger_budget_in_container(self):
        rec = _RecordingRunner(exit_code=0, stdout="added 300 packages")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(V, "_select_verifier_runner", return_value=rec):
                V._run_commands(
                    root, ["npm install --legacy-peer-deps", "npm test"]
                )
        by_cmd = {c["cmd"]: c["timeout"] for c in rec.calls}
        # the install carve-out is preserved: install gets the larger budget.
        self.assertGreater(
            by_cmd["npm install --legacy-peer-deps"], by_cmd["npm test"]
        )

    def test_funded_validation_verifies_receipt_and_never_runs_install(self):
        rec = _RecordingRunner(exit_code=0, stdout="ok")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.dict(
                os.environ, {"SIGNALOS_SANDBOX_PROFILE": "funded"}
            ), mock.patch.object(
                V, "_select_verifier_runner", return_value=rec
            ), mock.patch(
                "signalos_lib.product.dependency_broker."
                "verify_funded_dependencies_from_environment"
            ) as verify:
                result = V._run_commands(
                    root,
                    ["npm install --legacy-peer-deps", "npm run build", "npm test"],
                )

        self.assertEqual(result["status"], "passed")
        verify.assert_called_once_with(root)
        self.assertEqual(
            [call["cmd"] for call in rec.calls], ["npm run build", "npm test"]
        )
        self.assertIn("install skipped", result["output"])

    def test_env_overlay_is_ci_only(self):
        # Only CI/FORCE_COLOR cross the boundary -- no host env leaks in.
        rec = _RecordingRunner()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(V, "_select_verifier_runner", return_value=rec):
                V._run_commands(root, ["npm test"])
        self.assertEqual(rec.calls[0]["env"], {"CI": "1", "FORCE_COLOR": "0"})


if __name__ == "__main__":
    unittest.main()
