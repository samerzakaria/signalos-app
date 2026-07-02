"""Real Postgres TaskStore integration test (Wave 1.1 backend).

Runs against FOUNDRY_PG_DSN (from .env) in a THROWAWAY schema that is dropped at
the end -- so it is isolated from any other tables and leaves no trace. Skips
automatically if no DSN is set or the server can't be reached.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
_DSN = os.environ.get("FOUNDRY_PG_DSN")


def _reachable(dsn: str) -> bool:
    try:
        import psycopg2
        psycopg2.connect(dsn, connect_timeout=4).close()
        return True
    except Exception:
        return False


@unittest.skipUnless(_DSN and _reachable(_DSN), "no reachable FOUNDRY_PG_DSN")
class PostgresTaskStoreTests(unittest.TestCase):
    def setUp(self):
        from signalos_lib.postgres_task_store import PostgresTaskStore
        # unique throwaway schema so parallel runs never collide
        self.schema = "foundry_test_tmp"
        self.store = PostgresTaskStore(_DSN, schema=self.schema, max_attempts=3)
        self.store.teardown()  # clean any leftover
        self.store.setup()

    def tearDown(self):
        try:
            self.store.teardown()  # drop the throwaway schema — no trace
        finally:
            self.store.close()

    def test_enqueue_idempotent(self):
        self.store.enqueue("t1", {"x": 1})
        self.store.enqueue("t1", {"x": 2})  # no-op
        self.assertEqual(self.store.get("t1").payload, {"x": 1})

    def test_skip_locked_atomic_claim(self):
        self.store.enqueue("t1")
        first = self.store.claim("worker-a", 30)
        second = self.store.claim("worker-b", 30)
        self.assertEqual(first.id, "t1")
        self.assertIsNone(second)  # SKIP LOCKED -> no double-claim

    def test_expired_lease_reclaimed(self):
        self.store.enqueue("t1")
        self.store.claim("worker-a", -5)  # lease already in the past
        self.assertEqual(self.store.reclaim_expired(), 1)
        self.assertIsNotNone(self.store.claim("worker-b", 30))

    def test_heartbeat_prevents_reclaim(self):
        self.store.enqueue("t1")
        self.store.claim("worker-a", 30)
        self.assertTrue(self.store.heartbeat("t1", 60))
        self.assertEqual(self.store.reclaim_expired(), 0)

    def test_retry_then_dead_letter(self):
        self.store.enqueue("t1")
        for _ in range(3):
            self.store.claim("w", 30)
            self.store.fail("t1", "boom")
        self.assertEqual(self.store.get("t1").status, "dead")
        self.assertEqual([t.id for t in self.store.dead_letters()], ["t1"])

    def test_complete(self):
        self.store.enqueue("t1")
        self.store.claim("w", 30)
        self.store.complete("t1", {"ok": True})
        self.assertEqual(self.store.get("t1").status, "done")


if __name__ == "__main__":
    unittest.main()
