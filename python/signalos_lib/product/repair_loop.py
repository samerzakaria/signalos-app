# signalos_lib/product/repair_loop.py
# Phase P8 - Agent Execution Bridge: Repair Loop
#
# Runs a bounded repair loop for failed agent validation results.
# Each cycle stores logs and changed files; the loop never runs
# silently -- every repair cycle produces evidence.

from __future__ import annotations

__all__ = [
    "build_repair_packet",
    "run_repair_loop",
    "write_repair_packet",
]

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_packets import validate_agent_result


# ---------------------------------------------------------------------------
# run_repair_loop
# ---------------------------------------------------------------------------

def run_repair_loop(
    repo_root: Path,
    validation_result: dict,
    profile: str,
    max_cycles: int = 3,
    agent_mode: str = "packet-only",
) -> dict:
    """Run the repair loop for failed validation.

    Each cycle:
    1. Identify failures from validation result
    2. Create repair packet with failure logs
    3. If *agent_mode* is ``"packet-only"``, write packet and pause
       (return with ``status="awaiting_agent"``)
    4. If *agent_mode* is ``"none"``, return with
       ``status="manual_repair_needed"``
    5. Track cycle count
    6. Stop at *max_cycles* with evidence

    Parameters
    ----------
    repo_root:
        Workspace root containing ``.signalos/``.
    validation_result:
        Output of ``validate_agent_result`` (has ``valid``, ``checks``,
        ``violations`` keys).
    profile:
        Stack profile name (e.g. ``"react-vite"``).
    max_cycles:
        Upper bound on repair attempts.  0 means return immediately
        with no repair.
    agent_mode:
        ``"none"`` -- return immediately, no repair packet created.
        ``"packet-only"`` -- create a repair packet and pause.
        ``"orchestrator"`` -- reserved for future orchestrator dispatch.
        ``"auto"`` -- reserved for future fully-automated repair.
    """
    repairs: list[dict[str, Any]] = []

    if max_cycles <= 0:
        return {
            "status": "max_cycles_reached",
            "cycles_used": 0,
            "max_cycles": max_cycles,
            "repairs": [],
            "final_validation": validation_result,
        }

    if validation_result.get("valid", False):
        return {
            "status": "repaired",
            "cycles_used": 0,
            "max_cycles": max_cycles,
            "repairs": [],
            "final_validation": validation_result,
        }

    # Locate the run directory from the validation result.  The caller
    # should pass the result that came from validate_agent_result; we
    # look for the scope.json in a well-known location.  If no run_dir
    # can be found, we create a fresh one.
    run_dir = _find_run_dir(repo_root, validation_result)

    # Load original scope for repair packet context
    original_packet: dict = {}
    if run_dir is not None:
        scope_path = run_dir / "scope.json"
        if scope_path.is_file():
            try:
                original_packet = json.loads(
                    scope_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                pass

    current_validation = validation_result

    for cycle in range(1, max_cycles + 1):
        failures = current_validation.get("violations", [])
        if not failures:
            # All fixed
            return {
                "status": "repaired",
                "cycles_used": cycle - 1,
                "max_cycles": max_cycles,
                "repairs": repairs,
                "final_validation": current_validation,
            }

        validation_logs = json.dumps(
            current_validation.get("checks", []), indent=2
        )

        if agent_mode == "none":
            repairs.append({
                "cycle": cycle,
                "failures": failures,
                "action": "skipped",
                "packet_path": None,
            })
            return {
                "status": "manual_repair_needed",
                "cycles_used": cycle,
                "max_cycles": max_cycles,
                "repairs": repairs,
                "final_validation": current_validation,
            }

        if agent_mode == "packet-only":
            packet = build_repair_packet(
                repo_root=repo_root,
                cycle=cycle,
                failures=failures,
                validation_logs=validation_logs,
                original_packet=original_packet,
            )
            if run_dir is None:
                run_dir = (
                    repo_root
                    / ".signalos"
                    / "product"
                    / "agent-runs"
                    / packet["run_id"]
                )
                run_dir.mkdir(parents=True, exist_ok=True)

            packet_path = write_repair_packet(packet, run_dir, cycle)
            repairs.append({
                "cycle": cycle,
                "failures": failures,
                "action": "packet_created",
                "packet_path": str(packet_path),
            })
            return {
                "status": "awaiting_agent",
                "cycles_used": cycle,
                "max_cycles": max_cycles,
                "repairs": repairs,
                "final_validation": current_validation,
            }

        # Future modes ("orchestrator", "auto") would dispatch here and
        # re-validate. For now, treat them the same as "packet-only".
        packet = build_repair_packet(
            repo_root=repo_root,
            cycle=cycle,
            failures=failures,
            validation_logs=validation_logs,
            original_packet=original_packet,
        )
        if run_dir is None:
            run_dir = (
                repo_root
                / ".signalos"
                / "product"
                / "agent-runs"
                / packet["run_id"]
            )
            run_dir.mkdir(parents=True, exist_ok=True)
        packet_path = write_repair_packet(packet, run_dir, cycle)
        repairs.append({
            "cycle": cycle,
            "failures": failures,
            "action": "packet_created",
            "packet_path": str(packet_path),
        })
        return {
            "status": "awaiting_agent",
            "cycles_used": cycle,
            "max_cycles": max_cycles,
            "repairs": repairs,
            "final_validation": current_validation,
        }

    # Exhausted max_cycles (should only reach here if all cycles
    # completed without returning)
    return {
        "status": "max_cycles_reached",
        "cycles_used": max_cycles,
        "max_cycles": max_cycles,
        "repairs": repairs,
        "final_validation": current_validation,
    }


# ---------------------------------------------------------------------------
# build_repair_packet
# ---------------------------------------------------------------------------

def build_repair_packet(
    repo_root: Path,
    cycle: int,
    failures: list[str],
    validation_logs: str,
    original_packet: dict,
) -> dict:
    """Build a repair packet for a failed validation cycle.

    The repair packet inherits context from the original packet (tasks,
    allowed paths, etc.) and adds failure context so the agent knows
    exactly what to fix.
    """
    run_id = original_packet.get("run_id", str(uuid.uuid4()))

    return {
        "schema_version": "signalos.repair_packet.v1",
        "run_id": run_id,
        "repair_cycle": cycle,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": original_packet.get("profile", ""),
        "wave": original_packet.get("wave", ""),
        "intent_summary": original_packet.get("intent_summary", {}),
        "tasks": original_packet.get("tasks", []),
        "allowed_paths": original_packet.get("allowed_paths", []),
        "forbidden_paths": original_packet.get("forbidden_paths", []),
        "forbidden_actions": original_packet.get("forbidden_actions", []),
        "validation_commands": original_packet.get("validation_commands", []),
        "failures": failures,
        "validation_logs": validation_logs,
    }


# ---------------------------------------------------------------------------
# write_repair_packet
# ---------------------------------------------------------------------------

def write_repair_packet(
    packet: dict,
    run_dir: Path,
    cycle: int,
) -> Path:
    """Write repair packet to ``<run_dir>/repair-<cycle>/``.

    Creates:
    - ``repair-scope.json`` -- full repair packet
    - ``REPAIR.md``         -- human-readable repair instructions

    Returns the repair directory path.
    """
    repair_dir = run_dir / f"repair-{cycle}"
    repair_dir.mkdir(parents=True, exist_ok=True)

    (repair_dir / "repair-scope.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    md = _render_repair_md(packet, cycle)
    (repair_dir / "REPAIR.md").write_text(md, encoding="utf-8")

    return repair_dir


def _render_repair_md(packet: dict, cycle: int) -> str:
    """Render a human-readable Markdown repair instruction."""
    lines: list[str] = []
    lines.append(f"# Repair Cycle {cycle}")
    lines.append("")
    lines.append(f"**Run ID:** {packet.get('run_id', '')}")
    lines.append(f"**Created:** {packet.get('created_at', '')}")
    lines.append(f"**Profile:** {packet.get('profile', '')}")
    lines.append(f"**Wave:** {packet.get('wave', '')}")
    lines.append("")
    lines.append("## Failures to Fix")
    lines.append("")
    for failure in packet.get("failures", []):
        lines.append(f"- {failure}")
    lines.append("")
    lines.append("## Validation Logs")
    lines.append("")
    lines.append("```json")
    lines.append(packet.get("validation_logs", ""))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_run_dir(
    repo_root: Path, validation_result: dict
) -> Path | None:
    """Attempt to locate the agent-run directory for a validation result.

    The result doesn't carry a run_id directly; we look for the most
    recent run directory under ``.signalos/product/agent-runs/``.
    """
    runs_dir = repo_root / ".signalos" / "product" / "agent-runs"
    if not runs_dir.is_dir():
        return None
    # Find the most recently modified run directory
    candidates = [
        d for d in runs_dir.iterdir()
        if d.is_dir() and (d / "scope.json").is_file()
    ]
    if not candidates:
        return None
    # Sort by modification time, newest first
    candidates.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return candidates[0]
