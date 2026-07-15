from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shutil
import stat
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from signalos_lib.product.dependency_broker import (
    APPROVED_CONNECT_AUTHORITY,
    TRUSTED_INSTALL_SHELL_COMMAND,
    TRUSTED_LOCAL_DOCKER_DESKTOP_PROFILE,
    TRUSTED_LOCAL_DOCKER_ENGINE_PROFILE,
    WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT,
    load_dependency_policy,
    trusted_install_environment,
)
from signalos_lib.product.dependency_proxy import (
    DependencyProxyCleanupError,
    DependencyProxyInfrastructureError,
    DependencyProxyPolicyError,
    DependencyProxyTimeoutError,
    DockerRegistryProxyRunner,
)


ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = ROOT / "scripts" / "backend_matrix" / "dependencies"
FIXED_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
TOKEN = FIXED_UUID.hex
RUNTIME_IMAGE_ID = "sha256:" + "a" * 64


class _SafeSocketStat:
    st_mode = stat.S_IFSOCK | 0o660
    st_uid = 0


def _completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _all_after(argv: list[str], flag: str) -> list[str]:
    return [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == flag]


def _docker_command(argv: list[str]) -> list[str]:
    args = argv[1:]
    return args[2:] if args[:1] == ["--host"] else args


def _size(value: str) -> int:
    number = int(value[:-1])
    return number * {"m": 1024**2, "g": 1024**3}[value[-1].lower()]


