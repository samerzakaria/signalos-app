"""Product repo lifecycle management for the SignalOS delivery bridge.

Manages delivery state, mode detection, git state capture, and
checkpoints. Delegates to ``signalos_lib.commands.init`` for actual
repo initialization rather than duplicating scaffolding logic.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "create_delivery_state",
    "load_delivery_state",
    "update_delivery_phase",
    "detect_mode",
    "init_product_repo",
    "capture_git_state",
    "record_checkpoint",
]

_SCHEMA_VERSION = "signalos.delivery_state.v1"

_STATE_DIR = ".signalos/product"
_STATE_FILE = "DELIVERY_STATE.json"
_CHECKPOINT_DIR = "checkpoints"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _state_path(repo_root: Path) -> Path:
    return repo_root / _STATE_DIR / _STATE_FILE


def _ensure_product_dir(repo_root: Path) -> Path:
    d = repo_root / _STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Delivery state CRUD
# ---------------------------------------------------------------------------

def create_delivery_state(
    repo_root: Path,
    mode: str,
    prompt: str,
    profile: str,
    blueprint: str,
) -> dict[str, Any]:
    """Create and write ``.signalos/product/DELIVERY_STATE.json``.

    Returns the delivery state dict.
    """
    now = _now_iso()
    state: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "phase": "intent",
        "mode": mode,
        "repo_root": str(repo_root),
        "product_name": repo_root.name,
        "prompt_sha256": _sha256(prompt),
        "profile": profile,
        "blueprint": blueprint,
        "wave": "",
        "status": "running",
        "created_at": now,
        "updated_at": now,
        "checkpoints": [],
    }
    _ensure_product_dir(repo_root)
    _state_path(repo_root).write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8",
    )
    return state


def load_delivery_state(repo_root: Path) -> dict[str, Any] | None:
    """Load delivery state from ``.signalos/product/DELIVERY_STATE.json``."""
    path = _state_path(repo_root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def update_delivery_phase(
    repo_root: Path,
    phase: str,
    status: str = "running",
) -> dict[str, Any]:
    """Update phase and status in delivery state. Returns updated state."""
    state = load_delivery_state(repo_root)
    if state is None:
        raise FileNotFoundError(
            f"No delivery state at {_state_path(repo_root)}"
        )
    state["phase"] = phase
    state["status"] = status
    state["updated_at"] = _now_iso()
    _state_path(repo_root).write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8",
    )
    return state


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------

def detect_mode(repo_root: Path) -> str:
    """Auto-detect the appropriate mode for a repo path.

    - Path doesn't exist or is empty dir -> ``"greenfield"``
    - Has files but no ``.signalos/`` -> ``"adopt"``
    - Has ``.signalos/`` -> ``"refresh"``
    """
    if not repo_root.exists():
        return "greenfield"
    if not repo_root.is_dir():
        return "greenfield"
    entries = list(repo_root.iterdir())
    if not entries:
        return "greenfield"
    if (repo_root / ".signalos").is_dir():
        return "refresh"
    return "adopt"


# ---------------------------------------------------------------------------
# Init delegation
# ---------------------------------------------------------------------------

def init_product_repo(
    repo_root: Path,
    mode: str,
    profile: str,
    product_name: str,
) -> dict[str, Any]:
    """Initialize the product repo using existing ``signalos init``.

    For greenfield: creates directory, runs init with profile.
    For adopt: runs init with ``--keep-existing``.
    For refresh: runs init with ``--refresh-bundle``.

    Delegates to ``signalos_lib.commands.init.main`` - does NOT duplicate
    init logic.

    Returns ``{"success": bool, "mode": str, "errors": list}``.
    """
    from signalos_lib.commands.init import main as init_main

    result: dict[str, Any] = {"success": False, "mode": mode, "errors": []}

    # Resolve the actual mode if "auto"
    if mode == "auto":
        mode = detect_mode(repo_root)
        result["mode"] = mode

    # Build argv for init.main()
    argv = [str(repo_root), "--yes", "--profile", profile, "--minimal"]
    if product_name:
        argv.extend(["--name", product_name])

    if mode == "greenfield":
        # The delivery pipeline may have already created the target dir
        # (e.g., for writing INTENT.json). Use --keep-existing so init
        # merges governance files without rejecting a non-empty dir.
        argv.append("--keep-existing")
    elif mode == "adopt":
        argv.append("--keep-existing")
    elif mode == "refresh":
        argv.append("--refresh-bundle")
    else:
        result["errors"].append(f"unknown mode: {mode}")
        return result

    try:
        rc = init_main(argv)
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    if rc == 0:
        result["success"] = True
    else:
        result["errors"].append(f"signalos init exited with code {rc}")

    return result


# ---------------------------------------------------------------------------
# Git state capture
# ---------------------------------------------------------------------------

def capture_git_state(repo_root: Path) -> dict[str, Any]:
    """Capture current git state for closeout evidence.

    Returns ``{"has_git": bool, "head_sha": str|None, "branch": str|None,
    "clean": bool|None, "untracked_count": int|None}``.
    """
    state: dict[str, Any] = {
        "has_git": False,
        "head_sha": None,
        "branch": None,
        "clean": None,
        "untracked_count": None,
    }

    if not (repo_root / ".git").exists():
        return state

    state["has_git"] = True

    def _git(*args: str) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    state["head_sha"] = _git("rev-parse", "HEAD")
    state["branch"] = _git("branch", "--show-current")

    porcelain = _git("status", "--porcelain")
    if porcelain is not None:
        lines = [l for l in porcelain.splitlines() if l.strip()]
        state["clean"] = len(lines) == 0
        state["untracked_count"] = sum(
            1 for l in lines if l.startswith("??")
        )

    return state


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

def record_checkpoint(repo_root: Path, label: str) -> dict[str, Any]:
    """Record a checkpoint in the delivery lifecycle.

    Writes to ``.signalos/product/checkpoints/<label>.json`` with
    timestamp and git state. Returns the checkpoint dict.
    """
    checkpoint_dir = repo_root / _STATE_DIR / _CHECKPOINT_DIR
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    git = capture_git_state(repo_root)
    now = _now_iso()
    checkpoint: dict[str, Any] = {
        "label": label,
        "timestamp": now,
        "git_state": git,
    }

    checkpoint_path = checkpoint_dir / f"{label}.json"
    checkpoint_path.write_text(
        json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8",
    )

    # Also append reference to delivery state if it exists
    state = load_delivery_state(repo_root)
    if state is not None:
        state.setdefault("checkpoints", []).append(
            {"label": label, "timestamp": now}
        )
        state["updated_at"] = now
        _state_path(repo_root).write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8",
        )

    return checkpoint
