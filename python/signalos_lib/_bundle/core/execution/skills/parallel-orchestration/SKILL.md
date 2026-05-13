---
name: parallel-orchestration
description: Orchestrate wave tasks across parallel git worktrees with concurrent harness execution and reconciliation.
---

# Skill: parallel-orchestration
<!-- SignalOS Core v2.1 — AMD-CORE-008 -->

**Wave:** W2.1  
**Amendment:** AMD-CORE-008  
**Owner role:** PE  

## What this skill covers

How to fan out a Wave's tasks across parallel git worktrees, execute each
task concurrently via the headless harness, reconcile results, and retire
merged branches. The canonical command is `signalos orchestrate`.

## When to use

| Situation | Use |
|---|---|
| Wave has 2+ independent T1/T2 tasks that can proceed in parallel | `signalos orchestrate` |
| Single sequential task, or tasks have dependencies between them | `signalos harness call` per step |
| CI batch run with no editor attached | `signalos orchestrate` with `SIGNALOS_HARNESS_TEST=1` for dry runs |
| Debugging a single failed task | `signalos harness call --step <id>` directly |

## Lifecycle

```
signalos orchestrate --wave W2.1 --plan PLAN.md
    │
    ├─ worktree-manager create   → one git worktree per task
    │
    ├─ ThreadPoolExecutor        → concurrent harness.run_step() per task
    │     ├─ task T-001          → step.started / step.completed events
    │     ├─ task T-002          → T2 pause detected → logged, continues
    │     └─ task T-003          → step.started / step.completed events
    │
    ├─ print_status_card()       → after each state change
    │
    ├─ worktree-manager reconcile → drift + conflict check
    └─ worktree-manager retire    → remove merged branches
```

## PLAN.md task format

Declare parallel tasks using the HTML comment format (primary):

```markdown
## Build tasks

<!-- task: id=T-001 tier=T1 parallel=true -->
Implement the retry loop in the upload adapter.

<!-- task: id=T-002 tier=T2 parallel=true -->
Refactor the auth middleware (T2 — requires human review before merge).

<!-- task: id=T-003 tier=T1 parallel=true -->
Add unit tests for the retry loop.
```

The orchestrator also accepts the fallback checkbox and table formats.

## T2 pause handling

When a task step declares `pause: true` in the step-spec, the harness
blocks and `run_step()` returns `status="paused"`. The orchestrator:

1. Logs the pause: `[orchestrate] Task T-002 is PAUSED (T2). Resume with: signalos pause resume T-002`
2. Continues dispatching other tasks
3. Prints a "Pending T2 resumes needed" list at the end

The Wave does not complete until all T2 pauses are resolved:
```bash
signalos pause resume T-002 --rationale "Reviewed auth middleware — approved"
```

## Provider selection

The orchestrator inherits provider resolution from the harness. All five
providers are supported:

```bash
# Default: anthropic
signalos orchestrate --wave W2.1 --plan PLAN.md

# Override provider
signalos orchestrate --wave W2.1 --plan PLAN.md --provider openai

# Test mode (no API key, no network)
SIGNALOS_HARNESS_TEST=1 signalos orchestrate --wave W2.1 --plan PLAN.md
```

## Concurrency limits

The `--max-concurrent` flag caps the `ThreadPoolExecutor` pool size
and also limits worktree creation (worktree-manager respects the same cap).
Default is 5. Remaining tasks queue until a slot opens.

## Observability

Each task's `run_step()` emits:
- `step.started` — before the LLM call
- `step.completed` / `step.failed` — after the call
- One metrics row per task in `metrics.jsonl`

The Wave status card is reprinted after each task completes, showing
live gate status, task tier/status, and next blocking action.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All tasks completed |
| 2 | Worktree creation failed or all tasks failed |
| 4 | Some tasks failed (partial success) |

## Wiring guard

After implementing new tasks or skills, run the wiring guard to ensure
consistent registration across all config surfaces:

```bash
bash core/governance/Validators/wiring-guard.sh --repo-root .
```

The guard runs automatically at session-start and in CI (AMD-CORE-009).