class _FakeDocker:
    """Stateful Docker CLI double; no subprocess or daemon is ever reached."""

    def __init__(
        self,
        image: str,
        *,
        endpoint: str | None = None,
        fail_contains: tuple[str, ...] | None = None,
        timeout_contains: tuple[str, ...] | None = None,
        leave_installer: bool = False,
        mutate_contains: tuple[str, ...] | None = None,
        mutate: Callable[[], None] | None = None,
        repo_digests: list[str] | None = None,
        container_image_id: str = RUNTIME_IMAGE_ID,
        switch_endpoint_after_context: str | None = None,
        daemon_os_type: str = "linux",
        network_absence_error: str | None = None,
        network_remove_active_failures: int = 0,
        unexpected_running_network_member: str | None = None,
        declared_network_drift: str | None = None,
    ) -> None:
        self.image = image
        self.endpoint = endpoint or (
            "npipe:////./pipe/dockerDesktopLinuxEngine"
            if os.name == "nt"
            else "unix:///var/run/docker.sock"
        )
        self.fail_contains = fail_contains
        self.timeout_contains = timeout_contains
        self.leave_installer = leave_installer
        self.mutate_contains = mutate_contains
        self.mutate = mutate
        self.mutated = False
        self.repo_digests = [image] if repo_digests is None else repo_digests
        self.container_image_id = container_image_id
        self.switch_endpoint_after_context = switch_endpoint_after_context
        self.daemon_os_type = daemon_os_type
        self.network_absence_error = network_absence_error
        self.network_remove_active_failures = network_remove_active_failures
        self.unexpected_running_network_member = unexpected_running_network_member
        self.declared_network_drift = declared_network_drift
        self.calls: list[tuple[list[str], dict]] = []
        self.networks: dict[str, dict] = {}
        self.containers: dict[str, dict] = {}

    @staticmethod
    def _contains(argv: list[str], wanted: tuple[str, ...] | None) -> bool:
        if not wanted:
            return False
        return any(argv[index:index + len(wanted)] == list(wanted) for index in range(len(argv)))

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, dict(kwargs)))
        args = argv[1:]
        if args[:1] == ["--host"]:
            args = args[2:]
        if (
            not self.mutated
            and self.mutate is not None
            and self._contains(argv, self.mutate_contains)
        ):
            self.mutated = True
            self.mutate()
        if self._contains(argv, self.timeout_contains):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 1))
        is_cleanup = len(args) > 1 and (
            args[:2] == ["rm", "-f"]
            or args[:2] == ["network", "rm"]
            or (args[0] == "inspect" and args[1] not in self.containers)
            or (args[:2] == ["network", "inspect"] and args[2] not in self.networks)
        )
        if self._contains(argv, self.fail_contains) and not is_cleanup:
            return _completed(argv, 1, stderr="injected failure")

        if args[:4] == ["context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"]:
            reported_endpoint = self.endpoint
            if self.switch_endpoint_after_context is not None:
                self.endpoint = self.switch_endpoint_after_context
            return _completed(argv, stdout=json.dumps(reported_endpoint) + "\n")
        if args[:3] == ["info", "--format", "{{json .}}"]:
            return _completed(
                argv,
                stdout=json.dumps({"OSType": self.daemon_os_type}) + "\n",
            )
        if args[:4] == ["image", "inspect", "--format", "{{json .}}"]:
            return _completed(
                argv,
                stdout=json.dumps(
                    {"Os": "linux", "Architecture": "amd64", "Id": "sha256:" + "a" * 64}
                    | {"RepoDigests": self.repo_digests},
                ) + "\n",
            )
        if args[:2] == ["network", "create"]:
            name = args[-1]
            self.networks[name] = {
                "internal": "--internal" in args,
                "labels": {
                    item.split("=", 1)[0]: item.split("=", 1)[1]
                    for item in _all_after(args, "--label")
                },
            }
            return _completed(argv, stdout=name + "\n")
        if args[:2] == ["network", "connect"]:
            network, container = args[-2:]
            alias = _after(args, "--alias")
            self.containers[container]["networks"][network] = {container, alias}
            return _completed(argv)
        if args[:2] == ["network", "inspect"]:
            name = args[-1]
            if name not in self.networks:
                return _completed(argv, 1, stderr=self._network_absence(name))
            visible_containers = {
                container_name: {"Name": container_name}
                for container_name, container in self.containers.items()
                if container["running"] and name in container["networks"]
            }
            if self.unexpected_running_network_member is not None:
                visible_containers[self.unexpected_running_network_member] = {
                    "Name": self.unexpected_running_network_member,
                }
            return _completed(
                argv,
                stdout=json.dumps(
                    [{
                        "Name": name,
                        "Driver": "bridge",
                        "Scope": "local",
                        "Internal": self.networks[name]["internal"],
                        "Attachable": False,
                        "Ingress": False,
                        "Labels": self.networks[name]["labels"],
                        "Containers": visible_containers,
                    }]
                ),
            )
        if args and args[0] == "create":
            name = _after(args, "--name")
            self.containers[name] = {
                "argv": args,
                "networks": {_after(args, "--network"): {name}},
                "running": False,
            }
            return _completed(argv, stdout="sha256:" + "b" * 64 + "\n")
        if args and args[0] == "inspect":
            name = args[-1]
            if name not in self.containers:
                return _completed(argv, 1, stderr="Error: no such container")
            return _completed(argv, stdout=json.dumps([self._container_inspect(name)]))
        if args and args[0] == "start":
            self.containers[args[-1]]["running"] = True
            return _completed(argv, stdout=args[-1] + "\n")
        if args and args[0] == "exec":
            return _completed(argv)
        if args and args[0] == "wait":
            self.containers[args[-1]]["running"] = False
            return _completed(argv, stdout="0\n")
        if args and args[0] == "logs":
            return _completed(
                argv,
                stdout="SIGNALOS_RUNTIME=linux/x64\n10.8.2\nadded 236 packages\n",
            )
        if args[:2] == ["rm", "-f"]:
            name = args[-1]
            if self.leave_installer and "installer" in name:
                return _completed(argv)
            if self.containers.pop(name, None) is None:
                return _completed(argv, 1, stderr="Error: no such container")
            return _completed(argv, stdout=name + "\n")
        if args[:2] == ["network", "rm"]:
            name = args[-1]
            if name in self.networks and self.network_remove_active_failures > 0:
                self.network_remove_active_failures -= 1
                return _completed(
                    argv,
                    1,
                    stderr=(
                        "Error response from daemon: "
                        f"network {name} has active endpoints"
                    ),
                )
            if self.networks.pop(name, None) is None:
                return _completed(argv, 1, stderr=self._network_absence(name))
            return _completed(argv, stdout=name + "\n")
        raise AssertionError(f"unexpected fake Docker argv: {argv!r}")

    def _network_absence(self, name: str) -> str:
        if self.network_absence_error is not None:
            return self.network_absence_error.format(name=name)
        return f"Error response from daemon: network {name} not found"

    def _container_inspect(self, name: str) -> dict:
        state = self.containers[name]
        argv = state["argv"]
        proxy = "proxy" in name
        image_index = argv.index(self.image)
        labels = {
            entry.split("=", 1)[0]: entry.split("=", 1)[1]
            for entry in _all_after(argv, "--label")
        }
        env = _all_after(argv, "--env")
        memory = _size(_after(argv, "--memory"))
        cpus = int(float(_after(argv, "--cpus")) * 1_000_000_000)
        binds = _all_after(argv, "--volume") or None
        tmpfs = {
            entry.split(":", 1)[0]: entry.split(":", 1)[1]
            for entry in _all_after(argv, "--tmpfs")
        }
        network_settings = {
            network: {"Aliases": sorted(aliases)}
            for network, aliases in state["networks"].items()
        }
        if self.declared_network_drift == "missing" and network_settings:
            network_settings.pop(sorted(network_settings)[0])
        elif self.declared_network_drift == "extra":
            network_settings["bridge"] = {"Aliases": ["bridge"]}
        return {
            "Image": self.container_image_id,
            "Config": {
                "Image": self.image,
                "User": _after(argv, "--user"),
                "Entrypoint": [_after(argv, "--entrypoint")],
                "Cmd": argv[image_index + 1:],
                "Labels": labels,
                "Env": env,
                "WorkingDir": _after(argv, "--workdir") if "--workdir" in argv else "",
            },
            "HostConfig": {
                "NetworkMode": _after(argv, "--network"),
                "ReadonlyRootfs": "--read-only" in argv,
                "Privileged": False,
                "Init": "--init" in argv,
                "CapDrop": _all_after(argv, "--cap-drop"),
                "CapAdd": [],
                "SecurityOpt": _all_after(argv, "--security-opt"),
                "Memory": memory,
                "MemorySwap": _size(_after(argv, "--memory-swap")),
                "NanoCpus": cpus,
                "PidsLimit": int(_after(argv, "--pids-limit")),
                "PortBindings": {},
                "Devices": [],
                "ExtraHosts": [],
                "Dns": [],
                "Links": [],
                "Binds": binds,
                "RestartPolicy": {"Name": _after(argv, "--restart")},
                "LogConfig": {
                    "Type": _after(argv, "--log-driver"),
                    "Config": dict(item.split("=", 1) for item in _all_after(argv, "--log-opt")),
                },
                "Tmpfs": tmpfs,
            },
            "NetworkSettings": {
                "Networks": network_settings,
            },
            "Mounts": [] if proxy else [
                {
                    "Type": "bind",
                    "Source": binds[0][:-len(":/workspace:rw")],
                    "Destination": "/workspace",
                    "Mode": "rw",
                    "RW": True,
                }
            ],
        }


