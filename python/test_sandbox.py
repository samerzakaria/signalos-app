"""test_sandbox.py - Containerized-execution scaffolding tests.

The Docker daemon isn't guaranteed available in CI / on every dev
machine, so we cover everything testable without actually running
`docker run`:

  - get/set sandbox config persists to .signalos/sandbox.json
  - default config is sane when the file is missing
  - is_sandbox_enabled is False unless BOTH the flag is on AND docker
    is available (forbids enabling a non-functional sandbox)
  - build_docker_run_argv produces the exact argv structure callers
    rely on (workspace mount, workdir, image choice, extra mounts)
  - maybe_wrap_for_sandbox returns the cmd unchanged when off
  - The IPC handler (signal-sandbox status/enable/disable) round-trips
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.sandbox import (
    build_docker_run_argv,
    docker_available,
    get_sandbox_config,
    is_sandbox_enabled,
    maybe_wrap_for_sandbox,
    set_sandbox_config,
)
from signalos_ipc_server import handle_sandbox


class SandboxConfig(unittest.TestCase):
    def test_defaults_when_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = get_sandbox_config(Path(d))
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["image_js"], "node:20-alpine")
            self.assertEqual(cfg["image_py"], "python:3.11-slim")
            self.assertEqual(cfg["extra_mounts"], [])

    def test_set_persists_to_dotsignalos_json(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = set_sandbox_config(root, enabled=True, image_js="node:22-alpine")
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["image_js"], "node:22-alpine")
            # Default for keys we didn't touch.
            self.assertEqual(cfg["image_py"], "python:3.11-slim")
            # Persisted to disk.
            on_disk = json.loads((root / ".signalos" / "sandbox.json").read_text())
            self.assertEqual(on_disk["image_js"], "node:22-alpine")

    def test_unknown_keys_are_dropped_silently(self) -> None:
        """We only persist the documented schema keys; arbitrary input
        from a future config-bug doesn't pollute disk."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = set_sandbox_config(root, enabled=True, nonsense_key="x")  # type: ignore[arg-type]
            self.assertNotIn("nonsense_key", cfg)

    def test_corrupt_json_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir()
            (root / ".signalos" / "sandbox.json").write_text("not valid json")
            cfg = get_sandbox_config(root)
            self.assertFalse(cfg["enabled"])


