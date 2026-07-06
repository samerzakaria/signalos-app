"""sandbox.py - Containerized execution scaffolding (the "blast radius" gap).

Running LLM-generated code on the user's host is a real risk. Even with
the security-audit skill catching obvious foot-guns, `npm install` runs
arbitrary postinstall scripts and `npm run dev` mounts a webserver on
your machine. SignalOS shouldn't trust the AI not to ship malware in a
node-modules tree.

This module is the foundation for sandboxed execution. It is
intentionally **MINIMUM USEFUL**, not a finished system:

  Done:
    - Detect Docker availability (`docker --version`)
    - Read sandbox preference from .signalos/sandbox.json
    - Build the canonical `docker run` argv that mounts the workspace
      read-write and runs an arbitrary command inside the container
    - Image policy: default `node:20-alpine` for JS, `python:3.11-slim`
      for Python; overridable per-workspace via sandbox.json
    - First beachhead: tdd_runner's `run_tests_for_files` calls
      `maybe_wrap_for_sandbox` so when sandboxed mode is on, the test
      runner subprocess actually runs in a container.

  Not done in this commit (real engineering, deferred):
    - Long-lived container management (we shell-out per-command)
    - Port forwarding for `npm run dev` previews (preview path still
      runs on host)
    - Secrets passthrough (env-var injection from keychain into the
      container without leaking on the host)
    - UID/GID matching for files written inside the container
    - Image pre-pull + caching strategy
    - Windows path translation edge cases (Git Bash / WSL2 nuances)
    - Podman/colima fallback when Docker Desktop isn't running
    - Network policy (egress allow-list)

The scaffolding is here so the rest can be filled in without touching
every call site in the orchestrator.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "docker_available",
    "is_sandbox_enabled",
    "get_sandbox_config",
    "set_sandbox_config",
    "build_docker_run_argv",
    "maybe_wrap_for_sandbox",
]


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

_DEFAULT_IMAGE_JS = "node:20-alpine"
_DEFAULT_IMAGE_PY = "python:3.11-slim"
# Shell scripts (bash/sh) need bash + the standard unix toolchain. The
# default JS image (node:20-alpine) doesn't have bash, so wrapping a
# `bash` invocation against it would fail with "bash: not found".
# debian:bookworm-slim is ~75 MB and has bash + sed + awk + coreutils.
_DEFAULT_IMAGE_SH = "debian:bookworm-slim"
# Option 2: per-stack official base images, pulled on demand. Adding a language
# is one entry here -- Docker Hub already ships the image, so the platform never
# builds or installs a toolchain and is never "one language short".
#
# These are FLOATING FALLBACK tags (major line only, never a pinned minor), so
# they don't go stale as new versions ship. The EXACT version is resolved
# dynamically from what the generated app itself declares (go.mod / *.csproj /
# pom.xml / package.json / pyproject.toml) via ``resolve_stack_image`` -- the
# platform hardcodes no version; the app is the source of truth, and this map is
# only the "the app didn't say" default.
_DEFAULT_IMAGES: dict[str, str] = {
    "js": _DEFAULT_IMAGE_JS,
    "py": _DEFAULT_IMAGE_PY,
    "sh": _DEFAULT_IMAGE_SH,
    "go": "golang:1-bookworm",
    "dotnet": "mcr.microsoft.com/dotnet/sdk:latest",
    "java": "maven:3-eclipse-temurin-21",
    "rust": "rust:1-bookworm",
}

# Repositories per kind; the TAG is filled in from the app's declared version.
_IMAGE_REPO: dict[str, str] = {
    "go": "golang",
    "dotnet": "mcr.microsoft.com/dotnet/sdk",
    "java": "eclipse-temurin",
    "rust": "rust",
    "js": "node",
    "py": "python",
}


def resolve_stack_image(root: Path, kind: str) -> str:
    """Resolve the base image for *kind* — the LATEST, fetched from the registry
    at pull time, never a version the agent guessed from its training cutoff.

    We deliberately do NOT read the version out of the app's go.mod / *.csproj /
    package.json: those were written by the model from stale knowledge. Instead
    we return a FLOATING tag (major line / LTS) that the Docker registry resolves
    to the newest matching image *when it is pulled* -- so the build always uses
    the current release, and a new language version needs zero changes here. An
    explicit per-workspace ``image_<kind>`` override in sandbox.json still wins
    (that is an operator decision, not a model guess).

    (The registry IS the source of truth for "latest", exactly like discovering
    a model id from the provider API instead of hardcoding one.)
    """
    return _DEFAULT_IMAGES.get(kind, _DEFAULT_IMAGE_JS)


def _sandbox_path(root: Path) -> Path:
    return root / ".signalos" / "sandbox.json"


def get_sandbox_config(root: Path) -> dict[str, Any]:
    """Return the workspace's sandbox config or sane defaults.

    Schema:
      {
        "enabled":    bool   # global on/off for sandboxed mode
        "image_js":   str    # docker image for JS/TS commands
        "image_py":   str    # docker image for Python commands
        "extra_mounts": list[str]   # additional -v host:cont strings
      }
    """
    p = _sandbox_path(root)
    if not p.is_file():
        return {
            "enabled": False,
            "image_js": _DEFAULT_IMAGE_JS,
            "image_py": _DEFAULT_IMAGE_PY,
            "image_sh": _DEFAULT_IMAGE_SH,
            "extra_mounts": [],
        }
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "enabled": False,
            "image_js": _DEFAULT_IMAGE_JS,
            "image_py": _DEFAULT_IMAGE_PY,
            "image_sh": _DEFAULT_IMAGE_SH,
            "extra_mounts": [],
        }
    if not isinstance(data, dict):
        return get_sandbox_config(root)
    # Fill in defaults for missing keys.
    data.setdefault("enabled", False)
    data.setdefault("image_js", _DEFAULT_IMAGE_JS)
    data.setdefault("image_py", _DEFAULT_IMAGE_PY)
    data.setdefault("image_sh", _DEFAULT_IMAGE_SH)
    data.setdefault("extra_mounts", [])
    return data


def set_sandbox_config(root: Path, **patches: Any) -> dict[str, Any]:
    """Update sandbox config; only listed keys are touched."""
    current = get_sandbox_config(root)
    for k, v in patches.items():
        if k in {"enabled", "image_js", "image_py", "image_sh", "extra_mounts"}:
            current[k] = v
    p = _sandbox_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current


def is_sandbox_enabled(root: Path) -> bool:
    cfg = get_sandbox_config(root)
    return bool(cfg.get("enabled")) and docker_available()


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------

# Preference order for the container runtime. Docker first (most common when
# present), then Podman -- rootless, daemonless, LICENSE-CLEAN, and drop-in
# `docker`-CLI compatible, so it's the one we auto-install when nothing exists.
_CONTAINER_RUNTIMES = ("docker", "podman")


def _runtime_works(runtime: str) -> bool:
    """True iff *runtime* resolves AND its engine responds. An installed-but-
    stopped Docker Desktop / uninitialized podman machine does NOT count -- a
    `run` against it hangs, so we probe `info` with a short timeout."""
    if shutil.which(runtime) is None:
        return False
    try:
        proc = subprocess.run(
            [runtime, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        return proc.returncode == 0 and bool((proc.stdout or "").strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def container_runtime() -> str | None:
    """The name of the first working container runtime (``docker``|``podman``),
    or None when neither is installed+running. Runtime-agnostic: the sandbox
    shells `<runtime> run ...` and both speak the same CLI."""
    for rt in _CONTAINER_RUNTIMES:
        if _runtime_works(rt):
            return rt
    return None


def _runtime_cli() -> str:
    """The runtime CLI NAME for argv construction -- resolves by PATH presence
    only (fast, no engine probe), since callers verify the engine works via
    container_runtime() before wrapping. Falls back to 'docker'."""
    for rt in _CONTAINER_RUNTIMES:
        if shutil.which(rt) is not None:
            return rt
    return "docker"


def docker_available() -> bool:
    """Back-compat: any working container runtime is available."""
    return container_runtime() is not None


# --- Auto-install (consent-gated) -----------------------------------------
# Per-OS command to install Podman -- the license-clean, rootless runtime we
# bring up when a machine has none. NOT run silently: installing system
# software is privileged + hard to reverse, so ensure_container_runtime()
# requires explicit consent before invoking these.
def _podman_install_plan() -> list[list[str]]:
    """The ordered commands to install + start Podman for this OS. Empty when
    we don't have a supported installer path (caller surfaces a manual hint)."""
    plat = sys.platform
    if plat.startswith("win"):
        if shutil.which("winget"):
            return [
                ["winget", "install", "--id", "RedHat.Podman", "-e",
                 "--accept-source-agreements", "--accept-package-agreements"],
                ["podman", "machine", "init"],
                ["podman", "machine", "start"],
            ]
        return []
    if plat == "darwin":
        if shutil.which("brew"):
            return [
                ["brew", "install", "podman"],
                ["podman", "machine", "init"],
                ["podman", "machine", "start"],
            ]
        return []
    # linux -- host kernel runs containers natively, no VM/machine needed.
    if shutil.which("apt-get"):
        return [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "podman"]]
    if shutil.which("dnf"):
        return [["sudo", "dnf", "install", "-y", "podman"]]
    return []


