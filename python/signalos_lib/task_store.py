"""Governed parallel-executor foundation: the TaskStore (Wave 1.1, Phase 0).

The hard part of a parallel agent fleet is not the agent loop -- it is the job
semantics: idempotent enqueue, atomic claim, lease/heartbeat so a dead worker's
task becomes reclaimable, retry with a bounded attempt count, and a dead-letter
state for permanent failures. This module defines that contract and an in-memory
implementation with an injectable clock (so lease expiry is deterministically
testable). The real Postgres `SELECT … FOR UPDATE SKIP LOCKED` store implements
the same interface; the supervisor/worker loop that runs agent CLIs layers on top.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Task lifecycle: queued -> running -> (done | queued-again-on-retry | dead)
QUEUED, RUNNING, DONE, DEAD = "queued", "running", "done", "dead"


@dataclass
class StoredTask:
    id: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = QUEUED
    attempts: int = 0
    lease_until: float = 0.0
    runtime_id: str = ""
    result: Any = None
    error: str = ""


class InMemoryTaskStore:
    """Deterministic, single-process TaskStore. Same contract as the Postgres one."""

    def __init__(self, now: Optional[Callable[[], float]] = None, max_attempts: int = 3):
        self._tasks: dict[str, StoredTask] = {}
        self._now = now or time.monotonic
        self.max_attempts = max_attempts

    # — enqueue: at-least-once + idempotent (same id twice is a no-op) —
    def enqueue(self, task_id: str, payload: dict[str, Any] | None = None) -> str:
        if task_id not in self._tasks:
            self._tasks[task_id] = StoredTask(id=task_id, payload=dict(payload or {}))
        return task_id

    # — claim: atomic; never double-claims; sets a lease deadline —
    def claim(self, runtime_id: str, lease_ttl: float) -> Optional[StoredTask]:
        now = self._now()
        for task in self._tasks.values():
            reclaimable = task.status == RUNNING and task.lease_until <= now
            if task.status == QUEUED or reclaimable:
                task.status = RUNNING
                task.runtime_id = runtime_id
                task.lease_until = now + lease_ttl
                return task
        return None

    # — heartbeat: renew the lease for a long-running task —
    def heartbeat(self, task_id: str, lease_ttl: float) -> bool:
        task = self._tasks.get(task_id)
        if task and task.status == RUNNING:
            task.lease_until = self._now() + lease_ttl
            return True
        return False

    def complete(self, task_id: str, result: Any = None) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = DONE
            task.result = result

    # — fail: transient -> retry (bounded); permanent/exhausted -> dead-letter —
    def fail(self, task_id: str, error: str, retryable: bool = True) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.attempts += 1
        task.error = error
        if retryable and task.attempts < self.max_attempts:
            task.status = QUEUED
            task.runtime_id = ""
            task.lease_until = 0.0
        else:
            task.status = DEAD

    # — reclaim: a lapsed lease (dead worker) makes a task reclaimable —
    def reclaim_expired(self) -> int:
        now = self._now()
        reclaimed = 0
        for task in self._tasks.values():
            if task.status == RUNNING and task.lease_until <= now:
                task.status = QUEUED
                task.runtime_id = ""
                reclaimed += 1
        return reclaimed

    def dead_letters(self) -> list[StoredTask]:
        return [t for t in self._tasks.values() if t.status == DEAD]

    def get(self, task_id: str) -> Optional[StoredTask]:
        return self._tasks.get(task_id)