def _staging(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True)
    shutil.copy2(DEPENDENCIES / "react-vite" / "package.json", staging / "package.json")
    shutil.copy2(
        DEPENDENCIES / "react-vite" / "package-lock.json",
        staging / "package-lock.json",
    )
    return staging


def _runner(tmp_path: Path, fake: _FakeDocker, **kwargs) -> tuple[DockerRegistryProxyRunner, Path]:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    endpoint_hooks = (
        {
            "endpoint_lstat": lambda _path: _SafeSocketStat(),
            "endpoint_resolve": lambda path: path,
        }
        if os.name != "nt"
        else {}
    )
    return (
        DockerRegistryProxyRunner(
            policy,
            docker_cli="docker",
            runtime=fake,
            environ={
                "PATH": "C:\\Windows\\System32",
                "OPENROUTER_API_KEY": "provider-secret",
                "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY": "attestation-secret",
                "DOCKER_HOST": "tcp://untrusted.example:2375",
            },
            uuid_factory=lambda: FIXED_UUID,
            host_os_name=os.name,
            **endpoint_hooks,
            **kwargs,
        ),
        _staging(tmp_path),
    )


@pytest.fixture
def real_docker_host_shapes(tmp_path: Path) -> dict:
    """Live Docker inspect excerpts, independent of generated create argv."""

    common = {
        "ReadonlyRootfs": True,
        "Privileged": False,
        "Init": True,
        "CapDrop": ["ALL"],
        "CapAdd": [],
        "SecurityOpt": ["no-new-privileges:true"],
        "PortBindings": {},
        "Devices": [],
        "ExtraHosts": [],
        "Dns": [],
        "Links": [],
        "RestartPolicy": {"Name": "no"},
        "LogConfig": {
            "Type": "local",
            "Config": {"max-size": "1m", "max-file": "1", "compress": "false"},
        },
    }
    source = tmp_path.resolve()
    return {
        "source": source,
        "proxy_host": {
            **common,
            "Memory": 128 * 1024 * 1024,
            "MemorySwap": 128 * 1024 * 1024,
            "NanoCpus": 500_000_000,
            "PidsLimit": 64,
            "Binds": None,
            "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=16m,mode=1777"},
        },
        "proxy_mounts": [],
        "installer_host": {
            **common,
            "Memory": 2 * 1024 * 1024 * 1024,
            "MemorySwap": 2 * 1024 * 1024 * 1024,
            "NanoCpus": 2_000_000_000,
            "PidsLimit": 512,
            "Binds": [f"{source}:/workspace:rw"],
            "Tmpfs": {
                "/tmp": "rw,nosuid,nodev,noexec,size=512m,mode=1777",
                "/home/signalos": "rw,nosuid,nodev,noexec,size=64m,mode=1777",
            },
        },
        # Docker Desktop reports only the bind here; HostConfig owns tmpfs evidence.
        "installer_mounts": [{
            "Type": "bind",
            "Source": str(source),
            "Destination": "/workspace",
            "Mode": "rw",
            "RW": True,
        }],
    }


def test_real_docker_host_shapes_are_accepted_without_argv_synthesis(
    real_docker_host_shapes: dict,
) -> None:
    fixture = real_docker_host_shapes
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    runner = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=lambda *_args, **_kwargs: None,
        environ={},
        host_os_name=os.name,
    )
    assert runner._hardening_matches(
        fixture["proxy_host"],
        memory=128 * 1024 * 1024,
        cpus=500_000_000,
        pids=64,
        expected_tmpfs={
            "/tmp": {"rw", "nosuid", "nodev", "noexec", "size=16m", "mode=1777"}
        },
    )
    assert runner._mounts_match(
        fixture["proxy_mounts"],
        binds={},
        tmpfs_paths={"/tmp"},
    )
    assert runner._hardening_matches(
        fixture["installer_host"],
        memory=2 * 1024 * 1024 * 1024,
        cpus=2_000_000_000,
        pids=512,
        expected_tmpfs={
            "/tmp": {"rw", "nosuid", "nodev", "noexec", "size=512m", "mode=1777"},
            "/home/signalos": {
                "rw", "nosuid", "nodev", "noexec", "size=64m", "mode=1777"
            },
        },
    )
    assert runner._host_binds_match(
        fixture["installer_host"]["Binds"],
        binds={"/workspace": fixture["source"]},
    )
    assert runner._mounts_match(
        fixture["installer_mounts"],
        binds={"/workspace": fixture["source"]},
        tmpfs_paths={"/tmp", "/home/signalos"},
    )


