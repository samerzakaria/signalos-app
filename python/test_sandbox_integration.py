"""test_sandbox_integration.py - Real-Docker integration tests for sandbox.

The unit tests in test_sandbox.py mock docker_available + assert argv
SHAPE. They don't prove the wrapper actually launches a container or
that the workspace mount works.

This module fills that gap. It only runs meaningfully when a Docker
daemon is reachable:

  - On a developer machine: only if `docker info` succeeds. Otherwise
    the whole module is skipped (no false failures for devs without
    Docker Desktop).
  - In CI: the dedicated `l1-sandbox-integration` job on ubuntu-latest
    has Docker pre-installed; this is where the suite normally runs.
    (Windows/macOS hosted runners do NOT have Docker pre-installed,
    which is why the job is Linux-only.)

What we actually verify by spawning a real container:

  1. build_docker_run_argv -> the daemon accepts the argv
  2. The workspace -> /workspace mount is read-write
  3. Files we write inside the container appear on host
  4. Non-zero exits propagate as expected
  5. maybe_wrap_for_sandbox produces an argv that actually runs

Image policy: we use `alpine:latest` (~5 MB, busybox tools) rather than
node:* or python:* so the test stays fast (one tiny pull, ~2s on a
warm cache). The point is "does the sandbox plumbing work," not
"does node run inside it."
"""

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

from signalos_lib.sandbox import (
    build_docker_run_argv,
    docker_available,
    maybe_wrap_for_sandbox,
    set_sandbox_config,
)


_DOCKER_OK = docker_available()
_IMAGE = "alpine:latest"


