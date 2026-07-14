# signalos_lib/audit_replay.py
# Time-travel replay over the append-only audit trail.
#
# The audit trail (.signalos/AUDIT_TRAIL.jsonl) is a tamper-evident, append-only
# log of every decision: gate signs, wave transitions, aborts, overrides, etc.
# Each line is {"ts", "action", ...payload}. Because it is append-only, the
# state at any past moment is a left-fold of the entries up to that point.
#
# This module reconstructs that state so a UI scrubber can travel back to see
# what was true at any entry: which wave was active, which gates were signed,
# and what had happened so far. Read-only; never writes.

from __future__ import annotations

__all__ = [
    "GATES",
    "load_audit_trail",
    "replay_state",
    "build_timeline",
]

import json
from pathlib import Path
from typing import Any

GATES = ("G0", "G1", "G2", "G3", "G4", "G5")

# Actions (or action prefixes) that mark a gate as signed/sealed.
_SIGN_MARKERS = ("sign", "seal", "gate.signed", "gate.approved")
# Actions that mark a gate decision being reversed / a wave rolled back.
_REVERSE_MARKERS = ("rollback", "revert", "unsign", "reopen", "revoke")


def load_audit_trail(root) -> list[dict[str, Any]]:
    """Read every audit entry in order. Tolerant: skips unparseable lines."""
    path = Path(root) / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                entries.append(obj)
    except OSError:
        return []
    return entries


def _action_matches(action: str, markers: tuple[str, ...]) -> bool:
    low = (action or "").lower()
    return any(m in low for m in markers)


def _initial_state() -> dict[str, Any]:
    return {
        "index": -1,
        "ts": None,
        "action": None,
        "wave": None,
        "gates": {g: {"signed": False, "role": None, "ts": None} for g in GATES},
        "events_applied": 0,
        "files_touched": 0,
        "overrides": 0,
    }


def _apply(state: dict[str, Any], entry: dict[str, Any], index: int) -> None:
    """Fold a single entry into the running state (mutates state)."""
    action = str(entry.get("action", ""))
    state["index"] = index
    state["ts"] = entry.get("ts")
    state["action"] = action
    state["events_applied"] = index + 1

    # Wave context: latest wave wins.
    wave = entry.get("wave")
    if wave:
        state["wave"] = wave

    # Gate sign / reverse.
    gates = entry.get("gates")
    gate_values = gates if isinstance(gates, list) else [entry.get("gate")]
    for gate in gate_values:
        if gate in state["gates"]:
            if _action_matches(action, _REVERSE_MARKERS):
                state["gates"][gate] = {"signed": False, "role": None, "ts": None}
            elif _action_matches(action, _SIGN_MARKERS):
                state["gates"][gate] = {
                    "signed": True,
                    "role": entry.get("role"),
                    "ts": entry.get("ts"),
                }

    # Overrides (headless/audited gate bypass) are notable history events.
    if "override" in action.lower():
        state["overrides"] += 1

    # File-touch accounting (entries may carry files / files_written).
    files = entry.get("files_written") or entry.get("files")
    if isinstance(files, list):
        state["files_touched"] += len(files)
    elif isinstance(files, int):
        state["files_touched"] += files


def replay_state(entries: list[dict[str, Any]], at_index: int) -> dict[str, Any]:
    """Reconstruct the cumulative state immediately AFTER ``entries[at_index]``.

    ``at_index`` is clamped to the valid range. An empty trail (or a negative
    index) returns the initial (pre-history) state.
    """
    state = _initial_state()
    if not entries:
        return state
    upto = max(-1, min(at_index, len(entries) - 1))
    for i in range(upto + 1):
        _apply(state, entries[i], i)
    return state


def _summarize(entry: dict[str, Any]) -> str:
    """A short, founder-legible one-liner for an entry."""
    action = str(entry.get("action", "event"))
    gate = entry.get("gate")
    wave = entry.get("wave")
    parts = [action]
    if gate:
        parts.append(f"gate {gate}")
    if wave:
        parts.append(f"wave {wave}")
    return " · ".join(parts)


def build_timeline(root) -> list[dict[str, Any]]:
    """Build a scrubber-ready timeline: one frame per audit entry.

    Each frame carries the entry, a one-line summary, and the full
    reconstructed ``state_after`` so a UI can render any point without
    re-folding. Frames are in chronological order; frame[i] is the state
    after applying entries[0..i].
    """
    entries = load_audit_trail(root)
    frames: list[dict[str, Any]] = []
    state = _initial_state()
    for i, entry in enumerate(entries):
        _apply(state, entry, i)
        frames.append({
            "index": i,
            "ts": entry.get("ts"),
            "summary": _summarize(entry),
            "entry": entry,
            "state_after": json.loads(json.dumps(state)),  # deep copy
        })
    return frames
