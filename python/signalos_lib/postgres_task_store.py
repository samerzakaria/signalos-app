"""Real Postgres-backed TaskStore (Wave 1.1 backend).

Implements the same contract as InMemoryTaskStore against Postgres, using
``SELECT … FOR UPDATE SKIP LOCKED`` for atomic, race-free claims -- the proven
job-queue primitive. Isolated in its own schema so it never touches other tables;
``setup()``/``teardown()`` make it safe to run against a shared instance in tests
(create schema -> test -> drop schema cascade).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import psycopg2
import psycopg2.extras

QUEUED, RUNNING, DONE, DEAD = "queued", "running", "done", "dead"


@dataclass
class StoredTask:
    id: str
    payload: dict[str, Any]
    status: str
    attempts: int
    runtime_id: str = ""
    result: Any = None
    error: str = ""


class PostgresTaskStore:
    def __init__(self, dsn: str, schema: str = "foundry_tasks",
                 table: str = "task_store", max_attempts: int = 3):
        self.dsn = dsn
        self.schema = schema
        self.table = table
        self.max_attempts = max_attempts
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True

    @property
    def _qname(self) -> str:
        return f'"{self.schema}"."{self.table}"'

    def setup(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._qname} (
                    id           TEXT PRIMARY KEY,
                    payload      JSONB NOT NULL DEFAULT '{{}}',
                    status       TEXT NOT NULL DEFAULT 'queued',
                    attempts     INT  NOT NULL DEFAULT 0,
                    lease_until  TIMESTAMPTZ,
                    runtime_id   TEXT NOT NULL DEFAULT '',
                    result       JSONB,
                    error        TEXT NOT NULL DEFAULT '',
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

    def teardown(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # — idempotent enqueue —
    def enqueue(self, task_id: str, payload: dict[str, Any] | None = None) -> str:
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._qname} (id, payload) VALUES (%s, %s) "
                f"ON CONFLICT (id) DO NOTHING",
                (task_id, psycopg2.extras.Json(payload or {})),
            )
        return task_id

    # — atomic claim via SKIP LOCKED —
    def claim(self, runtime_id: str, lease_ttl: float) -> Optional[StoredTask]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self._qname} SET
                    status = 'running',
                    runtime_id = %s,
                    lease_until = now() + make_interval(secs => %s)
                WHERE id = (
                    SELECT id FROM {self._qname}
                    WHERE status = 'queued'
                       OR (status = 'running' AND lease_until <= now())
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, payload, status, attempts, runtime_id
                """,
                (runtime_id, float(lease_ttl)),
            )
            row = cur.fetchone()
        if not row:
            return None
        return StoredTask(id=row[0], payload=row[1], status=row[2],
                          attempts=row[3], runtime_id=row[4])

    def heartbeat(self, task_id: str, lease_ttl: float) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                f"UPDATE {self._qname} SET lease_until = now() + make_interval(secs => %s) "
                f"WHERE id = %s AND status = 'running'",
                (float(lease_ttl), task_id),
            )
            return cur.rowcount > 0

    def complete(self, task_id: str, result: Any = None) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                f"UPDATE {self._qname} SET status='done', result=%s WHERE id=%s",
                (psycopg2.extras.Json(result) if result is not None else None, task_id),
            )

    def fail(self, task_id: str, error: str, retryable: bool = True) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self._qname} SET
                    attempts = attempts + 1,
                    error = %s,
                    status = CASE
                        WHEN %s AND attempts + 1 < %s THEN 'queued'
                        ELSE 'dead' END,
                    runtime_id = CASE
                        WHEN %s AND attempts + 1 < %s THEN '' ELSE runtime_id END,
                    lease_until = NULL
                WHERE id = %s
                """,
                (error, retryable, self.max_attempts, retryable, self.max_attempts, task_id),
            )

    def reclaim_expired(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                f"UPDATE {self._qname} SET status='queued', runtime_id='' "
                f"WHERE status='running' AND lease_until <= now()")
            return cur.rowcount

    def dead_letters(self) -> list[StoredTask]:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT id, payload, status, attempts FROM {self._qname} "
                        f"WHERE status='dead'")
            return [StoredTask(id=r[0], payload=r[1], status=r[2], attempts=r[3])
                    for r in cur.fetchall()]

    def get(self, task_id: str) -> Optional[StoredTask]:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT id, payload, status, attempts, runtime_id, result, error "
                        f"FROM {self._qname} WHERE id=%s", (task_id,))
            r = cur.fetchone()
        if not r:
            return None
        return StoredTask(id=r[0], payload=r[1], status=r[2], attempts=r[3],
                          runtime_id=r[4], result=r[5], error=r[6] or "")
