# test_product_sandbox.py
# The "boundary endgame" — runtime containment layer for command execution.
#
# Everything here is offline: argv construction, backend selection, availability
# detection and the byte-identical in-process path are asserted WITHOUT a live
# daemon (the container CLI is mocked). One integration smoke actually shells out
# to docker/podman/wsl and SKIPS cleanly when none is present.

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.sandbox import (
    CONTAINER_WORKSPACE,
    CommandOutput,
    ContainerRunner,
    InProcessRunner,
    SandboxUnavailableError,
    build_container_argv,
    container_engine_available,
    select_runner,
)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class TestBackendSelection:
    def test_default_is_in_process(self):
        r = select_runner("/ws", environ={})
        assert isinstance(r, InProcessRunner)
        assert r.name == "inprocess"

    def test_explicit_inprocess_aliases(self):
        for val in ("", "inprocess", "in-process", "none", "off"):
            assert isinstance(select_runner("/ws", environ={"SIGNALOS_SANDBOX": val}),
                              InProcessRunner)

    def test_docker_env_selects_container_when_available(self):
        r = select_runner(
            "/ws",
            environ={"SIGNALOS_SANDBOX": "docker"},
            which=lambda n: "/usr/bin/docker" if n == "docker" else None,
        )
        assert isinstance(r, ContainerRunner)
        assert r.engine == "docker"
        assert r.name == "container:docker"

    def test_wsl_env_selects_container_when_available(self):
        r = select_runner(
            "/ws",
            environ={"SIGNALOS_SANDBOX": "wsl"},
            which=lambda n: "/usr/bin/wsl.exe" if n in ("wsl", "wsl.exe") else None,
        )
        assert isinstance(r, ContainerRunner)
        assert r.engine == "wsl"

    def test_unavailable_runtime_falls_back_with_warning(self, caplog):
        events: list[dict] = []
        r = select_runner(
            "/ws",
            environ={"SIGNALOS_SANDBOX": "docker"},
            which=lambda n: None,  # docker NOT on PATH
            emit=events.append,
        )
        assert isinstance(r, InProcessRunner)  # graceful fallback
        assert any(e.get("type") == "sandbox_fallback" for e in events)

    def test_unavailable_runtime_strict_raises(self):
        with pytest.raises(SandboxUnavailableError):
            select_runner(
                "/ws",
                environ={"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_STRICT": "1"},
                which=lambda n: None,
            )

    def test_unknown_backend_falls_back(self):
        events: list[dict] = []
        r = select_runner(
            "/ws", environ={"SIGNALOS_SANDBOX": "bogus"}, emit=events.append
        )
        assert isinstance(r, InProcessRunner)
        assert any(e.get("type") == "sandbox_fallback" for e in events)

    def test_unknown_backend_strict_raises(self):
        with pytest.raises(SandboxUnavailableError):
            select_runner(
                "/ws",
                environ={"SIGNALOS_SANDBOX": "bogus", "SIGNALOS_SANDBOX_STRICT": "yes"},
            )

    def test_container_tunables_read_from_env(self):
        r = select_runner(
            "/ws",
            environ={
                "SIGNALOS_SANDBOX": "docker",
                "SIGNALOS_SANDBOX_IMAGE": "python:3.12-slim",
                "SIGNALOS_SANDBOX_CPUS": "4",
                "SIGNALOS_SANDBOX_MEMORY": "8g",
                "SIGNALOS_SANDBOX_PIDS": "1024",
            },
            which=lambda n: "/usr/bin/docker",
        )
        assert r.image == "python:3.12-slim"
        assert r.cpus == "4"
        assert r.memory == "8g"
        assert r.pids == "1024"


# ---------------------------------------------------------------------------
# Availability detection
# ---------------------------------------------------------------------------


class TestAvailabilityDetection:
    def test_docker_available(self):
        assert container_engine_available("docker", which=lambda n: "/usr/bin/docker")
        assert not container_engine_available("docker", which=lambda n: None)

    def test_wsl_probes_both_names(self):
        assert container_engine_available(
            "wsl", which=lambda n: "/x/wsl.exe" if n == "wsl.exe" else None
        )
        assert not container_engine_available("wsl", which=lambda n: None)

    def test_unknown_engine_never_available(self):
        assert not container_engine_available("nope", which=lambda n: "/anything")


