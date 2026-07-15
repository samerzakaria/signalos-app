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
    DependencyMount,
    InProcessRunner,
    SandboxUnavailableError,
    build_container_argv,
    container_engine_available,
    select_runner,
    validate_pinned_image,
)
from signalos_lib.product.sandbox import _ENGINE_CLI  # CLI prefixes, for image probe


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

    def test_read_only_is_on_by_default(self):
        r = select_runner(
            "/ws",
            environ={"SIGNALOS_SANDBOX": "docker"},
            which=lambda n: "/usr/bin/docker",
        )
        assert r.read_only is True

    def test_read_only_env_escape_hatch(self):
        r = select_runner(
            "/ws",
            environ={"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_READONLY": "0"},
            which=lambda n: "/usr/bin/docker",
        )
        assert r.read_only is False

    def test_tmpfs_size_read_from_env(self):
        r = select_runner(
            "/ws",
            environ={"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_TMPFS_SIZE": "1g"},
            which=lambda n: "/usr/bin/docker",
        )
        assert r.tmpfs_size == "1g"

    def test_funded_profile_selects_only_hardened_pinned_container(self, tmp_path):
        image = "node:20-bookworm@sha256:" + "a" * 64
        r = select_runner(
            tmp_path,
            environ={
                "SIGNALOS_SANDBOX": "docker",
                "SIGNALOS_SANDBOX_PROFILE": "funded",
                "SIGNALOS_SANDBOX_IMAGE": image,
            },
            which=lambda n: "/usr/bin/docker" if n == "docker" else None,
        )
        assert isinstance(r, ContainerRunner)
        assert r.hardened is True
        assert r.workspace_read_only is True
        assert r.network == "none"
        assert r.pull == "never"
        assert r.image == image
        assert r.writable_paths == ("dist",)
        assert r.platform == "linux/amd64"
        assert r.require_funded_dependencies is True

    @pytest.mark.parametrize(
        "env,match",
        [
            ({"SIGNALOS_SANDBOX": "inprocess"}, "requires SIGNALOS_SANDBOX"),
            ({"SIGNALOS_SANDBOX": "wsl"}, "requires SIGNALOS_SANDBOX"),
            ({"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_IMAGE": "node:20"},
             "sha256"),
            ({"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_NETWORK": "bridge"},
             "network"),
            ({"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_READONLY": "0"},
             "read-only"),
            ({"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_PULL": "missing"},
             "pull policy"),
            ({"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_CPUS": "0"},
             "cpus"),
            ({"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_MEMORY": "0"},
             "memory"),
            ({"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_PIDS": "-1"},
             "pids"),
            ({"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_TMPFS_SIZE": "0"},
             "tmpfs"),
        ],
    )
    def test_funded_profile_rejects_every_safety_downgrade(self, tmp_path, env, match):
        values = {
            "SIGNALOS_SANDBOX_PROFILE": "funded",
            "SIGNALOS_SANDBOX_IMAGE": "node:20@sha256:" + "b" * 64,
            **env,
        }
        with pytest.raises(SandboxUnavailableError, match=match):
            select_runner(
                tmp_path,
                environ=values,
                which=lambda n: "/usr/bin/" + n,
            )


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
        # docker run --network none --read-only --tmpfs /tmp ... -v <ws>:/workspace
        #   -w /workspace ... sh -lc "npm test"
        assert argv[:3] == ["docker", "run", "--rm"]
        assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
        assert "-w" in argv and argv[argv.index("-w") + 1] == CONTAINER_WORKSPACE
        # rootfs hardening: immutable root filesystem
        assert "--read-only" in argv
        # writable tmpfs scratch at /tmp (world-writable sticky) + a writable HOME
        assert "--tmpfs" in argv
        tmpfs_specs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
        assert any(s.startswith("/tmp:") and "mode=1777" in s for s in tmpfs_specs)
        assert any(s.startswith("/root:") for s in tmpfs_specs)  # HOME/cache for npm
        # the ONLY bind mount is the workspace, read-write (no :ro), at /workspace
        vflag = argv[argv.index("-v") + 1]
        assert vflag.endswith(":" + CONTAINER_WORKSPACE)
        assert not vflag.endswith(":ro")
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

    def test_secret_overlay_and_credential_file_pointer_are_not_forwarded(self):
        with tempfile.TemporaryDirectory() as d:
            argv = build_container_argv(
                "node -v",
                Path(d),
                engine="docker",
                env={
                    "CI": "1",
                    "OPENROUTER_API_KEY": "fake-overlay-key",
                    "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY": "fake-attestation-key",
                    "SIGNALOS_ENV_FILE": "/workspace/.env",
                },
            )
        joined = " ".join(argv)
        assert "-e CI=1" in joined
        assert "OPENROUTER_API_KEY" not in joined
        assert "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY" not in joined
        assert "SIGNALOS_ENV_FILE" not in joined

    def test_subdir_sets_workdir_under_mount(self):
        # A peeled `cd frontend` cwd maps to -w /workspace/frontend so the
        # command runs in the right place INSIDE the single mount.
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "frontend").mkdir()
            r = ContainerRunner(ws, engine="docker")
            argv = r.build_argv("npm test", ws / "frontend", {"CI": "1"})
        assert argv[argv.index("-w") + 1] == CONTAINER_WORKSPACE + "/frontend"

    @pytest.mark.skipif(
        os.name != "nt",
        reason="WSL translates a Windows drive path (C:\\ -> /mnt/c) only on "
        "Windows; on POSIX Path('C:/Users/x/ws') is not an absolute drive path, "
        "so this Windows-only mount translation cannot be asserted. The engine "
        "argv wrapping itself is still covered cross-platform by "
        "test_wsl_argv_is_hardened_too.",
    )
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
# Read-only rootfs hardening — the immutable-rootfs + writable-tmpfs surface,
# asserted on the constructed argv WITHOUT a live daemon.
# ---------------------------------------------------------------------------


