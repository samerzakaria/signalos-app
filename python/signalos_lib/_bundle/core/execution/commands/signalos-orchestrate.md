---
description: "Run a Wave's tasks concurrently via parallel git worktrees."
---

<!-- SignalOS Core v2.1 — /signalos-orchestrate command spec (AMD-CORE-008). -->

# /signalos-orchestrate — Parallel Wave Orchestrator

Owner: PE. Operational lever — **not a Gate**.

## What it is

`signalos-orchestrate` dispatches all tasks in a Wave PLAN concurrently,
each in its own git worktree. It wraps the lifecycle of:

1. `worktree-manager.sh create` — creates one branch + worktree per task
2. Concurrent `harness call` via `ThreadPoolExecutor` — executes each task
3. `worktree-manager.sh reconcile` — checks for drift + conflicts
4. `worktree-manager.sh retire` — removes merged worktrees

Between each task state change, the Wave status card is printed via
`signalos status`. Paused T2 tasks are logged and the orchestrator
continues with other tasks; a list of pending resumes is printed at the end.

## What it is NOT

- **Not a new Gate.** Gate count remains five + Gate 0 (§4).
- **Not free of Trust Tier enforcement.** T3 surfaces (§2) still require
  human review. T2 steps pause and wait for `signalos pause resume`.
- **Not a background daemon.** The orchestrator blocks the calling process
  until all tasks are dispatched and lifecycle commands complete.

## CLI surface

```bash
# Run a Wave with default settings
signalos orchestrate --wave W2.1 --plan core/execution/PLAN.md

# Run with specific provider and concurrency cap
signalos orchestrate \
    --wave W2.1 \
    --plan core/execution/PLAN.md \
    --provider anthropic \
    --max-concurrent 3 \
    --session-id orch-session-20260424T120000Z

# Use the test provider (no API key needed, for CI)
SIGNALOS_HARNESS_TEST=1 signalos orchestrate \
    --wave W2.1 \
    --plan core/execution/PLAN.md
```

## Arguments

| Flag | Required | Purpose |
|---|---|---|
| `--wave <id>` | yes | Wave identifier (e.g. W2.1). |
| `--plan <path>` | yes | Path to PLAN.md for the Wave. |
| `--provider <name>` | no | LLM provider. Overrides `SIGNALOS_LLM_PROVIDER`. Valid: `anthropic`, `openai`, `gemini`, `ollama`, `test`. Default: `anthropic`. |
| `--session-id <sid>` | no | Attach to an existing session. Default: new orchestrate session. |
| `--max-concurrent <n>` | no | Maximum concurrent tasks (default: 5). |
| `--model <id>` | no | LLM model id for harness calls (default: `claude-sonnet-4-5`). |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All tasks completed successfully. |
| 1 | User error (bad arguments). |
| 2 | Worktree creation failed, or all tasks failed. |
| 4 | Some tasks failed (partial success). |

## Task format in PLAN.md

The orchestrator understands three task formats (in priority order):

**1. HTML comment format (primary — AMD-CORE-008):**
```
<!-- task: id=T-001 tier=T1 parallel=true -->
<!-- task: id=T-002 tier=T2 parallel=true -->
```

**2. Checkbox list (fallback):**
```
- [ ] **Task 1**: Implement retry loop
- [ ] **Task 2**: Add unit tests
```

**3. Markdown table (fallback):**
```
| 1 | Implement retry loop |
| 2 | Add unit tests       |
```

## On-disk layout

```
.signalos/
├── worktree-state.json       # task list + per-task status + step_id
├── worktrees/
│   └── wave-W2.1/
│       ├── task-T-001/      # one worktree per task
│       └── task-T-002/
└── sessions/<session-id>/
    ├── journal.jsonl         # one step.started/completed per task
    └── metrics.jsonl         # one metrics row per task
```

## Relation to worktree-manager.sh

`signalos orchestrate` is the Python-level orchestration surface; it shells
into `core/execution/build/worktree-manager.sh` for the git worktree
lifecycle. The worktree-manager handles PLAN parsing, git operations, state
JSON writes, and journal routing via `journal-append.sh`.

## Prior art

Parallel worktree dispatch concept adapted from `a5c-ai/babysitter` (MIT).
No source code copied. SignalOS implementation is Python + POSIX shell.
Core tracks attribution in `core/CREDITS.md`.