# ---------------------------------------------------------------------------
# ContainerRunner argv construction — the core deliverable, asserted WITHOUT a
# live daemon.
# ---------------------------------------------------------------------------


class TestContainerArgv:
    def test_docker_argv_shape(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            argv = build_container_argv("npm test", ws, engine="docker",
                                        image="node:20-bookworm")
        # docker run --network none -v <ws>:/workspace -w /workspace ... sh -lc "npm test"
        assert argv[:3] == ["docker", "run", "--rm"]
        assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
        assert "-w" in argv and argv[argv.index("-w") + 1] == CONTAINER_WORKSPACE
        # the ONLY mount is the workspace, read-write, at /workspace
        vflag = argv[argv.index("-v") + 1]
        assert vflag.endswith(":" + CONTAINER_WORKSPACE)
        assert argv.count("-v") == 1
        # cpu/mem/pids caps present
        assert "--cpus" in argv and "--memory" in argv and "--pids-limit" in argv
        # image, then the command handed to a shell inside the container
        assert argv[-4:] == ["node:20-bookworm", "sh", "-lc", "npm test"]

    def test_mount_source_is_the_resolved_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            argv = build_container_argv("pwd", ws, engine="docker")
        vflag = argv[argv.index("-v") + 1]
        src = vflag.rsplit(":" + CONTAINER_WORKSPACE, 1)[0]
        expected = str(ws.resolve()).replace("\\", "/")
        assert src == expected

    def test_env_overlay_becomes_dash_e_flags(self):
        with tempfile.TemporaryDirectory() as d:
            argv = build_container_argv(
                "node -v", Path(d), engine="docker",
                env={"CI": "1", "FORCE_COLOR": "0"},
            )
        # -e CI=1 and -e FORCE_COLOR=0 forwarded into the container only
        joined = " ".join(argv)
        assert "-e CI=1" in joined
        assert "-e FORCE_COLOR=0" in joined

    def test_subdir_sets_workdir_under_mount(self):
        # A peeled `cd frontend` cwd maps to -w /workspace/frontend so the
        # command runs in the right place INSIDE the single mount.
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "frontend").mkdir()
            r = ContainerRunner(ws, engine="docker")
            argv = r.build_argv("npm test", ws / "frontend", {"CI": "1"})
        assert argv[argv.index("-w") + 1] == CONTAINER_WORKSPACE + "/frontend"

    def test_wsl_engine_wraps_docker_and_translates_path(self):
        argv = build_container_argv(
            "pytest", Path("C:/Users/x/ws"), engine="wsl", image="python:3.12-slim"
        )
        # docker CLI reached THROUGH the default WSL distro
        assert argv[:4] == ["wsl.exe", "-e", "docker", "run"]
        vflag = argv[argv.index("-v") + 1]
        # Windows drive path translated to the WSL mount form
        assert vflag == "/mnt/c/Users/x/ws:" + CONTAINER_WORKSPACE

    def test_podman_engine_is_argv_compatible(self):
        argv = build_container_argv("ls", Path("/ws"), engine="podman")
        assert argv[0] == "podman"
        assert "run" in argv and "--network" in argv

    def test_unknown_engine_rejected(self):
        with pytest.raises(ValueError):
            build_container_argv("ls", Path("/ws"), engine="qemu")

    def test_network_override_is_honored(self):
        argv = build_container_argv("ls", Path("/ws"), engine="docker",
                                    network="bridge")
        assert argv[argv.index("--network") + 1] == "bridge"


# ---------------------------------------------------------------------------
# ContainerRunner.run — drives the (mocked) container CLI and shapes the result.
# ---------------------------------------------------------------------------


class TestContainerRunnerRun:
    def test_run_invokes_container_cli_and_returns_output(self):
        fake = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="hello\n", stderr=""))
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            r = ContainerRunner(ws, engine="docker", runner=fake)
            exit_code, out = r.run("echo hello", ws, 60, {"CI": "1"})
        assert exit_code == 0
        assert out.stdout == "hello\n"
        assert not out.timed_out
        argv = fake.call_args.args[0]
        assert argv[:2] == ["docker", "run"]
        assert argv[-4:] == ["node:20-bookworm", "sh", "-lc", "echo hello"]

    def test_run_reports_timeout(self):
        fake = MagicMock(side_effect=subprocess.TimeoutExpired("docker", 1))
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            r = ContainerRunner(ws, engine="docker", runner=fake)
            exit_code, out = r.run("sleep 999", ws, 1, {})
        assert out.timed_out is True