def test_installer_environment_uses_distinct_absent_npm_configs_and_closed_attestation(
) -> None:
    proxy_url = "http://signalos-registry-proxy:3128"
    expected = {
        **trusted_install_environment(),
        "HOME": "/home/signalos",
        "TMPDIR": "/tmp",
        "NPM_CONFIG_CACHE": "/tmp/npm-cache",
        "HTTPS_PROXY": proxy_url,
        "https_proxy": proxy_url,
        "HTTP_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "NO_PROXY": "",
        "no_proxy": "",
        "NPM_CONFIG_HTTPS_PROXY": proxy_url,
        "NPM_CONFIG_PROXY": proxy_url,
        "NPM_CONFIG_STRICT_SSL": "true",
        "NODE_TLS_REJECT_UNAUTHORIZED": "1",
        "NODE_EXTRA_CA_CERTS": "",
        "NPM_CONFIG_CA": "",
        "NPM_CONFIG_CAFILE": "",
        "SSL_CERT_FILE": "",
        "SSL_CERT_DIR": "",
        "NPM_CONFIG_USERCONFIG": "/tmp/signalos-npm-userconfig",
        "NPM_CONFIG_GLOBALCONFIG": "/tmp/signalos-npm-globalconfig",
    }
    actual = DockerRegistryProxyRunner._installer_environment(
        trusted_install_environment()
    )

    assert actual == expected
    user_config = actual["NPM_CONFIG_USERCONFIG"]
    global_config = actual["NPM_CONFIG_GLOBALCONFIG"]
    assert posixpath.isabs(user_config)
    assert posixpath.dirname(user_config) == "/tmp"
    assert posixpath.isabs(global_config)
    assert posixpath.dirname(global_config) == "/tmp"
    assert posixpath.normpath(user_config) != posixpath.normpath(global_config)
    assert not user_config.startswith("/workspace/")
    assert not global_config.startswith("/workspace/")

    official_node_image_defaults = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "NODE_VERSION": "20.20.2",
        "YARN_VERSION": "1.22.22",
    }
    assert DockerRegistryProxyRunner._installer_environment_matches(
        {**actual, **official_node_image_defaults}, actual
    )
    assert not DockerRegistryProxyRunner._installer_environment_matches(
        {**actual, "NPM_CONFIG_LOGLEVEL": "silent"}, actual
    )
    assert not DockerRegistryProxyRunner._installer_environment_matches(
        {**actual, "npm_config_userconfig": user_config}, actual
    )
    assert not DockerRegistryProxyRunner._installer_environment_matches(
        {**actual, "NPM_CONFIG_USERCONFIG": global_config}, actual
    )


def test_success_uses_exact_internal_proxy_topology_and_cleans_everything(tmp_path: Path) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image)
    runner, staging = _runner(tmp_path, fake)

    exit_code, output, evidence = runner.run(
        TRUSTED_INSTALL_SHELL_COMMAND,
        staging,
        300,
        trusted_install_environment(),
    )

    assert exit_code == 0
    assert output.stdout.startswith("SIGNALOS_RUNTIME=linux/x64")
    assert evidence.allowed_connect_authorities == (APPROVED_CONNECT_AUTHORITY,)
    assert evidence.runtime_image_id == RUNTIME_IMAGE_ID
    assert evidence.host_trust_profile == (
        TRUSTED_LOCAL_DOCKER_DESKTOP_PROFILE
        if os.name == "nt"
        else TRUSTED_LOCAL_DOCKER_ENGINE_PROFILE
    )
    assert evidence.docker_endpoint == (
        WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT
        if os.name == "nt"
        else "unix:///var/run/docker.sock"
    )
    assert evidence.daemon_os_type == "linux"
    assert evidence.installer_network == "docker-internal"
    assert evidence.proxy_egress_network == "dedicated-bridge"
    assert evidence.tls_mode == "end-to-end-strict"
    assert evidence.cleanup_verified is True
    assert fake.containers == {}
    assert fake.networks == {}

    raw_argvs = [call[0] for call in fake.calls]
    argvs = [_docker_command(argv) for argv in raw_argvs]
    internal = f"signalos-deps-int-{TOKEN}"[:63]
    egress = f"signalos-deps-egress-{TOKEN}"[:63]
    assert any(
        argv[:3] == ["network", "create", "--driver"] and "--internal" in argv
        for argv in argvs
    )
    proxy_create = next(
        argv
        for argv in argvs
        if argv[0] == "create" and "proxy" in _after(argv, "--name")
    )
    installer_create = next(
        argv for argv in argvs if argv[0] == "create" and "installer" in _after(argv, "--name")
    )
    assert _after(proxy_create, "--network") == egress
    assert _after(installer_create, "--network") == internal
    assert "--pull" in proxy_create and _after(proxy_create, "--pull") == "never"
    assert "--pull" in installer_create and _after(installer_create, "--pull") == "never"
    assert "--read-only" in proxy_create and "--read-only" in installer_create
    expected_log_options = ["max-size=1m", "max-file=1", "compress=false"]
    assert _all_after(proxy_create, "--log-opt") == expected_log_options
    assert _all_after(installer_create, "--log-opt") == expected_log_options
    assert _after(proxy_create, "--cap-drop") == "ALL"
    assert _after(installer_create, "--security-opt") == "no-new-privileges:true"
    assert _all_after(proxy_create, "--volume") == []
    assert len(_all_after(installer_create, "--volume")) == 1
    installer_env = dict(item.split("=", 1) for item in _all_after(installer_create, "--env"))
    assert installer_env["HTTPS_PROXY"] == "http://signalos-registry-proxy:3128"
    assert installer_env["NPM_CONFIG_STRICT_SSL"] == "true"
    assert installer_env["NODE_TLS_REJECT_UNAUTHORIZED"] == "1"
    assert installer_env["NODE_EXTRA_CA_CERTS"] == ""
    assert installer_env["NPM_CONFIG_USERCONFIG"] == "/tmp/signalos-npm-userconfig"
    assert installer_env["NPM_CONFIG_GLOBALCONFIG"] == "/tmp/signalos-npm-globalconfig"
    assert posixpath.normpath(installer_env["NPM_CONFIG_USERCONFIG"]) != posixpath.normpath(
        installer_env["NPM_CONFIG_GLOBALCONFIG"]
    )

    for _argv, kwargs in fake.calls:
        assert kwargs["shell"] is False
        assert "provider-secret" not in kwargs["env"].values()
        assert "attestation-secret" not in kwargs["env"].values()
        assert "DOCKER_HOST" not in kwargs["env"]
    flattened = "\n".join("\0".join(argv) for argv in raw_argvs)
    assert "provider-secret" not in flattened
    assert "attestation-secret" not in flattened