class TestReadOnlyHardening:
    def test_read_only_is_the_default(self):
        argv = build_container_argv("ls", Path("/ws"), engine="docker")
        assert "--read-only" in argv

    def test_read_only_can_be_disabled(self):
        # Escape hatch: drop the immutable rootfs but KEEP the tmpfs surface.
        argv = build_container_argv("ls", Path("/ws"), engine="docker",
                                    read_only=False)
        assert "--read-only" not in argv
        assert "--tmpfs" in argv  # tmpfs is independent of --read-only

    def test_default_writable_surface_is_tmp_and_home(self):
        argv = build_container_argv("ls", Path("/ws"), engine="docker")
        specs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
        paths = {s.split(":", 1)[0] for s in specs}
        # only /tmp (scratch) and /root (HOME cache) — nothing broader.
        assert paths == {"/tmp", "/root"}
        # /tmp is world-writable + sticky like a normal /tmp; both are size-capped.
        assert all("size=" in s for s in specs)
        assert any(s.startswith("/tmp:") and "mode=1777" in s for s in specs)

    def test_tmpfs_size_is_configurable(self):
        argv = build_container_argv("ls", Path("/ws"), engine="docker",
                                    tmpfs_size="128m")
        specs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
        assert specs and all("size=128m" in s for s in specs)

    def test_hardened_container_pins_bounded_shared_memory(self, tmp_path):
        # Docker's /dev/shm defaults to a tiny 64m (Chromium starves) and it must
        # never be left unbounded; the hardened runtime pins it to a validated
        # size so the source-blind Playwright oracle has enough shared memory
        # without opening a host-shared /dev/shm.
        image = validate_pinned_image("node:20@sha256:" + "c" * 64)
        argv = build_container_argv(
            "npm test", tmp_path, engine="docker", image=image, hardened=True,
            workspace_read_only=True,
        )
        assert "--shm-size" in argv
        assert argv[argv.index("--shm-size") + 1] == "1g"  # DEFAULT_SHM_SIZE

    def test_hardened_shared_memory_is_configurable_within_bounds(self, tmp_path):
        image = validate_pinned_image("node:20@sha256:" + "c" * 64)
        argv = build_container_argv(
            "npm test", tmp_path, engine="docker", image=image, hardened=True,
            workspace_read_only=True, shm_size="512m",
        )
        assert argv[argv.index("--shm-size") + 1] == "512m"

    def test_hardened_shared_memory_rejects_out_of_range_sizes(self, tmp_path):
        # < 64m starves the browser; > 4g is an unreasonable host commitment; a
        # malformed size must fail closed, never silently fall back.
        image = validate_pinned_image("node:20@sha256:" + "c" * 64)
        for bad in ("32m", "8g", "0", "not-a-size"):
            with pytest.raises(ValueError, match="shared-memory"):
                build_container_argv(
                    "npm test", tmp_path, engine="docker", image=image,
                    hardened=True, workspace_read_only=True, shm_size=bad,
                )

    def test_tmpfs_mapping_is_overridable_for_extension(self):
        # The writable surface is easy to extend when a build needs another path.
        argv = build_container_argv(
            "ls", Path("/ws"), engine="docker",
            tmpfs={"/tmp": "rw,size=64m,mode=1777", "/var/cache": "rw,size=64m"},
        )
        specs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
        paths = {s.split(":", 1)[0] for s in specs}
        assert paths == {"/tmp", "/var/cache"}

    def test_wsl_argv_is_hardened_too(self):
        argv = build_container_argv("pytest", Path("C:/Users/x/ws"), engine="wsl")
        assert argv[:4] == ["wsl.exe", "-e", "docker", "run"]
        assert "--read-only" in argv
        specs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
        assert any(s.startswith("/tmp:") for s in specs)

    def test_runner_threads_read_only_and_size_into_argv(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            r = ContainerRunner(ws, engine="docker", tmpfs_size="200m")
            assert r.read_only is True
            argv = r.build_argv("ls", ws, {})
        assert "--read-only" in argv
        assert any("size=200m" in a for a in argv)

    def test_hardened_argv_has_no_privilege_or_source_write_escape(self, tmp_path):
        image = validate_pinned_image("node:20@sha256:" + "c" * 64)
        argv = build_container_argv(
            "npm test",
            tmp_path,
            engine="docker",
            image=image,
            hardened=True,
            workspace_read_only=True,
            writable_paths=("dist",),
            container_name="signalos-funded-test",
            cidfile=str(tmp_path / "container.cid"),
            dependency_volume="signalos-deps-test123",
        )
        joined = " ".join(argv)
        assert "--init" in argv
        assert "--cap-drop ALL" in joined
        assert "--security-opt no-new-privileges:true" in joined
        assert "--memory-swap" in argv
        assert "--user" in argv
        assert "--entrypoint /bin/sh" in joined
        assert "--network none" in joined
        assert "--pull never" in joined
        assert "--platform linux/amd64" in joined
        assert "--name signalos-funded-test" in joined
        assert "--cidfile" in argv
        mounts = [argv[i + 1] for i, token in enumerate(argv) if token == "-v"]
        assert mounts[0].endswith(":/workspace:ro")
        assert any(m.endswith(":/workspace/dist:rw") for m in mounts)
        assert "signalos-deps-test123:/workspace/node_modules:ro" in mounts
        assert any(
            value.startswith("/workspace/node_modules/.vite:")
            for index, value in enumerate(argv)
            if index > 0 and argv[index - 1] == "--tmpfs"
        )
        assert all("docker.sock" not in mount for mount in mounts)
        assert "HOME=/home/signalos" in argv
        assert argv[-3:] == [image, "-lc", "npm test"]

    def test_hardened_argv_rejects_mutable_image_and_policy_downgrades(self, tmp_path):
        with pytest.raises(ValueError, match="sha256"):
            build_container_argv("true", tmp_path, image="node:20", hardened=True)
        image = "node:20@sha256:" + "d" * 64
        with pytest.raises(ValueError, match="network"):
            build_container_argv(
                "true", tmp_path, image=image, hardened=True, network="bridge"
            )
        with pytest.raises(ValueError, match="read-only"):
            build_container_argv(
                "true", tmp_path, image=image, hardened=True, read_only=False
            )
        with pytest.raises(ValueError, match="pull"):
            build_container_argv(
                "true", tmp_path, image=image, hardened=True, pull="missing"
            )

    @pytest.mark.parametrize(
        "kwargs,match",
        [
            ({"workspace_read_only": False}, "workspace"),
            ({"workspace_read_only": True, "container_user": "0:0"}, "non-root"),
            ({"workspace_read_only": True, "cpus": "0"}, "cpus"),
            ({"workspace_read_only": True, "memory": "0"}, "memory"),
            ({"workspace_read_only": True, "pids": "-1"}, "pids"),
            ({"workspace_read_only": True, "tmpfs_size": "0"}, "tmpfs"),
            ({"workspace_read_only": True, "tmpfs": {"/host": "rw"}}, "custom tmpfs"),
            ({"workspace_read_only": True, "platform": "linux/arm64"}, "platform"),
            ({"workspace_read_only": True, "writable_paths": ("node_modules",)},
             "unapproved writable"),
        ],
    )
    def test_hardened_argv_rejects_public_api_downgrades(self, tmp_path, kwargs, match):
        image = "node:20@sha256:" + "1" * 64
        with pytest.raises(ValueError, match=match):
            build_container_argv(
                "true", tmp_path, image=image, hardened=True, **kwargs
            )

    def test_hardened_installer_bridge_escape_is_rejected(self, tmp_path):
        image = "node:20@sha256:" + "5" * 64
        with pytest.raises(ValueError, match="network none"):
            build_container_argv(
                "npm ci --ignore-scripts --no-audit --no-fund",
                tmp_path,
                image=image,
                network="bridge",
                hardened=True,
                workspace_read_only=True,
            )


# ---------------------------------------------------------------------------
# Image pull policy — digest-pin / offline determinism: --pull=never by default
# so the workload never reaches the network to pull (consistent with
# --network none). The image must be pre-present.
# ---------------------------------------------------------------------------


class TestPullPolicy:
    def test_pull_never_is_the_default(self):
        argv = build_container_argv("ls", Path("/ws"), engine="docker")
        assert "--pull" in argv
        assert argv[argv.index("--pull") + 1] == "never"

    def test_pull_can_be_omitted(self):
        argv = build_container_argv("ls", Path("/ws"), engine="docker", pull=None)
        assert "--pull" not in argv

    def test_pull_policy_is_overridable(self):
        argv = build_container_argv("ls", Path("/ws"), engine="docker", pull="missing")
        assert argv[argv.index("--pull") + 1] == "missing"

    def test_pull_does_not_disturb_the_argv_tail_or_head(self):
        # --pull sits after --rm; the fixed head/tail the other tests rely on hold.
        argv = build_container_argv("npm test", Path("/ws"), engine="docker",
                                    image="node:20-bookworm")
        assert argv[:3] == ["docker", "run", "--rm"]
        assert argv[-4:] == ["node:20-bookworm", "sh", "-lc", "npm test"]

    def test_runner_threads_pull_into_argv(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            r = ContainerRunner(ws, engine="docker")
            assert r.pull == "never"
            argv = r.build_argv("ls", ws, {})
        assert argv[argv.index("--pull") + 1] == "never"

    def test_select_runner_defaults_pull_to_never(self):
        r = select_runner(
            "/ws",
            environ={"SIGNALOS_SANDBOX": "docker"},
            which=lambda n: "/usr/bin/docker",
        )
        assert r.pull == "never"

    def test_select_runner_reads_pull_from_env(self):
        r = select_runner(
            "/ws",
            environ={"SIGNALOS_SANDBOX": "docker", "SIGNALOS_SANDBOX_PULL": "missing"},
            which=lambda n: "/usr/bin/docker",
        )
        assert r.pull == "missing"


# ---------------------------------------------------------------------------
# ContainerRunner.run — drives the (mocked) container CLI and shapes the result.
# ---------------------------------------------------------------------------


def _funded_dependency_runner(tmp_path: Path, fake: MagicMock) -> ContainerRunner:
    image = "node:20@sha256:" + "9" * 64
    archive = tmp_path / ".signalos" / "dependencies" / "node_modules.tar"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"trusted archive")
    (tmp_path / "node_modules").mkdir(exist_ok=True)
    return ContainerRunner(
        tmp_path,
        engine="docker",
        image=image,
        hardened=True,
        workspace_read_only=True,
        writable_paths=("dist",),
        dependency_mount=DependencyMount(
            archive_path=archive,
            archive_sha256="a" * 64,
            tree_sha256="b" * 64,
            file_count=1,
            total_bytes=1,
        ),
        runner=fake,
    )


class TestContainerRunnerRun:
    def test_funded_dependency_snapshot_is_verified_then_mounted_read_only(self, tmp_path):
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="volume", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such volume"),
        ])
        runner = _funded_dependency_runner(tmp_path, fake)

        exit_code, output = runner.run("npm test", tmp_path, 30, {"CI": "1"})

        assert exit_code == 0 and output.stdout == "ok"
        calls = [call.args[0] for call in fake.call_args_list]
        bootstrap = calls[1]
        scored = calls[4]
        assert "sha256sum -c" in bootstrap[-1]
        assert bootstrap[-1].index("cp ") < bootstrap[-1].index("sha256sum -c")
        assert "extracted dependency tree evidence mismatch" in bootstrap[-1]
        assert "--network" in bootstrap and bootstrap[bootstrap.index("--network") + 1] == "none"
        assert "--platform" in bootstrap and bootstrap[bootstrap.index("--platform") + 1] == "linux/amd64"
        assert "--user" not in bootstrap
        assert "--user" in scored
        assert any(
            value.endswith(":/workspace/node_modules:ro")
            for value in scored
        )

    def test_dependency_bootstrap_nonzero_never_dispatches_scored_command(self, tmp_path):
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="volume", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=42, stdout="", stderr="tree mismatch"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such volume"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
        ])
        runner = _funded_dependency_runner(tmp_path, fake)

        with pytest.raises(SandboxUnavailableError, match="snapshot was rejected"):
            runner.run("npm test", tmp_path, 30, {"CI": "1"})

        run_calls = [
            call.args[0] for call in fake.call_args_list
            if call.args[0][:2] == ["docker", "run"]
        ]
        assert len(run_calls) == 1
        assert "npm test" not in run_calls[0]

    def test_dependency_bootstrap_timeout_cleans_and_never_dispatches(self, tmp_path):
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="volume", stderr=""),
            subprocess.TimeoutExpired("docker", 1),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such volume"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
        ])
        runner = _funded_dependency_runner(tmp_path, fake)

        with pytest.raises(SandboxUnavailableError, match="preparation timed out"):
            runner.run("npm test", tmp_path, 1, {})

        assert sum(
            call.args[0][:2] == ["docker", "run"] for call in fake.call_args_list
        ) == 1

    def test_dependency_volume_creation_failure_is_typed_and_cleaned(self, tmp_path):
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="daemon denied"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such volume"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such volume"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
        ])
        runner = _funded_dependency_runner(tmp_path, fake)

        with pytest.raises(SandboxUnavailableError, match="cannot create"):
            runner.run("npm test", tmp_path, 30, {})

        assert not any(
            call.args[0][:2] == ["docker", "run"] for call in fake.call_args_list
        )

    def test_dependency_volume_cleanup_failure_is_infrastructure(self, tmp_path):
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="volume", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="permission denied"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="still exists", stderr=""),
        ])
        runner = _funded_dependency_runner(tmp_path, fake)

        with pytest.raises(SandboxUnavailableError, match="volume still exists"):
            runner.run("npm test", tmp_path, 30, {})

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

    def test_hardened_run_forces_named_container_cleanup(self, tmp_path):
        image = "node:20@sha256:" + "e" * 64
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
        ])
        runner = ContainerRunner(
            tmp_path,
            engine="docker",
            image=image,
            hardened=True,
            workspace_read_only=True,
            writable_paths=("dist",),
            runner=fake,
        )

        exit_code, output = runner.run("npm test", tmp_path, 30, {"CI": "1"})

        assert exit_code == 0
        assert output.stdout == "ok\n"
        calls = [call.args[0] for call in fake.call_args_list]
        assert calls[0][:2] == ["docker", "run"]
        name = calls[0][calls[0].index("--name") + 1]
        assert calls[1] == ["docker", "rm", "-f", name]
        assert calls[2] == ["docker", "inspect", name]

    def test_hardened_runtime_launch_failure_is_infrastructure(self, tmp_path):
        image = "node:20@sha256:" + "2" * 64
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(args=[], returncode=125, stdout="", stderr="daemon down"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such container"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such object"),
        ])
        runner = ContainerRunner(
            tmp_path,
            engine="docker",
            image=image,
            hardened=True,
            workspace_read_only=True,
            runner=fake,
        )

        with pytest.raises(SandboxUnavailableError, match="exit 125"):
            runner.run("true", tmp_path, 30, {})

    def test_hardened_missing_product_command_remains_product_evidence(self, tmp_path):
        image = "node:20@sha256:" + "4" * 64
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(
                args=[], returncode=127, stdout="", stderr="missing-tool: not found"
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="no such container"
            ),
        ])
        runner = ContainerRunner(
            tmp_path,
            engine="docker",
            image=image,
            hardened=True,
            workspace_read_only=True,
            runner=fake,
        )

        exit_code, output = runner.run("missing-tool", tmp_path, 30, {})

        assert exit_code == 127
        assert "not found" in output.stderr

    def test_hardened_cleanup_failure_is_an_infrastructure_error(self, tmp_path):
        image = "node:20@sha256:" + "f" * 64
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="denied"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="still there", stderr=""),
        ])
        runner = ContainerRunner(
            tmp_path,
            engine="docker",
            image=image,
            hardened=True,
            workspace_read_only=True,
            runner=fake,
        )
        with pytest.raises(SandboxUnavailableError, match="still exists"):
            runner.run("true", tmp_path, 30, {})

    def test_hardened_cleanup_rejects_inconclusive_double_failure(self, tmp_path):
        image = "node:20@sha256:" + "3" * 64
        fake = MagicMock(side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="permission denied"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="daemon unavailable"),
        ])
        runner = ContainerRunner(
            tmp_path,
            engine="docker",
            image=image,
            hardened=True,
            workspace_read_only=True,
            runner=fake,
        )

        with pytest.raises(SandboxUnavailableError, match="inconclusive"):
            runner.run("true", tmp_path, 30, {})


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

    def test_non_secret_env_overlay_is_merged_onto_safe_host_environ(self):
        # Ordinary overlay + host vars reach the child after credential scrubbing.
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

    def test_governed_child_cannot_observe_parent_provider_credentials(self):
        parent_values = {
            "OPENROUTER_API_KEY": "fake-parent-key",
            "ANTHROPIC_AUTH_TOKEN": "fake-parent-token",
            "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY": "fake-attestation-key",
            "SIGNALOS_ENV_FILE": "C:/private/provider.env",
        }
        previous = {key: os.environ.get(key) for key in parent_values}
        os.environ.update(parent_values)
        try:
            with tempfile.TemporaryDirectory() as d:
                code = (
                    "import os;"
                    "names=['OPENROUTER_API_KEY','ANTHROPIC_AUTH_TOKEN',"
                    "'SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY',"
                    "'SIGNALOS_ENV_FILE','OPENAI_API_KEY'];"
                    "print('SECRET_PRESENT' if any(os.environ.get(n) for n in names) "
                    "else 'SECRET_ABSENT');"
                    "print(os.environ.get('CI','missing'))"
                )
                exit_code, out = InProcessRunner().run(
                    f'{shlex_quote(sys.executable)} -c "{code}"',
                    d,
                    30,
                    {"CI": "1", "OPENAI_API_KEY": "fake-overlay-key"},
                )
            assert exit_code == 0
            assert "SECRET_ABSENT" in out.stdout
            assert "1" in out.stdout  # non-secret overlay remains available
            # Sanitizing a copied child environment must never consume or
            # mutate the provider credentials retained by the sidecar.
            assert os.environ["OPENROUTER_API_KEY"] == "fake-parent-key"
        finally:
            for key, old_value in previous.items():
                if old_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_value

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


