# SignalOS Core v2.2 — Parallel Wave Orchestrator (AMD-CORE-008 + AMD-CORE-012).
#
# Orchestrates concurrent execution of PLAN tasks across git worktrees.
# Calls worktree-manager.sh for lifecycle management and dispatches
# run_step() calls concurrently using ThreadPoolExecutor.
#
# Public API:
#   run_wave(wave_id, plan_path, *, session_id, max_concurrent,
#            provider_name, cwd) -> dict


from __future__ import annotations

__all__ = ["run_wave"]  # W-2: explicit public API

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import harness as harness_lib
from .harness import _resolve_provider, LLMProvider
from .status import print_status_card

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT_MARKER = ".signalos"
_WORKTREE_MANAGER = "core/execution/build/worktree-manager.sh"

# AMD-CORE-012: task timeout (T1)
_DEFAULT_TASK_TIMEOUT = 3600  # seconds; override with SIGNALOS_TASK_TIMEOUT_SECS


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / REPO_ROOT_MARKER).is_dir():
            return cand
    raise RuntimeError(
        f"signalos orchestrate: no {REPO_ROOT_MARKER}/ ancestor of {p}. "
        "Run `signalos init` or cd into a repo that already has .signalos/."
    )


def _worktree_manager(root: Path) -> Path:
    return root / _WORKTREE_MANAGER


def _state_file(root: Path) -> Path:
    return root / REPO_ROOT_MARKER / "worktree-state.json"


# ---------------------------------------------------------------------------
# Worktree-manager shell-outs
# ---------------------------------------------------------------------------

