# signalos_lib/fleet_runtime.py
# Governed agent fleet runtime FOUNDATION.
#
# Clean-room reimplementation: the *concepts* (detect agent CLIs, register
# runtimes, isolated task workspaces, TTL garbage collection) are inspired by
# the publicly-documented behaviour of Multica's runtime. Multica's source is
# under a restrictive commercial license and was NOT read or copied; this module
# is designed only from the described public behaviour.
#
# The SignalOS differentiator vs an ungoverned dispatcher: every agent hand-off
# passes through a governance ADMISSION check first (an active wave/gate or a
# valid agent packet), and every admission writes an evidence record + audit
# row. SignalOS enforces, never advises -- a missing packet/gate fails closed
# (admitted=False), not as a warning.
#
# This is the FOUNDATION. The live server-backed executor (the daemon that
# actually claims, spawns, heartbeats, and streams a CLI) is roadmap, not built
# here. See docs/GOVERNED_FLEET_RUNTIME_DESIGN.md.

from __future__ import annotations

__all__ = [
    "AGENT_RUNTIMES",
    "GC_META_NAME",
    "detect_runtimes",
    "governed_dispatch",
    "gc_task_workspaces",
]

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# Runtime registry
# ---------------------------------------------------------------------------
# The agent CLIs SignalOS already targets are derived from the tool-adapter
# emitter directory names under
# ``_bundle/core/tool-adapters/emitters/`` (claude-code, codex, cursor,
# github-copilot, windsurf, vs-code, antigravity, harness), plus gemini which
# the runtime can also dispatch to. Each entry maps a stable runtime ``id`` to
# the executable name(s) that prove the CLI is installed on PATH and the
# ``kind`` of agent surface it exposes.


@dataclass(frozen=True)
class RuntimeSpec:
    """A known agent CLI SignalOS can dispatch governed work to."""

    id: str
    # The emitter/adapter name this runtime corresponds to (may differ from id).
    cli: str
    # Candidate executable basenames to probe on PATH, in priority order.
    executables: tuple[str, ...]
    # Coarse surface kind: "agent-cli" (interactive coding agent) or
    # "headless" (the built-in harness emitter).
    kind: str


# Order is stable so detection output is deterministic.
AGENT_RUNTIMES: tuple[RuntimeSpec, ...] = (
    RuntimeSpec("claude-code", "claude-code", ("claude",), "agent-cli"),
    RuntimeSpec("codex", "codex", ("codex",), "agent-cli"),
    RuntimeSpec("cursor", "cursor", ("cursor-agent", "cursor"), "agent-cli"),
    RuntimeSpec("github-copilot", "github-copilot", ("copilot",), "agent-cli"),
    RuntimeSpec("windsurf", "windsurf", ("windsurf",), "agent-cli"),
    RuntimeSpec("gemini", "gemini", ("gemini",), "agent-cli"),
    RuntimeSpec("antigravity", "antigravity", ("antigravity",), "agent-cli"),
    RuntimeSpec("vs-code", "vs-code", ("code",), "editor"),
    RuntimeSpec("harness", "harness", ("signalos",), "headless"),
)

GC_META_NAME = ".gc_meta.json"

_EXE_SUFFIXES_WINDOWS = (".exe", ".cmd", ".bat", "")
_EXE_SUFFIXES_POSIX = ("",)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------

def _resolve_on_path(
    executable: str,
    path_entries: list[str],
    *,
    is_windows: bool,
) -> Optional[str]:
    """Return the absolute path to *executable* found in *path_entries*.

    PATH is injectable so tests are deterministic and never depend on the host
    machine's real PATH. This is a self-contained re-implementation of the
    minimal ``shutil.which`` semantics we need, scoped to an explicit list of
    directories.
    """
    suffixes = _EXE_SUFFIXES_WINDOWS if is_windows else _EXE_SUFFIXES_POSIX
    for entry in path_entries:
        if not entry:
            continue
        base = Path(entry)
        for suffix in suffixes:
            candidate = base / f"{executable}{suffix}"
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
    return None


