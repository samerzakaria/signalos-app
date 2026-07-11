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
#   * InProcessRunner     -- the CURRENT behavior (subprocess, shell=True, cwd
#                            jailed). The DEFAULT, so nothing changes unless a
#                            caller opts in. Byte-identical to the pre-sandbox
#                            execution.
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
    "SandboxRunner",
    "InProcessRunner",
    "ContainerRunner",
    "SandboxUnavailableError",
    "select_runner",
    "build_container_argv",
    "container_engine_available",
    "CONTAINER_WORKSPACE",
]

import logging
import os
import re
import shutil
import subprocess
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
    pull: str | None = DEFAULT_PULL,
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
    mount_src = _mount_source(workspace, engine)
    workdir = CONTAINER_WORKSPACE
    if workdir_rel:
        workdir = f"{CONTAINER_WORKSPACE}/{workdir_rel.replace(chr(92), '/').strip('/')}"
    argv: list[str] = [
        *_ENGINE_CLI[engine],
        "run",
        "--rm",                 # throwaway: no state survives the command.
    ]
    if pull:
        # Digest-pin / offline determinism: never reach the network to pull. The
        # image must be pre-present (consistent with --network none); `run` then
        # fails fast on a missing image instead of pulling over the host network.
        argv += ["--pull", pull]
    argv += [
        "--network", network,   # 'none' -> the workload has NO network.
        "--cpus", cpus,
        "--memory", memory,
        "--pids-limit", pids,   # bound child/process fan-out.
    ]
    if read_only:
        argv.append("--read-only")  # immutable rootfs; only the mounts below write.
    tmpfs_mounts = _default_tmpfs(tmpfs_size) if tmpfs is None else dict(tmpfs)
    for path, opts in tmpfs_mounts.items():
        # size-capped writable scratch/HOME, discarded with the container.
        argv += ["--tmpfs", f"{path}:{opts}" if opts else path]
    argv += [
        "-v", f"{mount_src}:{CONTAINER_WORKSPACE}",  # ONLY the workspace (rw).
        "-w", workdir,          # cwd = the mount (or a contained subdir).
    ]
    for key, val in (env or {}).items():
        argv += ["-e", f"{key}={val}"]  # overlay env INTO the container only.
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
    not the whole host environment: InProcessRunner merges it onto ``os.environ``
    (byte-identical to today); ContainerRunner forwards ONLY these keys into the
    container as ``-e`` so no host env leaks past the boundary.
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
    """DEFAULT backend: the CURRENT behavior — a subprocess with ``shell=True``
    and cwd jailed to the workspace (or a contained subdir). No OS/process
    containment boundary beyond the jailed cwd; the in-code path/allowlist policy
    (applied upstream) is the only defense. Byte-identical to the pre-sandbox
    ``_tool_run_command`` execution."""

    name = "inprocess"

    def run(
        self,
        cmd: str,
        cwd: str | os.PathLike[str],
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, CommandOutput]:
        full_env = {**os.environ, **(env or {})}
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
        pull: str | None = DEFAULT_PULL,
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
        self.pull = pull
        self._runner = runner
        self.name = f"container:{engine}"

    def build_argv(
        self, cmd: str, cwd: str | os.PathLike[str], env: Mapping[str, str] | None
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
            pull=self.pull,
            workdir_rel=_rel_subdir(self.workspace, cwd),
        )

    def run(
        self,
        cmd: str,
        cwd: str | os.PathLike[str],
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, CommandOutput]:
        argv = self.build_argv(cmd, cwd, env)
        try:
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
            )
        except subprocess.TimeoutExpired:
            return _TIMEOUT_EXIT_CODE, CommandOutput("", "", timed_out=True)
        return proc.returncode, CommandOutput(proc.stdout or "", proc.stderr or "")


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
    strict = _is_true(env.get("SIGNALOS_SANDBOX_STRICT"))
    # read-only rootfs is the default; only an explicit falsey value drops it.
    read_only = not (env.get("SIGNALOS_SANDBOX_READONLY") or "").strip().lower() \
        in {"0", "false", "no", "off"}

    def _emit(event: dict) -> None:
        if emit is not None:
            try:
                emit(event)
            except Exception:
                pass

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

    runner = ContainerRunner(
        workspace,
        engine=raw,
        image=env.get("SIGNALOS_SANDBOX_IMAGE") or DEFAULT_IMAGE,
        cpus=env.get("SIGNALOS_SANDBOX_CPUS") or DEFAULT_CPUS,
        memory=env.get("SIGNALOS_SANDBOX_MEMORY") or DEFAULT_MEMORY,
        pids=env.get("SIGNALOS_SANDBOX_PIDS") or DEFAULT_PIDS,
        network=env.get("SIGNALOS_SANDBOX_NETWORK") or DEFAULT_NETWORK,
        read_only=read_only,
        tmpfs_size=env.get("SIGNALOS_SANDBOX_TMPFS_SIZE") or DEFAULT_TMPFS_SIZE,
        pull=(env.get("SIGNALOS_SANDBOX_PULL") or DEFAULT_PULL),
    )
    _LOGGER.info("runtime containment active: %s", runner.name)
    _emit({"type": "sandbox_selected", "engine": raw, "backend": runner.name})
    return runner
