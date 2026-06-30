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
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
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
# Progress emission for the desktop UI
# ---------------------------------------------------------------------------
#
# When this module is invoked via signalos_ipc_server's run_core_cli, stdout
# is captured into a StringIO buffer and returned as the command output.
# That means normal `sys.stdout.write` does NOT reach the Rust-side parser
# which only sees sidecar process stdout.
#
# To stream per-task progress to the UI in real time, we write directly to
# `sys.__stdout__` (the original process stdout, bypassing redirect_stdout).
# Rust's CommandEvent::Stdout handler parses each line and forwards
# `kind: "progress"` JSON as a `sidecar:progress` Tauri event, which the
# frontend already listens for (services/orchestratorEvents.ts).
#
# Existing PhaseContract events from signalos_ipc_server.py use the same
# JSON shape, so the frontend has one event handler for everything.

def _emit_task_progress(wave_id: str, task_id: str, state: str, detail: str | None = None) -> None:
    """Emit a per-task progress event to the real process stdout.

    Frontend correlates these to plan card tasks via phase="orchestrate"
    and substep=task_id.
    """
    payload = {
        "id": f"orchestrate-{wave_id}",
        "kind": "progress",
        "phase": "orchestrate",
        "substep": task_id,
        "state": state,  # "running" | "done" | "error"
        "detail": detail,
        "ts": int(time.time() * 1000),
    }
    try:
        sys.__stdout__.write(json.dumps(payload) + "\n")
        sys.__stdout__.flush()
    except Exception:
        # Never let progress emission crash the orchestrator
        pass


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