def test_current_context_switch_cannot_redirect_lifecycle_or_cleanup(
    tmp_path: Path,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    approved_endpoint = (
        "npipe:////./pipe/dockerDesktopLinuxEngine"
        if os.name == "nt"
        else "unix:///var/run/docker.sock"
    )
    attacker_endpoint = "tcp://attacker.invalid:2376"
    fake = _FakeDocker(
        policy.image,
        endpoint=approved_endpoint,
        switch_endpoint_after_context=attacker_endpoint,
    )
    runner, staging = _runner(tmp_path, fake)

    _exit_code, _output, evidence = runner.run(
        TRUSTED_INSTALL_SHELL_COMMAND,
        staging,
        300,
        trusted_install_environment(),
    )

    assert evidence.docker_endpoint == approved_endpoint
    assert evidence.host_trust_profile == (
        TRUSTED_LOCAL_DOCKER_DESKTOP_PROFILE
        if os.name == "nt"
        else TRUSTED_LOCAL_DOCKER_ENGINE_PROFILE
    )
    assert evidence.daemon_os_type == "linux"
    raw_argvs = [call[0] for call in fake.calls]
    assert raw_argvs[0] == [
        "docker",
        "context",
        "inspect",
        "--format",
        "{{json .Endpoints.docker.Host}}",
    ]
    assert all(
        argv[1:3] == ["--host", approved_endpoint] for argv in raw_argvs[1:]
    )
    assert all(attacker_endpoint not in argv for argv in raw_argvs)
    commands = [_docker_command(argv) for argv in raw_argvs[1:]]
    assert any(command[:2] == ["rm", "-f"] for command in commands)
    assert any(command[:2] == ["network", "rm"] for command in commands)


@pytest.mark.parametrize(
    "command,environment",
    [
        ("npm ci", trusted_install_environment()),
        (TRUSTED_INSTALL_SHELL_COMMAND, {**trusted_install_environment(), "EXTRA": "1"}),
        (TRUSTED_INSTALL_SHELL_COMMAND, {"CI": "1"}),
    ],
)
def test_contract_rejects_arbitrary_command_or_environment_before_docker(
    tmp_path: Path, command: str, environment: dict[str, str]
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image)
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(DependencyProxyPolicyError, match="fixed broker"):
        runner.run(command, staging, 300, environment)

    assert fake.calls == []


def test_remote_docker_context_is_rejected_before_resource_creation(tmp_path: Path) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, endpoint="tcp://remote.example:2376")
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(DependencyProxyPolicyError, match="Docker endpoint"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND, staging, 300, trusted_install_environment()
        )

    assert not any(_docker_command(call[0])[:2] == ["network", "create"] for call in fake.calls)


def test_cached_image_must_report_the_exact_policy_repo_digest(tmp_path: Path) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, repo_digests=["example.invalid/node@sha256:" + "f" * 64])
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(Exception, match="does not resolve"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND, staging, 300, trusted_install_environment()
        )

    assert not any(_docker_command(call[0])[:2] == ["network", "create"] for call in fake.calls)


def test_container_top_level_image_must_match_observed_config_id(tmp_path: Path) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, container_image_id="sha256:" + "f" * 64)
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(DependencyProxyPolicyError, match="hardened topology"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND, staging, 300, trusted_install_environment()
        )

    assert fake.containers == {}
    assert fake.networks == {}


def test_unexpected_running_network_member_is_rejected_and_cleaned(
    tmp_path: Path,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(
        policy.image,
        unexpected_running_network_member="untrusted-running-container",
    )
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(DependencyProxyPolicyError, match="network topology"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND,
            staging,
            300,
            trusted_install_environment(),
        )

    assert fake.containers == {}
    assert fake.networks == {}


@pytest.mark.parametrize("drift", ["missing", "extra"])
def test_declared_container_network_missing_or_extra_is_rejected_and_cleaned(
    tmp_path: Path,
    drift: str,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, declared_network_drift=drift)
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(DependencyProxyPolicyError, match="hardened topology"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND,
            staging,
            300,
            trusted_install_environment(),
        )

    assert fake.containers == {}
    assert fake.networks == {}


def test_unc_path_and_unreviewed_staging_files_are_rejected_before_docker(
    tmp_path: Path,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image)
    runner = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=fake,
        environ={"PATH": "C:\\Windows\\System32"},
        uuid_factory=lambda: FIXED_UUID,
        host_os_name="nt",
    )
    with pytest.raises(DependencyProxyPolicyError, match="UNC"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND,
            r"\\server\share\staging",
            300,
            trusted_install_environment(),
        )

    local_runner = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=fake,
        environ={"PATH": os.environ.get("PATH", "")},
        uuid_factory=lambda: FIXED_UUID,
        host_os_name=os.name,
    )
    staging = _staging(tmp_path)
    (staging / ".npmrc").write_text("strict-ssl=false\n", encoding="utf-8")
    with pytest.raises(DependencyProxyPolicyError, match="only the reviewed"):
        local_runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND,
            staging,
            300,
            trusted_install_environment(),
        )

    assert fake.calls == []


