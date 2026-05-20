"""test_freeze_consolidation.py — Milestone 2-b / AMD-CORE-107.

Documents the Python side of the freeze-state dual-write contract.

The freeze state lives in two stores that must converge:

  1. Python: a durable freeze record on disk under
     ``.signalos/safety/freeze/<hash>.json`` (the audit-trail source).
  2. Rust:   the ``EnforcementStore.wave_frozen`` mutex (the UI source).

Any user-initiated freeze must touch BOTH. The JS chat layer
(``src/js/ui/chat.js``) is the bridge: when a user types
``/signal-freeze`` it calls the Python CLI AND ``ipc.enforcement.freeze()``.

This test covers the Python half of that contract — that the CLI handlers
actually create / update the freeze record. The Rust mutex half is
verified by ``src/js/ui/__tests__/chat.freeze-consolidation.test.ts``
which asserts the JS layer invokes both IPCs.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.commands.safety import (  # noqa: E402
    cmd_signal_freeze,
    cmd_signal_unfreeze,
)
from signalos_lib.safety import (  # noqa: E402
    FREEZE_DIR_RELATIVE,
    _target_hash,
)


class FreezeConsolidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="signalos-freeze-")
        self.root = Path(self._tmp.name)
        self.target = "src/foo"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _record_path(self) -> Path:
        return self.root / FREEZE_DIR_RELATIVE / f"{_target_hash(self.target)}.json"

    def test_signal_freeze_writes_record(self) -> None:
        """`signalos signal-freeze` writes a freeze record on disk."""
        rc = cmd_signal_freeze(
            [
                self.target,
                "--wave",
                "W14",
                "--note",
                "milestone 2-b dual-write",
                "--repo-root",
                str(self.root),
                "--json",
            ]
        )
        self.assertEqual(rc, 0, "signal-freeze must exit 0 on success")

        rec_path = self._record_path()
        self.assertTrue(
            rec_path.exists(),
            f"freeze record missing at {rec_path}",
        )
        data = json.loads(rec_path.read_text(encoding="utf-8"))
        self.assertEqual(data["target"], self.target)
        self.assertEqual(data["wave"], "W14")
        self.assertEqual(data["status"], "frozen")
        self.assertEqual(data["note"], "milestone 2-b dual-write")
        # ID format is "freeze-NNN" — first freeze in an empty dir = 001.
        self.assertTrue(
            data["id"].startswith("freeze-"),
            f"unexpected freeze id format: {data['id']}",
        )

    def test_signal_unfreeze_updates_record(self) -> None:
        """`signalos signal-unfreeze` flips status from frozen -> unfrozen."""
        # Arrange: freeze first.
        rc = cmd_signal_freeze(
            [
                self.target,
                "--wave",
                "W14",
                "--repo-root",
                str(self.root),
            ]
        )
        self.assertEqual(rc, 0)
        rec_path = self._record_path()
        self.assertTrue(rec_path.exists())
        before = json.loads(rec_path.read_text(encoding="utf-8"))
        self.assertEqual(before["status"], "frozen")

        # Act: unfreeze.
        rc = cmd_signal_unfreeze(
            [
                self.target,
                "--repo-root",
                str(self.root),
            ]
        )
        self.assertEqual(rc, 0, "signal-unfreeze must exit 0 when target was frozen")

        # Assert: the same record now reports status=unfrozen. The JS layer
        # is what additionally flips the Rust mutex; here we only verify
        # the durable record stays in sync.
        after = json.loads(rec_path.read_text(encoding="utf-8"))
        self.assertEqual(after["status"], "unfrozen")
        self.assertEqual(after["target"], self.target)
        # ID should be preserved across the freeze->unfreeze lifecycle.
        self.assertEqual(after["id"], before["id"])

    def test_signal_unfreeze_returns_nonzero_when_not_frozen(self) -> None:
        """Calling unfreeze on a never-frozen target signals failure to the caller.

        This guard matters for the chat dispatcher: if the Python CLI exits
        non-zero we surface the error to the user instead of silently
        flipping the Rust mutex into an inconsistent state.
        """
        rc = cmd_signal_unfreeze(
            [
                self.target,
                "--repo-root",
                str(self.root),
            ]
        )
        self.assertEqual(rc, 1, "unfreezing a non-frozen target must exit 1")
        self.assertFalse(
            self._record_path().exists(),
            "no freeze record should be created by a failed unfreeze",
        )


if __name__ == "__main__":
    unittest.main()