def _bash_available() -> bool:
    """Return True if `bash` resolves to a working shell on this machine.

    On Windows this is typically Git Bash (C:/Program Files/Git/bin/bash.exe);
    a missing or non-functional bash falls back to no-worktree mode.
    """
    import shutil
    if shutil.which("bash") is None:
        return False
    try:
        proc = subprocess.run(
            ["bash", "-c", "echo ok"],
            capture_output=True, text=True, timeout=5,
        )
        return proc.returncode == 0 and "ok" in (proc.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return False


_BASH_PATH_STYLE: str | None = None


def _bash_path(path: Path) -> str:
    """Return a path string that the detected Windows bash can open."""
    resolved = path.resolve()
    if os.name != "nt":
        return str(resolved)

    global _BASH_PATH_STYLE
    if _BASH_PATH_STYLE is None:
        try:
            proc = subprocess.run(
                ["bash", "-lc", "test -d /mnt/c && printf wsl || printf msys"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            _BASH_PATH_STYLE = (proc.stdout or "").strip() or "msys"
        except (OSError, subprocess.SubprocessError):
            _BASH_PATH_STYLE = "msys"

    drive = resolved.drive.rstrip(":").lower()
    rest = resolved.as_posix()[2:] if drive else resolved.as_posix()
    if drive and _BASH_PATH_STYLE == "wsl":
        return f"/mnt/{drive}{rest}"
    if drive:
        return f"/{drive}{rest}"
    return resolved.as_posix()


def _tasks_from_plan(plan_path: Path, wave_id: str) -> list[dict[str, Any]]:
    """No-worktree fallback: load tasks straight from PLAN.tasks.yaml.

    Used when bash isn't available (Windows without Git Bash) or when
    the user passes --no-worktrees. The orchestrator runs tasks
    sequentially in the workspace root rather than in isolated git
    worktrees -- less safe, but unblocks the platform.
    """
    try:
        from signalos_lib.plan import load_tasks
        doc = load_tasks(plan_path)
    except Exception as exc:
        sys.stdout.write(f"[orchestrate] Could not load {plan_path}: {exc}\n")
        return []

    out: list[dict[str, Any]] = []
    for t in doc.tasks:
        if t.wave and str(t.wave) != str(wave_id):
            # Skip tasks tagged with a different wave
            continue
        out.append({
            "task": t.id,
            "branch": t.branch or f"task-{t.id}",
            "step_id": t.id,
            "wave": t.wave or wave_id,
            "title": t.title,
            "description": t.description,
            "files": list(t.files),
            "tier": t.tier,
            "depends_on": list(t.depends_on),
            "skills": list(t.skills),
            "previous_failure": t.previous_failure,
        })
    return ensure_design_skill_tagged(out)


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
        return ensure_design_skill_tagged(data.get("worktrees", []))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# G3 design auto-tagging (audit §6.7 — orchestrator emits a G3 sub-task
# whenever the wave's phase transitions G2 → G3; the auto-tagger here
# ensures the "design" skill validator runs on any such task even when
# the plan author forgot to add the skill explicitly.)
# ---------------------------------------------------------------------------

# Path prefix that classifies a task as G3 design output. Any task that
# writes under .signalos/designs/<wave>/... is producing design artifacts
# and should be validated against the three-shape contract.
_DESIGN_OUTPUT_PREFIX = (".signalos", "designs")


def _looks_like_design_task(task: dict[str, Any]) -> bool:
    """Return True iff *task* should run through the design validator.

    Three signals (any one is sufficient):
      1. task["gate"] == "G3"
      2. task["skills"] already contains "design" (explicit author intent)
      3. task["files"] includes any path under .signalos/designs/<wave>/

    The third signal is the "even when the author forgot the tag" path —
    the validator's three-shape contract is enforced as long as the
    output lands in the canonical design directory.
    """
    gate = task.get("gate")
    if isinstance(gate, str) and gate.upper() == "G3":
        return True
    skills = task.get("skills") or []
    if isinstance(skills, list) and any(
        isinstance(s, str) and s.strip().lower() == "design" for s in skills
    ):
        return True
    files = task.get("files") or []
    if isinstance(files, list):
        for f in files:
            if not isinstance(f, str):
                continue
            parts = f.replace("\\", "/").split("/")
            if len(parts) >= 2 and parts[0] == _DESIGN_OUTPUT_PREFIX[0] \
                    and parts[1] == _DESIGN_OUTPUT_PREFIX[1]:
                return True
    return False


def ensure_design_skill_tagged(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ensure every G3-design task has "design" in its skills list.

    Per v0.2 audit §6.7 — the orchestrator emits a G3 sub-task with the
    design skill attached so `_validate_design` runs post-write. This
    normalizer is the safety net: it adds the tag to any task that
    looks like a G3 design task (by gate field or output-path
    heuristic) but is missing it. Idempotent — re-running over an
    already-tagged task is a no-op.

    Tasks that don't look like design tasks are returned unchanged.
    """
    out: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            out.append(task)
            continue
        if not _looks_like_design_task(task):
            out.append(task)
            continue
        existing = task.get("skills") or []
        existing_list = list(existing) if isinstance(existing, list) else []
        already_tagged = any(
            isinstance(s, str) and s.strip().lower() == "design"
            for s in existing_list
        )
        if already_tagged:
            out.append(task)
            continue
        merged = {**task, "skills": existing_list + ["design"]}
        out.append(merged)
    return out


# ---------------------------------------------------------------------------
# Step execution for a single worktree task
# ---------------------------------------------------------------------------

_FILE_BLOCK_RE = re.compile(
    r"^[#`>\-\s*]*?(?:filepath|FILE|path)\s*:\s*([^\n\r`]+?)\s*$\n+```(?:[a-zA-Z0-9+\-_]*)\n([\s\S]*?)\n```",
    re.MULTILINE,
)


def _extract_files_from_response(response: str) -> list[tuple[str, str]]:
    """Parse a harness LLM response into [(path, content), ...].

    Looks for blocks shaped like:

        ### filepath: src/components/TodoList.tsx
        ```tsx
        ...code...
        ```

    or with `FILE:` / `path:` headers, with or without code-fence language tag.
    Returns the matches in document order. An empty list means the LLM did
    not emit a structured response and the caller should not write anything.
    """
    if not response:
        return []
    out: list[tuple[str, str]] = []
    for match in _FILE_BLOCK_RE.finditer(response):
        path = match.group(1).strip().strip("`'\"")
        content = match.group(2)
        if not path:
            continue
        # Reject paths that try to escape the workspace.
        if ".." in path.split("/") or path.startswith("/") or (len(path) > 2 and path[1] == ":"):
            continue
        # Trim a leading newline if the regex captured one.
        if content.startswith("\n"):
            content = content[1:]
        out.append((path, content))
    return out


# ---------------------------------------------------------------------------
# Dependency auto-detection (#3: auto-deps)
#
# The LLM frequently emits code that imports a package without remembering
# to add it to package.json (or requirements.txt). Static-scan the written
# files for top-level imports, diff against the workspace's declared deps,
# and write .signalos/missing-deps.json. The preview step reads this file
# before `npm install` and adds the missing entries.
# ---------------------------------------------------------------------------

# ESM/CJS bare-import: `import X from 'pkg'`, `import { x } from "pkg"`,
# `import 'pkg'`, `require('pkg')`. Captures the package specifier.
_JS_IMPORT_RE = re.compile(
    r"""(?:^|\s)
        (?: import \s+ (?:[^'"]+? \s+ from \s+ )?
          | require \s* \(
        )
        ['"]([^'"]+)['"]""",
    re.VERBOSE | re.MULTILINE,
)

# Built-in Node modules that should never be flagged as missing deps.
_NODE_BUILTINS = frozenset({
    "assert", "buffer", "child_process", "cluster", "console", "crypto",
    "dgram", "dns", "events", "fs", "http", "https", "net", "os", "path",
    "process", "querystring", "readline", "stream", "string_decoder",
    "timers", "tls", "tty", "url", "util", "v8", "vm", "zlib", "worker_threads",
    "fs/promises", "node:fs", "node:path", "node:os", "node:crypto",
    "node:child_process", "node:url", "node:util",
})


def _scan_js_imports(content: str) -> set[str]:
    """Return the set of bare module specifiers imported by *content*.

    Relative imports (`./`, `../`) and Node built-ins are excluded.
    Scoped packages (`@scope/name`) and sub-paths (`pkg/sub`) are
    reduced to their installable package name.
    """
    out: set[str] = set()
    for m in _JS_IMPORT_RE.finditer(content):
        spec = m.group(1).strip()
        if not spec or spec.startswith(".") or spec.startswith("/"):
            continue
        # `node:fs` and `fs/promises` style.
        if spec.startswith("node:") or spec in _NODE_BUILTINS:
            continue
        if "/" in spec and not spec.startswith("@"):
            # `pkg/sub` -> `pkg`
            spec = spec.split("/", 1)[0]
        elif spec.startswith("@") and spec.count("/") >= 1:
            # `@scope/name/sub` -> `@scope/name`
            parts = spec.split("/")
            spec = "/".join(parts[:2])
        if spec in _NODE_BUILTINS:
            continue
        out.add(spec)
    return out


def _declared_npm_deps(root: Path) -> set[str]:
    """Union of dependencies + devDependencies from the workspace's
    package.json. Empty set if package.json is absent or malformed."""
    pkg = root / "package.json"
    if not pkg.is_file():
        return set()
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    out: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            out.update(section.keys())
    return out


def _record_missing_deps(root: Path, written_files: list[str]) -> list[str]:
    """Scan *written_files* (paths relative to *root*) for JS imports
    that aren't in package.json. Append any new ones to
    .signalos/missing-deps.json (a deduplicated JSON array).

    Returns the list of newly-discovered missing deps from THIS task so
    callers can surface them in audit / progress.
    """
    # No package.json -> not a Node workspace (or pre-init); we have no
    # baseline to diff against and flagging every import would be noise.
    if not (root / "package.json").is_file():
        return []

    js_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
    imported: set[str] = set()
    for rel in written_files:
        target = root / rel
        if target.suffix.lower() not in js_exts or not target.is_file():
            continue
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        imported |= _scan_js_imports(content)
    if not imported:
        return []

    declared = _declared_npm_deps(root)
    missing = sorted(imported - declared)
    if not missing:
        return []

    # Merge with any pre-existing record so the preview step has a
    # cumulative list across the wave.
    record_path = root / ".signalos" / "missing-deps.json"
    existing: list[str] = []
    if record_path.is_file():
        try:
            existing = json.loads(record_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except (OSError, ValueError):
            existing = []
    new_set = set(existing) | set(missing)
    merged = sorted(new_set)
    truly_new = sorted(set(missing) - set(existing))
    try:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return truly_new


def _append_files_to_wave_checkpoint(root: Path, wave_id: str, new_files: list[str]) -> None:
    """Merge *new_files* into the wave's checkpoint files_written list.

    The checkpoint is created by the IPC server's handle_checkpoint
    before the wave starts; we just update its files_written array as
    tasks complete so the rollback handler knows what to delete.
    Missing-checkpoint and corrupt-JSON cases are handled silently --
    rollback for waves without a checkpoint just won't have the file
    list (it's already a fallback path on the rollback side).
    """
    cp = root / ".signalos" / "wave-checkpoints" / f"wave-{wave_id}.json"
    if not cp.is_file():
        return
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    existing = data.get("files_written") or []
    if not isinstance(existing, list):
        existing = []
    merged = sorted(set(existing) | set(new_files))
    data["files_written"] = merged
    try:
        cp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _run_pre_write_guard(root: Path, rel_path: str, content: str) -> tuple[bool, str]:
    """Invoke pre-tool-use-guard.sh on a pending file write (AMD-CORE-110).

    Returns (permitted, reason). permitted=True means the write may proceed;
    permitted=False means the guard rejected and the write must be skipped.
    The reason string is empty on success, or contains the guard's stderr +
    a hint of which check fired on failure.

    Fallback policy: if the guard script is absent (bundle not installed in
    this workspace) or bash is unavailable, this returns (True, "guard-missing")
    or (True, "guard-skipped-no-bash"). The caller records these as audit
    entries so silent bypass is visible; the lenient default keeps existing
    flows working for users who have not re-init'd.
    """
    guard = root / "core" / "execution" / "hooks" / "pre-tool-use-guard.sh"
    if not guard.is_file():
        return True, "guard-missing"
    redact_py = root / "core" / "execution" / "hooks" / "_lib" / "redact.py"
    if redact_py.is_file() and content:
        diff_input = "\n".join(f"+{line}" for line in content.splitlines()) + "\n"
        try:
            scan = subprocess.run(
                [sys.executable, str(redact_py), "--scan-diff"],
                input=diff_input,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return True, f"guard-error: direct secret scan failed: {exc}"
        if scan.returncode != 0:
            return False, (scan.stderr or scan.stdout or "secret pattern in write content").strip()
    if not _bash_available():
        return True, "guard-skipped-no-bash"
    bash_root = _bash_path(root)
    bash_guard = _bash_path(guard)
    bash_target = _bash_path(root / rel_path)
    try:
        if os.name == "nt" and _BASH_PATH_STYLE == "wsl":
            command = (
                f"SIGNALOS_PLUGIN_ROOT={shlex.quote(bash_root)} "
                f"CLAUDE_TOOL_INPUT_FILE_PATH={shlex.quote(bash_target)} "
                f"CLAUDE_TOOL_INPUT_CONTENT={shlex.quote(content)} "
                f"bash {shlex.quote(bash_guard)}"
            )
            result = subprocess.run(
                ["bash", "-lc", command],
                cwd=str(Path.cwd()),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        else:
            result = subprocess.run(
                ["bash", bash_guard],
                cwd=str(root),
                env={
                    **os.environ,
                    "SIGNALOS_PLUGIN_ROOT": bash_root,
                    "CLAUDE_TOOL_INPUT_FILE_PATH": bash_target,
                    "CLAUDE_TOOL_INPUT_CONTENT": content,
                },
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        # Guard invocation itself failed -- treat as missing (lenient) but
        # record so the operator notices.
        return True, f"guard-error: {exc}"
    if result.returncode == 0:
        return True, ""
    if result.returncode in {126, 127}:
        return True, f"guard-error: {(result.stderr or result.stdout or '').strip()}"
    return False, (result.stderr or result.stdout or f"exit {result.returncode}").strip()


def _append_audit_entry(root: Path, entry: dict[str, Any]) -> None:
    """Append a JSON entry to .signalos/AUDIT_TRAIL.jsonl. Silent on failure."""
    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    try:
        trail.parent.mkdir(parents=True, exist_ok=True)
        with trail.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Auto-commit at wave end (M4 / audit completion plan)
# ---------------------------------------------------------------------------
#
# When a wave finishes successfully we want the agent to actually *ship*
# the files it just wrote: stage everything in the workspace, generate a
# commit message summarising the wave, and land a commit. The push step
# is gated on G5 sign and lives in sign.py — auto-commit just locks in
# local state so a later G5 sign has something to push.
#
# This step is best-effort by design:
#   - No .git dir              -> silent skip (uninitialized workspace is valid)
#   - Clean tree               -> silent skip (no empty commits)
#   - Subprocess / hook failure -> audit-trail entry, but does NOT block
#                                  the run_wave success return. The user
#                                  can always `git commit` manually.

def _auto_commit_wave(
    root: Path,
    wave_id: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Stage + commit wave output in the workspace. Best-effort.

    Returns a small dict describing the outcome ({"status": "skipped"|"committed"|"failed", ...})
    purely so callers / tests can observe what happened. The caller does
    NOT need to inspect the return value to decide whether the wave
    succeeded — that's already settled by the time we reach this point.
    """
    git_dir = root / ".git"
    if not git_dir.exists():
        # Uninitialized workspace — auto-commit is not meaningful here.
        return {"status": "skipped", "reason": "no-git-dir"}

    # 1. Is there anything to commit?
    try:
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _append_audit_entry(root, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": "auto-commit-failed",
            "wave_id": wave_id,
            "reason": f"git-status-error: {exc}",
        })
        return {"status": "failed", "reason": f"git-status-error: {exc}"}

    if status_proc.returncode != 0:
        _append_audit_entry(root, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": "auto-commit-failed",
            "wave_id": wave_id,
            "reason": (status_proc.stderr or "").strip() or "git status non-zero",
        })
        return {"status": "failed", "reason": "git-status-nonzero"}

    if not status_proc.stdout.strip():
        return {"status": "skipped", "reason": "clean-tree"}

    # 2. Build a commit message from the wave summary.
    task_titles = []
    for task in summary.get("tasks", []) or []:
        title = task.get("title") or task.get("task") or task.get("branch")
        if title and title not in task_titles:
            task_titles.append(str(title))
    # Cap the subject-line title list so the first message line stays sane.
    subject_titles = ", ".join(task_titles[:3]) or "wave output"
    if len(task_titles) > 3:
        subject_titles += f", +{len(task_titles) - 3} more"

    completed = summary.get("completed", 0)
    failed = summary.get("failed", 0)
    # files_written count: aggregate per-task results' files_written arrays.
    files_count = 0
    for task in summary.get("tasks", []) or []:
        fw = (task.get("result") or {}).get("files_written") or []
        if isinstance(fw, list):
            files_count += len(fw)

    body_lines = [
        f"Wave summary: {completed} task(s) complete, "
        f"{files_count} file(s) written, {failed} failed.",
        "",
        "Auto-committed by SignalOS at wave end.",
    ]
    commit_msg = (
        f"feat(wave-{wave_id}): {subject_titles}\n\n"
        + "\n".join(body_lines)
    )

    # 3. Stage and commit.
    try:
        add_proc = subprocess.run(
            ["git", "add", "-A"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if add_proc.returncode != 0:
            _append_audit_entry(root, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "action": "auto-commit-failed",
                "wave_id": wave_id,
                "reason": (add_proc.stderr or "").strip() or "git add non-zero",
            })
            return {"status": "failed", "reason": "git-add-failed"}

        commit_proc = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _append_audit_entry(root, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": "auto-commit-failed",
            "wave_id": wave_id,
            "reason": f"subprocess-error: {exc}",
        })
        return {"status": "failed", "reason": f"subprocess-error: {exc}"}

    if commit_proc.returncode != 0:
        # A pre-commit hook can reject the commit. Record but don't fail
        # the wave — user can address the hook output and commit manually.
        _append_audit_entry(root, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": "auto-commit-failed",
            "wave_id": wave_id,
            "reason": (commit_proc.stderr or commit_proc.stdout or "").strip()[:500]
                       or f"exit {commit_proc.returncode}",
        })
        return {"status": "failed", "reason": "git-commit-failed"}

    _append_audit_entry(root, {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": "auto-commit-ok",
        "wave_id": wave_id,
        "files_count": files_count,
        "tasks_committed": task_titles,
    })
    sys.stdout.write(
        f"[orchestrate] auto-commit: wave-{wave_id} committed "
        f"({files_count} file(s), {len(task_titles)} task(s))\n"
    )
    return {"status": "committed", "files_count": files_count, "message": commit_msg}


def _write_extracted_files(root: Path, files: list[tuple[str, str]]) -> list[str]:
    """Write each (path, content) tuple under *root*, creating parent dirs.

    Returns the list of paths written. Skips paths that resolve outside the
    workspace root (defense-in-depth on top of the regex check). Each write
    is gated by pre-tool-use-guard.sh per AMD-CORE-110 — files the guard
    rejects are not written and an audit entry is appended.
    """
    written: list[str] = []
    root_resolved = root.resolve()
    for rel, content in files:
        try:
            target = (root / rel).resolve()
            # Reject paths that escape the workspace root.
            try:
                target.relative_to(root_resolved)
            except ValueError:
                continue
            # AMD-CORE-110: pre-write guard. Lenient on guard-missing /
            # bash-missing -- caller still gets a write -- but record both
            # cases so silent bypass is visible in audit.
            permitted, reason = _run_pre_write_guard(root, rel, content)
            if not permitted:
                _append_audit_entry(root, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "action": "violation:write-blocked",
                    "path": rel,
                    "reason": reason,
                    "source": "orchestrator._write_extracted_files",
                })
                continue
            if reason in {"guard-missing", "guard-skipped-no-bash"} or reason.startswith("guard-error"):
                _append_audit_entry(root, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "action": "guard-bypass",
                    "path": rel,
                    "reason": reason,
                    "source": "orchestrator._write_extracted_files",
                })
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(rel)
        except OSError:
            # Individual file failures don't abort the whole task -- record
            # what we managed to write.
            continue
    return written


_SKILL_BUDGET_CHARS = 2800


def _load_skill(root: Path, relative_path: str) -> str:
    """Read a SKILL.md from the workspace, trimmed to _SKILL_BUDGET_CHARS.

    Returns empty string if the file isn't present (project not signal-init'd
    yet, or skill not bundled). Per-skill budget keeps the per-task prompt
    under control when multiple skills get loaded.
    """
    p = root / relative_path
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(text) > _SKILL_BUDGET_CHARS:
        return text[:_SKILL_BUDGET_CHARS] + "\n\n[...skill trimmed for prompt budget...]\n"
    return text


# Catalog of skill keys the plan layer may attach explicitly to a task.
# Must stay in sync with VALID_SKILL_KEYS in src/services/signalosPrompt.ts.
# Each entry maps key -> (display-label, workspace-relative SKILL.md path).
#
# All 35 bundle SKILL.md files are routable. The first 15 are "deliverable"
# skills (have a structured-output artifact validator in skill_validators.py);
# the rest are context / process skills that get injected into prompts when
# relevant but don't have enforcement gates -- their value is the guidance
# they put in front of the LLM, not a post-write check.
_SKILL_KEY_TO_PATH: dict[str, tuple[str, str]] = {
    # --- Build pillar ----------------------------------------------------
    "test-driven-development":      ("Test-Driven Development",        "core/execution/build/test-driven-development/SKILL.md"),
    "test-generation":              ("Test Generation",                 "core/execution/build/test-generation/SKILL.md"),
    "e2e-testing":                  ("End-to-End Browser Testing",      "core/execution/build/e2e-testing/SKILL.md"),
    "systematic-debugging":         ("Systematic Debugging",            "core/execution/build/systematic-debugging/SKILL.md"),
    "verification-before-completion": ("Verification Before Completion", "core/execution/build/verification-before-completion/SKILL.md"),
    # --- Plan pillar -----------------------------------------------------
    "writing-plans":                ("Writing Plans",                   "core/execution/plan/writing-plans/SKILL.md"),
    "executing-plans":              ("Executing Plans",                 "core/execution/plan/executing-plans/SKILL.md"),
    # --- Review pillar ---------------------------------------------------
    "comprehensive-code-review":    ("Comprehensive Code Review",       "core/execution/review/comprehensive-code-review/SKILL.md"),
    "receiving-code-review":        ("Receiving Code Review",           "core/execution/review/receiving-code-review/SKILL.md"),
    "requesting-code-review":       ("Requesting Code Review",          "core/execution/review/requesting-code-review/SKILL.md"),
    # --- Governance ------------------------------------------------------
    "security-audit":               ("Security Audit",                  "core/governance/SecurityAudit/SKILL.md"),
    "retro-run":                    ("Retro Run",                       "core/governance/Retro/retro-run/SKILL.md"),
    "retrospective-analyze":        ("Retrospective Analyze",           "core/governance/Retro/retrospective-analyze/SKILL.md"),
    # --- Subagents -------------------------------------------------------
    "subagent-driven-development":  ("Subagent-Driven Development",     "core/execution/subagents/subagent-driven-development/SKILL.md"),
    "dispatching-parallel-agents":  ("Dispatching Parallel Agents",     "core/execution/subagents/dispatching-parallel-agents/SKILL.md"),
    # --- Worktree --------------------------------------------------------
    "using-git-worktrees":          ("Using Git Worktrees",             "core/execution/worktree/using-git-worktrees/SKILL.md"),
    "finishing-a-development-branch": ("Finishing a Development Branch", "core/execution/worktree/finishing-a-development-branch/SKILL.md"),
    # --- Cognitive / process skills (advisory text injection) -----------
    "belief-seed-generation":       ("Belief Seed Generation",          "core/execution/skills/belief-seed-generation/SKILL.md"),
    "brainstorming":                ("Brainstorming",                   "core/execution/skills/brainstorming/SKILL.md"),
    "compress-context":             ("Compress Context",                "core/execution/skills/compress-context/SKILL.md"),
    "context":                      ("Context Loading",                 "core/execution/skills/context/SKILL.md"),
    "design":                       ("Design",                          "core/execution/skills/design/SKILL.md"),
    "existing-product-kit":         ("Existing Product Kit",            "core/execution/skills/existing-product-kit/SKILL.md"),
    "goal-driven-execution":        ("Goal-Driven Execution",           "core/execution/skills/goal-driven-execution/SKILL.md"),
    "headless-execution":           ("Headless Execution",              "core/execution/skills/headless-execution/SKILL.md"),
    "intent-router":                ("Intent Router",                   "core/execution/skills/intent-router/SKILL.md"),
    "memory":                       ("Memory",                          "core/execution/skills/memory/SKILL.md"),
    "observability-dashboard":      ("Observability Dashboard",         "core/execution/skills/observability-dashboard/SKILL.md"),
    "operator-tooling":             ("Operator Tooling",                "core/execution/skills/operator-tooling/SKILL.md"),
    "parallel-orchestration":       ("Parallel Orchestration",          "core/execution/skills/parallel-orchestration/SKILL.md"),
    "plugin-registry":              ("Plugin Registry",                 "core/execution/skills/plugin-registry/SKILL.md"),
    "product-surface-mapping":      ("Product Surface Mapping",         "core/execution/skills/product-surface-mapping/SKILL.md"),
    "review":                       ("Review (lightweight)",            "core/execution/skills/review/SKILL.md"),
    "session-journal":              ("Session Journal",                 "core/execution/skills/session-journal/SKILL.md"),
    "simplicity-first":             ("Simplicity First",                "core/execution/skills/simplicity-first/SKILL.md"),
    "stakeholder-interview":        ("Stakeholder Interview",           "core/execution/skills/stakeholder-interview/SKILL.md"),
    "surgical-changes":             ("Surgical Changes",                "core/execution/skills/surgical-changes/SKILL.md"),
    "task-schema":                  ("Task Schema",                     "core/execution/skills/task-schema/SKILL.md"),
    "think-before-coding":          ("Think Before Coding",             "core/execution/skills/think-before-coding/SKILL.md"),
}


def _relevant_skills(task: dict[str, Any], root: Path) -> list[tuple[str, str]]:
    """Pick which SKILL.md docs apply to *task* and load their content.

    Skills come from two sources, unioned (explicit wins on order):
      1. Explicit ``task["skills"]`` keys set by the planner (chat AI tags
         tasks with skills like "security-audit", "test-generation").
      2. Keyword regex fallback against title + description, for plans
         that don't carry explicit tags.

    Verification skill is always included because "did this actually work"
    applies to every task.
    """
    title = (task.get("title") or "").lower()
    desc  = (task.get("description") or "").lower()
    haystack = f"{title} {desc}"

    candidates: list[tuple[str, str, str]] = [
        # (keyword regex, label, path-from-workspace).
        # Covers all 35 routable skills. Order matters only for the
        # display label; de-dup is by SKILL.md path further down.
        # --- Build ---
        ("tdd|red.green.refactor|write tests? first",
         "Test-Driven Development",
         "core/execution/build/test-driven-development/SKILL.md"),
        ("test|spec|vitest|jest|pytest|coverage|test suite",
         "Test Generation",
         "core/execution/build/test-generation/SKILL.md"),
        ("e2e|end.?to.?end|playwright|cypress|browser test|headless|smoke test",
         "End-to-End Browser Testing",
         "core/execution/build/e2e-testing/SKILL.md"),
        ("debug|investigate|reproduce|bug|crash|stack\\s*trace|regression",
         "Systematic Debugging",
         "core/execution/build/systematic-debugging/SKILL.md"),
        ("verify|verification|self.check|self.review|sanity check",
         "Verification Before Completion",
         "core/execution/build/verification-before-completion/SKILL.md"),
        # --- Plan ---
        ("write.*plan|decompose|breakdown|design the architecture",
         "Writing Plans",
         "core/execution/plan/writing-plans/SKILL.md"),
        ("execute.*plan|dispatch|run the plan|kick off",
         "Executing Plans",
         "core/execution/plan/executing-plans/SKILL.md"),
        # --- Review ---
        ("code review|review pr|review this|review the changes",
         "Comprehensive Code Review",
         "core/execution/review/comprehensive-code-review/SKILL.md"),
        ("address.*review|address.*feedback|address.*comments|respond.*review|incorporate.*comments|incorporate.*feedback",
         "Receiving Code Review",
         "core/execution/review/receiving-code-review/SKILL.md"),
        ("request.*review|open.*pr|prepare.*review",
         "Requesting Code Review",
         "core/execution/review/requesting-code-review/SKILL.md"),
        # --- Governance ---
        ("security|vulnerab|injection|xss|csrf|owasp|stride|threat|auth\\w*|password|secret",
         "Security Audit",
         "core/governance/SecurityAudit/SKILL.md"),
        ("retro|post.?mortem|lessons learned",
         "Retro Run",
         "core/governance/Retro/retro-run/SKILL.md"),
        ("analyze.*retro|retrospective.*analyze|trend.*retro",
         "Retrospective Analyze",
         "core/governance/Retro/retrospective-analyze/SKILL.md"),
        # --- Subagents ---
        ("subagent|sub.agent|delegate",
         "Subagent-Driven Development",
         "core/execution/subagents/subagent-driven-development/SKILL.md"),
        ("parallel agents?|dispatch.*agents?|fan.out",
         "Dispatching Parallel Agents",
         "core/execution/subagents/dispatching-parallel-agents/SKILL.md"),
        # --- Worktree ---
        ("worktree|isolated branch",
         "Using Git Worktrees",
         "core/execution/worktree/using-git-worktrees/SKILL.md"),
        ("merge.*branch|finish.*branch|cleanup.*worktree|retire.*branch",
         "Finishing a Development Branch",
         "core/execution/worktree/finishing-a-development-branch/SKILL.md"),
        # --- Cognitive / process ---
        ("belief|hypothesis seed|prior assumption",
         "Belief Seed Generation",
         "core/execution/skills/belief-seed-generation/SKILL.md"),
        ("brainstorm|ideation|divergent thinking",
         "Brainstorming",
         "core/execution/skills/brainstorming/SKILL.md"),
        ("compress.*context|trim.*context|summarize.*context",
         "Compress Context",
         "core/execution/skills/compress-context/SKILL.md"),
        ("load.*context|gather.*context|read.*context",
         "Context Loading",
         "core/execution/skills/context/SKILL.md"),
        ("design.*system|architecture|tech.?spec",
         "Design",
         "core/execution/skills/design/SKILL.md"),
        ("existing.*product|brownfield|legacy",
         "Existing Product Kit",
         "core/execution/skills/existing-product-kit/SKILL.md"),
        ("headless|non.interactive|ci mode",
         "Headless Execution",
         "core/execution/skills/headless-execution/SKILL.md"),
        ("intent.*rout|route the intent|classify.*intent",
         "Intent Router",
         "core/execution/skills/intent-router/SKILL.md"),
        ("memory|recall|long.term context",
         "Memory",
         "core/execution/skills/memory/SKILL.md"),
        ("observability|metrics dashboard|telemetry view",
         "Observability Dashboard",
         "core/execution/skills/observability-dashboard/SKILL.md"),
        ("operator|sre|admin tool",
         "Operator Tooling",
         "core/execution/skills/operator-tooling/SKILL.md"),
        ("parallel orchestration|coordinate parallel|wave parallel",
         "Parallel Orchestration",
         "core/execution/skills/parallel-orchestration/SKILL.md"),
        ("plugin|extension point|registry",
         "Plugin Registry",
         "core/execution/skills/plugin-registry/SKILL.md"),
        ("product surface|surface mapping|user surface",
         "Product Surface Mapping",
         "core/execution/skills/product-surface-mapping/SKILL.md"),
        ("session journal|session log|build journal",
         "Session Journal",
         "core/execution/skills/session-journal/SKILL.md"),
        ("stakeholder|interview.*user|user research",
         "Stakeholder Interview",
         "core/execution/skills/stakeholder-interview/SKILL.md"),
        ("task schema|plan schema|tasks?\\.yaml",
         "Task Schema",
         "core/execution/skills/task-schema/SKILL.md"),
        # --- Engineering-discipline pack (conservative routes) ---
        ("assumption|ambiguity|ambiguous|unclear|clarify|clarif\\w*|underspecified|requirements?",
         "Think Before Coding",
         "core/execution/skills/think-before-coding/SKILL.md"),
        ("simplif\\w*|minimal solution|over.?engineer\\w*|yagni|scope.?creep|gold.?plat\\w*",
         "Simplicity First",
         "core/execution/skills/simplicity-first/SKILL.md"),
        ("surgical|minimal.?diff|touch.?only|only the necessary|unrelated refactor|narrow.?diff|smallest change",
         "Surgical Changes",
         "core/execution/skills/surgical-changes/SKILL.md"),
        ("success.?criteria|acceptance criteria|done when|verify.*goal|goal.?driven|definition of done",
         "Goal-Driven Execution",
         "core/execution/skills/goal-driven-execution/SKILL.md"),
    ]

    seen_paths: set[str] = set()
    out: list[tuple[str, str]] = []

    # 1) Explicit skills from the plan -- ordered first so the agent reads
    #    the planner's intentional choices before the keyword fallback.
    raw_skills = task.get("skills") or []
    if isinstance(raw_skills, list):
        for key in raw_skills:
            if not isinstance(key, str):
                continue
            entry = _SKILL_KEY_TO_PATH.get(key.strip().lower())
            if entry is None:
                # Unknown key -- silent skip (forward-compat with future
                # bundle additions; JS validator already enforces the
                # current catalog).
                continue
            label, path = entry
            if path in seen_paths:
                continue
            content = _load_skill(root, path)
            if content:
                out.append((label, content))
                seen_paths.add(path)
            else:
                # Catalog entry resolves to a path that's not in the
                # workspace. This means the user's workspace has an
                # older bundle snapshot than the app. Surface a clear
                # remediation hint to stdout (orchestrator log) so the
                # user discovers the staleness instead of silently
                # getting a less-informed prompt.
                sys.stdout.write(
                    f"[orchestrate] WARN: task requested skill '{key}' but "
                    f"{path} is missing from the workspace. "
                    f"Run `signalos init <workspace> --refresh-bundle` to "
                    f"update bundled protocol files.\n"
                )
                seen_paths.add(path)

    # 2) Keyword fallback for plans that don't tag tasks explicitly.
    for pattern, label, path in candidates:
        if path in seen_paths:
            continue
        if re.search(pattern, haystack):
            content = _load_skill(root, path)
            if content:
                out.append((label, content))
                seen_paths.add(path)

    # 3) Always-on verification skill.
    verification_path = "core/execution/build/verification-before-completion/SKILL.md"
    if verification_path not in seen_paths:
        verification = _load_skill(root, verification_path)
        if verification:
            out.append(("Verification Before Completion", verification))

    return out


_EXISTING_FILES_BUDGET = 50_000  # total bytes of existing-file context per task


def _read_existing_files_context(root: Path, files: list[str]) -> str:
    """Read the current on-disk contents of *files* under *root* and format
    them for inclusion in the task prompt (iterative-refinement support).

    Files that don't exist yet are silently skipped -- this is a hint, not
    a prerequisite. Total injected bytes capped at _EXISTING_FILES_BUDGET
    so a giant file doesn't blow the prompt budget.
    """
    if not files:
        return ""
    blocks: list[str] = []
    spent = 0
    for rel in files:
        target = (root / rel)
        try:
            if not target.is_file():
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if spent + len(text) > _EXISTING_FILES_BUDGET:
            # Include a truncation marker and stop -- the LLM should still
            # see SOMETHING for files that fit so it doesn't regenerate
            # them blind.
            remaining = _EXISTING_FILES_BUDGET - spent
            if remaining > 200:
                text = text[:remaining] + "\n\n[...truncated for prompt budget...]\n"
            else:
                continue
        # Pick a reasonable code-fence language from the extension.
        suffix = target.suffix.lstrip(".") or ""
        blocks.append(f"### {rel}\n\n```{suffix}\n{text}\n```")
        spent += len(text)
        if spent >= _EXISTING_FILES_BUDGET:
            break
    if not blocks:
        return ""
    return (
        "\n\nCurrent state of files you may modify (read these before "
        "producing any new content -- preserve unrelated behaviour, fix only "
        "what the task asks for):\n\n"
        + "\n\n---\n\n".join(blocks)
        + "\n\n---\n"
    )


def _build_task_prompt(task: dict[str, Any], root: Path | None = None) -> str:
    """Build a structured prompt that instructs the LLM to emit files.

    The prompt now includes (in order):
      - Optional "previous attempt failed" section for retried tasks
      - Task identity (id, title, branch, wave, tier, description)
      - Declared file list
      - Current contents of any declared files that already exist on disk
        (iterative-refinement support: the LLM edits instead of regenerates)
      - Matching SKILL.md docs from core/execution/{build,review,plan,worktree}
        (loaded from the workspace at runtime so user-customized skills win)
      - The mandatory output protocol (### filepath: ... fenced blocks)

    The skill and existing-files sections are omitted when *root* is None.
    """
    task_id = task.get("task") or task.get("branch", "unknown")
    title = task.get("title") or ""
    description = task.get("description") or ""
    files = task.get("files") or []
    branch = task.get("branch", "")
    wave = task.get("wave", "?")
    tier = task.get("tier", "T2")
    previous_failure = task.get("previous_failure") or ""

    file_list = "\n".join(f"- {f}" for f in files) if files else "(no specific files declared -- decide which files are needed)"

    # Smart-retry context: previous failure prepended so the LLM sees it
    # before anything else and adjusts its approach.
    retry_section = ""
    if previous_failure:
        retry_section = (
            f"\n## Previous attempt failed\n\n"
            f"Reason: {previous_failure.strip()}\n\n"
            f"Avoid repeating this failure mode. Read the task carefully "
            f"and produce a different approach if needed.\n"
        )

    existing_section = ""
    if root is not None:
        existing_section = _read_existing_files_context(root, files)

    skills_section = ""
    if root is not None:
        skills = _relevant_skills(task, root)
        if skills:
            blocks = []
            for label, content in skills:
                blocks.append(f"### {label}\n\n{content.strip()}")
            skills_section = (
                "\n\nApplicable SignalOS skills (consult before producing files):\n\n"
                + "\n\n---\n\n".join(blocks)
                + "\n\n---\n"
            )

    return (
        f"You are SignalOS harness executing one task in wave {wave}.\n"
        f"{retry_section}"
        f"\nTask id: {task_id}\n"
        f"Task title: {title}\n"
        f"Branch: {branch}\n"
        f"Trust tier: {tier}\n\n"
        f"Description:\n{description or '(no description)'}\n\n"
        f"Files to create or modify:\n{file_list}"
        f"{existing_section}"
        f"{skills_section}\n\n"
        "Output format (MANDATORY):\n"
        "- For each file you produce, emit a single block in this exact shape:\n"
        "    ### filepath: <relative-path-from-workspace-root>\n"
        "    ```<language>\n"
        "    <full file contents>\n"
        "    ```\n"
        "- One block per file. Use the same shape even if the language is unknown.\n"
        "- Do not paraphrase or summarise the file contents inside the block.\n"
        "- After all file blocks, you may add a short prose summary (<= 3 lines).\n"
        "- Do not invent files outside the declared list unless strictly necessary.\n"
        "- Use forward slashes in paths. Paths must be relative to the workspace.\n"
        "- Do not include code fences nested inside a file's content; use the\n"
        "  outermost fence as the only one. If the file itself is markdown that\n"
        "  contains code fences, use tildes (~~~) for the outer fence.\n"
    )


def _execute_task(
    task: dict[str, Any],
    root: Path,
    session_id: str,
    provider: LLMProvider,
    model: str,
    status_callback: Any,
) -> dict[str, Any]:
    """Execute one task: prompt LLM via the harness, parse code blocks, write files.

    Called from a ThreadPoolExecutor worker. The harness handles journal +
    metrics emission via the hook scripts; this wrapper builds a structured
    prompt and turns the LLM response into actual on-disk file writes so
    "build me a todo app" actually produces code instead of just an audited
    LLM call.
    """
    task_id = task.get("task") or task.get("branch", "unknown")
    branch  = task.get("branch", "")
    step_id = task.get("step_id") or branch or f"task-{task_id}"

    prompt = _build_task_prompt(task, root)

    # TDD-tagged tasks run through a two-phase red->green loop in
    # tdd_runner. Every other task runs as a single LLM call.
    from signalos_lib.tdd_runner import is_tdd_task, run_tdd_task

    def _call_llm_closure(p: str) -> tuple[str, dict[str, Any]]:
        try:
            r = harness_lib.run_step(
                step_id=step_id,
                prompt=p,
                model=model,
                session_id=session_id,
                cwd=root,
                intent=f"orchestrated wave task: {task_id}",
                provider=provider,
            )
        except Exception as exc:
            r = {
                "step_id": step_id,
                "status": "failed",
                "failure": str(exc),
                "exit_code": 2,
            }
        # Pull the full response text so the TDD loop can hand it to
        # extract_files. _read_harness_response returns the file text;
        # if missing we use the preview as a fallback.
        text = ""
        if isinstance(r, dict) and r.get("status") == "completed":
            text = _read_harness_response(root, session_id, r) or ""
        return (text, r if isinstance(r, dict) else {"status": "failed"})

    def _emit_tdd_progress(label: str, detail: str | None) -> None:
        # Forward TDD phase events through the same progress channel
        # the UI already listens on so the plan card can render
        # "tdd-red", "tdd-red-run", "tdd-green-run", "tdd-done".
        _emit_task_progress(
            wave_id=str(task.get("wave") or "?"),
            task_id=str(task_id),
            state="running",
            detail=f"{label}: {detail}" if detail else label,
        )

    if is_tdd_task(task):
        result = run_tdd_task(
            task=task,
            root=root,
            base_prompt=prompt,
            call_llm=_call_llm_closure,
            write_files=_write_extracted_files,
            extract_files=_extract_files_from_response,
            emit_progress=_emit_tdd_progress,
        )
    else:
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

    # Parse the LLM's response and write files. The harness saved a trimmed
    # response_preview; we need the full response for file extraction, which
    # we re-read from the harness's call state.
    # For TDD tasks, file writing already happened inside run_tdd_task and
    # result["files_written"] is populated; we skip the re-extract path.
    files_written: list[str] = list(result.get("files_written") or []) if isinstance(result, dict) else []
    extraction_note: str | None = None
    if not is_tdd_task(task) and isinstance(result, dict) and result.get("status") == "completed":
        full_response = _read_harness_response(root, session_id, result)
        if full_response:
            extracted = _extract_files_from_response(full_response)
            if extracted:
                files_written = _write_extracted_files(root, extracted)
                if files_written:
                    extraction_note = f"wrote {len(files_written)} file(s)"
                    # Update wave checkpoint with this task's files so the
                    # rollback command can wipe them on user request.
                    try:
                        _append_files_to_wave_checkpoint(
                            root,
                            str(task.get("wave") or "?"),
                            files_written,
                        )
                    except Exception as exc:  # pragma: no cover -- defensive
                        sys.stdout.write(f"[orchestrate] checkpoint-append failed: {exc}\n")

                    # Auto-deps: scan the just-written files for bare-module
                    # imports that aren't in package.json. The preview path
                    # reads .signalos/missing-deps.json before npm install.
                    try:
                        new_missing = _record_missing_deps(root, files_written)
                        if new_missing:
                            extraction_note += f"; flagged {len(new_missing)} missing npm dep(s)"
                            sys.stdout.write(
                                f"[orchestrate] auto-deps: discovered missing "
                                f"package(s) on disk: {', '.join(new_missing)}. "
                                f"Recorded in .signalos/missing-deps.json.\n"
                            )
                    except Exception as exc:  # pragma: no cover -- defensive
                        sys.stdout.write(f"[orchestrate] auto-deps scan failed: {exc}\n")

                    # E2E browser smoke (only when task is tagged
                    # e2e-testing). This spawns the project's dev server
                    # and runs Playwright headless against the localhost
                    # URL. A failure (hydration crash, missing selector,
                    # console error) converts the task to status=failed
                    # with the smoke output as previous_failure for
                    # smart-retry.
                    try:
                        from signalos_lib.e2e_runner import is_e2e_task, run_e2e_task
                        if is_e2e_task(task):
                            sys.stdout.write("[orchestrate] e2e: spawning dev server + Playwright smoke...\n")
                            e2e = run_e2e_task(task, root)
                            if e2e.get("skipped"):
                                sys.stdout.write(f"[orchestrate] e2e: skipped ({e2e.get('log', '')[:200]})\n")
                            elif not e2e["ok"]:
                                sys.stdout.write(
                                    "[orchestrate] e2e: smoke FAILED — "
                                    + (e2e.get("failure") or "no detail")[:400] + "\n"
                                )
                                result = {
                                    **result,
                                    "status": "failed",
                                    "failure": e2e["failure"],
                                }
                                extraction_note = f"{extraction_note}; e2e smoke failed"
                            else:
                                sys.stdout.write(
                                    f"[orchestrate] e2e: OK ({e2e.get('url')}, "
                                    f"{len(e2e.get('checkedSelectors', []))} selector(s) verified)\n"
                                )
                    except Exception as exc:  # pragma: no cover -- defensive
                        sys.stdout.write(f"[orchestrate] e2e smoke crashed: {exc}\n")

                    # Smart skill enforcement (#1): for each skill tagged on
                    # this task, check the expected structured-output
                    # artifact exists with the right shape. Violations
                    # convert the task to failed and feed previous_failure
                    # into the smart-retry channel so the next attempt
                    # sees the exact problem.
                    try:
                        from signalos_lib.skill_validators import validate_skill_artifacts
                        violations = validate_skill_artifacts(
                            skills=task.get("skills"),
                            task=task,
                            root=root,
                            written_files=files_written,
                            task_response=full_response,
                        )
                        errors = [v for v in violations if v.severity == "error"]
                        warnings = [v for v in violations if v.severity == "warning"]
                        if warnings:
                            sys.stdout.write(
                                f"[orchestrate] skill-enforcement (warn): "
                                + "; ".join(str(v) for v in warnings) + "\n"
                            )
                        if errors:
                            failure_msg = (
                                "Skill artifact violation(s):\n"
                                + "\n".join(f"  - {v}" for v in errors)
                            )
                            result = {
                                **result,
                                "status": "failed",
                                "failure": failure_msg,
                            }
                            extraction_note = f"{extraction_note}; {len(errors)} skill violation(s)"
                            sys.stdout.write(
                                f"[orchestrate] skill-enforcement (fail): "
                                + "; ".join(str(v) for v in errors) + "\n"
                            )
                    except Exception as exc:  # pragma: no cover -- defensive
                        sys.stdout.write(f"[orchestrate] skill validation crashed: {exc}\n")
                else:
                    extraction_note = "extracted file blocks but write failed"
            else:
                # LLM didn't follow the structured output protocol; mark the
                # task as failed so the user sees it. The response_preview
                # is still saved for inspection.
                result = {**result, "status": "failed", "failure": "no file blocks in response"}
                extraction_note = "LLM response had no parseable file blocks"

    if files_written:
        result = {**result, "files_written": files_written, "summary": extraction_note}
    elif extraction_note:
        result = {**result, "summary": extraction_note}

    # Notify status card
    try:
        status_callback(root)
    except Exception:
        pass  # status card failures are non-fatal

    return {**task, "result": result, "step_id": step_id}


def _read_harness_response(root: Path, session_id: str | None, result: dict[str, Any]) -> str:
    """Pull the LLM's full response back out of the harness call directory.

    `harness._persist_response_preview` saves the (redacted, 4 KB-capped)
    response to .signalos/sessions/<sid>/calls/<call_id>/response.preview.txt
    immediately after the LLM call. We must look for that exact filename
    or file extraction silently no-ops and every wave produces zero files.

    `response.txt`, `preview.txt`, and `response.md` are kept as
    fallbacks for legacy session directories or future harness writes
    that might use those names.

    Last-resort: the result dict carries `response_preview` (first 200
    chars) -- not enough for real LLM responses but useful when running
    against the canned TestProvider, whose entire response fits.
    """
    call_id = result.get("call_id") or result.get("callId")
    sid = result.get("session_id") or session_id
    if call_id and sid:
        # SOURCE OF TRUTH for the call-dir layout is harness._call_dir():
        #   .signalos/sessions/<sid>/harness/<call_id>/...
        # The earlier "calls/" subdir name was a divergence between the
        # two modules' assumptions and meant the orchestrator looked in
        # an empty directory after every wave -- file extraction silently
        # no-op'd. If harness's layout ever changes, mirror it here.
        call_dir = root / ".signalos" / "sessions" / str(sid) / "harness" / str(call_id)
        # response.preview.txt is the actual filename harness writes today.
        # The others are historical / forward-compatible.
        for candidate in (
            "response.preview.txt",
            "response.txt",
            "preview.txt",
            "response.md",
        ):
            p = call_dir / candidate
            if p.is_file():
                try:
                    return p.read_text(encoding="utf-8")
                except OSError:
                    continue
    # In-memory fallback: when the disk write hasn't completed (or the
    # harness was patched in a test), the result dict's response_preview
    # may contain the full text -- worth trying before giving up.
    preview = result.get("response_preview") or ""
    return preview if isinstance(preview, str) else ""



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

def _route_next_gate_action(
    root: Path,
    wave_id: str,
    session_id: str | None,
    project_id: str = "default",
) -> dict[str, Any]:
    """Wave-engine router — decides what the engine should do next.

    Per WAVE-ENGINE-DESIGN §10. Replaces the earlier refuse-by-default
    `_check_orchestrate_gates`. The router never says "no" for normal
    flow — it tells the caller which gate-agent to fire next. Hard
    refusals are reserved for pathological cases.

    Return shape:
        {
          "action":  "build"
                   | "fire-agent-G0"
                   | "fire-agent-G1"
                   | "fire-agent-G2"
                   | "fire-agent-G3"
                   | "fire-agent-G5"
                   | "refuse-pathological"
                   | "override-with-audit",
          "current_gate": "G0" | "G1" | ... | None,
          "evidence": "<reason / context for the caller>",
        }

    Routing logic:
        - status read fails → refuse-pathological + log enforcement error
        - SIGNALOS_GATE_OVERRIDE=1 → override-with-audit + log violation
        - some prior gate unsigned → fire-agent-<that-gate>
        - all prior gates (G0..G3) signed → build (proceed to G4)

    The G4 build path is gated on G0..G3 because per the design G3 (design)
    auto-fires before build. Once G3-agent machinery exists, the router
    naturally re-routes through it.

    The `project_id` parameter is plumbing for future multi-project support
    per design §3.2. Today only "default" is used; future milestones expose
    a Sidebar picker that drives this.
    """
    try:
        from .status import get_wave_status
    except Exception as exc:
        _append_audit_entry(root, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": "enforcement-error-orchestrate-gate-check",
            "wave_id": wave_id,
            "session_id": session_id,
            "project_id": project_id,
            "reason": f"status module unavailable: {exc}",
            "source": "orchestrator._route_next_gate_action",
        })
        return {
            "action": "refuse-pathological",
            "current_gate": None,
            "evidence": "Gate-state check failed (status module unavailable). Failing closed for safety.",
        }

    try:
        status = get_wave_status(root)
    except Exception as exc:
        _append_audit_entry(root, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": "enforcement-error-orchestrate-gate-check",
            "wave_id": wave_id,
            "session_id": session_id,
            "project_id": project_id,
            "reason": f"get_wave_status raised: {exc}",
            "source": "orchestrator._route_next_gate_action",
        })
        return {
            "action": "refuse-pathological",
            "current_gate": None,
            "evidence": "Could not read gate state. Failing closed for safety.",
        }

    gates = status.get("gates") or {}

    # The build gate (G4) is what this orchestrator dispatches. G0..G3 must
    # all be signed for the build to proceed. G5 (ship) is the post-build
    # gate and is not a precondition.
    required_prior = ["G0", "G1", "G2", "G3"]
    next_unsigned = next((g for g in required_prior if not gates.get(g)), None)

    if next_unsigned is None:
        # Everything that must be signed before build is signed → proceed.
        return {
            "action": "build",
            "current_gate": "G4",
            "evidence": "G0..G3 signed; proceeding to G4 build dispatch.",
        }

    # Some prior gate unsigned. Check the headless-override env var (per
    # AMD-CORE-111 + design §8 — the env var is the CI/headless-only path;
    # interactive sessions get per-violation user confirmation, not silent
    # bypass).
    override = os.environ.get("SIGNALOS_GATE_OVERRIDE") == "1"
    if override:
        _append_audit_entry(root, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": "violation:orchestrate-gate-skip",
            "wave_id": wave_id,
            "session_id": session_id,
            "project_id": project_id,
            "missing_gate": next_unsigned,
            "reason": "SIGNALOS_GATE_OVERRIDE=1 — headless-mode skip-with-audit per AMD-CORE-111",
            "source": "orchestrator._route_next_gate_action",
        })
        return {
            "action": "override-with-audit",
            "current_gate": next_unsigned,
            "evidence": (
                f"Headless override active: skipping required gate {next_unsigned}. "
                "Skip logged as violation in audit trail."
            ),
        }

    # Normal routing: tell the caller which agent to fire.
    return {
        "action": f"fire-agent-{next_unsigned}",
        "current_gate": next_unsigned,
        "evidence": (
            f"{next_unsigned} not signed. Fire the {next_unsigned} agent before build can proceed."
        ),
    }


