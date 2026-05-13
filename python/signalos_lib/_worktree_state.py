#!/usr/bin/env python3
"""_worktree_state.py — safe worktree state mutations.

All state mutations accept values as positional CLI arguments so that no
shell variable is ever interpolated into Python source code (B-6 fix).

Usage:
  python3 _worktree_state.py append-worktree STATE_FILE WAVE TASK STEP_ID BRANCH PATH CREATED
  python3 _worktree_state.py append-pending  STATE_FILE TASK STEP_ID QUEUED_AT
  python3 _worktree_state.py read-step-id    STATE_FILE BRANCH
"""
import json
import os
import sys
import tempfile


def cmd_append_worktree() -> None:
    if len(sys.argv) < 9:
        print("usage: _worktree_state.py append-worktree STATE_FILE WAVE TASK STEP_ID BRANCH PATH CREATED",
              file=sys.stderr)
        sys.exit(1)
    state_file, wave, task, step_id, branch, path, created = sys.argv[2:9]
    try:
        with open(state_file) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"_worktree_state: cannot read {state_file}: {exc}", file=sys.stderr)
        sys.exit(1)
    state["worktrees"].append({
        "wave":        wave,
        "task":        task,
        "step_id":     step_id,
        "branch":      branch,
        "path":        path,
        "status":      "active",
        "created":     created,
        "last_commit": "",
        "merged":      False,
    })
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(state_file)), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, state_file)
    except OSError as exc:
        print(f"_worktree_state: cannot write {state_file}: {exc}", file=sys.stderr)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        sys.exit(1)


def cmd_append_pending() -> None:
    if len(sys.argv) < 6:
        print("usage: _worktree_state.py append-pending STATE_FILE TASK STEP_ID QUEUED_AT",
              file=sys.stderr)
        sys.exit(1)
    state_file, task, step_id, queued_at = sys.argv[2:6]
    try:
        with open(state_file) as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"_worktree_state: cannot read {state_file}: {exc}", file=sys.stderr)
        sys.exit(1)
    state.setdefault("pending_queue", []).append({
        "task":      task,
        "step_id":   step_id,
        "queued_at": queued_at,
    })
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(state_file)) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, state_file)
    except OSError as exc:
        print(f"_worktree_state: cannot write {state_file}: {exc}", file=sys.stderr)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        sys.exit(1)


def cmd_read_step_id() -> None:
    if len(sys.argv) < 4:
        print("usage: _worktree_state.py read-step-id STATE_FILE BRANCH", file=sys.stderr)
        sys.exit(1)
    state_file, branch = sys.argv[2], sys.argv[3]
    try:
        with open(state_file) as f:
            state = json.load(f)
        for wt in state.get("worktrees", []):
            if wt.get("branch") == branch:
                print(wt.get("step_id", ""))
                return
    except (json.JSONDecodeError, OSError):
        pass  # caller treats empty output as missing


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    _CMD = sys.argv[1]
    if _CMD == "append-worktree":
        cmd_append_worktree()
    elif _CMD == "append-pending":
        cmd_append_pending()
    elif _CMD == "read-step-id":
        cmd_read_step_id()
    else:
        print(f"_worktree_state: unknown command: {_CMD!r}", file=sys.stderr)
        sys.exit(1)
