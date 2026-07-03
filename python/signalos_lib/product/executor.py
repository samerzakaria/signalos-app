# signalos_lib/product/executor.py
# Live parallel executor (Wave 1.1): the supervisor/worker loop over a
# TaskStore that the plan flagged as missing -- claim, lease, heartbeat,
# retry+backoff, dead-letter, worktree isolation, merge-queue, all real.
#
# task_store.py / postgres_task_store.py define job semantics only. This
# module is the loop that actually drains a store: N worker threads claim
# tasks, run them inside an isolated git worktree so concurrent writers
# never touch the same working tree, heartbeat while running, and complete
# or fail through the store's retry -> dead-letter contract. Successful
# worktree branches are merged back through a single-threaded merge queue
# so merges never race each other.

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = [
    "TaskOutcome",
    "ExecutionReport",
    "NonRetryableTaskError",
    "run_worker_pool",
    "run_isolated_build_tasks",
]

DEFAULT_LEASE_TTL = 30.0
DEFAULT_HEARTBEAT_INTERVAL = 5.0


class NonRetryableTaskError(RuntimeError):
    """Raise from a run_task callable for failures that will never succeed
    on retry (e.g. a merge conflict) -- sends the task straight to the
    dead letter without burning retry attempts."""


@dataclass
class TaskOutcome:
    task_id: str
    status: str  # "done" | "queued" (requeued for retry) | "dead"
    result: Any = None
    error: str = ""


@dataclass
class ExecutionReport:
    outcomes: list[TaskOutcome] = field(default_factory=list)
    merged_task_ids: list[str] = field(default_factory=list)
    dead_letters: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> list[TaskOutcome]:
        return [o for o in self.outcomes if o.status == "done"]


# ---------------------------------------------------------------------------
# Generic supervisor/worker loop
# ---------------------------------------------------------------------------

def run_worker_pool(
    task_store: Any,
    run_task: Callable[[Any], Any],
    *,
    max_workers: int = 3,
    lease_ttl: float = DEFAULT_LEASE_TTL,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    on_task_done: Optional[Callable[[TaskOutcome], None]] = None,
) -> ExecutionReport:
    """Drain *task_store* with up to *max_workers* concurrent claimers.

    Each worker repeatedly claims a task, starts a background heartbeat
    thread so a slow task's lease never lapses while it's genuinely
    running, executes ``run_task(task)``, and completes or fails the task
    through the store. Retryable failures are requeued by the store itself
    (bounded by its own max_attempts) and will be re-claimed by any free
    worker; exhausted/non-retryable failures land in the dead letter.

    Stops once every worker simultaneously finds nothing claimable and
    nothing in flight -- i.e. the store is drained.
    """
    report = ExecutionReport()
    lock = threading.Lock()
    in_flight = 0

    def worker(worker_id: str) -> None:
        nonlocal in_flight
        idle_polls = 0
        while True:
            task = task_store.claim(worker_id, lease_ttl)
            if task is None:
                with lock:
                    still_running = in_flight > 0
                if not still_running:
                    return
                idle_polls += 1
                time.sleep(min(0.05 * idle_polls, 0.25))
                continue
            idle_polls = 0
            with lock:
                in_flight += 1

            hb_stop = threading.Event()

            def heartbeat_loop(task_id: str = task.id, stop_evt: threading.Event = hb_stop) -> None:
                while not stop_evt.wait(heartbeat_interval):
                    task_store.heartbeat(task_id, lease_ttl)

            hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
            hb_thread.start()
            try:
                result = run_task(task)
                task_store.complete(task.id, result)
                outcome = TaskOutcome(task.id, "done", result=result)
            except NonRetryableTaskError as exc:
                task_store.fail(task.id, str(exc), retryable=False)
                outcome = TaskOutcome(task.id, "dead", error=str(exc))
            except Exception as exc:
                task_store.fail(task.id, str(exc), retryable=True)
                refreshed = task_store.get(task.id)
                status = refreshed.status if refreshed is not None else "dead"
                outcome = TaskOutcome(task.id, status, error=str(exc))
            finally:
                hb_stop.set()
                hb_thread.join(timeout=heartbeat_interval)

            with lock:
                in_flight -= 1
                if outcome.status in ("done", "dead"):
                    report.outcomes.append(outcome)
                    if outcome.status == "dead":
                        report.dead_letters.append(task.id)
                    if on_task_done is not None:
                        on_task_done(outcome)

    workers = [
        threading.Thread(target=worker, args=(f"worker-{i}",), daemon=True)
        for i in range(max(1, max_workers))
    ]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    return report


# ---------------------------------------------------------------------------
# Git worktree isolation + merge queue, for build-agent tasks specifically
# ---------------------------------------------------------------------------

