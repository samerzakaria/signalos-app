<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
# Windsurf emitter

Renders SignalOS sessions into Windsurf's native layout:
- `.windsurfrules` — single markdown file holding the preamble, command registry, and W1.1 hook registrations

## W1.1 hooks

Windsurf has no JSON-based hook descriptor; the emitter therefore
appends a markdown section guarded by the `<!-- signalos-w11-hooks -->`
marker (idempotent). The dispatcher at
`core/tool-adapters/dispatcher/session-hook-dispatch.sh --event <name>`
invokes `core/execution/hooks/<event>/<event>.sh`.

| Event | Purpose | Script |
|---|---|---|
| `step-started` | Appends `step.started` to the session journal and consults the opt-in pause gate before a PLAN step runs. | `core/execution/hooks/step-started/` |
| `step-completed` | Closes the step's metrics timer and writes `step.completed` on success. | `core/execution/hooks/step-completed/` |
| `step-failed` | Records the failure reason and exit code, then writes `step.failed`. | `core/execution/hooks/step-failed/` |
| `pre-session-compress` | Guards the in-memory compression pass — refuses to run if any disk-truth file (journal, metrics, audit trail) is in the compressor's input path. | `core/execution/hooks/pre-session-compress/` |
