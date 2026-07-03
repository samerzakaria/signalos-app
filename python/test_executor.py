# python/test_executor.py
# Wave 1.1: the live supervisor/worker loop over TaskStore. Proves the
# components the dev-review named explicitly -- claim, lease, heartbeat,
# retry+backoff, dead-letter, worktree isolation, merge-queue -- for real,
# not just at the TaskStore-contract level (see test_task_store.py).

from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.executor import (
    ExecutionReport,
    NonRetryableTaskError,
    run_isolated_build_tasks,
    run_worker_pool,
)
from signalos_lib.task_store import InMemoryTaskStore


class _SpyStore:
    """Wraps InMemoryTaskStore to record heartbeat calls without changing behavior."""

    def __init__(self, inner: InMemoryTaskStore) -> None:
        self._inner = inner
        self.heartbeat_calls: list[str] = []

    def enqueue(self, *a, **k):
        return self._inner.enqueue(*a, **k)

    def claim(self, *a, **k):
        return self._inner.claim(*a, **k)

    def heartbeat(self, task_id, lease_ttl):
        self.heartbeat_calls.append(task_id)
        return self._inner.heartbeat(task_id, lease_ttl)

    def complete(self, *a, **k):
        return self._inner.complete(*a, **k)

    def fail(self, *a, **k):
        return self._inner.fail(*a, **k)

    def get(self, *a, **k):
        return self._inner.get(*a, **k)


class TestWorkerPoolDraining(unittest.TestCase):
    def test_drains_all_queued_tasks(self) -> None:
        store = InMemoryTaskStore()
        for i in range(6):
            store.enqueue(f"t-{i}")

        seen: list[str] = []
        lock = threading.Lock()

        def run_task(task):
            time.sleep(0.02)
            with lock:
                seen.append(task.id)
            return "ok"

        report = run_worker_pool(store, run_task, max_workers=3)
        self.assertEqual(len(report.succeeded), 6)
        self.assertEqual(sorted(seen), sorted(f"t-{i}" for i in range(6)))

    def test_never_double_claims_a_task(self) -> None:
        store = InMemoryTaskStore()
        for i in range(20):
            store.enqueue(f"t-{i}")

        counts: dict[str, int] = {}
        lock = threading.Lock()

        def run_task(task):
            with lock:
                counts[task.id] = counts.get(task.id, 0) + 1
            time.sleep(0.01)
            return "ok"

        report = run_worker_pool(store, run_task, max_workers=5)
        self.assertEqual(len(report.succeeded), 20)
        self.assertTrue(all(n == 1 for n in counts.values()), counts)

    def test_runs_genuinely_concurrently(self) -> None:
        store = InMemoryTaskStore()
        for i in range(6):
            store.enqueue(f"t-{i}")

        concurrent = {"current": 0, "max": 0}
        lock = threading.Lock()

        def run_task(task):
            with lock:
                concurrent["current"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["current"])
            time.sleep(0.08)
            with lock:
                concurrent["current"] -= 1
            return "ok"

        run_worker_pool(store, run_task, max_workers=3)
        self.assertGreater(concurrent["max"], 1, "tasks ran fully sequentially")


class TestRetryAndDeadLetter(unittest.TestCase):
    def test_retries_a_transient_failure_then_succeeds(self) -> None:
        store = InMemoryTaskStore(max_attempts=3)
        store.enqueue("flaky")
        attempts = {"n": 0}

        def run_task(task):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("transient")
            return "ok"

        report = run_worker_pool(store, run_task, max_workers=1)
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(len(report.succeeded), 1)
        self.assertFalse(report.dead_letters)

    def test_dead_letters_after_exhausting_retries(self) -> None:
        store = InMemoryTaskStore(max_attempts=2)
        store.enqueue("always-fails")

        def run_task(task):
            raise RuntimeError("permanent-ish")

        report = run_worker_pool(store, run_task, max_workers=1)
        self.assertEqual(report.dead_letters, ["always-fails"])
        dead = store.dead_letters()
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0].attempts, 2)

    def test_non_retryable_error_dead_letters_on_first_attempt(self) -> None:
        store = InMemoryTaskStore(max_attempts=5)
        store.enqueue("doomed")
        attempts = {"n": 0}

        def run_task(task):
            attempts["n"] += 1
            raise NonRetryableTaskError("will never work")

        report = run_worker_pool(store, run_task, max_workers=1)
        self.assertEqual(attempts["n"], 1)
        self.assertEqual(report.dead_letters, ["doomed"])


