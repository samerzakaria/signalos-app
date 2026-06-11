"""Tests for time-travel audit replay."""

from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.audit_replay import (
    GATES,
    build_timeline,
    load_audit_trail,
    replay_state,
)

_TRAIL = [
    {"ts": "2026-06-01T10:00:00Z", "action": "wave.start", "wave": "W1"},
    {"ts": "2026-06-01T10:05:00Z", "action": "agent.build", "wave": "W1", "files_written": ["a.ts", "b.ts"]},
    {"ts": "2026-06-01T10:10:00Z", "action": "gate.signed", "wave": "W1", "gate": "G4", "role": "PE"},
    {"ts": "2026-06-01T10:20:00Z", "action": "wave.start", "wave": "W2"},
    {"ts": "2026-06-01T10:25:00Z", "action": "rollback", "wave": "W2", "gate": "G4"},
    {"ts": "2026-06-01T10:30:00Z", "action": "gate.override", "wave": "W2", "gate": "G5", "role": "PO"},
]


def _write_trail(root: Path, rows):
    d = root / ".signalos"
    d.mkdir(parents=True, exist_ok=True)
    (d / "AUDIT_TRAIL.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_load_missing_trail_returns_empty(tmp_path):
    assert load_audit_trail(tmp_path) == []


def test_load_skips_blank_and_malformed(tmp_path):
    d = tmp_path / ".signalos"
    d.mkdir(parents=True)
    (d / "AUDIT_TRAIL.jsonl").write_text(
        '{"action":"ok"}\n\nNOT JSON\n{"action":"ok2"}\n', encoding="utf-8"
    )
    entries = load_audit_trail(tmp_path)
    assert [e["action"] for e in entries] == ["ok", "ok2"]


def test_replay_initial_before_history():
    state = replay_state(_TRAIL, -1)
    assert state["events_applied"] == 0
    assert state["wave"] is None
    assert all(not g["signed"] for g in state["gates"].values())


def test_replay_after_sign_marks_gate():
    state = replay_state(_TRAIL, 2)  # through the G4 sign
    assert state["wave"] == "W1"
    assert state["gates"]["G4"]["signed"] is True
    assert state["gates"]["G4"]["role"] == "PE"
    assert state["files_touched"] == 2


def test_replay_rollback_unsigns_gate():
    state = replay_state(_TRAIL, 4)  # through the rollback of G4
    assert state["gates"]["G4"]["signed"] is False
    assert state["wave"] == "W2"


def test_replay_counts_overrides():
    state = replay_state(_TRAIL, 5)
    assert state["overrides"] == 1


def test_replay_clamps_out_of_range():
    last = replay_state(_TRAIL, 999)
    assert last["index"] == len(_TRAIL) - 1
    assert last["events_applied"] == len(_TRAIL)


def test_build_timeline_frames_are_cumulative(tmp_path):
    _write_trail(tmp_path, _TRAIL)
    frames = build_timeline(tmp_path)
    assert len(frames) == len(_TRAIL)
    # Frame at the sign shows G4 signed; a later rollback frame shows it cleared.
    assert frames[2]["state_after"]["gates"]["G4"]["signed"] is True
    assert frames[4]["state_after"]["gates"]["G4"]["signed"] is False
    # Each frame carries a human summary and its source entry.
    assert "gate G4" in frames[2]["summary"]
    assert frames[0]["entry"]["action"] == "wave.start"


def test_timeline_state_snapshots_are_independent(tmp_path):
    _write_trail(tmp_path, _TRAIL)
    frames = build_timeline(tmp_path)
    # Mutating one frame's state must not bleed into another (deep-copied).
    frames[0]["state_after"]["gates"]["G0"]["signed"] = True
    assert frames[1]["state_after"]["gates"]["G0"]["signed"] is False


def test_all_gates_present_in_state():
    state = replay_state(_TRAIL, 0)
    assert tuple(state["gates"].keys()) == GATES
