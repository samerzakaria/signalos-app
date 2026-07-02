"""Governed parallel-executor foundation (Wave 1.1, Phase 0 TaskStore)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.task_store import InMemoryTaskStore, QUEUED, RUNNING, DONE, DEAD


class _Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class TaskStoreTests(unittest.TestCase):
    def test_enqueue_is_idempotent(self):
        store = InMemoryTaskStore()
        store.enqueue("t1", {"x": 1})
        store.enqueue("t1", {"x": 2})  # no-op, no duplicate
        self.assertEqual(store.get("t1").payload, {"x": 1})

    def test_claim_is_atomic_no_double_claim(self):
        store = InMemoryTaskStore()
        store.enqueue("t1")
        first = store.claim("worker-a", lease_ttl=30)
        second = store.claim("worker-b", lease_ttl=30)
        self.assertEqual(first.id, "t1")
        self.assertEqual(first.status, RUNNING)
        self.assertIsNone(second)  # nothing left to claim

    def test_lapsed_lease_is_reclaimable(self):
        clock = _Clock()
        store = InMemoryTaskStore(now=clock)
        store.enqueue("t1")
        store.claim("worker-a", lease_ttl=30)
        clock.advance(31)  # worker died, lease lapsed
        self.assertEqual(store.reclaim_expired(), 1)
        self.assertEqual(store.get("t1").status, QUEUED)
        self.assertIsNotNone(store.claim("worker-b", lease_ttl=30))

    def test_heartbeat_renews_lease(self):
        clock = _Clock()
        store = InMemoryTaskStore(now=clock)
        store.enqueue("t1")
        store.claim("worker-a", lease_ttl=30)
        clock.advance(20)
        self.assertTrue(store.heartbeat("t1", lease_ttl=30))  # extends to now+30
        clock.advance(20)  # past the ORIGINAL lease, within the renewed one
        self.assertEqual(store.reclaim_expired(), 0)  # not reclaimed

    def test_retry_then_dead_letter(self):
        store = InMemoryTaskStore(max_attempts=3)
        store.enqueue("t1")
        store.claim("w", 30)
        store.fail("t1", "boom")  # attempt 1 -> requeued
        self.assertEqual(store.get("t1").status, QUEUED)
        store.claim("w", 30)
        store.fail("t1", "boom")  # attempt 2 -> requeued
        store.claim("w", 30)
        store.fail("t1", "boom")  # attempt 3 -> dead-letter
        self.assertEqual(store.get("t1").status, DEAD)
        self.assertEqual([t.id for t in store.dead_letters()], ["t1"])

    def test_permanent_failure_goes_straight_to_dead_letter(self):
        store = InMemoryTaskStore()
        store.enqueue("t1")
        store.claim("w", 30)
        store.fail("t1", "unrecoverable", retryable=False)
        self.assertEqual(store.get("t1").status, DEAD)

    def test_complete_marks_done_with_result(self):
        store = InMemoryTaskStore()
        store.enqueue("t1")
        store.claim("w", 30)
        store.complete("t1", result={"ok": True})
        self.assertEqual(store.get("t1").status, DONE)
        self.assertEqual(store.get("t1").result, {"ok": True})


if __name__ == "__main__":
    unittest.main()