def detect_runtimes(
    path_env: Optional[str] = None,
    *,
    is_windows: Optional[bool] = None,
) -> list[dict[str, Any]]:
    """Scan PATH for the agent CLIs SignalOS emits to and report each runtime.

    Returns a list of records, one per known runtime, in stable order::

        {"id": str, "cli": str, "executable": str | None,
         "kind": str, "detected": bool}

    *path_env* is the ``PATH`` string to scan; when ``None`` the process
    ``PATH`` is used. Tests MUST inject *path_env* (and may inject
    *is_windows*) so detection never depends on the host's real PATH.
    """
    if path_env is None:
        path_env = os.environ.get("PATH", "")
    if is_windows is None:
        is_windows = os.name == "nt"

    path_entries = [p for p in path_env.split(os.pathsep) if p]

    records: list[dict[str, Any]] = []
    for spec in AGENT_RUNTIMES:
        resolved: Optional[str] = None
        for exe in spec.executables:
            resolved = _resolve_on_path(exe, path_entries, is_windows=is_windows)
            if resolved is not None:
                break
        records.append(
            {
                "id": spec.id,
                "cli": spec.cli,
                "executable": resolved,
                "kind": spec.kind,
                "detected": resolved is not None,
            }
        )
    return records


# ---------------------------------------------------------------------------
# Governed dispatch admission
# ---------------------------------------------------------------------------
# This is a THIN admission layer. It does NOT spawn a CLI in this foundation;
# the live executor is roadmap (see the design doc). Its only job is to enforce
# the SignalOS rule that no agent runs ungoverned: a dispatch is admitted only
# when there is an active wave/gate OR a structurally-valid agent packet, and
# every decision is written to evidence + audit. Fail-closed: when neither
# proof of governance is present, admission is refused (admitted=False).

EVIDENCE_DIR_REL = Path(".signalos") / "evidence" / "fleet"
DISPATCH_EVIDENCE_REL = EVIDENCE_DIR_REL / "dispatch.jsonl"
AUDIT_REL = Path(".signalos") / "AUDIT_TRAIL.jsonl"

AUDIT_DISPATCH_ADMITTED = "fleet-dispatch-admitted"
AUDIT_DISPATCH_REFUSED = "fleet-dispatch-refused"


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _packet_is_valid(packet: Optional[dict]) -> tuple[bool, str]:
    """Return (ok, reason) for a structurally-valid agent packet.

    This composes with the existing agent-packet contract rather than
    re-implementing validation: it reuses the same required contract fields
    that ``agent_packets.validate_agent_result`` enforces on ``scope.json``,
    so an admitted packet is one the downstream validator will also accept.
    """
    if not isinstance(packet, dict) or not packet:
        return False, "no agent packet provided"

    from signalos_lib.product.agent_packets import (
        _REQUIRED_PACKET_CONTRACT_FIELDS,
        _packet_contract_violations,
    )

    if not packet.get("run_id"):
        return False, "agent packet missing run_id"

    violations = _packet_contract_violations(packet)
    if violations:
        return False, "; ".join(violations)
    # Defensive: ensure every required field is genuinely present.
    missing = [f for f in _REQUIRED_PACKET_CONTRACT_FIELDS if not packet.get(f)]
    if missing:
        return False, "agent packet missing contract fields: " + ", ".join(missing)
    return True, "agent packet satisfies the SignalOS packet contract"


def _default_gate_check(repo_root: Path) -> dict[str, Any]:
    """Detect an active governed wave/gate under the repo.

    A dispatch may be admitted on the strength of an active wave/gate alone
    (no packet) when SignalOS has an open Journey wave. This is a conservative,
    file-truth check: it looks for an ACTIVE wave marker the rest of the app
    already writes. Tests inject their own ``gate_check`` for determinism.
    """
    journey = repo_root / "core" / "governance" / "Journey" / "JOURNEY.md"
    active_wave: Optional[str] = None
    try:
        if journey.is_file():
            for line in journey.read_text(encoding="utf-8", errors="replace").splitlines():
                low = line.lower()
                if "active" in low and ("wave" in low or "w" in low):
                    active_wave = line.strip()
                    break
    except OSError:
        active_wave = None
    return {"active": active_wave is not None, "wave": active_wave}


