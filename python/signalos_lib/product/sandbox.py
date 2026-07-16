# signalos_lib/product/sandbox.py
# The "boundary endgame" — runtime CONTAINMENT for governed command execution.
#
# WHY THIS EXISTS
# ---------------
# agent_loop's command policy (path canonicalization + jailed cwd + the
# read-only verification-command class) is static POLICY, not runtime
# CONTAINMENT. Under `shell=True` a permitted evaluator (`node -e`, `python -c`,
# `npm test`) can still open an absolute path, reach the network, or spawn
# children -- the in-code allowlist only inspects the command STRING, it does not
# bound what the process does once running.
#
# Real agent harnesses (SWE-agent / OpenHands / Devin) close that gap by running
# the agent's shell INSIDE a container / WSL with the workspace bind-mounted,
# the network off, and no other writable path. Then ANY command is bounded by an
# OS/process boundary and the allowlist becomes a BACKSTOP, not the primary
# defense.
#
# WHAT THIS MODULE PROVIDES
# -------------------------
#   * SandboxRunner       -- the WHERE-does-a-command-run abstraction.
#   * InProcessRunner     -- subprocess, shell=True, cwd jailed. The DEFAULT;
#                            provider credentials are removed from its child
#                            environment while ordinary host/overlay values stay.
#   * ContainerRunner     -- opt-in via SIGNALOS_SANDBOX=docker|podman|wsl. Runs
#                            the command inside a throwaway container with a
#                            READ-ONLY root filesystem, ONLY the workspace
#                            bind-mounted (read-write), size-capped writable
#                            tmpfs at /tmp (and the HOME cache dir), the network
#                            disabled (--network none), a digest-pinned image it
#                            never pulls (--pull=never), and cpu/mem/pids/time
#                            caps. The runtime containment boundary.
#   * select_runner()     -- reads SIGNALOS_SANDBOX and returns the backend
#                            (default InProcess). Falls back to InProcess with a
#                            clear warning when the container runtime is absent,
#                            or raises under SIGNALOS_SANDBOX_STRICT=1.
#
# The in-code path/allowlist policy in agent_loop stays in force regardless of
# backend -- containment and policy are layered, not either/or.
#
# HONEST SCOPE: argv construction, backend selection, availability detection and
# the byte-identical InProcess path are all unit-testable offline. Actually
# EXECUTING a command inside a container needs a docker/podman/WSL host; that is
# exercised only by the skip-if-absent integration smoke.

from __future__ import annotations

__all__ = [
    "CommandOutput",
    "DependencyMount",
    "SandboxRunner",
    "InProcessRunner",
    "ContainerRunner",
    "SandboxUnavailableError",
    "select_runner",
    "build_container_argv",
    "container_engine_available",
    "validate_pinned_image",
    "CONTAINER_WORKSPACE",
    "FUNDED_PROFILE",
    "FUNDED_PLATFORM",
]

import logging
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

_LOGGER = logging.getLogger(__name__)

# The absolute path the workspace is bind-mounted to INSIDE the container.
CONTAINER_WORKSPACE = "/workspace"

# Container defaults. Chosen so a JS/TS + Python build/test can run; each is
# overridable via env (see select_runner). The time cap is the OUTER subprocess
# timeout on the `docker run` process (COMMAND_TIMEOUT_S from the caller).
DEFAULT_IMAGE = "node:20-bookworm"
DEFAULT_CPUS = "2"
DEFAULT_MEMORY = "2g"
DEFAULT_PIDS = "512"
DEFAULT_NETWORK = "none"  # the containment win: no network from the workload.

# Image pull policy. `never` is the containment floor: the workload gets NO
# network (--network none), so the image must be PRE-PRESENT -- a digest-pinned,
# already-cached image. --pull=never makes `run` fail FAST when the image is
# absent (a clear infra signal) instead of silently reaching the host network to
# pull, or hanging. For a truly pinned verifier set SIGNALOS_SANDBOX_IMAGE to a
# `name@sha256:...` reference. Docker accepts always|missing|never.
DEFAULT_PULL = "never"

# Rootfs hardening: the container root filesystem is mounted READ-ONLY
# (--read-only) so the ONLY writable surface is the explicit list below. Anything
# else -- /etc, /usr, the image's own files -- cannot be tampered with, so a
# permitted evaluator cannot persist a payload outside the workspace.
DEFAULT_READ_ONLY = True

# Size cap for every writable tmpfs. A single knob (SIGNALOS_SANDBOX_TMPFS_SIZE)
# keeps a runaway write inside the container from exhausting host memory.
DEFAULT_TMPFS_SIZE = "512m"
DEFAULT_SHM_SIZE = "1g"
FUNDED_PROFILE = "funded"
FUNDED_PLATFORM = "linux/amd64"
ARCHIVE_BOOTSTRAP_NAME = "node_modules.tar"
_DEPENDENCY_TREE_VERIFY_JS = r"""
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const root = '/dependencies';
const records = [];
function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const item = path.join(dir, name);
    const rel = path.relative(root, item).split(path.sep).join('/');
    const info = fs.lstatSync(item);
    records.push([rel, item, info]);
    if (info.isDirectory() && !info.isSymbolicLink()) walk(item);
  }
}
walk(root);
records.sort((a, b) => Buffer.compare(Buffer.from(a[0]), Buffer.from(b[0])));
const digest = crypto.createHash('sha256');
let count = 0;
let total = 0;
for (const [rel, item, info] of records) {
  count += 1;
  const mode = (info.mode & 0o7777).toString(8);
  if (info.isSymbolicLink()) {
    digest.update(`L\0${rel}\0${fs.readlinkSync(item)}\n`);
  } else if (info.isDirectory()) {
    digest.update(`D\0${rel}\0${mode}\n`);
  } else if (info.isFile()) {
    total += info.size;
    digest.update(`F\0${rel}\0${mode}\0${info.size}\0`);
    digest.update(fs.readFileSync(item));
    digest.update('\n');
  } else {
    throw new Error(`special dependency entry: ${rel}`);
  }
}
const [expectedHash, expectedCount, expectedBytes] = process.argv.slice(1);
if (digest.digest('hex') !== expectedHash ||
    count !== Number(expectedCount) || total !== Number(expectedBytes)) {
  throw new Error('extracted dependency tree evidence mismatch');
}
""".strip()
FUNDED_WRITABLE_PATHS = (
    "dist",
)
FUNDED_EPHEMERAL_CACHE_PATH = "node_modules/.vite"
_PINNED_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-fA-F]{64}$")
_SIZE_RE = re.compile(r"^([1-9][0-9]*)([kmgt])(?:i?b)?$", re.IGNORECASE)
_SIZE_MULTIPLIERS = {
    "k": 1024,
    "m": 1024 ** 2,
    "g": 1024 ** 3,
    "t": 1024 ** 4,
}
_CONTAINER_NOT_FOUND_MARKERS = (
    "no such container",
    "no such object",
    "no container with name or id",
    "container not found",
)