def _pull_image_once() -> bool:
    """Best-effort `docker pull alpine`. Returns True if alpine is now
    locally available (whether we just pulled it or it was already there).
    Skips silently on network errors so tests can still report Docker-up
    but image-unreachable as a clear skip rather than crash."""
    if not _DOCKER_OK:
        return False
    try:
        proc = subprocess.run(
            ["docker", "pull", _IMAGE],
            capture_output=True,
            text=True,
            timeout=120,
            shell=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@unittest.skipUnless(_DOCKER_OK, "Docker daemon not reachable; skipping integration suite")
class SandboxRealDocker(unittest.TestCase):
    """Spawn real containers and assert the sandbox plumbing works."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _pull_image_once():
            raise unittest.SkipTest(f"could not pull {_IMAGE}; network issue?")

    def test_built_argv_launches_a_container(self) -> None:
        """A command built via build_docker_run_argv must actually run
        inside the container and produce the expected stdout."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            argv = build_docker_run_argv(root, ["echo", "from-inside"], image=_IMAGE)
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("from-inside", proc.stdout)

    def test_workspace_mount_is_visible_inside_container(self) -> None:
        """A file we drop on the host into the workspace dir must appear
        at /workspace inside the container."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "marker.txt").write_text("hello-from-host\n")
            argv = build_docker_run_argv(root, ["cat", "/workspace/marker.txt"], image=_IMAGE)
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("hello-from-host", proc.stdout)

    def test_workspace_mount_is_writable_from_container(self) -> None:
        """A file written inside the container must show up on the host
        (proves rw mount, not just ro)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            argv = build_docker_run_argv(
                root,
                ["sh", "-c", "echo wrote-from-container > /workspace/out.txt"],
                image=_IMAGE,
            )
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            target = root / "out.txt"
            self.assertTrue(target.is_file(), "host did not see container-written file")
            self.assertIn("wrote-from-container", target.read_text())

    def test_nonzero_exit_propagates(self) -> None:
        """A command that exits non-zero inside the container must
        surface a non-zero returncode to subprocess.run."""
        with tempfile.TemporaryDirectory() as d:
            argv = build_docker_run_argv(Path(d), ["false"], image=_IMAGE)
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            self.assertNotEqual(proc.returncode, 0)

    def test_maybe_wrap_when_enabled_actually_runs(self) -> None:
        """End-to-end: set sandbox.json enabled=true, ask maybe_wrap to
        wrap a command, execute the result, verify it ran in a container.

        This is the public-API path the orchestrator uses; if this
        works the TDD/preview/e2e wraps will too."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            set_sandbox_config(root, enabled=True, image_js=_IMAGE, image_sh=_IMAGE)
            # An sh-c command that prints a containery hostname pattern.
            # `hostname` inside a Docker container returns a short id,
            # never the host's hostname. That's our proof of containment.
            wrapped, was_wrapped = maybe_wrap_for_sandbox(
                root,
                ["sh", "-c", "hostname && echo SANDBOX_OK"],
            )
            self.assertTrue(was_wrapped, "expected wrapping; got bypass")
            proc = subprocess.run(wrapped, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("SANDBOX_OK", proc.stdout)
            # Sanity: the hostname inside is not the host's hostname.
            host_hostname = os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "")
            container_hostname = proc.stdout.splitlines()[0].strip()
            if host_hostname and container_hostname:
                self.assertNotEqual(
                    container_hostname.lower(),
                    host_hostname.lower(),
                    "hostname matches host -- did the container actually run isolated?",
                )

    def test_maybe_wrap_when_disabled_runs_on_host(self) -> None:
        """The off-path must NOT route through Docker even if Docker is
        available -- the user's toggle is authoritative."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # explicit disable
            set_sandbox_config(root, enabled=False)
            wrapped, was_wrapped = maybe_wrap_for_sandbox(root, ["echo", "host-side"])
            self.assertFalse(was_wrapped)
            self.assertEqual(wrapped, ["echo", "host-side"])


@unittest.skipUnless(_DOCKER_OK, "Docker daemon not reachable; skipping integration suite")
class TddInDockerEndToEnd(unittest.TestCase):
    """Closes v0.1 audit §5.2 — TDD-in-Docker verification.

    The deferred item: "enable sandbox toggle; run a wave with TDD-tagged
    task; verify tests execute in container." The orchestrator routes
    every test execution through `tdd_runner.run_tests_for_files`,
    which calls `maybe_wrap_for_sandbox`. The tests below pin that
    contract end-to-end: a TDD-tagged task's test command runs inside
    a container, the container's hostname differs from the host's, and
    when sandbox is off the same command runs on the host directly.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not _pull_image_once():
            raise unittest.SkipTest(f"could not pull {_IMAGE}; network issue?")

    def test_is_tdd_task_recognises_skill_tag(self) -> None:
        from signalos_lib.tdd_runner import is_tdd_task
        self.assertTrue(is_tdd_task({"skills": ["test-driven-development"]}))
        self.assertTrue(is_tdd_task({"skills": ["security-audit", "test-driven-development"]}))
        self.assertFalse(is_tdd_task({"skills": ["security-audit"]}))
        self.assertFalse(is_tdd_task({}))

    def test_tdd_runner_routes_through_docker_when_sandbox_enabled(self) -> None:
        """The TDD runner's public seam is `run_tests_for_files`. With
        sandbox.json enabled, it must wrap the test command in
        `docker run` — proven by container-vs-host hostname diff."""
        from signalos_lib.tdd_runner import run_tests_for_files

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            set_sandbox_config(root, enabled=True, image_js=_IMAGE, image_sh=_IMAGE)
            # Synthetic TestRunner: prints the in-container hostname.
            # We use hostname diff (host vs container) as the proof of
            # containment, matching the pattern in
            # test_maybe_wrap_when_enabled_actually_runs.
            fake_runner = (
                "shell",
                ["sh", "-c", "hostname; echo SANDBOX_TDD_OK"],
            )
            passed, output = run_tests_for_files(
                root, fake_runner, test_file_paths=[],
            )
            self.assertTrue(passed, f"TDD-in-Docker run failed: {output}")
            self.assertIn("SANDBOX_TDD_OK", output)
            # Hostname check — the container's hostname is a short ID,
            # never matches the host's.
            host_hostname = (
                os.uname().nodename if hasattr(os, "uname")
                else os.environ.get("COMPUTERNAME", "")
            )
            container_hostname = output.splitlines()[0].strip()
            if host_hostname and container_hostname:
                self.assertNotEqual(
                    container_hostname.lower(),
                    host_hostname.lower(),
                    "hostname matches host — TDD test did not run isolated",
                )

    def test_tdd_runner_runs_on_host_when_sandbox_disabled(self) -> None:
        """Sandbox toggle is authoritative: with sandbox.json disabled,
        the TDD test command runs directly on host (no Docker wrap)
        even when Docker is available."""
        from signalos_lib.tdd_runner import run_tests_for_files

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            set_sandbox_config(root, enabled=False)
            fake_runner = (
                "python",
                [
                    sys.executable,
                    "-c",
                    "import socket; print('HOST_TDD_OK'); print(socket.gethostname())",
                ],
            )
            passed, output = run_tests_for_files(
                root, fake_runner, test_file_paths=[],
            )
            self.assertTrue(passed, output)
            self.assertIn("HOST_TDD_OK", output)
            # Hostname is the host's, not a container ID.
            host_hostname = (
                os.uname().nodename if hasattr(os, "uname")
                else os.environ.get("COMPUTERNAME", "")
            )
            in_runner_hostname = output.splitlines()[1].strip()
            if host_hostname and in_runner_hostname:
                # On Linux CI the test runner sees the host's nodename.
                # We tolerate both lower/upper for runner naming quirks.
                self.assertEqual(
                    in_runner_hostname.lower(),
                    host_hostname.lower(),
                    "expected host execution; got something else",
                )


if __name__ == "__main__":
    unittest.main()
