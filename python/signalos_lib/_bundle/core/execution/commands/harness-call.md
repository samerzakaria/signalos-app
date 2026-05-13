---
description: "Run a single PLAN step headlessly through the harness emitter."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.2 — /harness-call command spec (AMD-CORE-004). -->

# /harness-call — Run a PLAN step headlessly

Owner: PE. Operational lever — **not a Gate**.

## What it is

`harness-call` executes one PLAN step without an attached editor. It
invokes the **Anthropic Messages API** through the `anthropic` Python
SDK (pinned `>=0.39,<1.0` in `cli/requirements.txt`) and emits the
same four W1.1 journal/metrics events as the seven editor emitters:

- `step.started`   — before the LLM call
- `step.completed` — on a clean response
- `step.failed`    — on any error OR if the call was aborted
- `pre-session-compress` — if the caller signals the journal is about
  to be compacted (forwarded through the dispatcher)

Because it emits identical events, the observability dashboard, the
step-pause controller, the `signalos session` reader, and the
`session.end` path all behave byte-identically whether the step ran in
Claude Code, Cursor, or the harness.

## What it is NOT

- **Not a new Gate.** It is a tool-adapter emitter (the 8th). Gate
  count remains five + Gate 0.
- **Not a free bypass of pause.** If the PLAN step-spec declares
  `pause: true`, the step-started hook still pauses the step. The
  harness blocks just like an editor would and requires
  `signalos pause resume <step-id> --rationale "<text>"` to continue.
  A T3-aborted step is still refused — pause semantics are orthogonal
  to which emitter fired the step.
- **Not silent.** Every call writes one metrics row and at minimum
  two journal events (`step.started` plus one of `step.completed` /
  `step.failed`). A headless run that leaves no trail is a bug.
- **Not network-required in CI.** `SIGNALOS_HARNESS_TEST=1` short-
  circuits the Messages API to a deterministic canned response so
  proof scenarios can exercise the event-emission path without an API
  key.

## CLI surface

```bash
# Run one step — prompt inline
signalos harness call \
    --step S-042 \
    --prompt "Implement the retry loop for the upload adapter." \
    --model claude-sonnet-4-5

# Run one step — prompt loaded from a file
signalos harness call \
    --step S-042 \
    --prompt-file .signalos/plans/S-042.prompt.md \
    --session-id harness-session-20260423T014200Z-a1b2c3

# Inspect a completed or in-flight call
signalos harness status harness-20260423T014203Z-1a2b3c4d

# Request abort on a running call
signalos harness abort harness-20260423T014203Z-1a2b3c4d
```

Every subcommand writes a single JSON blob to stdout on success; errors
go to stderr with a non-zero exit code.

## Arguments (call)

| Flag | Required | Purpose |
|---|---|---|
| `--step <id>` | yes | Step identifier. Mirrors the PLAN step-spec key. |
| `--prompt <text>` | one-of | Inline prompt body. Mutually exclusive with `--prompt-file`. |
| `--prompt-file <path>` | one-of | Path to a file whose contents are the prompt. |
| `--model <id>` | no | Anthropic model id. Defaults to `claude-sonnet-4-5`. |
| `--session-id <sid>` | no | Attach to an existing session. Default: new harness session. |
| `--parent-step-id <id>` | no | Parent step when this call was spawned by another. |
| `--intent <text>` | no | Short human-readable intent; ends up in the `step.started` event as `--intent`. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | `step.completed` event emitted; call state = `completed`. |
| 1 | User error (bad arg, prompt empty, session/step not found). No `step.failed` event. |
| 2 | Execution error (Anthropic API failure, hook script missing, IO error). `step.failed` event emitted when the session/step resolve. |
| 3 | Policy refusal. Currently unused by the harness itself; reserved for the future T3 hard-stop path. |

## Per-call on-disk layout

```
.signalos/sessions/<session-id>/
├── journal.jsonl               # four events emitted by this call land here
├── metrics.jsonl               # one tool-level metric row per call
└── harness/
    └── <call-id>/              # e.g. harness-20260423T014203Z-1a2b3c4d
        ├── state.json          # mutable per-call state (running/completed/failed/aborted)
        ├── response.preview.txt # truncated, redacted preview of the model output
        └── abort.flag          # present iff abort was requested
```

`state.json` is the only file the `signalos harness status` and
`signalos harness abort` commands read / mutate. `journal.jsonl` and
`metrics.jsonl` remain append-only — the harness writes to them only
through `core/execution/hooks/<event>/<event>.sh` and
`core/execution/hooks/_lib/metrics-append.sh` (AMD-CORE-001 invariant).

## How it relates to `--headless` on the dispatcher

`core/tool-adapters/dispatcher/session-hook-dispatch.sh --headless`
forces the dispatcher to select the 8th emitter
(`core/tool-adapters/emitters/harness/emit.sh`) instead of auto-
detecting an editor. It sets `SIGNALOS_TOOL=harness` in the dispatcher
process, so the existing tool-override path carries the selection.

`signalos harness call` does NOT need to re-run the dispatcher to fire
step events — it shells into `step-started.sh` / `step-completed.sh` /
`step-failed.sh` directly. The `--headless` flag is for operators who
want to re-render the `.signalos/harness/` config tree from the
canonical registries.

## Prior art

The `harness:call` concept is borrowed from `a5c-ai/babysitter` (MIT).
No source code was copied; the SignalOS implementation is Python + POSIX
shell, while babysitter is a Node workspace. Core tracks attribution in
`core/CREDITS.md`.