def test_relative_dot_and_dotdot_paths_are_rejected_lexically(tmp_path: Path) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image)
    runner, staging = _runner(tmp_path, fake)
    separator = "\\" if os.name == "nt" else "/"
    bad_paths = (
        "relative/staging",
        f"{staging.parent}{separator}.{separator}{staging.name}",
        f"{staging}{separator}..{separator}{staging.name}",
    )

    for value in bad_paths:
        with pytest.raises(DependencyProxyPolicyError):
            runner.run(
                TRUSTED_INSTALL_SHELL_COMMAND,
                value,
                300,
                trusted_install_environment(),
            )

    assert fake.calls == []


def test_symlinked_original_parent_is_rejected_before_resolve(tmp_path: Path) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image)
    runner, _staging_path = _runner(tmp_path / "normal", fake)
    real_parent = tmp_path / "real"
    staged = _staging(real_parent)
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(DependencyProxyPolicyError, match="symlink|reparse"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND,
            linked_parent / staged.name,
            300,
            trusted_install_environment(),
        )

    assert fake.calls == []


@pytest.mark.parametrize("mutation_phase", [("network", "create"), ("wait",)])
def test_input_mutation_races_are_detected_and_everything_is_cleaned(
    tmp_path: Path, mutation_phase: tuple[str, ...]
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, mutate_contains=mutation_phase)
    runner, staging = _runner(tmp_path, fake)
    fake.mutate = lambda: (staging / "package.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(DependencyProxyPolicyError, match="changed"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND,
            staging,
            300,
            trusted_install_environment(),
        )

    assert fake.containers == {}
    assert fake.networks == {}


@pytest.mark.parametrize(
    "endpoint",
    [
        "npipe:////./pipe/docker_engine",
        "npipe:////./pipe/untrusted",
        "npipe:////./pipe/dockerdesktoplinuxengine",
        "//./pipe/dockerDesktopLinuxEngine",
        r"\\.\pipe\dockerDesktopLinuxEngine",
        "tcp://127.0.0.1:2375",
        WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT + " ",
    ],
)
def test_windows_production_rejects_every_noncanonical_docker_endpoint(
    endpoint: str,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image)
    windows = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=fake,
        environ={},
        host_os_name="nt",
    )

    with pytest.raises(DependencyProxyPolicyError, match="canonical Docker Desktop Linux"):
        windows._validate_local_endpoint(endpoint)


def test_windows_production_preflight_accepts_only_exact_linux_pipe_without_injection(
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(
        policy.image,
        endpoint=WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT,
    )
    runner = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=fake,
        environ={},
        monotonic=lambda: 0.0,
        host_os_name="nt",
    )

    evidence = runner._preflight(300.0)

    assert evidence.host_trust_profile == TRUSTED_LOCAL_DOCKER_DESKTOP_PROFILE
    assert evidence.docker_endpoint == WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT
    assert evidence.daemon_os_type == "linux"
    assert evidence.runtime_image_id == RUNTIME_IMAGE_ID
    assert fake.calls[0][0][1:] == [
        "context",
        "inspect",
        "--format",
        "{{json .Endpoints.docker.Host}}",
    ]
    assert all(
        call[0][1:3] == ["--host", WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT]
        for call in fake.calls[1:]
    )


def test_preflight_derives_daemon_os_from_bound_daemon_and_rejects_windows() -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(
        policy.image,
        endpoint=WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT,
        daemon_os_type="windows",
    )
    runner = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=fake,
        environ={},
        monotonic=lambda: 0.0,
        host_os_name="nt",
    )

    with pytest.raises(
        DependencyProxyInfrastructureError,
        match="not running Linux containers",
    ):
        runner._preflight(300.0)

    assert [_docker_command(call[0])[0] for call in fake.calls] == ["context", "info"]


def test_unix_endpoint_allowlist_rejects_arbitrary_and_unsafe_socket() -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image)

    unix = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=fake,
        environ={},
        host_os_name="posix",
        endpoint_lstat=lambda _path: _SafeSocketStat(),
        endpoint_resolve=lambda path: path,
    )
    with pytest.raises(DependencyProxyPolicyError, match="canonical local socket"):
        unix._validate_local_endpoint("unix:///tmp/docker.sock")

    class UnsafeSocketStat(_SafeSocketStat):
        st_mode = stat.S_IFSOCK | 0o666

    unsafe = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=fake,
        environ={},
        host_os_name="posix",
        endpoint_lstat=lambda _path: UnsafeSocketStat(),
        endpoint_resolve=lambda path: path,
    )
    with pytest.raises(DependencyProxyPolicyError, match="permissions are unsafe"):
        unsafe._validate_local_endpoint("unix:///run/docker.sock")