# ---------------------------------------------------------------------------
# Read-only rootfs containment smoke — the REAL proof (not just argv) that the
# hardened container writes ONLY to the workspace + /tmp + HOME and CANNOT touch
# the rootfs. SKIPS cleanly with no runtime or no cached image (--network none
# cannot pull). Chooses a cached image so the test is hermetic (no surprise pull).
# ---------------------------------------------------------------------------

# Small base images (fs-only checks) and node images (a representative tool),
# in preference order. The first one CACHED for the engine is used.
_BASE_IMAGE_CANDIDATES = ("alpine:latest", "busybox:latest",
                          "debian:bookworm-slim", "ubuntu:latest")
_NODE_IMAGE_CANDIDATES = ("node:20-bookworm", "node:20", "node:22", "node:24",
                          "node:lts", "node:latest")


def _cached_images(engine: str) -> set[str]:
    """`repo:tag` images already cached for *engine* (empty on any failure)."""
    try:
        proc = subprocess.run(
            list(_ENGINE_CLI[engine]) + ["images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return set()
    if proc.returncode != 0:
        return set()
    return {ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()}


def _pick_cached(engine: str, candidates) -> str | None:
    override = os.environ.get("SIGNALOS_SANDBOX_IMAGE")
    cached = _cached_images(engine)
    if override and override in cached:
        return override
    return next((c for c in candidates if c in cached), None)


# The four-way containment probe reused by the docker + wsl smokes:
#   (a) workspace write OK, (b) /tmp write OK, (c) /etc + /usr write BLOCKED.
_FS_PROBE = (
    "echo ok > /workspace/ws.txt && echo WS_OK; "
    "echo ok > /tmp/t.txt && echo TMP_OK; "
    "(echo x > /etc/x 2>/dev/null && echo ETC_BAD) || echo ETC_BLOCKED; "
    "(echo x > /usr/x 2>/dev/null && echo USR_BAD) || echo USR_BLOCKED"
)


def _assert_fs_containment(out, host_write_landed: bool) -> None:
    s = out.stdout
    assert "WS_OK" in s        # (a) the workspace bind mount is writable
    assert "TMP_OK" in s       # (b) the /tmp tmpfs is writable
    assert "ETC_BLOCKED" in s  # (c) rootfs is read-only
    assert "USR_BLOCKED" in s
    assert "ETC_BAD" not in s and "USR_BAD" not in s
    assert host_write_landed   # the write really landed on the host mount


class TestReadOnlyContainmentSmoke:
    @pytest.mark.skipif(
        _first_available_engine() is None,
        reason="no docker/podman/wsl runtime on PATH — read-only E2E not verifiable",
    )
    def test_readonly_rootfs_contains_writes(self):
        engine = _first_available_engine()
        image = _pick_cached(engine, _BASE_IMAGE_CANDIDATES)
        if image is None:
            pytest.skip(f"no small base image cached for {engine} "
                        "(--network none cannot pull) — read-only E2E skipped")
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            r = ContainerRunner(ws, engine=engine, image=image)
            exit_code, out = r.run(_FS_PROBE, ws, 120, {})
            landed = (ws / "ws.txt").exists()  # check before the tempdir is removed
        if "WS_OK" not in out.stdout and "ETC_BLOCKED" not in out.stdout:
            pytest.skip(f"{engine}/{image} present but container did not run "
                        f"(exit {exit_code}): {(out.stderr or out.stdout)[:200]}")
        _assert_fs_containment(out, landed)

    @pytest.mark.skipif(
        _first_available_engine() is None,
        reason="no docker/podman/wsl runtime on PATH — node tool E2E not verifiable",
    )
    def test_node_tool_works_under_readonly(self):
        # A representative build tool: node writes to the workspace (OK) but not
        # the rootfs (blocked), and npm can write its cache/config to the writable
        # HOME tmpfs (/root) despite the read-only rootfs.
        engine = _first_available_engine()
        image = _pick_cached(engine, _NODE_IMAGE_CANDIDATES)
        if image is None:
            pytest.skip(f"no node image cached for {engine} "
                        "(--network none cannot pull) — node tool E2E skipped")
        script = (
            """node -e "require('fs').writeFileSync('/workspace/ok.txt','ok')" && echo NODE_WS_OK; """
            """( node -e "require('fs').writeFileSync('/etc/ok.txt','x')" 2>/dev/null && echo NODE_ETC_BAD ) || echo NODE_ETC_BLOCKED; """
            """npm config set fund false 2>/dev/null && echo NPM_HOME_OK || echo NPM_HOME_FAIL"""
        )
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            r = ContainerRunner(ws, engine=engine, image=image)
            exit_code, out = r.run(script, ws, 180, {})
            landed = (ws / "ok.txt").exists()  # check before the tempdir is removed
        if "NODE_WS_OK" not in out.stdout and "NODE_ETC_BLOCKED" not in out.stdout:
            pytest.skip(f"{engine}/{image} present but node did not run "
                        f"(exit {exit_code}): {(out.stderr or out.stdout)[:200]}")
        assert "NODE_WS_OK" in out.stdout        # node writes to the workspace
        assert "NODE_ETC_BLOCKED" in out.stdout  # node CANNOT write the rootfs
        assert "NODE_ETC_BAD" not in out.stdout
        assert "NPM_HOME_OK" in out.stdout       # writable HOME tmpfs works
        assert landed                            # the write really landed on host


# ---------------------------------------------------------------------------
# WSL-engine smoke — exercises the `wsl` backend for REAL (the /mnt/c path
# translation + --network none + read-only rootfs), previously offline-only.
# SKIPS with a clear reason when docker is not reachable via `wsl.exe -e docker`.
# ---------------------------------------------------------------------------


def _wsl_docker_reachable() -> bool:
    if shutil.which("wsl") is None and shutil.which("wsl.exe") is None:
        return False
    try:
        proc = subprocess.run(["wsl.exe", "-e", "docker", "version"],
                              capture_output=True, text=True, timeout=60)
        return proc.returncode == 0
    except Exception:
        return False


_WSL_DOCKER_REACHABLE = _wsl_docker_reachable()


class TestWslEngineSmoke:
    @pytest.mark.skipif(
        not _WSL_DOCKER_REACHABLE,
        reason="docker not reachable via `wsl.exe -e docker` — wsl E2E not verifiable",
    )
    def test_wsl_readonly_containment(self):
        image = _pick_cached("wsl", _BASE_IMAGE_CANDIDATES)
        if image is None:
            pytest.skip("no small base image cached in wsl docker "
                        "(--network none cannot pull) — wsl E2E skipped")
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            r = ContainerRunner(ws, engine="wsl", image=image)
            # The argv really goes THROUGH wsl.exe with the /mnt path + hardening.
            argv = r.build_argv("true", ws, {})
            assert argv[:4] == ["wsl.exe", "-e", "docker", "run"]
            assert "--read-only" in argv
            assert argv[argv.index("-v") + 1].startswith("/mnt/")
            exit_code, out = r.run(_FS_PROBE, ws, 180, {})
            landed = (ws / "ws.txt").exists()  # check before the tempdir is removed
        if "WS_OK" not in out.stdout and "ETC_BLOCKED" not in out.stdout:
            pytest.skip(f"wsl docker present but container did not run "
                        f"(exit {exit_code}): {(out.stderr or out.stdout)[:200]}")
        # Same containment guarantees hold through the wsl path translation.
        _assert_fs_containment(out, landed)