# Explicit identity + no-sign so the executor's commits/merges are
# self-sufficient: a freshly `git init`ed product repo has no user.name/
# user.email, and CI runners have no global git identity either, so a bare
# `git commit` fails there ("Please tell me who you are"). Passing these as
# per-invocation `-c` flags avoids depending on ambient config or mutating
# the repo's own config. commit.gpgsign=false prevents a signing hang/failure
# on machines with a global signing config.
_GIT_IDENTITY = (
    "-c", "user.email=executor@signalos.local",
    "-c", "user.name=SignalOS Executor",
    "-c", "commit.gpgsign=false",
)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *_GIT_IDENTITY, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _current_branch(repo_root: Path) -> str:
    # git rev-parse --abbrev-ref HEAD needs a real commit to resolve; a
    # freshly-scaffolded product repo (git init, no commit yet) fails it
    # with "ambiguous argument HEAD". symbolic-ref reads the branch name
    # HEAD points to regardless of whether it has been born yet.
    return _git(repo_root, "symbolic-ref", "--short", "HEAD")


def _ensure_has_commit(repo_root: Path) -> None:
    """git worktree add requires a real commit to fork from -- it rejects
    an unborn branch outright ("invalid reference"), not just a naming
    issue. A freshly-scaffolded product repo (git init, nothing committed
    yet) is exactly this state at the point generation runs, so make this
    a real precondition instead of a caller obligation: commit whatever is
    already on disk, or an empty commit if there's genuinely nothing yet."""
    try:
        _git(repo_root, "rev-parse", "HEAD")
        return
    except RuntimeError:
        pass
    _git(repo_root, "add", "-A")
    try:
        _git(repo_root, "commit", "--quiet", "-m", "executor: initial commit")
    except RuntimeError:
        _git(repo_root, "commit", "--quiet", "--allow-empty", "-m", "executor: initial commit")


def run_isolated_build_tasks(
    repo_root: Path,
    packets: list[dict],
    *,
    dispatch: Optional[Callable[[Path, dict], dict]] = None,
    task_store: Any = None,
    base_branch: Optional[str] = None,
    max_workers: int = 3,
) -> ExecutionReport:
    """Run independent build-agent packets in parallel, one git worktree per
    task, then serialize the merges back onto *base_branch*.

    Each entry in *packets* is a full generation packet (as produced by
    ``build_agent_packet``/``prepare_generation``) plus a ``task_id`` key.
    Callers are responsible for ensuring packets are file-disjoint -- this
    function isolates the working tree per task but does not itself infer
    independence; two packets that write the same path will conflict at
    merge time and the conflicting task is dead-lettered (non-retryable),
    not silently dropped.

    *dispatch* defaults to the deterministic local build agent
    (``dispatch_local_build_agent``) -- no external LLM key required, which
    is what makes this safe to exercise as a real, repeatable integration
    test.
    """
    from ..task_store import InMemoryTaskStore
    from .agent_dispatch import dispatch_local_build_agent

    _ensure_has_commit(repo_root)
    dispatch = dispatch or dispatch_local_build_agent
    store = task_store or InMemoryTaskStore()
    for packet in packets:
        task_id = packet["task_id"]
        store.enqueue(task_id, {"packet": packet})

    base_branch = base_branch or _current_branch(repo_root)
    merge_lock = threading.Lock()
    worktrees_root = repo_root / ".signalos" / "product" / "worktrees"

    def run_one(stored_task: Any) -> dict:
        payload = stored_task.payload
        packet = payload["packet"]
        task_id = stored_task.id
        branch = f"executor/{task_id}-{uuid.uuid4().hex[:8]}"
        worktree_dir = worktrees_root / f"{task_id}-{uuid.uuid4().hex[:8]}"
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        _git(repo_root, "worktree", "add", "-b", branch, str(worktree_dir), base_branch)
        try:
            result = dispatch(worktree_dir, packet)
            if result.get("status") != "completed":
                raise RuntimeError(
                    "; ".join(result.get("errors", [])) or f"task {task_id} did not complete"
                )
            # Exclude .signalos/ -- it's SignalOS's own internal bookkeeping
            # for this task (e.g. RESULT.json), not product source. Merging
            # it in would leave one agent-runs/ entry per sub-task instead
            # of the single one callers expect for the whole delivery.
            _git(worktree_dir, "add", "-A", "--", ".", ":!.signalos")
            _git(worktree_dir, "commit", "--quiet", "-m", f"executor: {task_id}")
            with merge_lock:
                try:
                    _git(repo_root, "merge", "--no-ff", "--quiet", branch, "-m", f"executor merge: {task_id}")
                except RuntimeError as merge_exc:
                    _git(repo_root, "merge", "--abort")
                    raise NonRetryableTaskError(f"merge conflict for {task_id}: {merge_exc}") from merge_exc
            return result
        finally:
            _git(repo_root, "worktree", "remove", "--force", str(worktree_dir))
            try:
                _git(repo_root, "branch", "-D", branch)
            except RuntimeError:
                pass

    report = run_worker_pool(store, run_one, max_workers=max_workers)
    report.merged_task_ids = [o.task_id for o in report.outcomes if o.status == "done"]
    return report
