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

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalos_lib.product import validation as V

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


if __name__ == "__main__":
    unittest.main()
