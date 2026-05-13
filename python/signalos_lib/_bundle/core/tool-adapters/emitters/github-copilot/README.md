<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
# GitHub Copilot emitter

Renders SignalOS sessions into GitHub Copilot's native layout:
- `.github/copilot-instructions.md` — system prompt injection
- `.github/copilot-chat-agents.json` — custom chat slash commands
- `.github/copilot-hooks.json` — W1.1 hook descriptor (sidecar)

## W1.1 hooks

Copilot has no native hook schema, so the emitter publishes a sidecar
JSON and appends a "SignalOS W1.1 hooks" section to
`copilot-instructions.md` (idempotent, protected by a marker comment).
The central dispatcher at
`core/tool-adapters/dispatcher/session-hook-dispatch.sh --event <name>`
invokes `core/execution/hooks/<event>/<event>.sh`.

| Event | Purpose | Script |
|---|---|---|
| `step-started` | Appends `step.started` to the session journal and consults the opt-in pause gate before a PLAN step runs. | `core/execution/hooks/step-started/` |
| `step-completed` | Closes the step's metrics timer and writes `step.completed` on success. | `core/execution/hooks/step-completed/` |
| `step-failed` | Records the failure reason and exit code, then writes `step.failed`. | `core/execution/hooks/step-failed/` |
| `pre-session-compress` | Guards the in-memory compression pass — refuses to run if any disk-truth file (journal, metrics, audit trail) is in the compressor's input path. | `core/execution/hooks/pre-session-compress/` |