def governed_dispatch(
    repo_root: Path,
    task: dict[str, Any],
    *,
    packet: Optional[dict] = None,
    gate_check: Optional[Callable[[Path], dict[str, Any]]] = None,
    dry_run: bool = True,
    runtime_id: Optional[str] = None,
    now_ts: Optional[float] = None,
) -> dict[str, Any]:
    """Admission wrapper around an agent hand-off.

    Before any agent dispatch, run a governance admission check and require
    EITHER an active wave/gate OR a structurally-valid agent packet. Refuse
    otherwise (fail-closed). Write an evidence record + audit row for every
    decision and return a structured decision::

        {"admitted": bool, "reason": str, "task": ..., "runtime_id": ...,
         "dry_run": bool, "governance": {...}, "decided_at": str,
         "executed": bool, "execution_note": str}

    The live CLI executor is NOT invoked here -- ``executed`` is always False
    and ``execution_note`` records that the live executor is roadmap. This
    keeps the admission layer thin and fully testable.
    """
    repo_root = Path(repo_root)
    decided_at = _utc_now()

    gate_fn = gate_check or _default_gate_check
    try:
        gate_state = gate_fn(repo_root) or {}
    except Exception as exc:  # never let a custom check crash admission
        gate_state = {"active": False, "error": str(exc)}
    gate_active = bool(gate_state.get("active"))

    packet_ok, packet_reason = _packet_is_valid(packet)

    admitted = gate_active or packet_ok

    if admitted:
        if gate_active and packet_ok:
            reason = "admitted: active gate and valid agent packet"
        elif gate_active:
            reason = "admitted: active wave/gate authorizes governed dispatch"
        else:
            reason = "admitted: valid agent packet authorizes governed dispatch"
    else:
        # Fail-closed refusal -- spell out both missing proofs.
        reason = (
            "refused: no active wave/gate and no valid agent packet "
            f"(packet: {packet_reason})"
        )

    decision: dict[str, Any] = {
        "admitted": admitted,
        "reason": reason,
        "task": {
            "id": task.get("id") or task.get("task") or task.get("title"),
            "title": task.get("title"),
        },
        "runtime_id": runtime_id,
        "dry_run": bool(dry_run),
        "governance": {
            "gate_active": gate_active,
            "gate_detail": gate_state,
            "packet_ok": packet_ok,
            "packet_reason": packet_reason,
            "packet_run_id": (packet or {}).get("run_id") if isinstance(packet, dict) else None,
        },
        "decided_at": decided_at,
        # The live server-backed executor is roadmap; admission never spawns.
        "executed": False,
        "execution_note": (
            "Admission decision only. The live agent CLI executor is roadmap "
            "(see docs/GOVERNED_FLEET_RUNTIME_DESIGN.md); this foundation does "
            "not spawn a runtime."
        ),
    }

    # Evidence + audit for EVERY decision (admitted or refused).
    evidence_row = dict(decision)
    evidence_row["kind"] = "fleet.governed_dispatch"
    try:
        _append_jsonl(repo_root / DISPATCH_EVIDENCE_REL, evidence_row)
    except OSError:
        pass

    action = AUDIT_DISPATCH_ADMITTED if admitted else AUDIT_DISPATCH_REFUSED
    audit_row = {
        "ts": decided_at,
        "action": action,
        "verdict": "admitted" if admitted else "refused",
        "task_id": decision["task"]["id"],
        "runtime_id": runtime_id,
        "gate_active": gate_active,
        "packet_ok": packet_ok,
        "reason": reason,
    }
    try:
        _append_jsonl(repo_root / AUDIT_REL, audit_row)
    except OSError:
        pass

    return decision


# ---------------------------------------------------------------------------
# Task workspace garbage collection
# ---------------------------------------------------------------------------
# Isolated per-task workspaces accumulate. GC prunes them on TTLs while
# PRESERVING source, .git, and logs. ``now_ts`` is passed in -- this function
# NEVER calls time.time(), so tests are deterministic.

# Names that must survive an artifact prune of an otherwise-kept task dir.
_PRESERVE_NAMES = frozenset({".git", "logs", GC_META_NAME})


