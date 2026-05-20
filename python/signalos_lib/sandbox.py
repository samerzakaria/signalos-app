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

def docker_available() -> bool:
    """Return True iff `docker` resolves AND `docker --version` works.

    Docker Desktop being installed but stopped doesn't count -- a
    `docker run` against a stopped daemon hangs. We probe with
    `docker info` and a short timeout to filter that case out.
    """
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=3,
            shell=False,
        )
        return proc.returncode == 0 and bool((proc.stdout or "").strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


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
        # kind -> config key
        image_key = {"py": "image_py", "sh": "image_sh", "js": "image_js"}.get(kind, "image_js")
        image = cfg.get(image_key)
    workspace_abs = str(root.resolve())
    argv = [
        "docker", "run", "--rm", "-i",
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
