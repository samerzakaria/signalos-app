# signalos_lib/product/identity.py
# 3.6 (C-bridge): identity continuity across the journey (local-only scope;
# cross-device continuity is explicitly deferred -- see plan doc revision log).
#
# .signalos/identity.json (name, role) is collected once during onboarding
# (src-tauri/src/ipc.rs::set_identity) specifically "so the bundled SignalOS
# Core and the gate-signing rule both see the same actor + role" -- but until
# now nothing on the Python side actually read it. Every real gate signature
# was recorded under the generic literal "foundry-agent", never the founder's
# real name, and a launch mini-build's isolated repo_root started with no
# identity.json at all, forcing the founder to re-enter who they are on their
# own journey. This module closes both gaps.

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

__all__ = ["load_identity", "format_signer", "copy_identity_to"]

IDENTITY_REL_PATH = ".signalos/identity.json"


def load_identity(repo_root: Path) -> dict[str, Any] | None:
    """Read the founder identity set during onboarding, if any.

    Mirrors src-tauri's get_identity: returns None (not an error) when unset
    or unreadable -- identity is a continuity nicety, never a hard gate on
    delivery itself.
    """
    path = Path(repo_root) / IDENTITY_REL_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("name"):
        return None
    return data


def format_signer(identity: dict[str, Any] | None, *, fallback: str = "foundry-agent") -> str:
    """Render an identity as the signer string recorded in the audit trail.

    Falls back to the historical generic signer when no identity is set,
    so behavior for a workspace that hasn't run the onboarding wizard is
    unchanged."""
    if not identity or not identity.get("name"):
        return fallback
    name = str(identity["name"]).strip()
    role = str(identity.get("role") or "").strip()
    return f"{name} ({role})" if role else name


def copy_identity_to(parent_repo_root: Path, child_repo_root: Path) -> bool:
    """Carry the parent's identity into an isolated child repo_root (e.g. a
    launch mini-build) so the founder doesn't have to re-declare who they
    are on a build that is genuinely part of their own journey.

    Returns True if an identity was copied, False if the parent has none
    (a no-op, not an error -- the child just starts unset like any fresh
    workspace would).
    """
    source = Path(parent_repo_root) / IDENTITY_REL_PATH
    if not source.is_file():
        return False
    dest = Path(child_repo_root) / IDENTITY_REL_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return True
