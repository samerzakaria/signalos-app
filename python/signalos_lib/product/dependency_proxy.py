"""Docker-only trusted registry proxy for funded dependency provisioning.

The installer is attached only to a per-run ``--internal`` bridge.  A second,
credential-free container running the reviewed Node CONNECT proxy is dual-homed
to that bridge and a separate per-run egress bridge.  Control-plane calls never
use a shell, images are digest pinned and pre-cached, and every Docker object is
removed and read back as absent before evidence is returned to the broker.
"""

from __future__ import annotations

__all__ = [
    "DockerRegistryProxyRunner",
    "DependencyProxyError",
    "DependencyProxyPolicyError",
    "DependencyProxyInfrastructureError",
    "DependencyProxyTimeoutError",
    "DependencyProxyCleanupError",
]

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dependency_broker import (
    APPROVED_CONNECT_AUTHORITY,
    PROXY_EGRESS_NETWORK,
    PROXY_INSTALLER_NETWORK,
    PROXY_TLS_MODE,
    RUNNER_EVIDENCE_SCHEMA,
    SUPPORTED_PLATFORM,
    TRUSTED_EGRESS_POLICY,
    TRUSTED_INSTALL_SHELL_COMMAND,
    DependencyBrokerError,
    DependencyPolicy,
    TrustedDependencyRunEvidence,
    trusted_install_environment,
)
from .sandbox import (
    CommandOutput,
    _child_process_env,
    _default_container_user,
    _is_sensitive_child_env_name,
    _mount_source,
    _validate_non_root_user,
)


_WINDOWS_REPARSE_POINT = 0x0400
_CONTROL_TIMEOUT = 30.0
_CLEANUP_TIMEOUT = 10.0
_PROXY_READY_TIMEOUT = 10.0
_PROXY_ALIAS = "signalos-registry-proxy"
_PROXY_PORT = 3128
_NOT_FOUND = (
    "no such container",
    "no such object",
    "no container with name or id",
    "container not found",
)
_NETWORK_NOT_FOUND = ("network not found", "no such network", "no such object")
_CONTAINER_SECRET_RE = re.compile(
    r"(?:^|_)(?:API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|SECRET(?:_?KEY)?|TOKEN|"
    r"PASSWORD|PASSWD|CREDENTIALS?|AUTHORIZATION)(?:_|$)",
    re.IGNORECASE,
)
_READY_PROBE = (
    "const net=require('node:net');"
    "let data='';"
    "const s=net.connect({host:'127.0.0.1',port:3128},()=>{"
    "s.write('CONNECT example.invalid:443 HTTP/1.1\\r\\nHost: example.invalid:443\\r\\n\\r\\n')});"
    "s.on('data',chunk=>{data+=chunk.toString('ascii');"
    "if(data.includes('\\r\\n\\r\\n')){s.destroy();"
    "process.exit(data.startsWith('HTTP/1.1 403 ')?0:4)}});"
    "s.setTimeout(1000,()=>{s.destroy();process.exit(2)});"
    "s.once('error',()=>process.exit(3));"
)


@dataclass(frozen=True)
class _ObjectIdentity:
    device: int
    inode: int
    mode: int
    windows_attributes: int


@dataclass(frozen=True)
class _WorkspaceIdentity:
    original: Path
    resolved: Path
    root: _ObjectIdentity
    package_json: _ObjectIdentity
    package_lock: _ObjectIdentity
    package_json_sha256: str
    package_lock_sha256: str


@dataclass(frozen=True)
class _WindowsPipeInspection:
    endpoint: str
    server_pid: int
    server_executable: str
    owner_sid: str
    dacl_protected: bool
    untrusted_allow_aces: bool
    binary_signature_trusted: bool


