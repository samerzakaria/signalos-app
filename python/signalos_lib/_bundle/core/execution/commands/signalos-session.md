<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
---
description: "Inspect and manage SignalOS session journals. Read-only with one exception (archive)."
---

<!-- SignalOS Core v1.1 — W1.1 signalos-session command doc. -->

# `signalos session` — session journal CLI

## One-liner

`signalos session` is the read-only front door to `.signalos/sessions/<id>/journal.jsonl`. Use it to list, inspect, and archive the session journals a Wave produces. The command never writes to a journal — the hooks do.

## Subcommands

### `signalos session list`

Prints every row of `.signalos/sessions/INDEX.jsonl`, sorted by `updated_at` descending, as a JSON array.

```sh
signalos session list
# [
#   {
#     "session_id": "W1.1-build-2026-04-22",
#     "journal": "/repo/.signalos/sessions/W1.1-build-2026-04-22/journal.jsonl",
#     "last_event": "step.completed",
#     "updated_at": "2026-04-22T14:35:10Z"
#   },
#   ...
# ]
```

### `signalos session show <session-id>`

Replays the journal and emits a summary JSON object:

```sh
signalos session show W1.1-build-2026-04-22
# {
#   "session_id": "W1.1-build-2026-04-22",
#   "started_at": "2026-04-22T14:00:00Z",
#   "ended_at": null,
#   "step_count": 4,
#   "last_event": "step.completed",
#   "last_event_ts": "2026-04-22T14:35:10Z",
#   "event_counts_by_type": {
#     "session.start": 1,
#     "step.started":  4,
#     "step.completed": 4
#   }
# }
```

Exit code 2 if the session has no journal.

### `signalos session resume <session-id>`

Inspects the journal and reports what a resuming caller should expect. **This subcommand writes nothing.** It returns the last `step_id`, the last event type, and a small whitelist of reasonable next-event types.

```sh
signalos session resume W1.1-build-2026-04-22
# {
#   "session_id": "W1.1-build-2026-04-22",
#   "last_step_id": "step-4-verify",
#   "last_event": "step.completed",
#   "last_event_ts": "2026-04-22T14:35:10Z",
#   "next_expected_event_types": ["step.started", "session.end", "gate.checked"],
#   "ended": false
# }
```

Exit code 2 if the session has no journal.

### `signalos session archive <session-id> [--force]`

Moves `.signalos/sessions/<id>/` under `.signalos/sessions/_archive/<id>/`. Refuses unless a `session.end` event exists in the journal. `--force` overrides the refusal.

```sh
signalos session archive W1.1-build-2026-04-22
# archived: /repo/.signalos/sessions/_archive/W1.1-build-2026-04-22

signalos session archive W1.1-still-running
# signalos: session W1.1-still-running has no session.end event; refusing to archive (use force=True / --force to override)
# $ echo $?
# 3
```

Exit code 2 if the session is missing; exit code 3 if the refusal fires.

## Exit codes

| Code | Meaning |
|-----:|---|
| 0 | Success. |
| 1 | Bad args, unknown subcommand, or argparse refusal. |
| 2 | Session missing (journal not found). |
| 3 | Archive refused (no `session.end` and no `--force`). |

## See also

- Skill: `core/execution/skills/session-journal/SKILL.md`
- Appender contract: `core/execution/hooks/_lib/journal-append.sh`
- Trust tiers: `core/TRUST_TIER.md`