@dataclass(frozen=True)
class DependencyMount:
    archive_path: Path
    archive_sha256: str
    tree_sha256: str
    file_count: int
    total_bytes: int


def _default_tmpfs(size: str) -> dict[str, str]:
    """The writable tmpfs surface layered on top of the read-only rootfs.

    Determined EMPIRICALLY with a real read-only container smoke:

      /tmp   -- build/test tools (npm, vitest, tsc, mktemp) need a writable,
                world-writable sticky temp dir; mode=1777 matches a normal /tmp.
      /root  -- HOME for the image's default root user. npm/yarn/pnpm and many
                CLIs write a cache/config under $HOME (e.g. /root/.npm/_logs);
                with a read-only rootfs and no writable HOME they break even for
                purely offline work. A small writable HOME tmpfs fixes that.

    Both are size-capped. To add a writable path a build unexpectedly needs,
    extend this mapping (or pass ``tmpfs=`` to build_container_argv) -- the
    read-only default stays the safe floor.
    """
    return {
        "/tmp": f"rw,size={size},mode=1777",
        "/root": f"rw,size={size}",
    }


def validate_pinned_image(image: str) -> str:
    """Validate and normalize a digest-pinned container image reference."""
    value = str(image or "").strip()
    if not _PINNED_IMAGE_RE.fullmatch(value):
        raise ValueError(
            "funded sandbox image must be an exact name@sha256:<64 hex> reference"
        )
    return value


def _default_container_user() -> str:
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        return f"{os.getuid()}:{os.getgid()}"
    return "1000:1000"


def _validate_non_root_user(user: str) -> str:
    value = str(user or "").strip()
    match = re.fullmatch(r"([0-9]+):([0-9]+)", value)
    if match is None or int(match.group(1)) == 0 or int(match.group(2)) == 0:
        raise ValueError(
            "hardened sandbox requires an explicit non-root numeric uid:gid"
        )
    return value


def _size_bytes(value: str, label: str) -> int:
    match = _SIZE_RE.fullmatch(str(value or "").strip())
    if match is None:
        raise ValueError(f"hardened sandbox {label} must be a positive size with k/m/g/t units")
    return int(match.group(1)) * _SIZE_MULTIPLIERS[match.group(2).lower()]


