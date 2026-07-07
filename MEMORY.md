# SignalOS — Project Memory

Index of source-of-truth documents and the durable design contracts that
govern this codebase. Read these before changing protocol-related code.

## Wave engine (G0 → G5 lifecycle)

| Document | Owns |
|---|---|
| [docs/WAVE-ENGINE-DESIGN.md](docs/WAVE-ENGINE-DESIGN.md) | The wave-engine state machine + per-gate agents + scope-drift + refusal taxonomy. §12 lists implementation milestones M-W1 through M-W7 (all shipped — see commit log). |
| [docs/SYSTEM-AUDIT-AND-COMPLETION-PLAN-v0.2-2026-05-20.md](docs/SYSTEM-AUDIT-AND-COMPLETION-PLAN-v0.2-2026-05-20.md) | The v0.2 audit. §6.7 defines the G3 design three-shape contract (`doc + prototype/` / `doc + external-design-ref` / `doc + no-UI-attestation`). §2.5.2 + §6.6.1 define the enforcement universality + override-with-audit pattern that the M-W7 refusal taxonomy implements. |
| [docs/GATE-REOPEN-DESIGN.md](docs/GATE-REOPEN-DESIGN.md) | The gate-reopen state machine: cascade invalidation of later signed/waived gates, reopen budget (`SIGNALOS_GATE_REOPEN_BUDGET`, default 3), audit event kinds (reopen/invalidate/unwaive + replay reverse markers), `agent:reopen-gate` IPC + UI contract, and the scope-drift extension that detects conflicts with signed G2/G3 (resolution option e = reopen). |
| [docs/MECHANICAL-VERIFICATION.md](docs/MECHANICAL-VERIFICATION.md) | The three mechanical-verification layers of the delivery bridge: verifiability tiers + the `mechanical_pct` contract metric (acceptance.py), evidence-freshness snapshot binding (evidence_freshness.py — snapshot after final validation and after proof, verified at closeout), and the deterministic test-quality gate (test_quality.py). Artifact locations + blocking-semantics table (strict blocks / warn records / advisory never blocks). |

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

Real since Task #19. `signalos_lib/projects.py` owns the registry at
`.signalos/projects.json` (schema `signalos.projects.v1`, atomic
tmp+`os.replace` writes): `list_projects` / `create_project` (id =
slugified name, collision-suffixed, `"default"` reserved; creating
switches to the new project) / `set_active_project` /
`get_active_project` (returns `"default"` when the file is absent —
full backward compat).

Resolution order in `signalos_ipc_server.handle()`: explicit
`req["project_id"]` wins → else the workspace's active project from the
registry → `"default"`. `dispatch_cli` appends `--project-id` to the
project-aware CLI subcommands (`status`, `orchestrate`). IPC commands
`project:list` / `project:create` / `project:switch` manage the
registry; create/switch refuse with `{"status": "delivery-active"}`
while `_ACTIVE_DELIVERIES` is non-empty.

Namespacing — three resolvers in `projects.py`, all defaulting to the
byte-identical single-project layout:

- `project_state_dir` (`"default"` → workspace-root `.signalos/`, any
  other id → `.signalos/projects/<id>/`): `wave-engine-state.json`
  (wave_engine) and `worktree-state.json`. `worktree-manager.sh` accepts
  `--project-id` (or `SIGNALOS_PROJECT_ID` env) and writes the state
  file to the SAME path; `orchestrator._run_wm` / `signalos worktree`
  append the flag only for non-default ids (older deployed scripts keep
  working for default workspaces).
- `project_plan_path` (`"default"` → `<root>/PLAN.tasks.yaml`, else
  `.signalos/projects/<id>/PLAN.tasks.yaml`): `orchestrator.run_wave`
  (plan_path=None now resolves per-project — the no-worktree fallback
  reads the project's own plan), `status._load_plan_doc`, `signalos
  plan`, `preamble._wave_id_from_plan`, and the `writing-plans` skill
  validator (run_wave stamps `project_id` on each task).
- `project_governance_dir` (WAVE-ENGINE-DESIGN §3.2 milestone SHIPPED —
  `"default"` → the workspace root itself, else
  `.signalos/projects/<id>/governance/` as the base for the canonical
  `core/...` gate-artifact rel_paths): routed through
  `artifacts.resolve_workspace_path`/`resolve_gate_artifacts` and
  threaded into `sign.check_gate`/`sign_gate` (+ `signalos sign
  --project-id`), `wave_engine.inspect`, `status` gate detection and
  belief/soul/delivery-mode reads, orchestrator gating (via status),
  `validate-gate`, `validate-wave-status`, and
  `product.gate_orchestrator`. Invariant (pinned in
  test_project_governance_namespacing.py): a gate signed as project X is
  seen signed by X's inspect/status/check_gate and NOT by default.
  Known limit: gate-agent artifact *generation* (AgentLoop file writes)
  still targets repo-root-relative paths; a non-default delivery fails
  closed at sign time until creation-side namespacing lands.

Workspace-global by design: `AUDIT_TRAIL.jsonl` (one append-only chain),
vault/secrets, git checkpoints (`.signalos/wave-checkpoints/`),
`sessions/`, `missing-deps.json`. Commands without a project flag
(release-readiness, ship, serve.py, ceremonies, handoff) operate on
`"default"` via the resolvers' defaults.

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
