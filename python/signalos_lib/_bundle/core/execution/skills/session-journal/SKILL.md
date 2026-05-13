<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
---
name: session-journal
description: "The session journal protocol — the append-only, per-session JSONL event log that SignalOS Core uses to reconstruct what happened during a Wave. Use whenever writing a hook that emits a journal event, debugging a session by replay, or querying a running Wave's state. Read this before touching core/execution/hooks/_lib/journal-append.sh or any step-* hook."
---

<!-- SignalOS Core v1.1 — W1.1 session-journal skill. T2 per core/TRUST_TIER.md. -->

# Skill — session-journal

## One-liner

Every SignalOS session writes one append-only `journal.jsonl` file of lowercase-dotted events. The journal is the **disk-truth** record of a Wave: every replay, resume, debrief, and amendment references it. Never mutate a past line; only append a new one.

## When to invoke

- Authoring a hook that emits a journal event (step boundaries, gate checks, subagent traffic).
- Writing a CLI that needs to reconstruct session state (`signalos session show|resume`).
- Debugging a stuck Wave by tailing / jq-ing the journal.
- Reviewing a closed Wave for a retrospective.

Do **not** invoke this skill when the task is metrics (use `metrics.jsonl` via W1.1 Agent C's helpers) or audit-level governance events (those go to `.signalos/AUDIT_TRAIL.jsonl`).

## Canonical event types

Event type names are **lowercase, dotted, singular**. The canonical set for v1.1 is exactly 13 entries:

| Type | Trigger | Carries |
|---|---|---|
| `session.start` | First event of a session. | `session_id`, `wave_id`, `phase`, `trust_tier_sheet_hash` |
| `session.end` | Last event of a session. | `session_id`, `verdict` (`keep` / `revise` / `abandon`) |
| `step.started` | Step body is about to run. | `session_id`, `step_id`, `actor`, `intent`, optional `parent_step_id`, optional `tool` |
| `step.completed` | Step body exited 0. | `session_id`, `step_id`, `outcome`, optional `duration_ms`, `tokens_in`, `tokens_out`, `cost_usd` |
| `step.failed` | Step body exited non-zero. | `session_id`, `step_id`, `reason`, `exit_code` |
| `step.paused` | Step-pause gate suspended the step. | `session_id`, `step_id`, `rationale`, `timeout_at` |
| `step.resumed` | Pause gate released the step. | `session_id`, `step_id`, `by`, `rationale` |
| `step.aborted` | Pause timed out, cost cap hit, or PE-abort fired. | `session_id`, `step_id`, `cause` (`timeout` / `pe-abort` / `cost-cap`) |
| `hook.fired` | A hook script executed. | `session_id`, `hook_name`, optional `step_id`, `exit_code` |
| `gate.checked` | A Gate verdict was recorded. | `session_id`, `gate_id`, `verdict`, `evidence_ref` |
| `amendment.requested` | An amendment was filed mid-Wave. | `session_id`, `amendment_id`, `requester` |
| `subagent.spawned` | A sub-agent was dispatched. | `session_id`, `parent_step_id`, `subagent_id`, `task` |
| `subagent.replied` | Sub-agent returned. | `session_id`, `subagent_id`, `outcome` |

## Event schema (v1)

Every event — without exception — carries these two fields:

- `ts` — ISO-8601 UTC with trailing `Z` (e.g. `2026-04-22T14:30:05Z`). The hook is responsible for passing this to `journal-append.sh`; the appender never synthesises it.
- `schema_version` — integer `1`. Any future breaking change to the schema bumps this and ships a migrator.

Every event also carries:

- `type` — one of the 13 canonical types above.
- `session_id` — identifier of the session the event belongs to.

Step-level events additionally carry `step_id`. Tool-attributed events carry `tool`. All other fields are event-specific (see table above).

Unknown fields are permitted in the JSON but readers MUST ignore them; adopters adding instrumentation are free to include extra keys without bumping `schema_version`.

## Append semantics

- Writes go through **one** path: `core/execution/hooks/_lib/journal-append.sh`. Python code does **not** write the journal directly.
- `journal-append.sh` uses `flock(1)` with a session-scoped lock file (`journal.jsonl.lock`). Concurrent writers are serialised, never lost.
- JSONL lines are built with `jq -nc`. String concatenation is forbidden — it is how embedded newlines and quotes corrupt the journal.
- After every append, the appender upserts a single row into `.signalos/sessions/INDEX.jsonl` (one row per `session_id`, replaced atomically via `mv` from a temp file).
- Perf budget: < 5 ms per append on SSD. Enforced by `proof/scenarios/18_journal_append_perf.sh`.

## Redaction contract

- Every event passes through `core/execution/hooks/_lib/redact.py` in `--filter` mode before the bytes touch the journal.
- Redaction strips 12 secret pattern families (see that file's docstring). Any pattern hit writes `[REDACTED:<rule>]` in place of the value and preserves the event's JSON shape.
- The redaction filter is **T3 permanently** per `core/TRUST_TIER.md`. Any edit requires PE + Security co-sign and an entry in `core/governance/Retro/AMENDMENTS.md`.
- Adopters never disable redaction. There is no flag for it. If your use case genuinely requires raw bytes, that is an amendment, not a runtime toggle.

## Resume recipe

1. `signalos session show <id>` — prints the event-count summary and the last event type.
2. If `last_event` is `session.end`, the session is closed; resume is a no-op.
3. Else, the dispatcher looks at `last_step_id` and replays from there. The resume path performs zero writes — it only reads.
4. The next hook invocation (the first real resumed action) emits a fresh event; the gap in timestamps is visible in the journal and is the canonical record of "we paused at X, resumed at Y".
5. Timing out a pause: if a `step.paused` has a `timeout_at` less than `now`, the dispatcher emits `step.aborted { cause: "timeout" }` instead of `step.resumed`.

## Example `jq` one-liners

Replace `<sid>` with your session id and run from the repo root.

```sh
# 1. Every event, pretty.
jq . .signalos/sessions/<sid>/journal.jsonl

# 2. Count events per type.
jq -r '.type' .signalos/sessions/<sid>/journal.jsonl | sort | uniq -c | sort -rn

# 3. Last event of the session.
tail -1 .signalos/sessions/<sid>/journal.jsonl | jq .

# 4. All failed steps with their reasons.
jq -c 'select(.type == "step.failed") | {step_id, reason, exit_code, ts}' \
  .signalos/sessions/<sid>/journal.jsonl

# 5. Total duration_ms across completed steps.
jq -s '[.[] | select(.type == "step.completed") | .duration_ms // 0] | add' \
  .signalos/sessions/<sid>/journal.jsonl

# 6. Every session and its last event.
jq -c '{session_id, last_event, updated_at}' .signalos/sessions/INDEX.jsonl

# 7. Find sessions with an open pause (step.paused never resumed or aborted).
jq -rc '.type + " " + .step_id' .signalos/sessions/<sid>/journal.jsonl | \
  awk '$1=="step.paused"{p[$2]=1} $1~/^step\.(resumed|aborted)$/{delete p[$2]} END{for(s in p) print s}'
```

## Cross-references

- Disk-truth invariant: `core/TRUST_TIER.md` rows for journal/INDEX/redaction.
- Metrics (separate stream): `core/execution/hooks/_lib/metrics-append.sh` (Agent C).
- Step-pause controller: `cli/signalos_lib/pause.py` (Agent B).
- Dispatcher that fires step-* hooks: `core/tool-adapters/dispatcher/` (Agent D).
- CLI wrappers: `signalos session list|show|resume|archive` (see `core/execution/commands/signalos-session.md`).