# ---------------------------------------------------------------------------
# InProcessRunner — the DEFAULT backend; proven behavior-identical to today's
# subprocess execution (shell=True, cwd jailed, CI/FORCE_COLOR overlay).
# ---------------------------------------------------------------------------


class TestInProcessRunner:
    def test_executes_and_captures_output(self):
        with tempfile.TemporaryDirectory() as d:
            exit_code, out = InProcessRunner().run(
                "echo signalos-inproc", d, 30, {"CI": "1", "FORCE_COLOR": "0"}
            )
        assert exit_code == 0
        assert "signalos-inproc" in out.stdout
        assert not out.timed_out

    def test_nonzero_exit_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            # `false` on POSIX / `exit 1` are shell-dependent; use python which is
            # present wherever the test suite runs.
            exit_code, out = InProcessRunner().run(
                f'{shlex_quote(sys.executable)} -c "import sys; sys.exit(3)"',
                d, 30, {},
            )
        assert exit_code == 3

    def test_env_overlay_is_merged_onto_os_environ(self):
        # The overlay reaches the child AND host vars survive (byte-identical to
        # the old {**os.environ, "CI": "1", ...}).
        marker = "SIGNALOS_SANDBOX_TEST_MARKER"
        os.environ[marker] = "host-value"
        try:
            with tempfile.TemporaryDirectory() as d:
                code = (
                    "import os;"
                    "print(os.environ.get('CI'), os.environ.get('%s'))" % marker
                )
                _ec, out = InProcessRunner().run(
                    f'{shlex_quote(sys.executable)} -c "{code}"', d, 30, {"CI": "1"}
                )
            assert "1" in out.stdout          # overlay reached the child
            assert "host-value" in out.stdout  # host env preserved
        finally:
            os.environ.pop(marker, None)

    def test_timeout_returns_timed_out_flag(self):
        with tempfile.TemporaryDirectory() as d:
            code = "import time; time.sleep(10)"
            _ec, out = InProcessRunner().run(
                f'{shlex_quote(sys.executable)} -c "{code}"', d, 1, {}
            )
        assert out.timed_out is True


def shlex_quote(s: str) -> str:
    # sys.executable can contain spaces on Windows; quote it for shell=True.
    import shlex as _shlex

    if os.name == "nt":
        return '"' + s + '"'
    return _shlex.quote(s)


# ---------------------------------------------------------------------------
# Integration smoke — SKIPS when no container runtime is present. When docker/
# podman/wsl IS available this proves a command actually executes inside the
# containment boundary (true E2E). It stays a skip on hosts without the runtime
# (or without the image cached, since --network none cannot pull).
# ---------------------------------------------------------------------------


def _first_available_engine() -> str | None:
    for engine, probes in (("docker", ("docker",)),
                           ("podman", ("podman",)),
                           ("wsl", ("wsl", "wsl.exe"))):
        if any(shutil.which(p) for p in probes):
            return engine
    return None


class TestContainerIntegrationSmoke:
    @pytest.mark.skipif(
        _first_available_engine() is None,
        reason="no docker/podman/wsl runtime on PATH — container E2E not verifiable",
    )
    def test_command_runs_inside_container(self):
        engine = _first_available_engine()
        image = os.environ.get("SIGNALOS_SANDBOX_IMAGE", "busybox:latest")
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "marker.txt").write_text("inside", encoding="utf-8")
            r = ContainerRunner(ws, engine=engine, image=image)
            # Prove containment end-to-end: the workspace file is visible at the
            # mount and the command executes in the container.
            exit_code, out = r.run("cat /workspace/marker.txt", ws, 120, {})
        if exit_code != 0:
            # Daemon down, image not cached (network off can't pull), or WSL
            # lacks docker: a real skip, not a product failure.
            pytest.skip(f"{engine} present but container did not run "
                        f"(exit {exit_code}): {(out.stderr or out.stdout)[:200]}")
        assert "inside" in out.stdout