def ensure_container_runtime(*, consent: bool = False, timeout_s: int = 600) -> dict[str, Any]:
    """Ensure a container runtime exists. Detect first; only install with
    explicit *consent* (installing system software is privileged/irreversible).

    Returns {runtime, installed, needs_consent, plan, error}:
      - runtime present already      -> {runtime, installed: False}
      - none, consent=False          -> {runtime: None, needs_consent: True, plan}
      - none, consent=True, ok        -> {runtime, installed: True}
      - none, no installer path/fail -> {runtime: None, error}
    """
    existing = container_runtime()
    if existing:
        return {"runtime": existing, "installed": False}
    plan = _podman_install_plan()
    if not consent:
        return {"runtime": None, "installed": False, "needs_consent": True, "plan": plan}
    if not plan:
        return {"runtime": None, "installed": False,
                "error": "no supported auto-installer for this OS; install Podman or Docker manually"}
    for step in plan:
        try:
            proc = subprocess.run(step, capture_output=True, text=True, timeout=timeout_s, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"runtime": None, "installed": False, "error": f"{' '.join(step)}: {exc}"}
        if proc.returncode != 0:
            return {"runtime": None, "installed": False,
                    "error": f"{' '.join(step)} failed: {(proc.stderr or proc.stdout or '')[:300]}"}
    rt = container_runtime()
    if rt:
        return {"runtime": rt, "installed": True}
    return {"runtime": None, "installed": False, "error": "runtime not usable after install"}