def _validate_hardened_limits(
    cpus: str,
    memory: str,
    pids: str,
    tmpfs_size: str,
    shm_size: str,
) -> None:
    try:
        cpu_value = float(str(cpus).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("hardened sandbox cpus must be numeric") from exc
    if not math.isfinite(cpu_value) or not 0 < cpu_value <= 16:
        raise ValueError("hardened sandbox cpus must be greater than 0 and at most 16")
    try:
        pids_value = int(str(pids).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("hardened sandbox pids must be an integer") from exc
    if not 16 <= pids_value <= 4096:
        raise ValueError("hardened sandbox pids must be between 16 and 4096")
    memory_bytes = _size_bytes(memory, "memory")
    if not 256 * 1024 ** 2 <= memory_bytes <= 16 * 1024 ** 3:
        raise ValueError("hardened sandbox memory must be between 256m and 16g")
    tmpfs_bytes = _size_bytes(tmpfs_size, "tmpfs size")
    if not 64 * 1024 ** 2 <= tmpfs_bytes <= 4 * 1024 ** 3:
        raise ValueError("hardened sandbox tmpfs size must be between 64m and 4g")
    shm_bytes = _size_bytes(shm_size, "shared-memory size")
    if not 64 * 1024 ** 2 <= shm_bytes <= 4 * 1024 ** 3:
        raise ValueError(
            "hardened sandbox shared-memory size must be between 64m and 4g"
        )


def _validated_writable_relpaths(paths: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for raw in paths:
        value = str(raw).replace("\\", "/").strip("/")
        parts = value.split("/") if value else []
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"invalid funded sandbox writable path: {raw!r}")
        result.append("/".join(parts))
    return tuple(dict.fromkeys(result))

_TIMEOUT_EXIT_CODE = 124  # GNU `timeout` convention (unused by the caller, which
#                            short-circuits on CommandOutput.timed_out).

# Recognized container engines and the CLI used to reach each.
#   docker / podman -> the binary directly (podman is argv-compatible).
#   wsl             -> the docker CLI that lives INSIDE the default WSL distro,
#                      the common way to reach a Linux container runtime on
#                      Windows without Docker Desktop. Needs docker installed in
#                      the distro; the workspace path is translated to /mnt/<d>.
_ENGINE_CLI: dict[str, list[str]] = {
    "docker": ["docker"],
    "podman": ["podman"],
    "wsl": ["wsl.exe", "-e", "docker"],
}
# The host binary whose presence gates availability for each engine.
_ENGINE_PROBE: dict[str, tuple[str, ...]] = {
    "docker": ("docker",),
    "podman": ("podman",),
    "wsl": ("wsl", "wsl.exe"),
}

_TRUE = {"1", "true", "yes", "on"}
# Accepted values of SIGNALOS_SANDBOX that mean "no containment, current path".
_INPROCESS_ALIASES = {"", "inprocess", "in-process", "none", "off", "0", "false"}

# Model/provider credentials belong to the trusted sidecar process that creates
# ProviderAdapter.  Model-authored build/test commands are a separate child
# trust domain and must not inherit those credentials merely because the
# in-process runner starts from os.environ.
_SENSITIVE_ENV_NAME_RE = re.compile(
    r"(?:^|_)(?:API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|SECRET(?:_?KEY)?|TOKEN|"
    r"PASSWORD|PASSWD|CREDENTIALS?|AUTHORIZATION)(?:_|$)",
    re.IGNORECASE,
)
_CREDENTIAL_FILE_POINTERS = {
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AZURE_AUTH_LOCATION",
    "DOCKER_CONFIG",
    "DOTENV_CONFIG_PATH",
    "DOTENV_KEY",
    "ENV_FILE",
    "ENVFILE",
    "GH_CONFIG_DIR",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "KUBECONFIG",
    "NETRC",
    "NPM_CONFIG_USERCONFIG",
    "OPENAI_CONFIG_FILE",
    "OPENROUTER_CONFIG_FILE",
    "SIGNALOS_ENV_FILE",
}
_CREDENTIAL_FILE_SUFFIXES = (
    "_CREDENTIALS_FILE",
    "_ENV_FILE",
    "_KEY_FILE",
    "_SECRET_FILE",
    "_TOKEN_FILE",
)


def _is_sensitive_child_env_name(name: str) -> bool:
    normalized = str(name).strip().upper()
    return bool(
        normalized in _CREDENTIAL_FILE_POINTERS
        or normalized.endswith(_CREDENTIAL_FILE_SUFFIXES)
        or _SENSITIVE_ENV_NAME_RE.search(normalized)
    )


def _safe_env_overlay(
    env: Mapping[str, str] | None,
) -> dict[str, str]:
    """Keep explicitly supplied non-secret command environment only."""
    return {
        str(key): str(value)
        for key, value in (env or {}).items()
        if not _is_sensitive_child_env_name(str(key))
    }


def _child_process_env(
    overlay: Mapping[str, str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a child env without mutating or weakening the parent sidecar."""
    parent = os.environ if environ is None else environ
    child = {
        str(key): str(value)
        for key, value in parent.items()
        if not _is_sensitive_child_env_name(str(key))
    }
    child.update(_safe_env_overlay(overlay))
    return child


@dataclass(frozen=True)
class CommandOutput:
    """The `output` half of a runner's ``(exit_code, output)`` result.

    Carries the command's stdout/stderr separately (so the caller's redaction +
    output-cap policy stays byte-identical across backends) plus a ``timed_out``
    flag the caller renders into its own timeout message.
    """

    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxUnavailableError(RuntimeError):
    """A container backend was requested with SIGNALOS_SANDBOX_STRICT=1 but the
    runtime (docker/podman/wsl) is not available on this host."""


# ---------------------------------------------------------------------------
# Path / argv helpers (pure — no daemon required, so unit-testable offline)
# ---------------------------------------------------------------------------


def _to_wsl_path(host_path: str) -> str:
    """Translate a Windows host path to its WSL mount form:
    ``C:\\Users\\x\\ws`` -> ``/mnt/c/Users/x/ws``. A path that is already POSIX
    is returned unchanged."""
    p = host_path.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if m:
        return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
    return p


def _mount_source(workspace: Path, engine: str) -> str:
    """The host side of the ``-v <src>:/workspace`` bind mount for *engine*.

    docker/podman get the resolved host path with forward slashes (Docker
    Desktop accepts ``C:/Users/...``; on Linux it is already POSIX). The wsl
    engine gets the ``/mnt/<drive>/...`` translation.
    """
    host = str(Path(workspace).resolve())
    if engine == "wsl":
        return _to_wsl_path(host)
    return host.replace("\\", "/")


def _rel_subdir(workspace: Path, cwd: str | os.PathLike[str]) -> str:
    """POSIX subdir of *cwd* relative to *workspace* (``""`` for the root or an
    uncontained cwd). Used to set ``-w /workspace/<rel>`` so a peeled leading
    ``cd frontend`` runs in the right place INSIDE the single mount."""
    try:
        rel = Path(cwd).resolve().relative_to(Path(workspace).resolve())
    except (ValueError, OSError):
        return ""
    s = str(rel).replace("\\", "/")
    return "" if s in (".", "") else s


def build_container_argv(
    command: str,
    workspace: Path,
    *,
    engine: str = "docker",
    image: str = DEFAULT_IMAGE,
    env: Mapping[str, str] | None = None,
    cpus: str = DEFAULT_CPUS,
    memory: str = DEFAULT_MEMORY,
    pids: str = DEFAULT_PIDS,
    network: str = DEFAULT_NETWORK,
    workdir_rel: str = "",
    read_only: bool = DEFAULT_READ_ONLY,
    tmpfs: Mapping[str, str] | None = None,
    tmpfs_size: str = DEFAULT_TMPFS_SIZE,
    shm_size: str = DEFAULT_SHM_SIZE,
    pull: str | None = DEFAULT_PULL,
    hardened: bool = False,
    workspace_read_only: bool = False,
    writable_paths: tuple[str, ...] = (),
    container_user: str | None = None,
    container_name: str | None = None,
    cidfile: str | None = None,
    platform: str | None = None,
    dependency_volume: str | None = None,
) -> list[str]:
    """Construct the container CLI argv that runs *command* inside a throwaway
    container with a READ-ONLY root filesystem, ONLY *workspace* bind-mounted
    read-write at ``/workspace``, size-capped writable tmpfs (``/tmp`` + the HOME
    cache dir), the network disabled, and cpu/mem/pids caps.

    This is the runtime CONTAINMENT boundary: because the rootfs is immutable, the
    workload can only see the workspace mount plus a couple of size-capped tmpfs,
    cannot reach the network, and cannot spawn beyond the pids cap, ANY command is
    bounded by the container and the in-code allowlist becomes a backstop rather
    than the primary defense.

    Writable surface (everything else is read-only):
      * ``/workspace``     -- the bind mount (rw), the only path that persists.
      * ``/tmp`` + ``/root`` (or *tmpfs* keys) -- size-capped tmpfs, discarded
        with the container. See ``_default_tmpfs``.

    Pass ``read_only=False`` to drop the immutable rootfs, ``tmpfs=`` to
    override the writable tmpfs surface, or ``pull=None`` to omit ``--pull``
    (the default ``never`` keeps the run offline/digest-pinned).

    The argv is built (and assertable) WITHOUT a running daemon, so it is unit
    testable offline; executing it needs a docker/podman/WSL host.
    """
    if engine not in _ENGINE_CLI:
        raise ValueError(f"unknown container engine: {engine!r}")
    if hardened:
        platform = platform or FUNDED_PLATFORM
        if engine == "wsl":
            raise ValueError("hardened sandbox requires native docker or podman")
        image = validate_pinned_image(image)
        if network != "none":
            raise ValueError("hardened sandbox requires --network none")
        if not read_only:
            raise ValueError("hardened sandbox requires a read-only root filesystem")
        if pull != "never":
            raise ValueError("hardened sandbox requires --pull=never")
        if not workspace_read_only:
            raise ValueError("hardened sandbox requires a read-only workspace source mount")
        requested_writable = _validated_writable_relpaths(tuple(writable_paths))
        if any(
            path not in FUNDED_WRITABLE_PATHS for path in requested_writable
        ):
            raise ValueError("hardened sandbox requested an unapproved writable path")
        if platform != FUNDED_PLATFORM:
            raise ValueError(
                f"hardened sandbox platform must be exactly {FUNDED_PLATFORM}"
            )
        if tmpfs is not None:
            raise ValueError("hardened sandbox does not allow custom tmpfs mounts")
        container_user = _validate_non_root_user(
            container_user or _default_container_user()
        )
        _validate_hardened_limits(cpus, memory, pids, tmpfs_size, shm_size)
    mount_src = _mount_source(workspace, engine)
    workdir = CONTAINER_WORKSPACE
    if workdir_rel:
        workdir = f"{CONTAINER_WORKSPACE}/{workdir_rel.replace(chr(92), '/').strip('/')}"
    argv: list[str] = [
        *_ENGINE_CLI[engine],
        "run",
        "--rm",                 # throwaway: no state survives the command.
    ]
    if container_name:
        argv += ["--name", container_name]
    if cidfile:
        argv += ["--cidfile", cidfile]
    if pull:
        # Digest-pin / offline determinism: never reach the network to pull. The
        # image must be pre-present (consistent with --network none); `run` then
        # fails fast on a missing image instead of pulling over the host network.
        argv += ["--pull", pull]
    if platform:
        argv += ["--platform", platform]
    argv += [
        "--network", network,   # 'none' -> the workload has NO network.
        "--cpus", cpus,
        "--memory", memory,
        "--pids-limit", pids,   # bound child/process fan-out.
    ]
    if hardened:
        argv += [
            "--init",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--memory-swap", memory,
            "--shm-size", shm_size,
            "--user", container_user,
            "--entrypoint", "/bin/sh",
        ]
    if read_only:
        argv.append("--read-only")  # immutable rootfs; only the mounts below write.
    if hardened and tmpfs is None:
        tmpfs_mounts = {
            "/tmp": f"rw,nosuid,nodev,noexec,size={tmpfs_size},mode=1777",
            # One process/user runs per throwaway container.  Mode 1777 avoids
            # the root-owned-mode-700 EACCES trap for the mandatory non-root
            # workload while the tmpfs remains private to this container.
            "/home/signalos": f"rw,nosuid,nodev,size={tmpfs_size},mode=1777",
        }
    else:
        tmpfs_mounts = _default_tmpfs(tmpfs_size) if tmpfs is None else dict(tmpfs)
    for path, opts in tmpfs_mounts.items():
        # size-capped writable scratch/HOME, discarded with the container.
        argv += ["--tmpfs", f"{path}:{opts}" if opts else path]
    if hardened and dependency_volume:
        # The vite-cache tmpfs mounts INSIDE /workspace/node_modules, which
        # only exists when the dependency volume provides it. Pre-G4 funded
        # commands run without the volume (deps are not materialized yet) --
        # mounting this tmpfs then makes Docker mkdir node_modules on the
        # read-only rootfs and the container dies with exit 125 before the
        # command runs. No volume -> no build -> no cache needed.
        argv += [
            "--tmpfs",
            f"{CONTAINER_WORKSPACE}/{FUNDED_EPHEMERAL_CACHE_PATH}:"
            f"rw,nosuid,nodev,size={tmpfs_size},mode=1777",
        ]
    argv += [
        "-v", (
            f"{mount_src}:{CONTAINER_WORKSPACE}:ro"
            if workspace_read_only
            else f"{mount_src}:{CONTAINER_WORKSPACE}"
        ),
        "-w", workdir,          # cwd = the mount (or a contained subdir).
    ]
    for rel in _validated_writable_relpaths(tuple(writable_paths)):
        root = Path(workspace).resolve()
        candidate = (root / Path(rel)).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"funded sandbox writable path escapes workspace: {rel!r}")
        host = str(candidate).replace("\\", "/")
        argv += ["-v", f"{host}:{CONTAINER_WORKSPACE}/{rel}:rw"]
    if dependency_volume:
        if not hardened or not re.fullmatch(r"signalos-deps-[a-z0-9-]+", dependency_volume):
            raise ValueError("dependency volume name is invalid")
        argv += ["-v", f"{dependency_volume}:{CONTAINER_WORKSPACE}/node_modules:ro"]
    if hardened:
        argv += [
            "-e", "HOME=/home/signalos",
            "-e", "NPM_CONFIG_CACHE=/tmp/npm-cache",
            "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache",
        ]
    for key, val in _safe_env_overlay(env).items():
        argv += ["-e", f"{key}={val}"]  # overlay env INTO the container only.
    if hardened:
        argv += [image, "-lc", command]
    else:
        argv += [image, "sh", "-lc", command]
    return argv


# ---------------------------------------------------------------------------
# Availability detection
# ---------------------------------------------------------------------------


def container_engine_available(
    engine: str, *, which: Callable[[str], str | None] = shutil.which
) -> bool:
    """True when the host binary that reaches *engine* is on PATH. Detection
    only — it does NOT prove the daemon is up or an image is present."""
    probes = _ENGINE_PROBE.get(engine, ())
    return any(which(p) is not None for p in probes)


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


class SandboxRunner:
    """Abstraction over WHERE a governed command runs.

    A runner receives an ALREADY path-canonicalized + allowlist-checked command
    (the in-code policy is applied upstream in agent_loop and stays a backstop
    regardless of backend) and executes it, returning ``(exit_code, output)``.

    ``env`` is the environment OVERLAY for the command (e.g. ``{"CI": "1"}``),
    not the whole host environment: InProcessRunner merges non-secret overlay
    keys onto a credential-scrubbed host environment; ContainerRunner forwards
    only non-secret overlay keys into the container as ``-e``.
    """

    name: str = "sandbox"

    def run(
        self,
        cmd: str,
        cwd: str | os.PathLike[str],
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, CommandOutput]:
        raise NotImplementedError


def _kill_lingering_children() -> None:
    """Best-effort tree cleanup after a timeout on Windows: `timeout` kills the
    shell but NOT its children, and a still-open stdout handle can block past the
    deadline. Never raises."""
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/FI", "WINDOWTITLE eq signalos-agent-cmd"],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass  # best-effort tree cleanup only


class InProcessRunner(SandboxRunner):
    """DEFAULT backend: a subprocess with ``shell=True`` and jailed cwd.

    There is no OS/process containment boundary beyond the cwd, so the upstream
    path/allowlist policy remains essential.  The child environment is distinct
    from the trusted sidecar: provider credentials and credential-file pointers
    are removed while normal host variables and non-secret overlays remain.
    """

    name = "inprocess"

    def run(
        self,
        cmd: str,
        cwd: str | os.PathLike[str],
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, CommandOutput]:
        full_env = _child_process_env(env)
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                # Force UTF-8 decoding: without an explicit encoding, text=True
                # uses the OS locale (cp1252 on Windows), which raises
                # UnicodeDecodeError on the non-ASCII bytes tools like npm emit.
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=full_env,
            )
        except subprocess.TimeoutExpired:
            _kill_lingering_children()
            return _TIMEOUT_EXIT_CODE, CommandOutput("", "", timed_out=True)
        return proc.returncode, CommandOutput(proc.stdout or "", proc.stderr or "")


class ContainerRunner(SandboxRunner):
    """Opt-in backend: runs the command inside a throwaway container with ONLY
    the workspace bind-mounted read-write, the network disabled, and cpu/mem/pids
    caps (see build_container_argv). The runtime containment boundary that makes
    the allowlist a backstop.

    Construct with the workspace ROOT (the bind-mount source); each ``run`` call's
    ``cwd`` derives the ``-w`` subdir relative to the single mount.
    """

    name = "container"

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        engine: str = "docker",
        image: str = DEFAULT_IMAGE,
        cpus: str = DEFAULT_CPUS,
        memory: str = DEFAULT_MEMORY,
        pids: str = DEFAULT_PIDS,
        network: str = DEFAULT_NETWORK,
        read_only: bool = DEFAULT_READ_ONLY,
        tmpfs_size: str = DEFAULT_TMPFS_SIZE,
        shm_size: str = DEFAULT_SHM_SIZE,
        pull: str | None = DEFAULT_PULL,
        hardened: bool = False,
        workspace_read_only: bool = False,
        writable_paths: tuple[str, ...] = (),
        container_user: str | None = None,
        platform: str | None = None,
        dependency_mount: DependencyMount | None = None,
        require_funded_dependencies: bool = False,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        if engine not in _ENGINE_CLI:
            raise ValueError(f"unknown container engine: {engine!r}")
        self.workspace = Path(workspace).resolve()
        self.engine = engine
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.pids = pids
        self.network = network
        self.read_only = read_only
        self.tmpfs_size = tmpfs_size
        self.shm_size = shm_size
        self.pull = pull
        self.hardened = bool(hardened)
        self.workspace_read_only = bool(workspace_read_only)
        self.writable_paths = _validated_writable_relpaths(tuple(writable_paths))
        self.container_user = container_user or _default_container_user()
        self.platform = platform or (FUNDED_PLATFORM if self.hardened else None)
        self.dependency_mount = dependency_mount
        self.require_funded_dependencies = bool(require_funded_dependencies)
        self._runner = runner
        self.name = f"container:{engine}"
        if self.hardened:
            if self.engine == "wsl":
                raise ValueError(
                    "funded sandbox requires the native docker or podman CLI; "
                    "the WSL transport cannot safely bind a host CID file"
                )
            self.image = validate_pinned_image(self.image)
            if self.network != "none" or not self.read_only or self.pull != "never":
                raise ValueError(
                    "hardened container has an invalid network/read-only/pull policy"
                )
            if not self.workspace_read_only:
                raise ValueError(
                    "hardened container requires a read-only workspace source mount"
                )
            if any(
                path not in FUNDED_WRITABLE_PATHS for path in self.writable_paths
            ):
                raise ValueError("hardened container requested an unapproved writable path")
            if self.platform != FUNDED_PLATFORM:
                raise ValueError(
                    f"hardened sandbox platform must be exactly {FUNDED_PLATFORM}"
                )
            self.container_user = _validate_non_root_user(self.container_user)
            _validate_hardened_limits(
                self.cpus,
                self.memory,
                self.pids,
                self.tmpfs_size,
                self.shm_size,
            )
            for rel in self.writable_paths:
                candidate = self.workspace / Path(rel)
                cursor = self.workspace
                for part in Path(rel).parts:
                    cursor = cursor / part
                    if cursor.exists() and cursor.is_symlink():
                        raise ValueError(
                            f"funded sandbox writable path crosses a symlink: {rel!r}"
                        )
                candidate.mkdir(parents=True, exist_ok=True)
                resolved = candidate.resolve()
                if resolved != self.workspace and self.workspace not in resolved.parents:
                    raise ValueError(
                        f"funded sandbox writable path escapes workspace: {rel!r}"
                    )

    def build_argv(
        self,
        cmd: str,
        cwd: str | os.PathLike[str],
        env: Mapping[str, str] | None,
        *,
        container_name: str | None = None,
        cidfile: str | None = None,
        dependency_volume: str | None = None,
    ) -> list[str]:
        return build_container_argv(
            cmd,
            self.workspace,
            engine=self.engine,
            image=self.image,
            env=env,
            cpus=self.cpus,
            memory=self.memory,
            pids=self.pids,
            network=self.network,
            read_only=self.read_only,
            tmpfs_size=self.tmpfs_size,
            shm_size=self.shm_size,
            pull=self.pull,
            workdir_rel=_rel_subdir(self.workspace, cwd),
            hardened=self.hardened,
            workspace_read_only=self.workspace_read_only,
            writable_paths=self.writable_paths,
            container_user=self.container_user,
            container_name=container_name,
            cidfile=cidfile,
            platform=self.platform,
            dependency_volume=dependency_volume,
        )

    def _runtime_call(self, args: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess:
        return self._runner(
            [*_ENGINE_CLI[self.engine], *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_child_process_env(),
        )

    def _load_dependency_mount(self) -> DependencyMount | None:
        mount = self.dependency_mount
        if mount is None and self.require_funded_dependencies:
            from .dependency_broker import (
                funded_dependencies_pending,
                funded_dependency_mount_from_environment,
            )

            # The sandbox enforces BOUNDARIES (network=none, read-only,
            # non-root, pinned image), not project state. Before G4
            # materializes the attested bundle, the governance gates
            # legitimately run commands with no node_modules -- so a pristine,
            # never-materialized workspace runs the same hardened container
            # without the dependency volume (an `npm test` there fails
            # naturally as a tool error the model can read). Any PARTIAL
            # materialization state is not pending and falls through to
            # strict verification, which fails closed on tamper.
            if funded_dependencies_pending(self.workspace):
                return None
            raw = funded_dependency_mount_from_environment(self.workspace)
            if raw is None:
                raise SandboxUnavailableError(
                    "funded dependency archive is not configured"
                )
            mount = DependencyMount(
                archive_path=Path(raw["archive_path"]),
                archive_sha256=str(raw["archive_sha256"]),
                tree_sha256=str(raw["tree_sha256"]),
                file_count=int(raw["file_count"]),
                total_bytes=int(raw["total_bytes"]),
            )
        if mount is None:
            return None
        archive = Path(mount.archive_path)
        expected = self.workspace / ".signalos" / "dependencies" / "node_modules.tar"
        try:
            info = archive.lstat()
            resolved = archive.resolve(strict=True)
            expected_resolved = expected.resolve(strict=True)
        except OSError as exc:
            raise SandboxUnavailableError("funded dependency archive is unreadable") from exc
        attrs = int(getattr(info, "st_file_attributes", 0) or 0)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or attrs & 0x0400
            or resolved != expected_resolved
            or not re.fullmatch(r"[0-9a-f]{64}", mount.archive_sha256)
            or not re.fullmatch(r"[0-9a-f]{64}", mount.tree_sha256)
            or mount.file_count <= 0
            or mount.total_bytes <= 0
        ):
            raise SandboxUnavailableError("funded dependency mount evidence is invalid")
        return DependencyMount(
            archive_path=resolved,
            archive_sha256=mount.archive_sha256,
            tree_sha256=mount.tree_sha256,
            file_count=mount.file_count,
            total_bytes=mount.total_bytes,
        )

    def _prepare_dependency_volume(
        self,
        mount: DependencyMount,
        token: str,
        timeout: float,
    ) -> str:
        volume = f"signalos-deps-{uuid.uuid4().hex}"
        bootstrap = f"signalos-deps-bootstrap-{token}"[:63]
        cid_dir = Path(tempfile.gettempdir()) / "signalos-sandbox-cids"
        bootstrap_cid = cid_dir / f"bootstrap-{token}.cid"
        archive_source = str(mount.archive_path).replace("\\", "/")
        check_line = f"{mount.archive_sha256}  /tmp/{ARCHIVE_BOOTSTRAP_NAME}"
        verify_tree = " ".join((
            "node -e",
            shlex.quote(_DEPENDENCY_TREE_VERIFY_JS),
            mount.tree_sha256,
            str(mount.file_count),
            str(mount.total_bytes),
        ))
        command = (
            "set -eu; "
            f"cp /signalos/input/{ARCHIVE_BOOTSTRAP_NAME} /tmp/{ARCHIVE_BOOTSTRAP_NAME}; "
            f"printf '%s\\n' '{check_line}' | sha256sum -c - >/dev/null; "
            f"tar --extract --file=/tmp/{ARCHIVE_BOOTSTRAP_NAME} "
            "--directory=/dependencies --no-same-owner --same-permissions; "
            + verify_tree
        )
        # Narrow trusted-bootstrap exception: this fixed, model-inaccessible
        # command starts as UID 0 with every capability dropped, a read-only
        # rootfs, and no network.  Its only writable persistent mount is the
        # fresh dependency volume, so extracted files become root-owned.  The
        # scored/model command never runs here; it runs in the second container
        # as the mandatory non-root UID with this volume mounted read-only.
        argv = [
            *_ENGINE_CLI[self.engine],
            "run", "--rm", "--name", bootstrap,
            "--cidfile", str(bootstrap_cid),
            "--pull", "never", "--platform", FUNDED_PLATFORM,
            "--network", "none", "--cpus", self.cpus,
            "--memory", self.memory, "--memory-swap", self.memory,
            "--pids-limit", self.pids, "--init", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--read-only",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,noexec,size={self.tmpfs_size},mode=1777",
            "-v", f"{archive_source}:/signalos/input/{ARCHIVE_BOOTSTRAP_NAME}:ro",
            "-v", f"{volume}:/dependencies:rw",
            "--entrypoint", "/bin/sh", self.image, "-lc", command,
        ]
        failure: SandboxUnavailableError | None = None
        cleanup_errors: list[str] = []
        volume_maybe_created = False
        try:
            cid_dir.mkdir(parents=True, exist_ok=True)
            bootstrap_cid.unlink(missing_ok=True)
            volume_maybe_created = True
            created = self._runtime_call(
                ["volume", "create", "--label", "signalos.scope=funded", volume]
            )
            if created.returncode != 0:
                detail = (
                    created.stderr or created.stdout or "volume creation failed"
                ).strip()
                raise SandboxUnavailableError(
                    f"cannot create the funded dependency snapshot: {detail}"
                )
            proc = self._runner(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(timeout, 300),
                env=_child_process_env(),
            )
        except subprocess.TimeoutExpired as exc:
            failure = SandboxUnavailableError(
                "funded dependency snapshot preparation timed out"
            )
            failure.__cause__ = exc
        except SandboxUnavailableError as exc:
            failure = exc
        except (OSError, subprocess.SubprocessError) as exc:
            failure = SandboxUnavailableError(
                f"funded dependency snapshot preparation failed: {exc}"
            )
            failure.__cause__ = exc
        else:
            if proc.returncode != 0:
                detail = (
                    proc.stderr or proc.stdout or "archive verification failed"
                ).strip()
                failure = SandboxUnavailableError(
                    f"funded dependency snapshot was rejected: {detail}"
                )
        finally:
            try:
                self._cleanup_hardened_container(bootstrap, bootstrap_cid)
            except SandboxUnavailableError as exc:
                cleanup_errors.append(str(exc))
            if (failure is not None or cleanup_errors) and volume_maybe_created:
                try:
                    self._cleanup_dependency_volume(volume)
                except SandboxUnavailableError as exc:
                    cleanup_errors.append(str(exc))
        if failure is not None or cleanup_errors:
            messages = ([str(failure)] if failure is not None else []) + cleanup_errors
            raise SandboxUnavailableError("; ".join(messages)) from failure
        return volume

    def _cleanup_dependency_volume(self, volume: str) -> None:
        errors: list[str] = []
        try:
            removed = self._runtime_call(["volume", "rm", "-f", volume])
            if removed.returncode != 0 and not self._volume_not_found(removed):
                errors.append(
                    (removed.stderr or removed.stdout or "volume removal failed").strip()
                )
            inspected = self._runtime_call(["volume", "inspect", volume])
            if inspected.returncode == 0:
                errors.append("dependency volume still exists after cleanup")
            elif not self._volume_not_found(inspected):
                errors.append("dependency volume cleanup verification was inconclusive")
        except Exception as exc:
            errors.append(f"dependency volume cleanup failed: {type(exc).__name__}: {exc}")
        if errors:
            raise SandboxUnavailableError("; ".join(errors))

    def _cleanup_hardened_container(self, name: str, cidfile: Path) -> None:
        cleanup_errors: list[str] = []
        try:
            removed = self._runtime_call(["rm", "-f", name])
            if removed.returncode != 0 and not self._container_not_found(removed):
                cleanup_errors.append(
                    "forced remove failed: "
                    + ((removed.stderr or removed.stdout or "unknown runtime error").strip())
                )
        except Exception as exc:
            cleanup_errors.append(f"remove failed: {type(exc).__name__}: {exc}")
        try:
            inspected = self._runtime_call(["inspect", name])
            if inspected.returncode == 0:
                cleanup_errors.append("container still exists after forced cleanup")
            elif not self._container_not_found(inspected):
                cleanup_errors.append(
                    "cleanup verification was inconclusive: "
                    + ((inspected.stderr or inspected.stdout or "unknown runtime error").strip())
                )
        except Exception as exc:
            cleanup_errors.append(f"cleanup verification failed: {type(exc).__name__}: {exc}")
        try:
            cidfile.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"CID file cleanup failed: {exc}")
        if cleanup_errors:
            raise SandboxUnavailableError("; ".join(cleanup_errors))

    @staticmethod
    def _container_not_found(proc: subprocess.CompletedProcess) -> bool:
        output = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
        return any(marker in output for marker in _CONTAINER_NOT_FOUND_MARKERS)

    @staticmethod
    def _volume_not_found(proc: subprocess.CompletedProcess) -> bool:
        output = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
        return "no such volume" in output or "volume not found" in output

    def run(
        self,
        cmd: str,
        cwd: str | os.PathLike[str],
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, CommandOutput]:
        container_name: str | None = None
        cidfile: Path | None = None
        dependency_volume: str | None = None
        token = f"{os.getpid()}-{uuid.uuid4().hex}"
        if self.hardened:
            container_name = f"signalos-funded-{token}"[:63]
            cid_dir = Path(tempfile.gettempdir()) / "signalos-sandbox-cids"
            try:
                cid_dir.mkdir(parents=True, exist_ok=True)
                cidfile = cid_dir / f"{token}.cid"
                cidfile.unlink(missing_ok=True)
            except OSError as exc:
                raise SandboxUnavailableError(
                    "cannot create the funded container identity file"
                ) from exc
        result: tuple[int, CommandOutput]
        cleanup_errors: list[str] = []
        try:
            dependency_mount = self._load_dependency_mount() if self.hardened else None
            if dependency_mount is not None:
                dependency_volume = self._prepare_dependency_volume(
                    dependency_mount, token, timeout
                )
            argv = self.build_argv(
                cmd,
                cwd,
                env,
                container_name=container_name,
                cidfile=str(cidfile) if cidfile is not None else None,
                dependency_volume=dependency_volume,
            )
            # The overlay env goes INTO the container via -e (built into argv);
            # the OUTER docker/podman/wsl process inherits the host env (env=None)
            # only so the CLI itself resolves on PATH -- the workload never sees
            # the host environment.
            proc = self._runner(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                # The container workload already receives only the filtered
                # -e overlay.  Sanitize the outer docker/podman/WSL process too
                # so credentials cannot leak through runtime-specific behavior.
                env=_child_process_env(),
            )
        except subprocess.TimeoutExpired:
            result = (_TIMEOUT_EXIT_CODE, CommandOutput("", "", timed_out=True))
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxUnavailableError(
                f"container runtime execution failed: {type(exc).__name__}: {exc}"
            ) from exc
        else:
            # Docker/Podman reserve 125 for a runtime/daemon launch failure.
            # 126/127 are the workload shell's normal "cannot execute" / "not
            # found" results and therefore remain product/toolchain evidence.
            if self.hardened and proc.returncode == 125:
                detail = (proc.stderr or proc.stdout or "container launch failed").strip()
                raise SandboxUnavailableError(
                    f"container runtime could not start the funded workload "
                    f"(exit {proc.returncode}): {detail}"
                )
            result = (
                proc.returncode,
                CommandOutput(proc.stdout or "", proc.stderr or ""),
            )
        finally:
            if self.hardened and container_name is not None and cidfile is not None:
                try:
                    self._cleanup_hardened_container(container_name, cidfile)
                except SandboxUnavailableError as exc:
                    cleanup_errors.append(str(exc))
            if dependency_volume is not None:
                try:
                    self._cleanup_dependency_volume(dependency_volume)
                except SandboxUnavailableError as exc:
                    cleanup_errors.append(str(exc))
            if cleanup_errors:
                raise SandboxUnavailableError("; ".join(cleanup_errors))
        return result


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def _is_true(val: str | None) -> bool:
    return bool(val) and val.strip().lower() in _TRUE


def select_runner(
    workspace: str | os.PathLike[str],
    *,
    environ: Mapping[str, str] | None = None,
    emit: Callable[[dict], None] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> SandboxRunner:
    """Return the SandboxRunner selected by ``SIGNALOS_SANDBOX``.

    Unset / ``inprocess`` / ``none`` -> InProcessRunner (the DEFAULT: nothing
    changes unless a caller opts in). ``docker`` / ``podman`` / ``wsl`` ->
    ContainerRunner for that engine.

    When a container engine is requested but its runtime is not on PATH:
    fall back to InProcessRunner with a clear warning, UNLESS
    ``SIGNALOS_SANDBOX_STRICT`` is truthy, in which case raise
    SandboxUnavailableError (fail closed rather than silently drop containment).

    Container tunables read from the environment when set:
    ``SIGNALOS_SANDBOX_IMAGE``, ``SIGNALOS_SANDBOX_CPUS``,
    ``SIGNALOS_SANDBOX_MEMORY``, ``SIGNALOS_SANDBOX_PIDS``,
    ``SIGNALOS_SANDBOX_NETWORK``, ``SIGNALOS_SANDBOX_TMPFS_SIZE`` (writable tmpfs
    cap), ``SIGNALOS_SANDBOX_READONLY`` (set falsey to drop the immutable rootfs
    -- an escape hatch; the read-only default is the safe floor), and
    ``SIGNALOS_SANDBOX_PULL`` (image pull policy always|missing|never; default
    ``never`` -- digest-pinned/offline, consistent with --network none).
    """
    env = os.environ if environ is None else environ
    raw = (env.get("SIGNALOS_SANDBOX") or "").strip().lower()
    profile = (env.get("SIGNALOS_SANDBOX_PROFILE") or "").strip().lower()
    funded = profile == FUNDED_PROFILE
    if profile not in ("", FUNDED_PROFILE):
        raise SandboxUnavailableError(
            f"unknown SIGNALOS_SANDBOX_PROFILE={profile!r}"
        )
    strict = funded or _is_true(env.get("SIGNALOS_SANDBOX_STRICT"))
    # read-only rootfs is the default; only an explicit falsey value drops it.
    read_only = not (env.get("SIGNALOS_SANDBOX_READONLY") or "").strip().lower() \
        in {"0", "false", "no", "off"}

    def _emit(event: dict) -> None:
        if emit is not None:
            try:
                emit(event)
            except Exception:
                pass

    if funded and raw not in {"docker", "podman"}:
        raise SandboxUnavailableError(
            "funded sandbox requires SIGNALOS_SANDBOX=docker or podman"
        )

    if raw in _INPROCESS_ALIASES:
        return InProcessRunner()

    if raw not in _ENGINE_CLI:
        msg = (
            f"SIGNALOS_SANDBOX={raw!r} is not a recognized backend "
            f"(expected one of: inprocess, {', '.join(sorted(_ENGINE_CLI))}); "
            "using the in-process runner."
        )
        if strict:
            raise SandboxUnavailableError(msg)
        _LOGGER.warning(msg)
        _emit({"type": "sandbox_fallback", "requested": raw, "reason": "unknown_backend"})
        return InProcessRunner()

    if not container_engine_available(raw, which=which):
        msg = (
            f"SIGNALOS_SANDBOX={raw!r} requested but no {raw} runtime is on PATH; "
            "runtime containment is NOT active -- falling back to the in-process "
            "runner (the in-code allowlist remains the only boundary)."
        )
        if strict:
            raise SandboxUnavailableError(msg)
        _LOGGER.warning(msg)
        _emit({"type": "sandbox_fallback", "requested": raw, "reason": "runtime_unavailable"})
        return InProcessRunner()

    image = env.get("SIGNALOS_SANDBOX_IMAGE") or DEFAULT_IMAGE
    if funded:
        try:
            image = validate_pinned_image(image)
        except ValueError as exc:
            raise SandboxUnavailableError(str(exc)) from exc
        requested_network = (env.get("SIGNALOS_SANDBOX_NETWORK") or "none").strip().lower()
        if requested_network != "none":
            raise SandboxUnavailableError("funded sandbox network must be 'none'")
        if not read_only:
            raise SandboxUnavailableError("funded sandbox root filesystem must be read-only")
        requested_pull = (env.get("SIGNALOS_SANDBOX_PULL") or "never").strip().lower()
        if requested_pull != "never":
            raise SandboxUnavailableError("funded sandbox pull policy must be 'never'")

    try:
        runner = ContainerRunner(
            workspace,
            engine=raw,
            image=image,
            cpus=env.get("SIGNALOS_SANDBOX_CPUS") or DEFAULT_CPUS,
            memory=env.get("SIGNALOS_SANDBOX_MEMORY") or DEFAULT_MEMORY,
            pids=env.get("SIGNALOS_SANDBOX_PIDS") or DEFAULT_PIDS,
            network="none" if funded else (env.get("SIGNALOS_SANDBOX_NETWORK") or DEFAULT_NETWORK),
            read_only=read_only,
            tmpfs_size=env.get("SIGNALOS_SANDBOX_TMPFS_SIZE") or DEFAULT_TMPFS_SIZE,
            pull="never" if funded else (env.get("SIGNALOS_SANDBOX_PULL") or DEFAULT_PULL),
            hardened=funded,
            workspace_read_only=funded,
            writable_paths=FUNDED_WRITABLE_PATHS if funded else (),
            platform=FUNDED_PLATFORM if funded else None,
            require_funded_dependencies=funded,
        )
    except ValueError as exc:
        if funded:
            raise SandboxUnavailableError(str(exc)) from exc
        raise
    _LOGGER.info("runtime containment active: %s", runner.name)
    _emit({"type": "sandbox_selected", "engine": raw, "backend": runner.name})
    return runner
