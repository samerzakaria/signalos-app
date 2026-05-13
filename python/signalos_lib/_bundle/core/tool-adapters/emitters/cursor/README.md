<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
# Cursor emitter

Renders SignalOS sessions into Cursor's native layout:
- `.cursorrules` — session preamble
- `.cursor/rules/*.mdc` — one rule per SignalOS command
- `.cursor/rules/signalos-hooks.mdc` — W1.1 hook registrations

## W1.1 hooks

The Cursor emitter registers the same four W1.1 hook events as every
other emitter. All four fire from the central dispatcher at
`core/tool-adapters/dispatcher/session-hook-dispatch.sh --event <name>`
and route to `core/execution/hooks/<event>/<event>.sh`.

| Event | Purpose | Script |
|---|---|---|
| `step-started` | Appends `step.started` to the session journal and consults the opt-in pause gate before a PLAN step runs. | `core/execution/hooks/step-started/` |
| `step-completed` | Closes the step's metrics timer and writes `step.completed` on success. | `core/execution/hooks/step-completed/` |
| `step-failed` | Records the failure reason and exit code, then writes `step.failed`. | `core/execution/hooks/step-failed/` |
| `pre-session-compress` | Guards the in-memory compression pass — refuses to run if any disk-truth file (journal, metrics, audit trail) is in the compressor's input path. | `core/execution/hooks/pre-session-compress/` |