def _run_wm(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    wm = _worktree_manager(root)
    if not wm.is_file():
        raise RuntimeError(
            f"signalos orchestrate: worktree-manager.sh not found at {wm}"
        )
    # Pass script as relative-from-root POSIX path with cwd=str(root).
    # On Windows, subprocess.run(["bash", ...]) resolves to WSL bash
    # (C:\Windows\System32\bash.exe wins via CreateProcess System32
    # priority over user PATH). WSL bash does not understand drive-
    # letter paths (C:/Users/...); it expects /mnt/c/Users/... A
    # relative path against the cwd argument works for both WSL bash
    # and git-bash because Python translates cwd correctly when
    # launching the child process.
    cmd = ["bash", wm.relative_to(root).as_posix()] + list(args)
    return subprocess.run(cmd, cwd=str(root), check=check)


# ---------------------------------------------------------------------------
# Task list from state file
# ---------------------------------------------------------------------------

def _read_tasks(root: Path) -> list[dict[str, Any]]:
    sf = _state_file(root)
    if not sf.is_file():
        return []
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
        return data.get("worktrees", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Step execution for a single worktree task
# ---------------------------------------------------------------------------

def _execute_task(
    task: dict[str, Any],
    root: Path,
    session_id: str,
    provider: LLMProvider,
    model: str,
    status_callback: Any,
) -> dict[str, Any]:
    """Execute one task via run_step and return the result dict.

    Called from a ThreadPoolExecutor worker. Handles T2 pauses by
    catching the pause condition and returning early with status='paused'.
    """
    task_id = task.get("task") or task.get("branch", "unknown")
    branch  = task.get("branch", "")
    step_id = task.get("step_id") or branch or f"task-{task_id}"

    # Build a minimal prompt for the task
    prompt = (
        f"Execute task '{task_id}' in branch '{branch}' for wave {task.get('wave', '?')}. "
        f"This is a parallel worktree task. Complete the implementation and report status."
    )

    try:
        result = harness_lib.run_step(
            step_id=step_id,
            prompt=prompt,
            model=model,
            session_id=session_id,
            cwd=root,
            intent=f"orchestrated wave task: {task_id}",
            provider=provider,
        )
    except Exception as exc:
        result = {
            "step_id": step_id,
            "status": "failed",
            "failure": str(exc),
            "exit_code": 2,
        }

    # Notify status card
    try:
        status_callback(root)
    except Exception:
        pass  # status card failures are non-fatal

    return {**task, "result": result, "step_id": step_id}



# ---------------------------------------------------------------------------
# DAG: dependency-aware task ordering (AMD-CORE-012 T2)
# ---------------------------------------------------------------------------

def _parse_dag(plan_path: Path) -> dict[str, list[str]]:
    """Return {task_id: [dep_task_id, ...]} parsed from PLAN.md comments.

    Looks for lines of the form:
        # depends_on: T3, T5
    immediately below a task heading of the form:
        ## T4  or  - [ ] T4
    Returns an empty dict when PLAN.md is absent or has no depends_on lines.
    """
    if not plan_path.is_file():
        return {}
    deps: dict[str, list[str]] = {}
    current: str | None = None
    heading_re = re.compile(r"^(?:#{1,3}|-\s+\[.\])\s+(T\d+)", re.IGNORECASE)
    dep_re = re.compile(r"depends_on:\s*(.*)", re.IGNORECASE)
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        m = heading_re.match(line.strip())
        if m:
            current = m.group(1)
            continue
        if current:
            dm = dep_re.search(line)
            if dm:
                raw = dm.group(1)
                deps[current] = [t.strip() for t in raw.split(",") if t.strip()]
            elif line.strip() and not line.strip().startswith("#"):
                current = None  # end of task block
    return deps


def _topological_sort(
    tasks: list[dict], dag: dict[str, list[str]]
) -> list[list[dict]]:
    """Return tasks grouped into ordered waves respecting dag dependencies.

    Each inner list is a 'level' whose tasks can run concurrently.
    Tasks with no deps land in level 0; tasks whose all deps are in earlier
    levels land in the next level. Tasks not mentioned in the dag are treated
    as having no deps.
    """
    task_ids = {t.get("task") or t.get("branch", f"t{i}"): t for i, t in enumerate(tasks)}
    # Build level map
    level: dict[str, int] = {}

    def _depth(tid: str, visiting: set[str]) -> int:
        if tid in level:
            return level[tid]
        if tid in visiting:
            return 0  # cycle → treat as no dep
        visiting = visiting | {tid}
        parents = dag.get(tid, [])
        d = max((_depth(p, visiting) + 1 for p in parents if p in task_ids), default=0)
        level[tid] = d
        return d

    for tid in task_ids:
        _depth(tid, set())

    max_level = max(level.values(), default=0)
    waves: list[list[dict]] = [[] for _ in range(max_level + 1)]
    for tid, task in task_ids.items():
        waves[level.get(tid, 0)].append(task)
    return [w for w in waves if w]


# ---------------------------------------------------------------------------
# Main orchestration entry point
# ---------------------------------------------------------------------------

def run_wave(
    wave_id: str,
    plan_path: str,
    *,
    session_id: str | None = None,
    max_concurrent: int = 5,
    provider_name: str | None = None,
    cwd: Path | None = None,
    model: str = harness_lib.DEFAULT_MODEL,
) -> dict[str, Any]:
    """Orchestrate parallel execution of all tasks in a Wave.

    1. Calls worktree-manager.sh create --wave <id> --plan <path>
    2. Reads .signalos/worktree-state.json for the task list
    3. Dispatches run_step() calls concurrently (up to max_concurrent)
    4. Prints the status card after each task state change
    5. Calls worktree-manager.sh reconcile then retire after all tasks
    6. Returns a summary dict with per-task results

    Returns dict keys:
        wave_id, session_id, tasks, completed, failed, paused,
        elapsed_ms, status ("all_completed"|"some_failed"|"empty")
    """
    root = _repo_root(cwd)

    # Resolve provider
    provider = _resolve_provider(provider_name)

    # Ensure session
    if not session_id:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        session_id = f"orchestrate-{wave_id}-{ts}"

    sys.stdout.write(
        f"[orchestrate] Wave {wave_id} · plan={plan_path} · "
        f"max_concurrent={max_concurrent} · session={session_id}\n"
    )

    # Step 1: Create worktrees
    sys.stdout.write(f"[orchestrate] Creating worktrees...\n")
    proc = _run_wm(root, "create", "--wave", wave_id, "--plan", plan_path)
    if proc.returncode != 0:
        return {
            "wave_id": wave_id,
            "session_id": session_id,
            "status": "worktree_create_failed",
            "tasks": [],
            "completed": 0,
            "failed": 1,
            "paused": 0,
            "elapsed_ms": 0,
        }

    # Step 2: Read task list
    tasks = _read_tasks(root)
    if not tasks:
        sys.stdout.write("[orchestrate] No tasks found in worktree-state.json\n")
        print_status_card(root)
        return {
            "wave_id": wave_id,
            "session_id": session_id,
            "status": "empty",
            "tasks": [],
            "completed": 0,
            "failed": 0,
            "paused": 0,
            "elapsed_ms": 0,
        }

    # Filter to only tasks for this wave
    wave_tasks = [t for t in tasks if str(t.get("wave", "")) == str(wave_id)]
    if not wave_tasks:
        wave_tasks = tasks  # use all if no wave filter matches

    sys.stdout.write(f"[orchestrate] Dispatching {len(wave_tasks)} task(s)...\n")
    print_status_card(root)

    # Step 3: Dispatch concurrently with DAG ordering + timeout (AMD-CORE-012 T1/T2/T3)
    task_timeout = float(os.environ.get("SIGNALOS_TASK_TIMEOUT_SECS", _DEFAULT_TASK_TIMEOUT))
    plan_path = Path(plan_path)
    dag = _parse_dag(plan_path)
    ordered_levels = _topological_sort(wave_tasks, dag) if dag else [wave_tasks]

    t0 = time.perf_counter()
    task_results: list[dict[str, Any]] = []
    paused_tasks: list[str] = []
    aborted_task_ids: set[str] = set()

    def _audit_abort(step_id: str, cause: str) -> None:
        """Append step.aborted event to AUDIT_TRAIL.jsonl."""
        trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
        try:
            trail.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "step.aborted",
                "step_id": step_id,
                "cause": cause,
            }
            with trail.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    for level_idx, level_tasks in enumerate(ordered_levels):
        # Skip tasks whose deps failed/aborted
        runnable = []
        for task in level_tasks:
            tid = task.get("task") or task.get("branch", "")
            deps = dag.get(tid, [])
            blocked_by = [d for d in deps if d in aborted_task_ids]
            if blocked_by:
                sid = task.get("step_id") or tid
                sys.stdout.write(
                    f"[orchestrate] Task {sid} ABORTED: peer failure in {blocked_by}\n"
                )
                _audit_abort(sid, f"peer_failed:{','.join(blocked_by)}")
                aborted_task_ids.add(tid)
                task_results.append({
                    **task,
                    "step_id": sid,
                    "result": {"status": "aborted", "cause": "peer_failed"},
                })
            else:
                runnable.append(task)

        if not runnable:
            continue

        sys.stdout.write(
            f"[orchestrate] Level {level_idx}: dispatching {len(runnable)} task(s)...\n"
        )
        with ThreadPoolExecutor(max_workers=min(max_concurrent, len(runnable))) as pool:
            futures = {
                pool.submit(
                    _execute_task,
                    task, root, session_id, provider, model,
                    lambda r: print_status_card(r),
                ): task
                for task in runnable
            }
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=task_timeout, return_when=FIRST_COMPLETED)
                if not done:
                    # Timeout — cancel remaining, record failures
                    for fut in pending:
                        fut.cancel()
                        task = futures[fut]
                        step_id = task.get("step_id") or task.get("task") or task.get("branch", "unknown")
                        tid = task.get("task") or task.get("branch", step_id)
                        sys.stdout.write(
                            f"[orchestrate] Task {step_id} TIMED OUT after {task_timeout}s\n"
                        )
                        _audit_abort(step_id, "task_timeout")
                        aborted_task_ids.add(tid)
                        entry = {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "event": "step.failed",
                            "step_id": step_id,
                            "reason": "task_timeout",
                            "timeout_secs": task_timeout,
                        }
                        trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
                        try:
                            trail.parent.mkdir(parents=True, exist_ok=True)
                            with trail.open("a", encoding="utf-8") as fh:
                                fh.write(json.dumps(entry) + "\n")
                        except OSError:
                            pass
                        task_results.append({
                            **task,
                            "step_id": step_id,
                            "result": {"status": "failed", "reason": "task_timeout"},
                        })
                    pending = set()
                    break
                for future in done:
                    try:
                        result = future.result()
                        task_results.append(result)
                        step_result = result.get("result", {})
                        status = step_result.get("status")
                        step_id = result.get("step_id", "unknown")
                        if status == "paused":
                            paused_tasks.append(step_id)
                            sys.stdout.write(
                                f"[orchestrate] Task {step_id} is PAUSED (T2). "
                                f"Resume with: signalos pause resume {step_id}\n"
                            )
                        elif status in ("failed", "aborted"):
                            # T3: mark for downstream cancellation
                            tid = futures[future].get("task") or futures[future].get("branch", step_id)
                            aborted_task_ids.add(tid)
                    except Exception as exc:
                        task_results.append({
                            "result": {"status": "failed", "failure": str(exc)},
                            "step_id": "unknown",
                        })

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # Step 4: Print final status card
    print_status_card(root)

    # Step 5: Reconcile + retire
    sys.stdout.write("[orchestrate] Reconciling worktrees...\n")
    _run_wm(root, "reconcile", "--wave", wave_id)
    sys.stdout.write("[orchestrate] Retiring merged worktrees...\n")
    _run_wm(root, "retire", "--wave", wave_id)

    # Step 6: Summarise
    completed = sum(
        1 for r in task_results
        if r.get("result", {}).get("status") == "completed"
    )
    failed = sum(
        1 for r in task_results
        if r.get("result", {}).get("status") in {"failed", "aborted"}
    )
    paused = sum(
        1 for r in task_results
        if r.get("result", {}).get("status") == "paused"
    )

    overall_status = "all_completed" if failed == 0 and paused == 0 else "some_failed"

    # Print pending T2 resumes if any
    if paused_tasks:
        sys.stdout.write("\n[orchestrate] Pending T2 resumes needed:\n")
        for sid in paused_tasks:
            sys.stdout.write(f"  PE → signalos pause resume {sid}\n")

    return {
        "wave_id": wave_id,
        "session_id": session_id,
        "status": overall_status,
        "tasks": task_results,
        "completed": completed,
        "failed": failed,
        "paused": paused,
        "elapsed_ms": elapsed_ms,
    }