# ---------------------------------------------------------------------------
# Per-gate agent auto-dispatch (G0, G1, G3, G5)
# ---------------------------------------------------------------------------


def _dispatch_gate_agent(
    root: Path,
    gate: str,
    wave_id: str,
    session_id: str | None,
    provider_name: str | None = None,
    model: str = harness_lib.DEFAULT_MODEL,
    project_id: str = "default",
) -> dict[str, Any]:
    """Load and invoke the LLM agent for *gate*, returning its output.

    Follows the same pattern as the G4 build dispatch (harness + provider)
    but operates at the gate level: loads the agent .md as a system prompt,
    sends workspace context, and returns the agent's response for the
    orchestrator to process.

    Returns:
        {
            "status": "completed" | "failed" | "no_agent" | "no_api_key",
            "gate": str,
            "output": str,          # agent response text
            "tokens_in": int | None,
            "tokens_out": int | None,
            "error": str | None,
        }
    """
    from .agent_loader import load_agent

    agent = load_agent(gate)
    if not agent["exists"]:
        return {
            "status": "no_agent",
            "gate": gate,
            "output": "",
            "tokens_in": None,
            "tokens_out": None,
            "error": f"Agent file for {gate} not found at {agent.get('path', '?')}",
        }

    # Build context from workspace inspection.
    from .wave_engine import inspect as wave_inspect
    inspection = wave_inspect(root, project_id=project_id)

    context_lines = [
        f"Gate: {gate}",
        f"Wave: {wave_id}",
        f"Project: {project_id}",
        f"Session: {session_id or 'none'}",
        "",
        "Current gate status:",
    ]
    for g in ["G0", "G1", "G2", "G3", "G4", "G5"]:
        art = inspection["artifacts"].get(g, {})
        signed = "signed" if art.get("signed") else "unsigned"
        exists = "exists" if art.get("exists") else "missing"
        context_lines.append(f"  {g}: {signed}, artifact {exists}")
        if art.get("snippet"):
            context_lines.append(f"      snippet: {art['snippet'][:120]}")
    context_lines.append("")
    context_lines.append("Workspace root: " + str(root))

    context = "\n".join(context_lines)
    system_prompt = agent["content"]
    user_prompt = (
        f"You are the {gate} gate agent. Produce the required artifact for this gate.\n\n"
        f"--- Workspace Context ---\n{context}\n"
    )

    # Resolve LLM provider.
    try:
        provider = _resolve_provider(provider_name)
    except Exception as exc:
        return {
            "status": "failed",
            "gate": gate,
            "output": "",
            "tokens_in": None,
            "tokens_out": None,
            "error": f"Provider resolution failed: {exc}",
        }

    # Call the LLM.
    try:
        response_text, tokens_in, tokens_out = provider.call(
            f"{system_prompt}\n\n{user_prompt}",
            model,
        )
    except Exception as exc:
        _append_audit_entry(root, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": "gate-agent-dispatch-error",
            "gate": gate,
            "wave_id": wave_id,
            "session_id": session_id,
            "project_id": project_id,
            "error": str(exc),
        })
        return {
            "status": "failed",
            "gate": gate,
            "output": "",
            "tokens_in": None,
            "tokens_out": None,
            "error": f"LLM call failed: {exc}",
        }

    # Audit success.
    _append_audit_entry(root, {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": "gate-agent-dispatched",
        "gate": gate,
        "wave_id": wave_id,
        "session_id": session_id,
        "project_id": project_id,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    })

    return {
        "status": "completed",
        "gate": gate,
        "output": response_text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "error": None,
    }