# ---------------------------------------------------------------------------
# Command wrapping
# ---------------------------------------------------------------------------

def _classify_command(cmd: list[str]) -> str:
    """Return 'js' / 'py' / 'sh' for image selection.

    - py:  python / python3 / pytest / pip / uv
    - sh:  bash / sh / dash / zsh -- the harness's hook subprocess paths
           shell out to bundle bash scripts; they need bash + standard
           unix tools, which node:20-alpine doesn't have.
    - js:  everything else (npm / npx / node / yarn / etc.)

    Each maps to an image_{kind} entry in sandbox.json; users can
    override any of them per-workspace.
    """
    if not cmd:
        return "js"
    name = os.path.basename(cmd[0]).lower()
    if name in {"python", "python3", "python.exe", "pytest", "pip", "uv"}:
        return "py"
    if name.startswith("python"):
        return "py"
    if name in {"bash", "sh", "dash", "zsh", "bash.exe"}:
        return "sh"
    # Option 2: compiled-stack toolchains -> their official base image.
    if name in {"go", "go.exe"}:
        return "go"
    if name in {"dotnet", "dotnet.exe"}:
        return "dotnet"
    if name in {"mvn", "mvn.cmd", "gradle", "gradlew", "java", "javac"}:
        return "java"
    if name in {"cargo", "cargo.exe", "rustc"}:
        return "rust"
    return "js"


