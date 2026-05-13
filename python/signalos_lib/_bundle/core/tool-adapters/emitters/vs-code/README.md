<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
# VS Code emitter

Renders SignalOS sessions into VS Code's native layout:
- `.github/copilot-instructions.md` — session preamble (shared with the GitHub Copilot flow)
- `.vscode/settings.json` — workspace settings + `signalos.hooks` stanza
- `.vscode/signalos-hooks.json` — authoritative W1.1 hook descriptor (sidecar)

## W1.1 hooks

VS Code has no first-class "hooks" runtime; the emitter therefore
writes a **declarative** sidecar JSON and merges a `signalos.hooks`
key into `settings.json`. No Node build step is introduced. The
dispatcher at
`core/tool-adapters/dispatcher/session-hook-dispatch.sh --event <name>`
invokes the corresponding script under `core/execution/hooks/<event>/<event>.sh`.

| Event | Purpose | Script |
|---|---|---|
| `step-started` | Appends `step.started` to the session journal and consults the opt-in pause gate before a PLAN step runs. | `core/execution/hooks/step-started/` |
| `step-completed` | Closes the step's metrics timer and writes `step.completed` on success. | `core/execution/hooks/step-completed/` |
| `step-failed` | Records the failure reason and exit code, then writes `step.failed`. | `core/execution/hooks/step-failed/` |
| `pre-session-compress` | Guards the in-memory compression pass — refuses to run if any disk-truth file (journal, metrics, audit trail) is in the compressor's input path. | `core/execution/hooks/pre-session-compress/` |