@pytest.mark.parametrize(
    "endpoint",
    ["unix:///var/run/docker.sock", "unix:///run/docker.sock"],
)
def test_unix_exact_local_endpoints_emit_engine_trust_profile(endpoint: str) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    runner = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=lambda *_args, **_kwargs: None,
        environ={},
        host_os_name="posix",
        endpoint_lstat=lambda _path: _SafeSocketStat(),
        endpoint_resolve=lambda path: path,
    )

    evidence = runner._validate_local_endpoint(endpoint)

    assert evidence.host_trust_profile == TRUSTED_LOCAL_DOCKER_ENGINE_PROFILE
    assert evidence.docker_endpoint == endpoint


@pytest.mark.parametrize(
    "failure",
    [
        ("network", "create"),
        ("create", "--name"),
        ("network", "connect"),
        ("start",),
        ("wait",),
        ("logs",),
    ],
)
def test_every_lifecycle_failure_attempts_full_cleanup(
    tmp_path: Path, failure: tuple[str, ...]
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, fail_contains=failure)
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(Exception):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND, staging, 300, trusted_install_environment()
        )

    argvs = [_docker_command(call[0]) for call in fake.calls]
    assert sum(argv[:2] == ["rm", "-f"] for argv in argvs) == 2
    assert sum(argv[:2] == ["network", "rm"] for argv in argvs) == 2
    assert fake.containers == {}
    assert fake.networks == {}


def test_timeout_after_daemon_side_create_still_cleans_known_names(tmp_path: Path) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, timeout_contains=("create", "--name"))
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(DependencyProxyTimeoutError):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND, staging, 300, trusted_install_environment()
        )

    argvs = [_docker_command(call[0]) for call in fake.calls]
    assert sum(argv[:2] == ["rm", "-f"] for argv in argvs) == 2
    assert sum(argv[:2] == ["network", "rm"] for argv in argvs) == 2


def test_cleanup_readback_residue_invalidates_otherwise_successful_run(tmp_path: Path) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, leave_installer=True)
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(DependencyProxyCleanupError, match="still exists"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND, staging, 300, trusted_install_environment()
        )


@pytest.mark.parametrize(
    "wording",
    [
        "Error response from daemon: network {name} not found\n",
        "Error: No such network: {name}\n",
        "Error: No such object: {name}\n",
    ],
)
def test_cleanup_accepts_exact_docker_network_absence_wording(wording: str) -> None:
    name = f"signalos-deps-int-{TOKEN}"
    result = _completed(
        ["docker", "network", "inspect", name],
        1,
        stderr=wording.format(name=name),
    )

    assert DockerRegistryProxyRunner._is_network_not_found(result, name) is True


@pytest.mark.parametrize(
    "error",
    [
        "Error response from daemon: permission denied",
        "network not found",
        "Error: no such network",
        "Error response from daemon: network {name} not found: permission denied",
        "Error response from daemon: network {name} not found\npermission denied",
        "Error response from daemon: network signalos-deps-int-ffffffffffffffffffffffffffffffff not found",
    ],
)
def test_cleanup_rejects_unrelated_network_errors(error: str) -> None:
    name = f"signalos-deps-int-{TOKEN}"
    result = _completed(
        ["docker", "network", "inspect", name],
        1,
        stderr=error.format(name=name),
    )

    assert DockerRegistryProxyRunner._is_network_not_found(result, name) is False