def run_wave(
    wave_id: str,
    plan_path: str,
    *,
    session_id: str | None = None,
    max_concurrent: int = 5,
    provider_name: str | None = None,
    cwd: Path | None = None,
    model: str = harness_lib.DEFAULT_MODEL,
    project_id: str = "default",
) -> dict[str, Any]:
    """Orchestrate parallel execution of all tasks in a Wave.

    1. Calls worktree-manager.sh create --wave <id> --plan <path>
    2. Reads .signalos/worktree-state.json for the task list
    3. Dispatches run_step() calls concurrently (up to max_concurrent)
    4. Prints the status card after each task state change
    5. Calls worktree-manager.sh reconcile then retire after all tasks
    6. Returns a summary dict with per-task results

    The *project_id* parameter is plumbing for future multi-project support
    per WAVE-ENGINE-DESIGN §3.2. Today only "default" flows from callers;
    the router and audit entries record it so future UI exposure doesn't
    require an engine refactor.

    Returns dict keys:
        wave_id, session_id, tasks, completed, failed, paused,
        elapsed_ms, status ("all_completed"|"some_failed"|"empty"),
        project_id
    """
    root = _repo_root(cwd)

    # AMD-CORE-110 Layer 3 — wave-engine router (per WAVE-ENGINE-DESIGN §10).
    # Replaces the earlier refuse-by-default check. Decides whether to:
    #   - proceed with G4 build (all prior gates signed)
    #   - signal that an earlier gate-agent must fire first (re-route)
    #   - proceed under headless override (logged as violation)
    #   - refuse for pathological cases (status read failure / gate corruption)
    route = _route_next_gate_action(root, wave_id, session_id, project_id=project_id)
    if route["action"] == "refuse-pathological":
        return {
            "wave_id": wave_id,
            "session_id": session_id,
            "project_id": project_id,
            "status": "blocked_by_status_error",
            "tasks": [],
            "completed": 0,
            "failed": 0,
            "paused": 0,
            "elapsed_ms": 0,
            "route": route,
        }
    if route["action"].startswith("fire-agent-"):
        # Auto-dispatch the gate agent. Extract the gate name from the action
        # (e.g. "fire-agent-G0" → "G0") and invoke the corresponding agent.
        target_gate = route["action"].replace("fire-agent-", "")
        _emit_task_progress(wave_id, f"gate-{target_gate}", "running",
                           f"Dispatching {target_gate} gate agent")
        agent_result = _dispatch_gate_agent(
            root,
            target_gate,
            wave_id,
            session_id,
            provider_name=provider_name,
            model=model,
            project_id=project_id,
        )
        if agent_result["status"] == "no_agent":
            # Agent file not available — fall back to needs_gate so the
            # caller (chat layer) can surface the re-route to the user.
            _emit_task_progress(wave_id, f"gate-{target_gate}", "error",
                               f"No agent file for {target_gate}")
            return {
                "wave_id": wave_id,
                "session_id": session_id,
                "project_id": project_id,
                "status": "needs_gate",
                "tasks": [],
                "completed": 0,
                "failed": 0,
                "paused": 0,
                "elapsed_ms": 0,
                "route": route,
            }

        state = "done" if agent_result["status"] == "completed" else "error"
        _emit_task_progress(wave_id, f"gate-{target_gate}", state,
                           f"{target_gate} agent {agent_result['status']}")
        return {
            "wave_id": wave_id,
            "session_id": session_id,
            "project_id": project_id,
            "status": "gate_agent_completed" if agent_result["status"] == "completed" else "gate_agent_failed",
            "gate": target_gate,
            "agent_output": agent_result["output"],
            "agent_error": agent_result.get("error"),
            "tokens_in": agent_result.get("tokens_in"),
            "tokens_out": agent_result.get("tokens_out"),
            "tasks": [],
            "completed": 0,
            "failed": 0,
            "paused": 0,
            "elapsed_ms": 0,
            "route": route,
        }
    # action == "build" or "override-with-audit" → proceed.

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

    # Step 1: Create worktrees (skipped when bash is unavailable or the
    # caller forced --no-worktrees via SIGNALOS_NO_WORKTREES=1). When skipped,
    # tasks are loaded directly from PLAN.tasks.yaml and executed in root.
    use_worktrees = (
        os.environ.get("SIGNALOS_NO_WORKTREES") != "1"
        and _bash_available()
        and _worktree_manager(root).is_file()
    )

    tasks: list[dict[str, Any]] = []
    if use_worktrees:
        sys.stdout.write(f"[orchestrate] Creating worktrees...\n")
        proc = _run_wm(root, "create", "--wave", wave_id, "--plan", plan_path)
        if proc.returncode != 0:
            return {
                "wave_id": wave_id,
                "session_id": session_id,
                "project_id": project_id,
                "status": "worktree_create_failed",
                "tasks": [],
                "completed": 0,
                "failed": 1,
                "paused": 0,
                "elapsed_ms": 0,
            }
        # Step 2: Read task list from worktree-state.json
        tasks = _read_tasks(root)
        if not tasks:
            sys.stdout.write("[orchestrate] No tasks found in worktree-state.json\n")
            print_status_card(root)
            return {
                "wave_id": wave_id,
                "session_id": session_id,
                "project_id": project_id,
                "status": "empty",
                "tasks": [],
                "completed": 0,
                "failed": 0,
                "paused": 0,
                "elapsed_ms": 0,
            }
    else:
        # No-worktree fallback path. Read tasks from PLAN.tasks.yaml directly.
        # Each task runs in the workspace root (sequential, isolation gone).
        # This is the path Windows users without Git Bash hit by default.
        reason = "bash unavailable" if not _bash_available() else (
            "worktree-manager.sh missing" if not _worktree_manager(root).is_file()
            else "SIGNALOS_NO_WORKTREES=1"
        )
        sys.stdout.write(
            f"[orchestrate] No-worktree mode ({reason}). Tasks will run sequentially in workspace root.\n"
        )
        tasks = _tasks_from_plan(Path(plan_path), wave_id)
        if not tasks:
            sys.stdout.write(f"[orchestrate] No tasks loaded from {plan_path}\n")
            return {
                "wave_id": wave_id,
                "session_id": session_id,
                "project_id": project_id,
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
        # Emit a 'running' progress event per dispatched task so the UI can
        # mark them as in_progress immediately, before the LLM call returns.
        for task in runnable:
            sid = task.get("step_id") or task.get("task") or task.get("branch", "unknown")
            _emit_task_progress(wave_id, sid, "running", detail=task.get("title"))
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
                        _emit_task_progress(wave_id, step_id, "error", detail=f"timed out after {task_timeout}s")
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
                            _emit_task_progress(wave_id, step_id, "running", detail="paused (T2) — awaiting resume")
                        elif status in ("failed", "aborted"):
                            # T3: mark for downstream cancellation
                            tid = futures[future].get("task") or futures[future].get("branch", step_id)
                            aborted_task_ids.add(tid)
                            _emit_task_progress(wave_id, step_id, "error", detail=step_result.get("reason") or status)
                        else:
                            _emit_task_progress(wave_id, step_id, "done", detail=step_result.get("summary"))
                    except Exception as exc:
                        task_results.append({
                            "result": {"status": "failed", "failure": str(exc)},
                            "step_id": "unknown",
                        })

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # Step 4: Print final status card
    print_status_card(root)

    # Step 5: Reconcile + retire (only when worktrees were used; the
    # no-bash fallback path leaves files in workspace root and has nothing
    # to merge or retire).
    if use_worktrees:
        sys.stdout.write("[orchestrate] Reconciling worktrees...\n")
        _run_wm(root, "reconcile", "--wave", wave_id)
        sys.stdout.write("[orchestrate] Retiring merged worktrees...\n")
        _run_wm(root, "retire", "--wave", wave_id)
    else:
        sys.stdout.write("[orchestrate] No-worktree mode: skipping reconcile/retire.\n")

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

    summary = {
        "wave_id": wave_id,
        "session_id": session_id,
        "project_id": project_id,
        "status": overall_status,
        "tasks": task_results,
        "completed": completed,
        "failed": failed,
        "paused": paused,
        "elapsed_ms": elapsed_ms,
    }

    # M4: auto-commit wave output. Best-effort — never blocks the return.
    # Only fire on a successful wave (no failed/aborted tasks); otherwise
    # we'd be locking in half-finished state the user almost certainly
    # wants to revisit.
    if overall_status == "all_completed" and completed > 0:
        try:
            commit_outcome = _auto_commit_wave(root, wave_id, summary)
            summary["auto_commit"] = commit_outcome
        except Exception as exc:  # pragma: no cover — defensive
            _append_audit_entry(root, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "action": "auto-commit-failed",
                "wave_id": wave_id,
                "reason": f"unhandled: {exc}",
            })
            summary["auto_commit"] = {"status": "failed", "reason": f"unhandled: {exc}"}

    return summary
