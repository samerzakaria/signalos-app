<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
# Claude Code emitter

Renders SignalOS sessions into Claude Code's native layout:
- `CLAUDE.md` — session preamble
- `.claude/commands/*.md` — one file per SignalOS command
- `.claude/hooks.json` — hook registrations (W1.1+)

## W1.1 hooks

Every Claude Code session registers the four new W1.1 step-level hooks.
Run `register-hooks.sh <output-dir>` (invoked by the installer) to write
`.claude/hooks.json`. All four events are emitted by the central
dispatcher at `core/tool-adapters/dispatcher/session-hook-dispatch.sh`
with `--event <name>` and route to the matching script under
`core/execution/hooks/<event>/<event>.sh`.

| Event | Purpose | Script |
|---|---|---|
| `step-started` | Appends `step.started` to the session journal and consults the opt-in pause gate before a PLAN step runs. | `core/execution/hooks/step-started/` |
| `step-completed` | Closes the step's metrics timer and writes `step.completed` on success. | `core/execution/hooks/step-completed/` |
| `step-failed` | Records the failure reason and exit code, then writes `step.failed`. | `core/execution/hooks/step-failed/` |
| `pre-session-compress` | Guards the in-memory compression pass — refuses to run if any disk-truth file (journal, metrics, audit trail) is in the compressor's input path. | `core/execution/hooks/pre-session-compress/` |