class TestHeartbeat(unittest.TestCase):
    def test_renewed_lease_survives_expiry_check(self) -> None:
        clock = {"t": 0.0}
        store = InMemoryTaskStore(now=lambda: clock["t"])
        store.enqueue("slow-1")

        def run_task(task):
            clock["t"] += 10.0  # advance well past the lease TTL below
            self.assertTrue(store.heartbeat(task.id, lease_ttl=5.0))
            self.assertEqual(store.reclaim_expired(), 0)
            return "ok"

        report = run_worker_pool(store, run_task, max_workers=1, lease_ttl=5.0, heartbeat_interval=100)
        self.assertEqual(len(report.succeeded), 1)

    def test_background_heartbeat_fires_for_a_slow_task(self) -> None:
        spy = _SpyStore(InMemoryTaskStore())
        spy.enqueue("slow-1")

        def run_task(task):
            time.sleep(0.25)
            return "ok"

        report = run_worker_pool(spy, run_task, max_workers=1, lease_ttl=30, heartbeat_interval=0.05)
        self.assertEqual(len(report.succeeded), 1)
        self.assertGreaterEqual(len(spy.heartbeat_calls), 3, spy.heartbeat_calls)


# ---------------------------------------------------------------------------
# Real git worktree isolation + merge queue
# ---------------------------------------------------------------------------

def _run_git(cwd: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "executor-test@example.com")
    _run_git(repo, "config", "user.name", "Executor Test")
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "init")
    return repo


def _make_packet(task_id: str, filename: str, entity: str) -> dict:
    return {
        "task_id": task_id,
        "run_id": task_id,
        "generation": {
            "profile": "generic",
            "file_specs": [
                {
                    "path": f"src/{filename}.py",
                    "kind": "source",
                    "entity": entity,
                    "description": "Fields: id, name.",
                },
                {
                    "path": f"tests/test_{filename}.py",
                    "kind": "test",
                    "entity": entity,
                    "description": "",
                },
            ],
            "allowed_paths": [f"src/{filename}.py", f"tests/test_{filename}.py"],
            "forbidden_paths": [],
        },
    }


class TestIsolatedBuildTasks(unittest.TestCase):
    def test_independent_tasks_build_in_parallel_and_merge_cleanly(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            packets = [
                _make_packet("task-a", "alpha", "Alpha"),
                _make_packet("task-b", "beta", "Beta"),
            ]

            report = run_isolated_build_tasks(repo, packets, max_workers=2)

            self.assertEqual(len(report.succeeded), 2)
            self.assertFalse(report.dead_letters)
            self.assertTrue((repo / "src" / "alpha.py").exists())
            self.assertTrue((repo / "src" / "beta.py").exists())
            self.assertTrue((repo / "tests" / "test_alpha.py").exists())
            self.assertTrue((repo / "tests" / "test_beta.py").exists())

            worktrees_dir = repo / ".signalos" / "product" / "worktrees"
            leftover = list(worktrees_dir.glob("*")) if worktrees_dir.exists() else []
            self.assertEqual(leftover, [], "worker worktrees were not cleaned up")

    def test_conflicting_tasks_dead_letter_the_loser_not_both(self) -> None:
        import tempfile

        from signalos_lib.product.agent_dispatch import dispatch_local_build_agent

        def slow_dispatch(worktree_dir, packet):
            # Widen the race window so both worktrees fork before either merges,
            # which is what makes the conflict real instead of a clean rebase.
            time.sleep(0.15)
            return dispatch_local_build_agent(worktree_dir, packet)

        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            packets = [
                _make_packet("task-a", "dup", "Alpha"),
                _make_packet("task-b", "dup", "Beta"),
            ]

            report = run_isolated_build_tasks(
                repo, packets, dispatch=slow_dispatch, max_workers=2,
            )

            self.assertEqual(len(report.succeeded), 1, report.outcomes)
            self.assertEqual(len(report.dead_letters), 1, report.outcomes)
            self.assertEqual(set(report.dead_letters) | {o.task_id for o in report.succeeded},
                              {"task-a", "task-b"})
            # the repo must be left clean, not mid-conflict
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=str(repo),
                capture_output=True, text=True,
            ).stdout
            self.assertEqual(status.strip(), "")


if __name__ == "__main__":
    unittest.main()