def test_cleanup_readback_remains_fail_closed_for_unrelated_network_error(
    tmp_path: Path,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(
        policy.image,
        network_absence_error="Error response from daemon: permission denied",
    )
    runner, staging = _runner(tmp_path, fake)

    with pytest.raises(DependencyProxyCleanupError, match="could not be verified"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND,
            staging,
            300,
            trusted_install_environment(),
        )

    assert fake.containers == {}
    assert fake.networks == {}


def test_cleanup_retries_transient_active_endpoints_then_proves_absence(
    tmp_path: Path,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, network_remove_active_failures=1)
    runner, staging = _runner(tmp_path, fake, sleep=lambda _seconds: None)

    _exit_code, _output, evidence = runner.run(
        TRUSTED_INSTALL_SHELL_COMMAND,
        staging,
        300,
        trusted_install_environment(),
    )

    commands = [_docker_command(call[0]) for call in fake.calls]
    assert sum(command[:2] == ["network", "rm"] for command in commands) == 3
    assert evidence.cleanup_verified is True
    assert fake.networks == {}


def test_cleanup_persistent_active_endpoints_fails_after_bounded_retries_and_readback(
    tmp_path: Path,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    fake = _FakeDocker(policy.image, network_remove_active_failures=10)
    runner, staging = _runner(tmp_path, fake, sleep=lambda _seconds: None)

    with pytest.raises(DependencyProxyCleanupError, match="active endpoints"):
        runner.run(
            TRUSTED_INSTALL_SHELL_COMMAND,
            staging,
            300,
            trusted_install_environment(),
        )

    commands = [_docker_command(call[0]) for call in fake.calls]
    assert sum(command[:2] == ["network", "rm"] for command in commands) == 6
    for name in (f"signalos-deps-int-{TOKEN}", f"signalos-deps-egress-{TOKEN}"):
        relevant = [
            command
            for command in commands
            if command[-1:] == [name]
            and command[:2] in (["network", "rm"], ["network", "inspect"])
        ]
        assert relevant[-1] == ["network", "inspect", name]
    assert fake.networks != {}


def test_mount_attestation_requires_binds_and_allows_omitted_or_exact_tmpfs(
    tmp_path: Path,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    runner, _staging_path = _runner(tmp_path, _FakeDocker(policy.image))
    source = tmp_path.resolve()
    bind = {
        "Type": "bind",
        "Source": str(source),
        "Destination": "/workspace",
        "Mode": "rw",
        "RW": True,
    }
    tmpfs = {
        "Type": "tmpfs",
        "Source": "",
        "Destination": "/tmp",
        "Mode": "",
        "RW": True,
    }

    home_tmpfs = {**tmpfs, "Destination": "/home/signalos"}
    unknown_tmpfs = {**tmpfs, "Destination": "/unknown"}

    assert runner._mounts_match([], binds={}, tmpfs_paths={"/tmp"}) is True
    assert runner._mounts_match(
        [bind],
        binds={"/workspace": source},
        tmpfs_paths={"/tmp", "/home/signalos"},
    ) is True
    assert runner._mounts_match(
        [bind, tmpfs, home_tmpfs],
        binds={"/workspace": source},
        tmpfs_paths={"/tmp", "/home/signalos"},
    ) is True
    assert runner._mounts_match(
        [bind, bind],
        binds={"/workspace": source},
        tmpfs_paths=set(),
    ) is False
    assert runner._mounts_match(
        [tmpfs],
        binds={},
        tmpfs_paths={"/tmp", "/home/signalos"},
    ) is False
    assert runner._mounts_match(
        [tmpfs, tmpfs],
        binds={},
        tmpfs_paths={"/tmp"},
    ) is False
    assert runner._mounts_match(
        [unknown_tmpfs],
        binds={},
        tmpfs_paths={"/tmp"},
    ) is False


@pytest.mark.parametrize(
    "source",
    [
        "C:/Users/Alice/work",
        r"C:\Users\Alice\work",
        "/host_mnt/c/Users/Alice/work",
        "/run/desktop/mnt/host/c/Users/Alice/work",
    ],
)
def test_windows_bind_attestation_accepts_only_normalized_equivalent_sources(
    source: str,
) -> None:
    policy = load_dependency_policy(DEPENDENCIES / "policy.json")
    runner = DockerRegistryProxyRunner(
        policy,
        docker_cli="docker",
        runtime=lambda *_args, **_kwargs: None,
        environ={},
        host_os_name="nt",
    )
    expected = Path("C:/Users/Alice/work")
    mounts = [{
        "Type": "bind",
        "Source": source,
        "Destination": "/workspace",
        "Mode": "rw",
        "RW": True,
    }]

    assert runner._host_binds_match(
        [f"{source}:/workspace:rw"],
        binds={"/workspace": expected},
    ) is True
    assert runner._mounts_match(
        mounts,
        binds={"/workspace": expected},
        tmpfs_paths=set(),
    ) is True
    assert runner._host_binds_match(
        [f"{source}:/workspace:ro"],
        binds={"/workspace": expected},
    ) is False


def test_policy_binds_exact_proxy_script_hash_and_same_image(tmp_path: Path) -> None:
    copied = tmp_path / "dependencies"
    shutil.copytree(DEPENDENCIES, copied)
    policy_path = copied / "policy.json"
    raw = json.loads(policy_path.read_text(encoding="utf-8"))

    raw["registryProxy"]["scriptSha256"] = "0" * 64
    policy_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Exception, match="script hash"):
        load_dependency_policy(policy_path)

    raw["registryProxy"]["scriptSha256"] = hashlib.sha256(
        (copied / "proxy" / "connect-proxy.cjs").read_bytes()
    ).hexdigest()
    raw["registryProxy"]["image"] = "example.invalid/node@sha256:" + "f" * 64
    policy_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Exception, match="exact funded build image"):
        load_dependency_policy(policy_path)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("egressPolicy", "npm-registry-proxy-v1", "egress policy"),
        ("listenPort", 8080, "listen port"),
        ("allowedConnectAuthorities", ["registry.npmjs.org:444"], "CONNECT authority"),
        ("allowedConnectAuthorities", ["registry.npmjs.org.evil:443"], "CONNECT authority"),
        ("tlsMode", "intercept", "TLS mode"),
        ("installerNetwork", "bridge", "installer network"),
        ("proxyEgressNetwork", "default-bridge", "egress network"),
    ],
)
def test_policy_rejects_every_proxy_boundary_downgrade(
    tmp_path: Path, field: str, value, match: str
) -> None:
    copied = tmp_path / field
    shutil.copytree(DEPENDENCIES, copied)
    policy_path = copied / "policy.json"
    raw = json.loads(policy_path.read_text(encoding="utf-8"))
    raw["registryProxy"][field] = value
    policy_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(Exception, match=match):
        load_dependency_policy(policy_path)


def test_proxy_source_has_one_fixed_connect_authority_and_no_runtime_configuration() -> None:
    source = (DEPENDENCIES / "proxy" / "connect-proxy.cjs").read_text(encoding="utf-8")

    assert 'const HOST = "registry.npmjs.org"' in source
    assert "const PORT = 443" in source
    assert 'net.connect({host: HOST, port: PORT})' in source
    assert 'response.writeHead(405' in source
    assert 'closeWith(client, 403, "Forbidden")' in source
    assert "process.env" not in source
    assert "process.argv" not in source