def _read_gc_meta(task_dir: Path) -> Optional[dict[str, Any]]:
    meta_path = task_dir / GC_META_NAME
    try:
        if meta_path.is_file():
            return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _dir_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _is_terminal_status(status: str) -> bool:
    return status.lower() in {"done", "complete", "completed", "idle", "abandoned", "failed"}


def gc_task_workspaces(
    root: Path,
    *,
    now_ts: float,
    done_ttl_s: float,
    orphan_ttl_s: float,
    artifact_ttl_s: float,
    artifact_globs: Iterable[str] = ("node_modules", ".next", ".turbo"),
) -> dict[str, Any]:
    """TTL-based pruning of task workspaces under *root*.

    For each immediate child directory of *root* (one per task):

    * **done/idle past ``done_ttl_s``** -> the whole task dir is removed.
    * **orphan (no ``.gc_meta.json``) past ``orphan_ttl_s``** -> removed.
    * **kept** task dirs still get artifact pruning: any artifact dir whose
      name matches ``artifact_globs`` and is older than ``artifact_ttl_s`` is
      removed, while source, ``.git``, and ``logs`` are PRESERVED.

    ``now_ts`` is supplied by the caller; this function never reads the wall
    clock so behaviour is deterministic. Returns a structured summary::

        {"root": str, "now_ts": float, "scanned": int,
         "removed_tasks": [...], "kept_tasks": [...],
         "pruned_artifacts": [...], "errors": [...]}
    """
    root = Path(root)
    summary: dict[str, Any] = {
        "root": str(root),
        "now_ts": now_ts,
        "scanned": 0,
        "removed_tasks": [],
        "kept_tasks": [],
        "pruned_artifacts": [],
        "errors": [],
    }

    if not root.is_dir():
        return summary

    artifact_names = {str(name) for name in artifact_globs}

    for task_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        summary["scanned"] += 1
        name = task_dir.name
        meta = _read_gc_meta(task_dir)

        # 1. Orphan (no/invalid .gc_meta.json) past the orphan TTL -> remove.
        if meta is None:
            age = now_ts - _dir_mtime(task_dir)
            if age >= orphan_ttl_s:
                if _remove_tree(task_dir, summary):
                    summary["removed_tasks"].append(
                        {"task": name, "reason": "orphan-expired", "age_s": age}
                    )
                continue
            summary["kept_tasks"].append(
                {"task": name, "reason": "orphan-fresh", "age_s": age}
            )
            continue

        status = str(meta.get("status", "active"))
        # last_active_ts from meta wins; fall back to dir mtime.
        last_active = meta.get("last_active_ts")
        try:
            last_active = float(last_active)
        except (TypeError, ValueError):
            last_active = _dir_mtime(task_dir)
        age = now_ts - last_active

        # 2. Done/idle past the done TTL -> remove the whole task dir.
        if _is_terminal_status(status) and age >= done_ttl_s:
            if _remove_tree(task_dir, summary):
                summary["removed_tasks"].append(
                    {"task": name, "reason": "done-expired",
                     "status": status, "age_s": age}
                )
            continue

        # 3. Otherwise keep the task, but prune expired artifact dirs.
        kept_entry: dict[str, Any] = {
            "task": name, "status": status, "age_s": age,
        }
        pruned_here: list[str] = []
        for child in sorted(c for c in task_dir.iterdir() if c.is_dir()):
            if child.name in _PRESERVE_NAMES:
                continue
            if child.name not in artifact_names:
                continue
            artifact_age = now_ts - _dir_mtime(child)
            if artifact_age >= artifact_ttl_s:
                if _remove_tree(child, summary):
                    pruned_here.append(child.name)
                    summary["pruned_artifacts"].append(
                        {"task": name, "artifact": child.name,
                         "age_s": artifact_age}
                    )
        if pruned_here:
            kept_entry["pruned_artifacts"] = pruned_here
        summary["kept_tasks"].append(kept_entry)

    return summary


def _remove_tree(path: Path, summary: dict[str, Any]) -> bool:
    try:
        shutil.rmtree(path)
        return True
    except OSError as exc:
        summary["errors"].append({"path": str(path), "error": str(exc)})
        return False