def build_docker_run_argv(
    root: Path,
    cmd: list[str],
    *,
    image: str | None = None,
    extra_mounts: list[str] | None = None,
    ports: list[str] | None = None,
    host_network: bool = False,
) -> list[str]:
    """Construct the `docker run` argv that runs *cmd* inside a container.

    Mount + isolation strategy:
      - Workspace -> /workspace (rw)
      - WORKDIR set to /workspace
      - --rm so the container goes away when the command exits
      - Default bridge networking (own namespace, own hostname, own
        loopback). The whole point of the sandbox is blast-radius
        reduction; `--network host` defeats that by sharing the host's
        network namespace.
      - When a caller needs a port reachable from host (the preview
        path's `npm run dev`), it passes ports=["5173:5173"] and we
        emit explicit `-p host:container` mappings. The container still
        keeps its own namespace; only the named ports are bridged.
      - `host_network=True` opts into `--network host`. Use only for
        cases where the host-side caller (e.g. Playwright running on
        host) needs to reach the containerized server at 127.0.0.1
        without dealing with port-mapping discovery. This trades some
        of the blast-radius reduction (shared network namespace) for
        operational simplicity in the dev-server case.

    No long-lived container management here -- this is per-command. A
    future commit can swap this for a `docker exec` against a kept-warm
    container to save the per-invocation cold-start.
    """
    cfg = get_sandbox_config(root)
    if image is None:
        kind = _classify_command(cmd)
        # Prefer an explicit per-workspace override (image_<kind>); otherwise
        # resolve the image DYNAMICALLY from the app's declared version -- never
        # a platform-hardcoded pin (Option 2).
        image = cfg.get(f"image_{kind}") or resolve_stack_image(root, kind)
    # Docker's -v parser wants forward slashes even on Windows: a native
    # `C:\path` mangles the source/dest split, so the container mounts nothing
    # and every build "fails" with empty output. Forward slashes work on Docker
    # Desktop (Windows) and are a no-op on Linux/CI.
    workspace_abs = str(root.resolve()).replace("\\", "/")
    argv = [
        _runtime_cli(), "run", "--rm", "-i",
        # -i (--interactive): forward stdin into the container. Required
        # for any wrapped call that passes input= to subprocess.run
        # (e.g. the harness's redact.py filter pipes text through stdin).
        # Harmless when there's no input.
        "-v", f"{workspace_abs}:/workspace",
        "-w", "/workspace",
    ]
    if host_network:
        # OPT-IN: container shares the host's network namespace. Used
        # by preview / e2e dev-server wraps where the user's browser
        # (or Playwright running on host) needs to reach the dev
        # server at 127.0.0.1:<port>. Defeats network isolation; the
        # container can reach anywhere the host can. We still keep
        # process + filesystem isolation (workspace mount only). This
        # is a deliberate tradeoff for the "run a dev server" use case
        # where pre-declaring every possible port via -p isn't viable.
        argv.extend(["--network", "host"])
    for p in (ports or []):
        argv.extend(["-p", p])
    for m in (extra_mounts or []) + list(cfg.get("extra_mounts") or []):
        argv.extend(["-v", m])
    argv.append(image)  # type: ignore[arg-type]
    argv.extend(cmd)
    return argv


def maybe_wrap_for_sandbox(
    root: Path,
    cmd: list[str],
    *,
    image: str | None = None,
    ports: list[str] | None = None,
    host_network: bool = False,
) -> tuple[list[str], bool]:
    """Wrap *cmd* in `docker run` if sandboxed mode is enabled.

    Returns (final_cmd, was_wrapped). Callers stay the same regardless
    of sandbox state -- just use the returned list. When was_wrapped
    is True, the orchestrator can surface a "(sandboxed)" hint in
    progress events.

    `ports`: optional list of "host:container" port mappings, threaded
    through to `build_docker_run_argv`. Use it when the wrapped command
    needs a port reachable from host (e.g. the preview wrap passing
    ["5173:5173"] for `npm run dev`). Default isolation otherwise.

    `host_network`: opt-in for the e2e / preview dev-server case where
    the container needs to share the host's network namespace so
    Playwright / the user's browser can reach the dev server at
    127.0.0.1:<arbitrary-port>. Defeats network isolation; keeps
    process + filesystem isolation. See build_docker_run_argv comment.

    Falls back to *cmd* unchanged when:
      - Sandbox mode is off in .signalos/sandbox.json
      - Docker isn't installed / daemon isn't running
    """
    if is_sandbox_enabled(root):
        return (
            build_docker_run_argv(
                root, cmd, image=image, ports=ports, host_network=host_network,
            ),
            True,
        )
    return (cmd, False)
