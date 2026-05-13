---
description: "Manage opt-in step pauses (list, resume, abort) for active sessions."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.1 — /signal-pause command spec. -->

# /signal-pause — Step-level pause (opt-in, per step)

Owner: PO. Agent executes. Operational lever — **not a Gate**.

## What it is

`signal-pause` halts a single PLAN step between Gate 3 (Build) and Gate 4
(Review) so a human can inspect intermediate state before the step
completes. Pause is **opt-in per step-spec**: you add `pause: true` to the
step declaration in the Wave's PLAN. Nothing else triggers a pause.

State lives on disk at `.signalos/sessions/<session-id>/pauses/<step-id>.json`,
so it survives editor restarts. The pause is cleared by
`signalos pause resume <step-id> --rationale "<text>"` (or aborted by
`signalos pause abort …`), which writes a sibling `.resume` (or `.abort`)
marker and appends `step.resumed` (or `step.aborted`) to the session
journal.

## What it is NOT

- **Not a Gate.** Pause is step-level and operational; Gates are
  phase-level and governance-bearing. Core still has exactly five Gates +
  Gate 0. Pause does not add a sixth.
- **Not on by default.** Default behaviour is NO pause. An adopter who
  never writes `pause: true` in any step-spec will never see a pause.
- **No `/yolo` global bypass.** Core deliberately rejects babysitter's
  "breakpoint by default, /yolo to skip" model. Pause is not a global
  mode you toggle — it is an explicit property of individual steps. The
  only way to avoid a pause is to not request one.
- **Not a T3 rescue lever.** Permanently-T3 surfaces (Constitution §C.3)
  HARD-STOP instead of pausing; resuming is refused.

## Opting a step into pause

In the Wave's PLAN, mark the step:

```yaml
- id: phase-3a.build-4
  description: "Migrate analytics schema; audit before Gate 4."
  tier: T2
  pause: true          # <-- opt-in flag
```

The subagent emits a step-spec JSON one-liner via
`SIGNALOS_PLAN_STEP_JSON` when the `step-started` hook fires. The shared
helper `core/execution/hooks/_lib/step-pause-check.sh` reads that env and
decides fast-path vs block.

## The three verbs

| Verb | Effect | Marker file | Journal event |
|---|---|---|---|
| `pause list` | Enumerate pending pauses across every session. | — | — |
| `pause resume <step-id>` | Unblock and continue. Requires `--rationale`. | `<step-id>.resume` | `step.resumed` |
| `pause abort <step-id>`  | Terminate the step. Requires `--rationale`. | `<step-id>.abort`  | `step.aborted` (cause: `manual-abort`) |

Empty rationale on `resume` / `abort` is a **hard error** (exit 1). The
rationale is written into both the marker file and the journal event;
there is no silent clear path.

## Example session

```bash
# Subagent hits a step whose PLAN spec has `pause: true`.
# step-pause-check.sh writes a pending-pause file and exits 2, halting the tool.
# `step.paused` is appended to the journal.

$ signalos pause list
{"paused_at":"2026-04-22T14:03:11Z","session_id":"W1.1.s001","step_id":"phase-3a.build-4","status":"pending","tier":"T2"}

# PO reviews, then releases:
$ signalos pause resume phase-3a.build-4 --rationale "schema diff reviewed; migrate OK"
{"event":{"rationale":"schema diff reviewed; migrate OK","schema_version":1,
          "step_id":"phase-3a.build-4","ts":"2026-04-22T14:05:42Z",
          "type":"step.resumed"},
 "marker":".signalos/sessions/W1.1.s001/pauses/phase-3a.build-4.resume",
 "session_id":"W1.1.s001","step_id":"phase-3a.build-4"}

# Re-running step-pause-check now fast-paths (exit 0) — the subagent continues.
```

## Exit codes

| Exit | Meaning |
|---|---|
| 0 | `pause list` printed; or resume/abort succeeded. |
| 1 | User error — bad args, unknown subcommand, empty rationale. |
| 2 | `resume`/`abort` on a step that has no pending pause. |
| 3 | Policy refusal — trying to resume a step that was aborted (includes T3 hard-stop). |

The hook-side helper exits with its own codes (read by `step-started`):

| Exit | Meaning |
|---|---|
| 0 | No pause requested, OR `.resume` marker already present — continue. |
| 1 | Internal error (missing env var, invalid spec JSON, `jq` absent). |
| 2 | Pause is active. Tool must halt until PO runs `pause resume`. |
| 3 | Pause requested on a T3 step — refused, step is aborted. |

## T3 refuses pause (invariant)

Pause is **incompatible** with the permanently-T3 refusal semantics of
Constitution §C.3. If a PLAN step-spec combines `pause: true` with
`tier: T3`, the hook emits `step.aborted` with
`cause: "t3-refuses-pause"` and exits 3. A subsequent
`signalos pause resume` on that step is refused (exit 3). This is the
canonical invariant that distinguishes Core from babysitter; it is
exercised by `proof/scenarios/24_pause_t3_hard_stop.sh`.

## Authority

Resuming or aborting is a PO action. The rationale is the audit record — empty rationales
are refused at the CLI layer.