class DependencyProxyError(DependencyBrokerError):
    """Base error with a stable machine-readable provisioning classification."""

    def __init__(
        self,
        code: str,
        phase: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.phase = phase
        self.retryable = retryable
        super().__init__(f"{code} [{phase}]: {message}")


class DependencyProxyPolicyError(DependencyProxyError):
    pass


class DependencyProxyInfrastructureError(DependencyProxyError):
    pass


class DependencyProxyTimeoutError(DependencyProxyInfrastructureError):
    pass


class DependencyProxyCleanupError(DependencyProxyInfrastructureError):
    def __init__(
        self,
        errors: list[str],
        *,
        primary_error: BaseException | None = None,
    ) -> None:
        self.cleanup_errors = tuple(errors)
        self.primary_error = primary_error
        prefix = f"primary failure: {primary_error}; " if primary_error is not None else ""
        super().__init__(
            "dependency.cleanup.failed",
            "cleanup",
            prefix + "; ".join(errors),
            retryable=True,
        )


class DockerRegistryProxyRunner:
    """One-shot Docker runner implementing the broker's fixed ``run`` contract.

    Construction is side-effect free.  ``run`` owns the complete Docker
    lifecycle and returns only after every possibly-created object has been
    removed and read back as absent.
    """

    dependency_egress_policy = TRUSTED_EGRESS_POLICY
    engine = "docker"
    platform = SUPPORTED_PLATFORM

    def __init__(
        self,
        policy: DependencyPolicy,
        *,
        docker_cli: str | None = None,
        runtime: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        environ: Mapping[str, str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        host_os_name: str | None = None,
        endpoint_lstat: Callable[[Path], os.stat_result] | None = None,
        endpoint_resolve: Callable[[Path], Path] | None = None,
        windows_pipe_inspector: Callable[[str], _WindowsPipeInspection] | None = None,
    ) -> None:
        if not isinstance(policy, DependencyPolicy):
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "construction", "validated policy required"
            )
        self.policy = policy
        self.image = policy.image
        self.proxy_image = policy.proxy_image
        self.proxy_script_sha256 = policy.proxy_script_sha256
        self._runtime = runtime
        self._monotonic = monotonic
        self._sleep = sleep
        self._uuid_factory = uuid_factory
        self._host_os_name = host_os_name or os.name
        self._endpoint_lstat = endpoint_lstat or (lambda path: path.lstat())
        self._endpoint_resolve = endpoint_resolve or (
            lambda path: path.resolve(strict=True)
        )
        self._windows_pipe_inspector = (
            windows_pipe_inspector or self._fail_closed_windows_pipe_inspection
        )
        self._bound_endpoint: str | None = None
        self._run_started = False
        self._docker_cli = docker_cli or shutil.which("docker") or ""
        parent = os.environ if environ is None else environ
        child = _child_process_env(environ=parent)
        for key in (
            "DOCKER_CONFIG",
            "DOCKER_CONTEXT",
            "DOCKER_HOST",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        ):
            child.pop(key, None)
        # Defence in depth over the shared scrubber: no sensitive entry reaches
        # even the local Docker CLI control-plane process.
        self._docker_env = {
            key: value
            for key, value in child.items()
            if not _is_sensitive_child_env_name(key)
        }
        self._container_user = _validate_non_root_user(_default_container_user())

    def run(
        self,
        command: str,
        cwd: str | os.PathLike[str],
        timeout: float,
        env: Mapping[str, str] | None,
    ) -> tuple[int, CommandOutput, TrustedDependencyRunEvidence]:
        if command != TRUSTED_INSTALL_SHELL_COMMAND:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "contract",
                "dependency installer command is not the fixed broker command",
            )
        supplied_env = {str(key): str(value) for key, value in (env or {}).items()}
        if supplied_env != trusted_install_environment():
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "contract",
                "dependency installer environment is not the fixed broker environment",
            )
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError) as exc:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "contract", "timeout must be numeric"
            ) from exc
        if not 1 <= timeout_value <= 3600:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "contract",
                "timeout must be between 1 and 3600 seconds",
            )
        workspace_identity, mount_source = self._validated_workspace(cwd)
        if not self._docker_cli:
            raise DependencyProxyInfrastructureError(
                "dependency.docker.unavailable",
                "preflight",
                "docker CLI is not on PATH",
                retryable=True,
            )
        if self._run_started:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "lifecycle",
                "Docker registry proxy runner instances are single-use",
            )
        self._run_started = True

        deadline = self._monotonic() + timeout_value
        token = self._uuid_factory().hex.lower()
        if re.fullmatch(r"[0-9a-f]{32}", token) is None:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "construction", "UUID factory returned unsafe data"
            )
        internal_network = f"signalos-deps-int-{token}"[:63]
        egress_network = f"signalos-deps-egress-{token}"[:63]
        proxy_name = f"signalos-deps-proxy-{token}"[:63]
        installer_name = f"signalos-deps-installer-{token}"[:63]
        labels = {
            "signalos.owner": "dependency-broker",
            "signalos.run": token,
            "signalos.scope": "funded-dependency",
        }

        primary: BaseException | None = None
        install_result: tuple[int, CommandOutput] | None = None
        lifecycle_started = False
        try:
            runtime_image_id = self._preflight(deadline)
            lifecycle_started = True
            self._create_network(internal_network, labels, internal=True, deadline=deadline)
            self._create_network(egress_network, labels, internal=False, deadline=deadline)
            self._reverify_workspace(workspace_identity, require_pristine=True)
            self._create_proxy(proxy_name, egress_network, labels, deadline)
            self._require_ok(
                self._call(
                    [
                        "network",
                        "connect",
                        "--alias",
                        _PROXY_ALIAS,
                        internal_network,
                        proxy_name,
                    ],
                    phase="proxy-network-connect",
                    deadline=deadline,
                ),
                code="dependency.proxy.start_failed",
                phase="proxy-network-connect",
            )
            self._inspect_network(
                internal_network,
                internal=True,
                labels=labels,
                expected_container_names={proxy_name},
                deadline=deadline,
            )
            self._inspect_network(
                egress_network,
                internal=False,
                labels=labels,
                expected_container_names={proxy_name},
                deadline=deadline,
            )
            self._inspect_proxy(
                proxy_name,
                internal_network,
                egress_network,
                labels,
                runtime_image_id,
                deadline,
            )
            self._reverify_workspace(workspace_identity, require_pristine=True)
            self._require_ok(
                self._call(
                    ["start", proxy_name], phase="proxy-start", deadline=deadline
                ),
                code="dependency.proxy.start_failed",
                phase="proxy-start",
            )
            self._wait_for_proxy(proxy_name, deadline)
            mount_spec = f"{mount_source}:/workspace:rw"
            expected_container_env = self._installer_environment(supplied_env)
            self._reverify_workspace(workspace_identity, require_pristine=True)
            self._create_installer(
                installer_name,
                internal_network,
                labels,
                mount_spec,
                expected_container_env,
                command,
                deadline,
            )
            self._reverify_workspace(workspace_identity, require_pristine=True)
            self._inspect_installer(
                installer_name,
                internal_network,
                labels,
                mount_spec,
                expected_container_env,
                command,
                workspace_identity,
                runtime_image_id,
                deadline,
            )
            self._inspect_network(
                internal_network,
                internal=True,
                labels=labels,
                expected_container_names={proxy_name, installer_name},
                deadline=deadline,
            )
            self._inspect_network(
                egress_network,
                internal=False,
                labels=labels,
                expected_container_names={proxy_name},
                deadline=deadline,
            )
            self._reverify_workspace(workspace_identity, require_pristine=True)
            self._require_ok(
                self._call(
                    ["start", installer_name],
                    phase="installer-start",
                    deadline=deadline,
                ),
                code="dependency.installer.mount_failed",
                phase="installer-start",
            )
            waited = self._call(
                ["wait", installer_name], phase="installer-wait", deadline=deadline,
                control_cap=None,
            )
            self._require_ok(
                waited,
                code="dependency.docker.daemon_unavailable",
                phase="installer-wait",
            )
            lines = [line.strip() for line in (waited.stdout or "").splitlines() if line.strip()]
            if len(lines) != 1 or re.fullmatch(r"[0-9]{1,3}", lines[0]) is None:
                raise DependencyProxyInfrastructureError(
                    "dependency.docker.daemon_unavailable",
                    "installer-wait",
                    "docker wait returned an invalid exit code",
                    retryable=True,
                )
            exit_code = int(lines[0])
            if not 0 <= exit_code <= 255:
                raise DependencyProxyInfrastructureError(
                    "dependency.docker.daemon_unavailable",
                    "installer-wait",
                    "docker wait exit code is outside the valid range",
                    retryable=True,
                )
            self._reverify_workspace(workspace_identity, require_pristine=False)
            logs = self._call(
                ["logs", installer_name], phase="installer-logs", deadline=deadline
            )
            self._require_ok(
                logs,
                code="dependency.docker.daemon_unavailable",
                phase="installer-logs",
            )
            self._reverify_workspace(workspace_identity, require_pristine=False)
            install_result = (
                exit_code,
                CommandOutput(logs.stdout or "", logs.stderr or ""),
            )
        except BaseException as exc:  # cleanup must run for every failure class
            primary = exc
        finally:
            cleanup_errors = (
                self._cleanup(
                    installer_name,
                    proxy_name,
                    internal_network,
                    egress_network,
                )
                if lifecycle_started
                else []
            )

        if cleanup_errors:
            raise DependencyProxyCleanupError(
                cleanup_errors, primary_error=primary
            ) from primary
        if primary is not None:
            raise primary
        if install_result is None:
            raise DependencyProxyInfrastructureError(
                "dependency.docker.daemon_unavailable",
                "installer",
                "dependency installer completed without a result",
                retryable=True,
            )
        evidence = TrustedDependencyRunEvidence(
            schema=RUNNER_EVIDENCE_SCHEMA,
            engine=self.engine,
            platform=self.platform,
            installer_image=self.image,
            proxy_image=self.proxy_image,
            runtime_image_id=runtime_image_id,
            proxy_script_sha256=self.proxy_script_sha256,
            runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            egress_policy=self.dependency_egress_policy,
            allowed_connect_authorities=(APPROVED_CONNECT_AUTHORITY,),
            installer_network=PROXY_INSTALLER_NETWORK,
            proxy_egress_network=PROXY_EGRESS_NETWORK,
            tls_mode=PROXY_TLS_MODE,
            pull_policy="never",
            cleanup_verified=True,
        )
        return install_result[0], install_result[1], evidence

    def _validated_workspace(
        self, cwd: str | os.PathLike[str]
    ) -> tuple[_WorkspaceIdentity, str]:
        raw = os.fspath(cwd)
        if not isinstance(raw, str) or not raw or any(
            value in raw for value in ("\x00", "\r", "\n")
        ):
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "workspace", "staging path is unsafe"
            )
        if self._host_os_name == "nt" and raw.startswith(("\\\\", "//")):
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "workspace",
                "UNC and network-share staging paths are forbidden",
            )
        path = Path(raw)
        if not path.is_absolute():
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "workspace",
                "staging path must be lexically absolute",
            )
        lexical = raw.replace("\\", "/") if self._host_os_name == "nt" else raw
        if any(part in (".", "..") for part in lexical.split("/")):
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "workspace",
                "staging path contains a dot or dot-dot component",
            )
        self._inspect_original_chain(path)
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "workspace", "staging path cannot be resolved"
            ) from exc
        # Re-check the lexical chain after resolution to narrow the path-swap
        # window; all later checkpoints repeat both checks and the root identity.
        root_info = self._inspect_original_chain(path)
        if not resolved.is_dir():
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "workspace", "staging path is not a directory"
            )
        try:
            names = {entry.name for entry in resolved.iterdir()}
        except OSError as exc:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "workspace", "staging path is unreadable"
            ) from exc
        if names != {"package.json", "package-lock.json"}:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "workspace",
                "staging must contain only the reviewed package manifest and lockfile",
            )
        for name in names:
            try:
                info = (resolved / name).lstat()
            except OSError as exc:
                raise DependencyProxyPolicyError(
                    "dependency.policy.invalid", "workspace", "reviewed input is unreadable"
                ) from exc
            attrs = int(getattr(info, "st_file_attributes", 0) or 0)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or (
                attrs & _WINDOWS_REPARSE_POINT
            ):
                raise DependencyProxyPolicyError(
                    "dependency.policy.invalid",
                    "workspace",
                    "reviewed input must be a real non-reparse file",
                )
        package_json = resolved / "package.json"
        package_lock = resolved / "package-lock.json"
        identity = _WorkspaceIdentity(
            original=path,
            resolved=resolved,
            root=self._object_identity(root_info),
            package_json=self._file_identity(package_json, "package.json"),
            package_lock=self._file_identity(package_lock, "package-lock.json"),
            package_json_sha256=self._hash_file(package_json),
            package_lock_sha256=self._hash_file(package_lock),
        )
        mount_source = _mount_source(identity.resolved, "docker")
        if self._host_os_name == "nt" and re.fullmatch(r"[A-Za-z]:/[^\r\n\x00]+", mount_source) is None:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "workspace",
                "Docker Desktop staging path must be a local drive path",
            )
        self._reverify_workspace(identity, require_pristine=True)
        return identity, mount_source

    def _inspect_original_chain(self, path: Path) -> os.stat_result:
        parts = path.parts
        if not parts or not path.anchor:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "workspace", "staging path has no absolute root"
            )
        cursor = Path(path.anchor)
        try:
            root_info = cursor.lstat()
            self._reject_link_or_reparse(root_info)
            if not stat.S_ISDIR(root_info.st_mode):
                raise DependencyProxyPolicyError(
                    "dependency.policy.invalid", "workspace", "staging root is not a directory"
                )
            current = root_info
            for index, part in enumerate(parts[1:], start=1):
                cursor = cursor / part
                current = cursor.lstat()
                self._reject_link_or_reparse(current)
                if index < len(parts) - 1 and not stat.S_ISDIR(current.st_mode):
                    raise DependencyProxyPolicyError(
                        "dependency.policy.invalid",
                        "workspace",
                        "staging parent is not a directory",
                    )
            return current
        except DependencyProxyPolicyError:
            raise
        except OSError as exc:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "workspace",
                "staging root or parent cannot be inspected",
            ) from exc

    @staticmethod
    def _reject_link_or_reparse(info: os.stat_result) -> None:
        attrs = int(getattr(info, "st_file_attributes", 0) or 0)
        if stat.S_ISLNK(info.st_mode) or attrs & _WINDOWS_REPARSE_POINT:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "workspace",
                "staging path crosses a symlink, junction, or reparse point",
            )

    @staticmethod
    def _object_identity(info: os.stat_result) -> _ObjectIdentity:
        return _ObjectIdentity(
            device=int(info.st_dev),
            inode=int(info.st_ino),
            mode=int(info.st_mode),
            windows_attributes=int(getattr(info, "st_file_attributes", 0) or 0),
        )

    def _file_identity(self, path: Path, label: str) -> _ObjectIdentity:
        try:
            info = path.lstat()
        except OSError as exc:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "workspace", f"{label} cannot be inspected"
            ) from exc
        self._reject_link_or_reparse(info)
        if not stat.S_ISREG(info.st_mode):
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid", "workspace", f"{label} is not a regular file"
            )
        return self._object_identity(info)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise DependencyProxyPolicyError(
                "dependency.policy.invalid",
                "workspace",
                "reviewed dependency input cannot be hashed",
            ) from exc
        return digest.hexdigest()

    def _reverify_workspace(
        self, identity: _WorkspaceIdentity, *, require_pristine: bool
    ) -> None:
        current_root = self._inspect_original_chain(identity.original)
        try:
            resolved = identity.original.resolve(strict=True)
        except OSError as exc:
            raise DependencyProxyPolicyError(
                "dependency.proxy.policy_probe_failed",
                "workspace-race-check",
                "staging path changed during dependency provisioning",
            ) from exc
        if (
            resolved != identity.resolved
            or self._object_identity(current_root) != identity.root
            or self._file_identity(identity.resolved / "package.json", "package.json")
            != identity.package_json
            or self._file_identity(identity.resolved / "package-lock.json", "package-lock.json")
            != identity.package_lock
            or not hmac.compare_digest(
                self._hash_file(identity.resolved / "package.json"),
                identity.package_json_sha256,
            )
            or not hmac.compare_digest(
                self._hash_file(identity.resolved / "package-lock.json"),
                identity.package_lock_sha256,
            )
        ):
            raise DependencyProxyPolicyError(
                "dependency.proxy.policy_probe_failed",
                "workspace-race-check",
                "staging identity or reviewed inputs changed during provisioning",
            )
        if require_pristine:
            try:
                names = {entry.name for entry in identity.resolved.iterdir()}
            except OSError as exc:
                raise DependencyProxyPolicyError(
                    "dependency.proxy.policy_probe_failed",
                    "workspace-race-check",
                    "staging directory cannot be enumerated",
                ) from exc
            if names != {"package.json", "package-lock.json"}:
                raise DependencyProxyPolicyError(
                    "dependency.proxy.policy_probe_failed",
                    "workspace-race-check",
                    "unreviewed staging content appeared before installation",
                )

    def _preflight(self, deadline: float) -> str:
        context = self._context_call(
            ["context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
            phase="docker-context",
            deadline=deadline,
        )
        self._require_ok(
            context,
            code="dependency.docker.context_untrusted",
            phase="docker-context",
            retryable=False,
        )
        try:
            endpoint = json.loads((context.stdout or "").strip())
        except json.JSONDecodeError as exc:
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                "docker-context",
                "Docker context endpoint is unreadable",
            ) from exc
        # This is the sole unbound Docker call. It returns the endpoint in the
        # same process that resolves currentContext; no second context lookup
        # occurs. Every later call is explicitly prefixed with --host.
        self._bound_endpoint = self._validate_local_endpoint(endpoint)

        info = self._call(
            ["info", "--format", "{{json .}}"], phase="docker-info", deadline=deadline
        )
        self._require_ok(
            info,
            code="dependency.docker.daemon_unavailable",
            phase="docker-info",
            retryable=True,
        )
        daemon = self._json_object(info.stdout, "docker-info")
        if str(daemon.get("OSType") or "").lower() != "linux":
            raise DependencyProxyInfrastructureError(
                "dependency.docker.linux_required",
                "docker-info",
                "Docker daemon is not running Linux containers",
            )

        image = self._call(
            ["image", "inspect", "--format", "{{json .}}", self.image],
            phase="image-inspect",
            deadline=deadline,
        )
        self._require_ok(
            image,
            code="dependency.image.missing",
            phase="image-inspect",
            retryable=True,
        )
        image_data = self._json_object(image.stdout, "image-inspect")
        runtime_image_id = str(image_data.get("Id") or "")
        expected_digest = self.image.rsplit("@", 1)[-1].lower()
        repo_digests = image_data.get("RepoDigests")
        digest_present = isinstance(repo_digests, list) and any(
            isinstance(value, str)
            and "@" in value
            and value.rsplit("@", 1)[-1].lower() == expected_digest
            for value in repo_digests
        )
        if (
            str(image_data.get("Os") or "").lower() != "linux"
            or str(image_data.get("Architecture") or "").lower() != "amd64"
            or re.fullmatch(r"sha256:[0-9a-f]{64}", runtime_image_id) is None
            or not digest_present
        ):
            raise DependencyProxyInfrastructureError(
                "dependency.image.missing",
                "image-inspect",
                "cached funded image does not resolve to linux/amd64 content",
            )
        return runtime_image_id

    def _validate_local_endpoint(self, endpoint: Any) -> str:
        if not isinstance(endpoint, str):
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                "docker-context",
                "Docker context endpoint is not a string",
            )
        normalized = endpoint.strip().lower()
        if self._host_os_name == "nt":
            approved = {
                "npipe:////./pipe/docker_engine": "npipe:////./pipe/docker_engine",
                "npipe:////./pipe/dockerdesktoplinuxengine": (
                    "npipe:////./pipe/dockerDesktopLinuxEngine"
                ),
            }
            canonical = approved.get(normalized)
            if canonical is None:
                raise DependencyProxyPolicyError(
                    "dependency.docker.context_untrusted",
                    "docker-context",
                    "Docker endpoint is not an official local Docker named pipe",
                )
            self._validate_windows_pipe_identity(canonical)
            return canonical
        allowed = {
            "unix:///var/run/docker.sock": Path("/var/run/docker.sock"),
            "unix:///run/docker.sock": Path("/run/docker.sock"),
        }
        socket_path = allowed.get(normalized)
        if socket_path is None:
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                "docker-context",
                "Docker endpoint is not an approved canonical local socket",
            )
        try:
            resolved = self._endpoint_resolve(socket_path)
            info = self._endpoint_lstat(socket_path)
        except OSError as exc:
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                "docker-context",
                "approved Docker socket cannot be inspected",
            ) from exc
        if resolved not in {Path("/var/run/docker.sock"), Path("/run/docker.sock")}:
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                "docker-context",
                "Docker socket resolves outside the approved runtime paths",
            )
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISSOCK(info.st_mode)
            or int(getattr(info, "st_uid", -1)) != 0
            or mode & 0o7000
            or mode & 0o007
        ):
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                "docker-context",
                "Docker socket ownership or permissions are unsafe",
            )
        return normalized

    @staticmethod
    def _fail_closed_windows_pipe_inspection(
        _endpoint: str,
    ) -> _WindowsPipeInspection:
        raise DependencyProxyPolicyError(
            "dependency.docker.context_untrusted",
            "docker-context",
            "Windows named-pipe server identity and ACL cannot be defensibly "
            "verified with the available standard-library boundary; funded "
            "dependency provisioning is disabled",
        )

    def _validate_windows_pipe_identity(self, endpoint: str) -> None:
        try:
            inspected = self._windows_pipe_inspector(endpoint)
        except DependencyProxyError:
            raise
        except Exception as exc:
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                "docker-context",
                "Windows Docker named-pipe inspection failed closed",
            ) from exc
        if not isinstance(inspected, _WindowsPipeInspection):
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                "docker-context",
                "Windows Docker named-pipe inspector returned invalid evidence",
            )
        approved_servers = {
            "com.docker.backend.exe",
            "com.docker.proxy.exe",
            "dockerd.exe",
        }
        server_name = (
            str(getattr(inspected, "server_executable", ""))
            .replace("\\", "/")
            .rsplit("/", 1)[-1]
            .lower()
        )
        if (
            inspected.endpoint.lower() != endpoint.lower()
            or inspected.server_pid <= 0
            or server_name not in approved_servers
            or inspected.owner_sid not in {"S-1-5-18", "S-1-5-32-544"}
            or inspected.dacl_protected is not True
            or inspected.untrusted_allow_aces is not False
            or inspected.binary_signature_trusted is not True
        ):
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                "docker-context",
                "Windows Docker named-pipe server or ACL identity is untrusted",
            )

    def _create_network(
        self,
        name: str,
        labels: Mapping[str, str],
        *,
        internal: bool,
        deadline: float,
    ) -> None:
        args = ["network", "create", "--driver", "bridge"]
        if internal:
            args.append("--internal")
        for key, value in labels.items():
            args.extend(("--label", f"{key}={value}"))
        args.append(name)
        self._require_ok(
            self._call(args, phase="network-create", deadline=deadline),
            code="dependency.network.create_failed",
            phase="network-create",
            retryable=True,
        )

    def _create_proxy(
        self,
        name: str,
        egress_network: str,
        labels: Mapping[str, str],
        deadline: float,
    ) -> None:
        args = ["create", "--name", name]
        for key, value in labels.items():
            args.extend(("--label", f"{key}={value}"))
        args.extend(
            (
                "--pull", "never",
                "--platform", self.platform,
                "--network", egress_network,
                "--restart", "no",
                "--read-only",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--user", "1000:1000",
                "--cpus", "0.5",
                "--memory", "128m",
                "--memory-swap", "128m",
                "--pids-limit", "64",
                "--init",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
                "--no-healthcheck",
                "--log-driver", "local",
                "--log-opt", "max-size=1m",
                "--log-opt", "max-file=1",
                "--entrypoint", "/usr/local/bin/node",
                self.proxy_image,
                "--input-type=commonjs",
                "-e",
                self.policy.proxy_script_bytes.decode("utf-8"),
            )
        )
        self._require_ok(
            self._call(args, phase="proxy-create", deadline=deadline),
            code="dependency.proxy.start_failed",
            phase="proxy-create",
            retryable=True,
        )

    @staticmethod
    def _installer_environment(base: Mapping[str, str]) -> dict[str, str]:
        proxy_url = f"http://{_PROXY_ALIAS}:{_PROXY_PORT}"
        result = dict(base)
        result.update(
            {
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
                "NPM_CONFIG_USERCONFIG": "/dev/null",
                "NPM_CONFIG_GLOBALCONFIG": "/dev/null",
            }
        )
        return result

    def _create_installer(
        self,
        name: str,
        internal_network: str,
        labels: Mapping[str, str],
        mount_spec: str,
        container_env: Mapping[str, str],
        command: str,
        deadline: float,
    ) -> None:
        args = ["create", "--name", name]
        for key, value in labels.items():
            args.extend(("--label", f"{key}={value}"))
        args.extend(
            (
                "--pull", "never",
                "--platform", self.platform,
                "--network", internal_network,
                "--restart", "no",
                "--read-only",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--user", self._container_user,
                "--cpus", "2",
                "--memory", "2g",
                "--memory-swap", "2g",
                "--pids-limit", "512",
                "--init",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=512m,mode=1777",
                "--tmpfs", "/home/signalos:rw,nosuid,nodev,noexec,size=64m,mode=1777",
                "--volume", mount_spec,
                "--workdir", "/workspace",
                "--log-driver", "local",
                "--log-opt", "max-size=1m",
                "--log-opt", "max-file=1",
            )
        )
        for key, value in container_env.items():
            args.extend(("--env", f"{key}={value}"))
        args.extend(("--entrypoint", "/bin/sh", self.image, "-lc", command))
        self._require_ok(
            self._call(args, phase="installer-create", deadline=deadline),
            code="dependency.installer.mount_failed",
            phase="installer-create",
            retryable=True,
        )

    def _inspect_network(
        self,
        name: str,
        *,
        internal: bool,
        labels: Mapping[str, str],
        expected_container_names: set[str],
        deadline: float,
    ) -> None:
        proc = self._call(
            ["network", "inspect", name], phase="network-inspect", deadline=deadline
        )
        self._require_ok(
            proc,
            code="dependency.network.create_failed",
            phase="network-inspect",
            retryable=True,
        )
        data = self._json_array_object(proc.stdout, "network-inspect")
        containers = data.get("Containers") or {}
        attached_names = {
            str(value.get("Name"))
            for value in containers.values()
            if isinstance(value, dict) and value.get("Name")
        } if isinstance(containers, dict) else set()
        if (
            data.get("Name") != name
            or data.get("Driver") != "bridge"
            or data.get("Scope") != "local"
            or data.get("Internal") is not internal
            or data.get("Attachable") is not False
            or data.get("Ingress") is not False
            or not self._labels_match(data.get("Labels"), labels)
            or attached_names != expected_container_names
        ):
            raise DependencyProxyPolicyError(
                "dependency.proxy.policy_probe_failed",
                "network-inspect",
                "Docker network topology does not match the funded policy",
            )

    def _inspect_proxy(
        self,
        name: str,
        internal_network: str,
        egress_network: str,
        labels: Mapping[str, str],
        runtime_image_id: str,
        deadline: float,
    ) -> None:
        data = self._inspect_container(name, "proxy-inspect", deadline)
        config = data.get("Config") if isinstance(data.get("Config"), dict) else {}
        host = data.get("HostConfig") if isinstance(data.get("HostConfig"), dict) else {}
        networks = (
            data.get("NetworkSettings", {}).get("Networks", {})
            if isinstance(data.get("NetworkSettings"), dict)
            else {}
        )
        internal_aliases = self._network_aliases(networks, internal_network)
        egress_aliases = self._network_aliases(networks, egress_network)
        if (
            data.get("Image") != runtime_image_id
            or config.get("Image") != self.proxy_image
            or config.get("User") != "1000:1000"
            or config.get("Entrypoint") != ["/usr/local/bin/node"]
            or config.get("Cmd") != [
                "--input-type=commonjs",
                "-e",
                self.policy.proxy_script_bytes.decode("utf-8"),
            ]
            or not self._labels_match(config.get("Labels"), labels)
            or host.get("NetworkMode") != egress_network
            or set(networks) != {internal_network, egress_network}
            or _PROXY_ALIAS not in internal_aliases
            or _PROXY_ALIAS in egress_aliases
            or not self._hardening_matches(
                host,
                memory=128 * 1024 * 1024,
                cpus=500_000_000,
                pids=64,
                expected_tmpfs={
                    "/tmp": {"rw", "nosuid", "nodev", "noexec", "size=16m", "mode=1777"}
                },
            )
            or host.get("Binds") not in (None, [])
            or not self._mounts_match(data.get("Mounts"), binds={}, tmpfs_paths={"/tmp"})
        ):
            raise DependencyProxyPolicyError(
                "dependency.proxy.policy_probe_failed",
                "proxy-inspect",
                "proxy container does not match the fixed hardened topology",
            )

    def _inspect_installer(
        self,
        name: str,
        internal_network: str,
        labels: Mapping[str, str],
        mount_spec: str,
        expected_env: Mapping[str, str],
        command: str,
        workspace: _WorkspaceIdentity,
        runtime_image_id: str,
        deadline: float,
    ) -> None:
        data = self._inspect_container(name, "installer-inspect", deadline)
        config = data.get("Config") if isinstance(data.get("Config"), dict) else {}
        host = data.get("HostConfig") if isinstance(data.get("HostConfig"), dict) else {}
        networks = (
            data.get("NetworkSettings", {}).get("Networks", {})
            if isinstance(data.get("NetworkSettings"), dict)
            else {}
        )
        aliases = self._network_aliases(networks, internal_network)
        actual_env = self._env_map(config.get("Env"))
        has_secret = any(
            _CONTAINER_SECRET_RE.search(key) and value
            for key, value in actual_env.items()
        )
        if (
            data.get("Image") != runtime_image_id
            or config.get("Image") != self.image
            or config.get("User") != self._container_user
            or config.get("Entrypoint") != ["/bin/sh"]
            or config.get("Cmd") != ["-lc", command]
            or config.get("WorkingDir") != "/workspace"
            or not self._labels_match(config.get("Labels"), labels)
            or host.get("NetworkMode") != internal_network
            or set(networks) != {internal_network}
            or _PROXY_ALIAS in aliases
            or not self._hardening_matches(
                host,
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
            or host.get("Binds") != [mount_spec]
            or not self._mounts_match(
                data.get("Mounts"),
                binds={"/workspace": workspace.resolved},
                tmpfs_paths={"/tmp", "/home/signalos"},
            )
            or any(actual_env.get(key) != value for key, value in expected_env.items())
            or has_secret
        ):
            raise DependencyProxyPolicyError(
                "dependency.proxy.policy_probe_failed",
                "installer-inspect",
                "installer container does not match the fixed internal-only policy",
            )

    def _inspect_container(self, name: str, phase: str, deadline: float) -> dict[str, Any]:
        proc = self._call(["inspect", name], phase=phase, deadline=deadline)
        self._require_ok(
            proc,
            code="dependency.proxy.policy_probe_failed",
            phase=phase,
            retryable=False,
        )
        return self._json_array_object(proc.stdout, phase)

    def _wait_for_proxy(self, name: str, deadline: float) -> None:
        ready_deadline = min(deadline, self._monotonic() + _PROXY_READY_TIMEOUT)
        last_detail = "proxy did not accept local connections"
        while self._monotonic() < ready_deadline:
            probe = self._call(
                ["exec", name, "/usr/local/bin/node", "-e", _READY_PROBE],
                phase="proxy-readiness",
                deadline=ready_deadline,
                control_cap=2.0,
            )
            if probe.returncode == 0:
                return
            last_detail = self._detail(probe)
            self._sleep(0.1)
        raise DependencyProxyInfrastructureError(
            "dependency.proxy.start_failed",
            "proxy-readiness",
            last_detail,
            retryable=True,
        )

    @staticmethod
    def _hardening_matches(
        host: Mapping[str, Any],
        *,
        memory: int,
        cpus: int,
        pids: int,
        expected_tmpfs: Mapping[str, set[str]],
    ) -> bool:
        restart = host.get("RestartPolicy") or {}
        log_config = host.get("LogConfig") or {}
        tmpfs = host.get("Tmpfs") or {}
        return bool(
            host.get("ReadonlyRootfs") is True
            and host.get("Privileged") is False
            and host.get("Init") is True
            and "ALL" in (host.get("CapDrop") or [])
            and not host.get("CapAdd")
            and "no-new-privileges:true" in (host.get("SecurityOpt") or [])
            and int(host.get("Memory") or 0) == memory
            and int(host.get("MemorySwap") or 0) == memory
            and int(host.get("NanoCpus") or 0) == cpus
            and int(host.get("PidsLimit") or 0) == pids
            and not host.get("PortBindings")
            and not host.get("Devices")
            and not host.get("ExtraHosts")
            and not host.get("Dns")
            and not host.get("Links")
            and restart.get("Name") in ("", "no")
            and log_config.get("Type") == "local"
            and (log_config.get("Config") or {}).get("max-size") == "1m"
            and (log_config.get("Config") or {}).get("max-file") == "1"
            and DockerRegistryProxyRunner._tmpfs_matches(tmpfs, expected_tmpfs)
        )

    @staticmethod
    def _tmpfs_matches(value: Any, expected: Mapping[str, set[str]]) -> bool:
        if not isinstance(value, dict) or set(value) != set(expected):
            return False
        for path, raw_options in value.items():
            if not isinstance(raw_options, str):
                return False
            options = {item.strip().lower() for item in raw_options.split(",") if item.strip()}
            if options != {item.lower() for item in expected[path]}:
                return False
        return True

    @staticmethod
    def _network_aliases(networks: Any, name: str) -> set[str]:
        if not isinstance(networks, dict) or not isinstance(networks.get(name), dict):
            return set()
        aliases = networks[name].get("Aliases") or []
        return {str(value) for value in aliases if isinstance(value, str)}

    def _mounts_match(
        self,
        value: Any,
        *,
        binds: Mapping[str, Path],
        tmpfs_paths: set[str],
    ) -> bool:
        if value is None:
            value = []
        if not isinstance(value, list):
            return False
        seen_binds: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                return False
            kind = item.get("Type")
            destination = item.get("Destination")
            if kind == "bind":
                if destination not in binds or item.get("RW") is not True:
                    return False
                if str(item.get("Mode") or "rw") not in {"rw", ""}:
                    return False
                if not self._mount_source_matches(item.get("Source"), binds[destination]):
                    return False
                seen_binds.add(destination)
            elif kind == "tmpfs":
                if destination not in tmpfs_paths or item.get("RW") is not True:
                    return False
            else:
                return False
        return seen_binds == set(binds)

    def _mount_source_matches(self, actual: Any, expected: Path) -> bool:
        if not isinstance(actual, str) or not actual:
            return False
        expected_text = str(expected).replace("\\", "/").rstrip("/")
        actual_text = actual.replace("\\", "/").rstrip("/")
        if self._host_os_name != "nt":
            return actual_text == expected_text
        expected_folded = expected_text.casefold()
        actual_folded = actual_text.casefold()
        if actual_folded == expected_folded:
            return True
        match = re.fullmatch(r"([a-zA-Z]):/(.*)", expected_text)
        if match is None:
            return False
        drive, rest = match.group(1).lower(), match.group(2).casefold()
        return actual_folded in {
            f"/host_mnt/{drive}/{rest}",
            f"/run/desktop/mnt/host/{drive}/{rest}",
        }

    @staticmethod
    def _labels_match(value: Any, expected: Mapping[str, str]) -> bool:
        return isinstance(value, dict) and all(value.get(key) == item for key, item in expected.items())

    @staticmethod
    def _env_map(value: Any) -> dict[str, str]:
        result: dict[str, str] = {}
        if not isinstance(value, list):
            return result
        for item in value:
            if isinstance(item, str) and "=" in item:
                key, entry = item.split("=", 1)
                result[key] = entry
        return result

    def _cleanup(
        self,
        installer_name: str,
        proxy_name: str,
        internal_network: str,
        egress_network: str,
    ) -> list[str]:
        errors: list[str] = []
        for name in (installer_name, proxy_name):
            removed = self._cleanup_call(["rm", "-f", name], f"remove {name}", errors)
            if removed is not None and removed.returncode != 0 and not self._is_not_found(removed):
                errors.append(f"container removal failed for {name}: {self._detail(removed)}")
            inspected = self._cleanup_call(["inspect", name], f"inspect {name}", errors)
            if inspected is not None:
                if inspected.returncode == 0:
                    errors.append(f"container still exists after cleanup: {name}")
                elif not self._is_not_found(inspected):
                    errors.append(f"container cleanup could not be verified for {name}")
        for name in (internal_network, egress_network):
            removed = self._cleanup_call(["network", "rm", name], f"remove {name}", errors)
            if removed is not None and removed.returncode != 0 and not self._is_network_not_found(removed):
                errors.append(f"network removal failed for {name}: {self._detail(removed)}")
            inspected = self._cleanup_call(
                ["network", "inspect", name], f"inspect {name}", errors
            )
            if inspected is not None:
                if inspected.returncode == 0:
                    errors.append(f"network still exists after cleanup: {name}")
                elif not self._is_network_not_found(inspected):
                    errors.append(f"network cleanup could not be verified for {name}")
        return errors

    def _cleanup_call(
        self, args: list[str], label: str, errors: list[str]
    ) -> subprocess.CompletedProcess[str] | None:
        if self._bound_endpoint is None:
            errors.append(f"{label} failed: Docker endpoint was never immutably bound")
            return None
        try:
            return self._runtime(
                [self._docker_cli, "--host", self._bound_endpoint, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_CLEANUP_TIMEOUT,
                env=dict(self._docker_env),
                shell=False,
            )
        except Exception as exc:
            errors.append(f"{label} failed: {type(exc).__name__}: {exc}")
            return None

    def _context_call(
        self,
        args: list[str],
        *,
        phase: str,
        deadline: float,
    ) -> subprocess.CompletedProcess[str]:
        if self._bound_endpoint is not None:
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                phase,
                "Docker context may be resolved only once before endpoint binding",
            )
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise DependencyProxyTimeoutError(
                "dependency.installer.timeout",
                phase,
                "trusted dependency deadline expired",
                retryable=True,
            )
        try:
            return self._runtime(
                [self._docker_cli, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(remaining, _CONTROL_TIMEOUT),
                env=dict(self._docker_env),
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DependencyProxyTimeoutError(
                "dependency.installer.timeout",
                phase,
                "Docker context resolution exceeded the funded dependency deadline",
                retryable=True,
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise DependencyProxyInfrastructureError(
                "dependency.docker.daemon_unavailable",
                phase,
                f"Docker context resolution failed: {type(exc).__name__}: {exc}",
                retryable=True,
            ) from exc

    def _call(
        self,
        args: list[str],
        *,
        phase: str,
        deadline: float,
        control_cap: float | None = _CONTROL_TIMEOUT,
    ) -> subprocess.CompletedProcess[str]:
        if self._bound_endpoint is None:
            raise DependencyProxyPolicyError(
                "dependency.docker.context_untrusted",
                phase,
                "Docker lifecycle call attempted before immutable endpoint binding",
            )
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise DependencyProxyTimeoutError(
                "dependency.installer.timeout",
                phase,
                "trusted dependency deadline expired",
                retryable=True,
            )
        call_timeout = remaining if control_cap is None else min(remaining, control_cap)
        try:
            return self._runtime(
                [self._docker_cli, "--host", self._bound_endpoint, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=call_timeout,
                env=dict(self._docker_env),
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DependencyProxyTimeoutError(
                "dependency.installer.timeout",
                phase,
                "Docker operation exceeded the funded dependency deadline",
                retryable=True,
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise DependencyProxyInfrastructureError(
                "dependency.docker.daemon_unavailable",
                phase,
                f"Docker control-plane call failed: {type(exc).__name__}: {exc}",
                retryable=True,
            ) from exc

    def _require_ok(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        code: str,
        phase: str,
        retryable: bool = True,
    ) -> None:
        if proc.returncode != 0:
            raise DependencyProxyInfrastructureError(
                code, phase, self._detail(proc), retryable=retryable
            )

    @staticmethod
    def _detail(proc: subprocess.CompletedProcess[str]) -> str:
        return (proc.stderr or proc.stdout or "Docker operation failed").strip()[-2000:]

    @staticmethod
    def _json_object(value: str | None, phase: str) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "")
        except json.JSONDecodeError as exc:
            raise DependencyProxyInfrastructureError(
                "dependency.docker.daemon_unavailable",
                phase,
                "Docker returned malformed JSON",
                retryable=True,
            ) from exc
        if not isinstance(parsed, dict):
            raise DependencyProxyInfrastructureError(
                "dependency.docker.daemon_unavailable",
                phase,
                "Docker returned an unexpected JSON value",
                retryable=True,
            )
        return parsed

    @classmethod
    def _json_array_object(cls, value: str | None, phase: str) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "")
        except json.JSONDecodeError as exc:
            raise DependencyProxyInfrastructureError(
                "dependency.docker.daemon_unavailable",
                phase,
                "Docker returned malformed JSON",
                retryable=True,
            ) from exc
        if not isinstance(parsed, list) or len(parsed) != 1 or not isinstance(parsed[0], dict):
            raise DependencyProxyInfrastructureError(
                "dependency.docker.daemon_unavailable",
                phase,
                "Docker inspect returned an unexpected object count",
                retryable=True,
            )
        return parsed[0]

    @staticmethod
    def _is_not_found(proc: subprocess.CompletedProcess[str]) -> bool:
        output = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
        return any(marker in output for marker in _NOT_FOUND)

    @staticmethod
    def _is_network_not_found(proc: subprocess.CompletedProcess[str]) -> bool:
        output = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
        return any(marker in output for marker in _NETWORK_NOT_FOUND)
