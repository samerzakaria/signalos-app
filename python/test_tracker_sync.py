"""Headless bidirectional tracker sync (Wave 1.7)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.plan import PlanDoc, Task
from signalos_lib.product import tracker_sync as ts

_ID_A = "A" * 26
_ID_B = "B" * 26


def _doc() -> PlanDoc:
    return PlanDoc(wave="W1", tasks=[
        Task(id=_ID_A, title="Story A", status="pending", tier="T3", epic="E1"),
        Task(id=_ID_B, title="Story B", status="in_progress", tier="T3", epic="E1"),
    ])


class TrackerSyncTests(unittest.TestCase):
    def test_push_creates_issues_and_mapping(self):
        tracker = ts.InMemoryTracker()
        mapping = ts.push_plan(_doc(), tracker)
        self.assertEqual(set(mapping), {_ID_A, _ID_B})
        self.assertEqual(len(tracker.issues), 2)

    def test_push_is_idempotent(self):
        tracker = ts.InMemoryTracker()
        doc = _doc()
        m1 = ts.push_plan(doc, tracker)
        m2 = ts.push_plan(doc, tracker, m1)
        self.assertEqual(m1, m2)              # same external ids
        self.assertEqual(len(tracker.issues), 2)  # no duplicates

    def test_pull_reflects_external_status_change(self):
        tracker = ts.InMemoryTracker()
        mapping = ts.push_plan(_doc(), tracker)
        # a collaborator moves Story A to "done" in the external tracker
        tracker.set_status(mapping[_ID_A], "done")
        statuses = ts.pull_statuses(tracker, mapping)
        self.assertEqual(statuses[_ID_A], "done")
        self.assertEqual(statuses[_ID_B], "in_progress")

    def test_in_memory_tracker_satisfies_the_protocol(self):
        self.assertIsInstance(ts.InMemoryTracker(), ts.TrackerAdapter)


if __name__ == "__main__":
    unittest.main()
