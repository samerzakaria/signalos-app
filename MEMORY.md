# SignalOS — Project Memory

Index of source-of-truth documents and the durable design contracts that
govern this codebase. Read these before changing protocol-related code.

## Wave engine (G0 → G5 lifecycle)

| Document | Owns |
|---|---|
| [docs/WAVE-ENGINE-DESIGN.md](docs/WAVE-ENGINE-DESIGN.md) | The wave-engine state machine + per-gate agents + scope-drift + refusal taxonomy. §12 lists implementation milestones M-W1 through M-W7 (all shipped — see commit log). |
| [docs/SYSTEM-AUDIT-AND-COMPLETION-PLAN-v0.2-2026-05-20.md](docs/SYSTEM-AUDIT-AND-COMPLETION-PLAN-v0.2-2026-05-20.md) | The v0.2 audit. §6.7 defines the G3 design three-shape contract (`doc + prototype/` / `doc + external-design-ref` / `doc + no-UI-attestation`). §2.5.2 + §6.6.1 define the enforcement universality + override-with-audit pattern that the M-W7 refusal taxonomy implements. |

### Wave-engine modules (Python)

| Module | Role |
|---|---|
| [python/signalos_lib/wave_engine.py](python/signalos_lib/wave_engine.py) | `WaveState` enum, `inspect()`, `detect_scope_drift()`, `classify_user_reply()`, `build_system_bubble()`, `WaveEngine` class. State machine + per-turn entry points. |
| [python/signalos_lib/agent_loader.py](python/signalos_lib/agent_loader.py) | `GATE_AGENT_FILES` map (G0→onboarding.md, …, G5→observability.md). `load_agent(gate)` returns the agent .md as bytes for use as LLM system prompt. |
| [python/signalos_lib/translator.py](python/signalos_lib/translator.py) | `detect_format()` + `translate()` for non-SignalOS artifacts. Markdown ships always; PDF + DOCX use pypdf + python-docx (optional deps, graceful fallback). Figma + generic URLs recorded as references without HTTP fetch. |
| [python/signalos_lib/refusal_taxonomy.py](python/signalos_lib/refusal_taxonomy.py) | `RefusalCategory` enum (A/B/C/D/E per design §9). `build_violation_prompt()` + `record_violation_confirmation()` for the §8 3-way override-with-audit flow. |
| [python/signalos_lib/_bundle/core/execution/agents/](python/signalos_lib/_bundle/core/execution/agents/) | The 6 agent markdown files — `onboarding.md` (G0), `brainstorm.md` (G1), `plan.md` (G2), `design.md` (G3 — created in M-W4), `build.md` (G4), `observability.md` (G5). Each declares Purpose / Activates / Prerequisites / Inputs / Outputs / Refusal conditions / Handoff / Trust Tier ceiling per the canonical agent shape. |

### IPC handlers exposing the engine

[python/signalos_ipc_server.py](python/signalos_ipc_server.py) routes the
following commands to a per-request `WaveEngine` (state is reconstructed
from `inspect()` each turn — design §3.1 v1 persistence model):

- `wave:begin` — ENTRY → INSPECT → DECIDE → DISPATCH
- `wave:reply` — auto-sign on affirmation per §8
- `wave:scope-drift-resolve` — 4-way prompt resolution per §6
- `wave:translate-external` — translator-mode dispatch per §7
- `wave:violation-request` / `wave:violation-confirm` — §8 3-way override
- `wave:g5-handoff` — fires `orchestrator._auto_commit_wave` after G5 sign

### Frontend wiring

| File | Role |
|---|---|
| [src/services/waveEngineClient.ts](src/services/waveEngineClient.ts) | Typed TS wrappers for every `wave:*` IPC command. `tryBegin()` swallows errors so the chat layer keeps working when the sidecar is down. |
| [src/components/ChatBubbleSystem.tsx](src/components/ChatBubbleSystem.tsx) | Renders system-kind bubbles: plain info row, 4-way scope-drift prompt, or 3-way violation prompt depending on `bubble.waveAction`. Uses `@preact/signals` (matching the rest of the codebase — preact-preset's babel transform doesn't carry `preact/hooks`). |
| [src/js/ui/chat.js](src/js/ui/chat.js) | The composer. Calls `waveEngineTryBegin()` before the LLM stream and appends the engine's `system_bubble` as a `'system'`-kind chat row. Skips the LLM stream when scope-drift is detected (the user picks the resolution first). |

## Multi-project plumbing

Per WAVE-ENGINE-DESIGN §3.2, every state-touching function takes a
`project_id: str = "default"` parameter. Today only `"default"` is used
and the layout is workspace-root (matching the pre-engine layout
exactly). When a future M exposes a project picker in the UI, the
namespace shifts to `.signalos/projects/<project_id>/...` without an
engine refactor — the parameter already threads through:

- `signalos_lib/status.py` — `get_wave_status` / `build_status_json` / `print_status_card`
- `signalos_lib/orchestrator.py` — `_route_next_gate_action` / `run_wave`
- `signalos_lib/commands/status.py` and `commands/orchestrate.py` — `--project-id` CLI flag
- `signalos_ipc_server.py` — `handle()` reads `req["project_id"]` and threads to handlers
- `signalos_ipc_server.get_status_json` — passes `--project-id` to the CLI

## Skill validators

[python/signalos_lib/skill_validators.py](python/signalos_lib/skill_validators.py)
registers per-skill artifact validators in the `VALIDATORS` dict. When a
task is tagged with a skill, `validate_skill_artifacts()` runs the
validator post-write; failures feed back into the task's
`previous_failure` for smart-retry.

The `"design"` validator (v0.2 audit §6.7) accepts any one of three
shapes for the G3 design output: `doc + prototype/`, `doc + external-ref`,
or `doc + no-UI-attestation`. Failure of all three triggers smart-retry
with shape-specific guidance.

## Test gate scripts

[scripts/test-gates.sh](scripts/test-gates.sh) (bash) and
[scripts/test-gates.ps1](scripts/test-gates.ps1) (PowerShell) implement
L0–L3 gates. L0 covers cargo fmt/clippy/check, cargo test, python tests,
and secret scan. The secret-scan exclusion list intentionally skips
`python/test_*.py` and `*.test.ts` / `*.test.tsx` because those test
fixtures contain fake-shaped secrets that the runtime redaction guard
must reject.

## Conventions

- **State in components**: `@preact/signals` (`useSignal`), not
  `preact/hooks`. The preact-preset babel transform in this codebase
  doesn't carry `preact/hooks`; importing `useState` triggers a
  `preact:transform-hook-names` plugin error in vitest. See
  [src/components/TestDebtPanel.tsx](src/components/TestDebtPanel.tsx)
  for the documented pattern.
- **Ambient .d.ts for plain-JS modules**: TS callers that import from
  `*.js` need a colocated `.d.ts`. The codebase has these for `ipc.js`,
  `chat.js`, and `dashboard.js`. Add a new one whenever a new TS module
  imports a JS module.
- **Auto-commits**: `orchestrator._auto_commit_wave` commits the wave's
  output locally at G5 sign; `git push` is intentionally manual ("user
  owns hard-to-reverse actions").