class IsSandboxEnabled(unittest.TestCase):
    def test_false_when_flag_off(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(is_sandbox_enabled(Path(d)))

    def test_false_when_flag_on_but_docker_missing(self) -> None:
        """Enabling sandbox in a workspace where Docker isn't installed
        is a configuration-level mistake. We refuse to acknowledge
        sandbox is enabled in that case, so callers don't accidentally
        try to wrap commands that would then fail to execute."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            set_sandbox_config(root, enabled=True)
            with patch("signalos_lib.sandbox.docker_available", return_value=False):
                self.assertFalse(is_sandbox_enabled(root))

    def test_true_when_flag_on_and_docker_available(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            set_sandbox_config(root, enabled=True)
            with patch("signalos_lib.sandbox.docker_available", return_value=True):
                self.assertTrue(is_sandbox_enabled(root))


class DockerRunArgvShape(unittest.TestCase):
    def test_includes_workspace_mount_and_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            argv = build_docker_run_argv(root, ["npm", "test"])
            self.assertEqual(argv[0], "docker")
            self.assertEqual(argv[1], "run")
            self.assertIn("--rm", argv)
            # Workspace -> /workspace mount
            self.assertIn("-v", argv)
            mount_idx = argv.index("-v")
            self.assertEqual(argv[mount_idx + 1], f"{root.resolve()}:/workspace")
            # WORKDIR
            wd_idx = argv.index("-w")
            self.assertEqual(argv[wd_idx + 1], "/workspace")

    def test_classifies_python_command_to_python_image(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            set_sandbox_config(root, image_py="python:3.12-slim")
            argv = build_docker_run_argv(root, ["python", "-m", "pytest"])
            self.assertIn("python:3.12-slim", argv)

    def test_classifies_js_command_to_js_image(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            set_sandbox_config(root, image_js="node:21-alpine")
            argv = build_docker_run_argv(root, ["npm", "test"])
            self.assertIn("node:21-alpine", argv)

    def test_extra_mounts_passed_through(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            argv = build_docker_run_argv(
                root, ["npm", "test"],
                extra_mounts=["/host/cache:/cache:ro"],
            )
            self.assertIn("/host/cache:/cache:ro", argv)

    def test_ports_passed_through_as_p_flags(self) -> None:
        """The preview wrap will need ports reachable from host
        (e.g. 5173:5173 for `npm run dev`). build_docker_run_argv must
        emit explicit `-p host:container` mappings so containment is
        preserved while named ports are bridged."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            argv = build_docker_run_argv(
                root, ["npm", "run", "dev"],
                ports=["5173:5173", "9229:9229"],
            )
            self.assertIn("5173:5173", argv)
            self.assertIn("9229:9229", argv)
            # And they must be -p flags, not -v mounts.
            for port in ["5173:5173", "9229:9229"]:
                idx = argv.index(port)
                self.assertEqual(argv[idx - 1], "-p")

    def test_no_network_host_in_default_argv(self) -> None:
        """Regression guard: the original implementation included
        --network host which DEFEATS the blast-radius reduction the
        sandbox is supposed to provide (container shares host's network
        namespace, hostname, loopback). Default must be bridge."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            argv = build_docker_run_argv(root, ["echo", "hi"])
            self.assertNotIn("--network", argv)
            self.assertNotIn("host", [argv[i] for i, a in enumerate(argv) if i > 0 and argv[i - 1] == "--network"])

    def test_command_is_appended_at_the_end(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            argv = build_docker_run_argv(root, ["my-binary", "--flag", "val"])
            self.assertEqual(argv[-3:], ["my-binary", "--flag", "val"])


class MaybeWrapForSandbox(unittest.TestCase):
    def test_returns_unwrapped_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cmd = ["npm", "test"]
            wrapped, was_wrapped = maybe_wrap_for_sandbox(Path(d), cmd)
            self.assertEqual(wrapped, cmd)
            self.assertFalse(was_wrapped)

    def test_wraps_when_enabled_and_docker_available(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            set_sandbox_config(root, enabled=True)
            with patch("signalos_lib.sandbox.docker_available", return_value=True):
                wrapped, was_wrapped = maybe_wrap_for_sandbox(root, ["npm", "test"])
            self.assertTrue(was_wrapped)
            self.assertEqual(wrapped[0], "docker")
            self.assertEqual(wrapped[-2:], ["npm", "test"])


class IpcHandler(unittest.TestCase):
    def test_status_returns_docker_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = json.loads(handle_sandbox(["status"], d))
            self.assertTrue(out["ok"])
            self.assertIn("docker_available", out)
            self.assertIn("config", out)
            self.assertFalse(out["config"]["enabled"])  # default off

    def test_enable_sets_flag(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = json.loads(handle_sandbox(["enable"], d))
            self.assertTrue(out["ok"])
            self.assertTrue(out["config"]["enabled"])
            # Survives a round-trip read.
            after = json.loads(handle_sandbox(["status"], d))
            self.assertTrue(after["config"]["enabled"])

    def test_enable_with_custom_image(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = json.loads(handle_sandbox(
                ["enable", "--image-js", "node:22-bullseye"], d,
            ))
            self.assertTrue(out["ok"])
            self.assertEqual(out["config"]["image_js"], "node:22-bullseye")

    def test_disable_clears_flag(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            handle_sandbox(["enable"], d)
            out = json.loads(handle_sandbox(["disable"], d))
            self.assertTrue(out["ok"])
            self.assertFalse(out["config"]["enabled"])

    def test_unknown_subcommand_rejected_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = json.loads(handle_sandbox(["frobnicate"], d))
            self.assertFalse(out["ok"])
            self.assertIn("Unknown", out["error"])


class DockerAvailableSmoke(unittest.TestCase):
    def test_returns_a_boolean(self) -> None:
        # We don't assert True or False -- depends on the machine.
        # We assert the function doesn't crash.
        self.assertIsInstance(docker_available(), bool)


if __name__ == "__main__":
    unittest.main()
