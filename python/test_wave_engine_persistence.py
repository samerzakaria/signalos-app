"""test_wave_engine_persistence.py — engine state survives process restart.

Per WAVE-ENGINE-DESIGN §3.1 — the v1 persistence model reconstructs the
engine per IPC turn from `inspect()`. This module adds a state file at
`.signalos/wave-engine-state.json` (per-project for project_id != "default")
so the engine can also rehydrate `current_gate` + `last_user_request`
across process restarts.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.wave_engine import (
    STATE_FILE_PATH,
    WaveEngine,
    WaveState,
    _state_file_path,
    load_persisted_state,
    save_persisted_state,
)


def _mk_workspace() -> Path:
    root = Path(tempfile.mkdtemp(prefix="signalos-persist-")).resolve()
    (root / ".signalos").mkdir()
    return root


class StateFilePathTests(unittest.TestCase):
    def test_default_project_uses_signalos_root(self):
        root = Path("/tmp/foo")
        self.assertEqual(
            _state_file_path(root, "default"),
            root.joinpath(*STATE_FILE_PATH),
        )

    def test_named_project_uses_projects_namespace(self):
        root = Path("/tmp/foo")
        self.assertEqual(
            _state_file_path(root, "alpha"),
            root / ".signalos" / "projects" / "alpha" / "wave-engine-state.json",
        )


class LoadSaveTests(unittest.TestCase):
    def test_load_missing_returns_none(self):
        root = _mk_workspace()
        self.assertIsNone(load_persisted_state(root))

    def test_save_then_load_round_trips(self):
        root = _mk_workspace()
        save_persisted_state(root, {"state": "DISPATCH", "current_gate": "G2"})
        loaded = load_persisted_state(root)
        self.assertEqual(loaded["state"], "DISPATCH")
        self.assertEqual(loaded["current_gate"], "G2")

    def test_load_corrupt_json_returns_none(self):
        root = _mk_workspace()
        target = root.joinpath(*STATE_FILE_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not valid json", encoding="utf-8")
        self.assertIsNone(load_persisted_state(root))

    def test_load_empty_file_returns_none(self):
        root = _mk_workspace()
        target = root.joinpath(*STATE_FILE_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        self.assertIsNone(load_persisted_state(root))

    def test_named_project_writes_to_namespaced_path(self):
        root = _mk_workspace()
        save_persisted_state(root, {"state": "ENTRY"}, project_id="alpha")
        expected = root / ".signalos" / "projects" / "alpha" / "wave-engine-state.json"
        self.assertTrue(expected.is_file())


class EngineRehydrationTests(unittest.TestCase):
    def test_fresh_engine_with_no_state_starts_at_entry(self):
        root = _mk_workspace()
        eng = WaveEngine(root)
        self.assertEqual(eng.state, WaveState.ENTRY)
        self.assertIsNone(eng.current_gate)

    def test_rehydrate_false_ignores_existing_state_file(self):
        root = _mk_workspace()
        save_persisted_state(root, {
            "state": "DISPATCH",
            "current_gate": "G2",
            "last_user_request": "Build it",
        })
        eng = WaveEngine(root, rehydrate=False)
        self.assertEqual(eng.state, WaveState.ENTRY)
        self.assertIsNone(eng.current_gate)
        self.assertIsNone(eng.last_user_request)

    def test_rehydrate_default_loads_persisted_state(self):
        root = _mk_workspace()
        save_persisted_state(root, {
            "version": 1,
            "state": "DISPATCH",
            "current_gate": "G2",
            "last_user_request": "Build the helper",
        })
        eng = WaveEngine(root)
        self.assertEqual(eng.state, WaveState.DISPATCH)
        self.assertEqual(eng.current_gate, "G2")
        self.assertEqual(eng.last_user_request, "Build the helper")

    def test_rehydrate_falls_back_for_unknown_enum_value(self):
        root = _mk_workspace()
        save_persisted_state(root, {"state": "MADE_UP_STATE"})
        eng = WaveEngine(root)
        self.assertEqual(eng.state, WaveState.ENTRY)

    def test_rehydrate_ignores_unknown_gate(self):
        root = _mk_workspace()
        save_persisted_state(root, {"state": "DISPATCH", "current_gate": "G99"})
        eng = WaveEngine(root)
        # Bad current_gate is ignored; state still rehydrates.
        self.assertEqual(eng.state, WaveState.DISPATCH)
        self.assertIsNone(eng.current_gate)

    def test_named_project_rehydrates_from_its_own_namespace(self):
        root = _mk_workspace()
        save_persisted_state(root, {"state": "DISPATCH", "current_gate": "G1"},
                             project_id="alpha")
        # Default project should NOT see alpha's state.
        default_eng = WaveEngine(root, project_id="default")
        self.assertEqual(default_eng.state, WaveState.ENTRY)
        # Alpha project loads its own state.
        alpha_eng = WaveEngine(root, project_id="alpha")
        self.assertEqual(alpha_eng.state, WaveState.DISPATCH)
        self.assertEqual(alpha_eng.current_gate, "G1")


class EnginePersistTests(unittest.TestCase):
    def test_persist_writes_current_snapshot(self):
        root = _mk_workspace()
        eng = WaveEngine(root)
        eng.transition(WaveState.INSPECT)
        eng.current_gate = "G0"
        eng.last_user_request = "Build a todo app"
        eng.persist()

        on_disk = json.loads(root.joinpath(*STATE_FILE_PATH).read_text())
        self.assertEqual(on_disk["state"], "INSPECT")
        self.assertEqual(on_disk["current_gate"], "G0")
        self.assertEqual(on_disk["last_user_request"], "Build a todo app")
        self.assertEqual(on_disk["version"], 1)

    def test_persist_is_idempotent_across_calls(self):
        root = _mk_workspace()
        eng = WaveEngine(root)
        eng.persist()
        eng.persist()
        # Single line of JSON; no append-style duplication.
        text = root.joinpath(*STATE_FILE_PATH).read_text().strip()
        parsed = json.loads(text)
        self.assertEqual(parsed["state"], "ENTRY")

    def test_round_trip_dispatch_then_rehydrate(self):
        """Mid-wave snapshot — DISPATCH @ G2 — survives a fresh engine."""
        root = _mk_workspace()
        eng = WaveEngine(root)
        # Manually drive to DISPATCH @ G2.
        eng.transition(WaveState.INSPECT)
        eng.transition(WaveState.DECIDE)
        eng.current_gate = "G2"
        eng.transition(WaveState.DISPATCH)
        eng.last_user_request = "Plan the work"
        eng.persist()

        # Fresh engine on the same workspace picks up where we left off.
        eng2 = WaveEngine(root)
        self.assertEqual(eng2.state, WaveState.DISPATCH)
        self.assertEqual(eng2.current_gate, "G2")
        self.assertEqual(eng2.last_user_request, "Plan the work")


if __name__ == "__main__":
    unittest.main()
