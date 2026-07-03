# signalos_lib/product/launch.py
# 3.4 (C-bridge): a launch surface (e.g. a landing page) re-enters the SAME
# enforced G0-G5 gate loop as a genuine second mini-build -- not a bypass,
# not hand-authored marketing copy dropped straight to disk.
#
# GateOrchestrator's gate pointer comes from wave_engine.inspect(), which is
# repo_root-scoped; its project_id parameter is documented as unimplemented
# multi-project plumbing today (see wave_engine.inspect docstring), so a
# second GateOrchestrator against the parent's own repo_root would just
# resume wherever the parent's single wave pointer already sits, not
# restart at G0. The mini-build therefore gets its own isolated repo_root
# (its own .signalos/ tree) and walks G0-G5 through the identical
# orchestrator, linked back to the parent's journey by an explicit record
# on both sides.

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

__all__ = ["start_launch_build", "load_launch_link", "list_launches"]

LAUNCH_SUBDIR = "launch"
LAUNCHES_REGISTRY_REL = ".signalos/product/LAUNCHES.json"


def start_launch_build(
    parent_repo_root: Path,
    orchestrator_factory: Callable[[Path, str, str], Any],
    *,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Start a launch-surface mini-build for an already-delivered product.

    *orchestrator_factory(child_repo_root, prompt, run_id) -> GateOrchestrator*
    is injected so this is testable without a real LLM adapter; the IPC
    layer supplies the real one (same construction as agent:deliver).

    Requires the parent to already have a closeout -- a product that
    hasn't shipped has nothing to launch, and starting a disconnected
    build without that link would defeat the point of this being a
    *re-entry* into the same journey rather than a fresh, unrelated build.
    """
    parent_repo_root = Path(parent_repo_root)
    from .closeout import load_closeout

    parent_closeout = load_closeout(parent_repo_root / ".signalos")
    if not parent_closeout:
        raise ValueError(
            "no closeout found for the parent product -- launch re-entry "
            "requires a delivered product to launch"
        )

    product_name = parent_closeout.get("product_name") or "the product"
    scoped_prompt = prompt or (
        f"Landing page for {product_name}: a hero section stating the "
        f"value proposition, one clear call to action, and a signup form. "
        f"No backend beyond capturing the signup."
    )

    run_id = f"launch-{uuid.uuid4().hex[:8]}"
    child_repo_root = parent_repo_root / ".signalos" / "product" / LAUNCH_SUBDIR / run_id
    child_repo_root.mkdir(parents=True, exist_ok=True)

    # Carry the founder's identity into the isolated child before the gate
    # loop starts, so they aren't asked to re-declare who they are on a
    # build that is genuinely part of their own journey (3.6).
    from .identity import copy_identity_to
    copy_identity_to(parent_repo_root, child_repo_root)

    orch = orchestrator_factory(child_repo_root, scoped_prompt, run_id)
    gate_result = orch.start()

    started_at = datetime.now(timezone.utc).isoformat()
    link = {
        "run_id": run_id,
        "parent_repo_root": str(parent_repo_root),
        "child_repo_root": str(child_repo_root),
        "parent_product_name": product_name,
        "started_at": started_at,
        "prompt": scoped_prompt,
    }
    (child_repo_root / "LAUNCH_LINK.json").write_text(
        json.dumps(link, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )

    registry_path = parent_repo_root / Path(LAUNCHES_REGISTRY_REL)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    launches: list[dict[str, Any]] = []
    if registry_path.is_file():
        try:
            launches = json.loads(registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            launches = []
    launches.append({"run_id": run_id, "child_repo_root": str(child_repo_root), "started_at": started_at})
    registry_path.write_text(
        json.dumps(launches, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )

    return {"run_id": run_id, "child_repo_root": str(child_repo_root), "link": link, "gate_result": gate_result}


def load_launch_link(child_repo_root: Path) -> dict[str, Any] | None:
    link_path = Path(child_repo_root) / "LAUNCH_LINK.json"
    if not link_path.is_file():
        return None
    try:
        return json.loads(link_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_launches(parent_repo_root: Path) -> list[dict[str, Any]]:
    registry_path = Path(parent_repo_root) / Path(LAUNCHES_REGISTRY_REL)
    if not registry_path.is_file():
        return []
    try:
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
