# SignalOS Product Delivery Bridge Implementation Plan

Status: Draft
Owner: SignalOS app
Last updated: 2026-05-24
Source: Current `signalos-app` code reality, translated from the SignalOS.NET bridge concept.

## Purpose

This plan defines the implementation path for turning the installed SignalOS
desktop app from a governed workspace and wave orchestrator into a reliable
prompt-to-product delivery system.

The bridge must not turn SignalOS into a task-management generator. Task
management can be the first proof blueprint, but the system must remain a
generic product-development operating system that can create or adopt different
product types under the same governance, evidence, validation, preview, and
handoff contract.

This plan is written for this app, not SignalOS.NET. Implementation belongs in:

- `src/` for the Preact desktop UI.
- `src-tauri/src/` for Rust IPC, workspace safety, preview runtime, and native
  permissions.
- `python/signalos_lib/` for the bundled SignalOS sidecar CLI/runtime.
- `python/` tests, `src/**/*.test.tsx`, and `src-tauri` tests for proof.

## Current Code Reality

The app already has many real pieces:

- native Tauri shell with persisted active workspace and recent workspaces;
- guarded workspace file reads and writes through Rust IPC;
- bundled Python SignalOS runtime and sidecar IPC;
- `signalos init` for product repo bootstrap;
- non-destructive existing repo adoption through `init --keep-existing`;
- prompt and document source capture through `signalos intent`;
- stack/profile manifests in `python/signalos_lib/profiles/`;
- `generic` and `react-vite` profiles;
- G0-G5 gate status and gate signing surfaces;
- wave engine and scope-drift handlers;
- plan emission through the chat prompt contract;
- plan approval, checkpointing, rollback, and orchestrator dispatch;
- worktree orchestration and no-worktree fallback;
- generated file guards, reserved path guards, and audit writes;
- preview runtime for runnable web projects;
- sandbox toggle for preview commands;
- product verification through `signalos verify-product --json`;
- release readiness through `signalos release-readiness --json`;
- QA, E2E, TDD, validator, and release evidence helpers;
- handoff export and G5 wave handoff surfaces.

The current gap is that a prompt-to-product flow can still appear complete when
the generated product is only planned, partially written, or preview-skipped.
The bridge closes that gap by making product delivery pass only when the target
repo contains a real runnable product, executed tests, runtime or preview proof,
and evidence-derived closeout.

## Validation Snapshot

Validated on 2026-05-24 against the current `signalos-app` workspace.

Commands run:

```text
python -m pytest python/test_adoption_init.py python/test_verify_product.py python/test_release_readiness.py python/test_factory_release_scenarios.py -q
```

Result:

```text
20 passed
```

Path validation also confirmed that the core files referenced by this plan
exist in the repo:

- `python/signalos_lib/commands/init.py`
- `python/signalos_lib/adoption.py`
- `python/signalos_lib/intent.py`
- `python/signalos_lib/commands/intent.py`
- `python/signalos_lib/profiles/fixtures/generic.json`
- `python/signalos_lib/profiles/fixtures/react-vite.json`
- `python/signalos_lib/commands/verify_product.py`
- `python/signalos_lib/commands/release_readiness.py`
- `python/signalos_lib/orchestrator.py`
- `python/signalos_lib/wave_engine.py`
- `src-tauri/src/ipc.rs`
- `src-tauri/src/runtime.rs`
- `src/services/signalosPrompt.ts`
- `src/services/approvePlan.ts`
- `src/services/preview.ts`
- `src/components/views/BuildView.tsx`

## Already Present Versus Bridge Work

| Capability | Current status | Evidence | Bridge implication |
|---|---|---|---|
| Product repo init | Present | `init.py`; focused tests passed | Reuse as bridge bootstrap. |
| Existing repo adoption | Present | `adoption.py`; `test_adoption_init.py`; release scenario tests passed | Extend, do not replace. |
| Prompt/source capture | Present | `intent.py`, `commands/intent.py`; release scenario tests passed | Extend into structured product intent. |
| Profiles | Present | `generic.json`, `react-vite.json`; release scenario tests passed | Extend into stack adapters and scaffold plans. |
| Layer 1 validation | Present | Existing validator suite; release scenario tests passed | Keep as pre-delivery prerequisite. |
| Product verification | Present | `verify_product.py`; `test_verify_product.py` passed | Make strict closeout depend on this. |
| Release readiness | Present | `release_readiness.py`; `test_release_readiness.py` passed | Use for publish readiness, not hidden publish. |
| Wave orchestration | Present | `orchestrator.py`, plan approval UI | Add blueprint/acceptance metadata and strict post-validation. |
| Preview runtime | Present | `runtime.rs`, `preview.ts` | Add proof capture and closeout consumption. |
| Workspace write safety | Present | `ipc.rs` generated-file guards | Keep all desktop writes on this route. |
| Handoff surfaces | Partly present | G5 handoff handler and `.signalos/handoffs` export surfaces | Extend to structured product closeout. |
| `signalos deliver` | Missing | No registered command yet | Add as bridge orchestrator entrypoint. |
| Product intent schema | Missing | Current intent is command routing and source capture | Add `.signalos/product/INTENT.json`. |
| Blueprint registry | Missing | No product blueprint files yet | Add registry and at least two blueprints. |
| Generation manifest | Missing | Current generation is plan/task oriented | Add file-level manifest and trace. |
| Runtime/UX proof closeout | Missing | Preview exists, but not strict evidence closeout | Add proof artifacts and closeout gates. |

## Non-Negotiable Principles

1. SignalOS develops products; it is not one product generator.
2. Task management must be a blueprint, not hardcoded product behavior.
3. Existing implementation is preferred over new implementation.
4. New code is allowed only when no existing command, IPC route, validator,
   profile, runner, prompt, or orchestrator surface can satisfy the behavior.
5. Every named product-delivery asset is used, optional and discoverable, or
   explicitly de-scoped with evidence.
6. A document, prompt, markdown command brief, or generated skeleton is not
   product proof.
7. The bridge fails until the generated product is buildable, testable, and
   evidence-backed for its selected profile.
8. Live deployment is never automatic. Publish or provider deploy actions require
   explicit user request.
9. Human approval remains human. The bridge may ask, pause, record, and resume,
   but it must not forge `signed_by` or auto-sign G5.
10. Existing repos are first-class. Empty repo creation, existing repo adoption,
    and existing SignalOS repo refresh must use the same closeout contract.
11. Agent execution is allowed only through typed task plans, scoped packets, or
    the existing orchestrator evidence loop.
12. The final handoff summarizes actual product evidence, not intended work.

## Reuse-First Rule

Before adding a new command, script, validator, generator, or runbook, the owner
must answer these questions in the PR notes or plan proof:

1. Which existing SignalOS app asset already covers part of this behavior?
2. Why can it not be reused as-is?
3. Can it be wrapped by the bridge instead of duplicated?
4. Can it be extended without breaking its existing proof scenario?
5. Which test prevents the new code from becoming a dead duplicate?

If those answers are missing, the work is not ready to merge.

## Existing Asset Utilization Map

| Bridge need | Existing asset to use first | Required bridge behavior |
|---|---|---|
| Product repo creation | `python/signalos_lib/commands/init.py` | Use `init` with `--name`, `--profile`, `--yes`, and safe flags. Do not create a second repo bootstrapper. |
| Existing repo adoption | `init --keep-existing`, `python/signalos_lib/adoption.py` | Preserve source files, write adoption artifacts under `.signalos/`, and continue through the same bridge path. |
| Existing SignalOS refresh | `init --refresh-bundle`, `hooks`, `validate` | Refresh runtime assets without overwriting user product code. |
| Workspace switching | `src-tauri/src/ipc.rs`, workspace settings | Keep the active product repo separate from the app repo. |
| Source prompt capture | `python/signalos_lib/intent.py`, `commands/intent.py` | Persist prompt/PRD/spec metadata in `.signalos/sources/`. |
| Product intent extraction | Extend `signalos_lib.intent` | Add product intent model there; do not create a disconnected intent subsystem. |
| Stack selection | `python/signalos_lib/profiles/` | Extend profiles into stack adapters and validation profiles. |
| UI profile selection | `src/components/NewProjectModal.tsx`, Settings UI | Reuse existing profile selectors and status hydration. |
| Governance artifacts | `gate_artifacts.json`, artifact helpers, `get_project_artifacts()` | Keep one artifact map. Do not add a third gate artifact list. |
| Gate signing | `python/signalos_lib/sign.py`, Rust gate status IPC | Use existing signing transitions. Do not write gate frontmatter directly. |
| Wave state machine | `python/signalos_lib/wave_engine.py`, `src/services/waveEngineClient.ts` | Reuse typed wave handlers for G0-G5 lifecycle and scope drift. |
| Plan generation | `src/services/signalosPrompt.ts`, `PLAN.tasks.yaml` | Extend plan schema for blueprint, acceptance, tests, and profile targets. |
| Execution | `python/signalos_lib/orchestrator.py` | Keep bounded orchestrator dispatch and existing skill validators. |
| Worktrees | bundled `worktree-manager.sh`, orchestrator fallback | Reuse for parallel work; do not create another worktree manager. |
| Generated writes | `write_workspace_files`, `preview_workspace_files` | Keep Rust path and reserved-folder enforcement. |
| Runtime preview | `src-tauri/src/runtime.rs`, `src/services/preview.ts` | Use for web profile smoke proof and user preview. |
| Product verification | `python/signalos_lib/commands/verify_product.py` | Compose build/test/lint/QA/E2E proof through this command. |
| Release readiness | `python/signalos_lib/commands/release_readiness.py` | Use for publish readiness, not for hidden publish. |
| QA/E2E/TDD | `qa_runner.py`, `e2e_runner.py`, `tdd_runner.py` | Reuse as validation engines; product tests supplement them. |
| Audit trail | Rust `audit()`, Python `_append_audit`, `.signalos/AUDIT_TRAIL.jsonl` | Append typed events; do not invent parallel audit files. |
| Deployment records | `python/signalos_lib/commands/deploy.py` | Prepare or record deploy only after explicit user action. |
| Handoff | `wave:g5-handoff`, `.signalos/handoffs`, export IPC | Extend with structured closeout evidence. Do not create a second handoff format. |
| Permissions | `src-tauri/capabilities/default.json` | Every new IPC route must be granted and tested. |

## No-Duplicate Gate

Every bridge PR must pass a no-duplicate review:

- no new product bootstrap command if `init` can be extended;
- no new adoption command if `init --keep-existing` can own the flow;
- no new validator command if `signalos validate` or `verify-product` can be
  extended;
- no new release gate if `release-readiness` can compose it;
- no new handoff writer if existing handoff export or G5 handoff can consume the
  evidence;
- no new product generator path outside the blueprint renderer or orchestrator
  task execution;
- no raw workspace writes outside `write_workspace_files` or existing Python
  SignalOS-owned `.signalos/` paths;
- no hidden publish or remote deploy path outside explicit publish/deploy
  commands;
- no direct gate artifact mutation outside `sign.py`, governance helpers, or
  approved artifact instantiation code.

The proof suite must assert the critical no-duplicate rules for init, adoption,
generation, verification, preview, deploy, and handoff.

## Target User Experience

### Greenfield Product

User intent:

```text
Build me a financial dashboard for recurring revenue, churn, and cash runway.
```

Expected outcome:

```text
FinancePulse/
  .git/
  .signalos/
  package.json or selected stack manifest
  src/
  tests or *.test.*
  README.md
  .signalos/evidence/<wave>/
  .signalos/product/CLOSEOUT.json
  .signalos/handoffs/
```

The guided flow must:

- create or select the product repo;
- capture the original prompt;
- ask only blocking clarifying questions;
- record assumptions when the user chooses speed;
- select a profile and blueprint;
- scaffold a real runnable app for the selected profile;
- generate tests and implementation;
- run build/test/verification;
- start preview or runtime smoke when supported;
- produce evidence and closeout;
- never live-deploy unless explicitly requested.

### Existing Repo Adoption

User intent:

```text
Adopt this CRM repo and add a customer risk dashboard.
```

Expected outcome:

- existing source files are preserved;
- adoption inventory and unknowns are written under `.signalos/adoption/`;
- product surfaces and profile are detected;
- prompt/PRD source is captured;
- onboarding and gates continue through the same bridge;
- generation uses patch plans and overwrite guards;
- closeout names what was changed and what was left untouched.

### Existing SignalOS Repo Refresh

User intent:

```text
Refresh SignalOS runtime in this product and continue delivery.
```

Expected outcome:

- existing `.signalos` history is preserved;
- bundled protocol files are refreshed through existing init refresh behavior;
- validators confirm runtime shape;
- delivery resumes from recorded state or asks for the missing human decision.

## New Bridge Surface

Add a bridge entrypoint in the Python sidecar:

```text
signalos deliver
```

Initial options:

```text
--prompt <text>                 Original product request.
--name <name>                   Product/repo name.
--repo-root <path>              Existing or target repo root.
--target-root <path>            Parent folder for greenfield repos.
--mode <greenfield|adopt|refresh|auto>
--profile <auto|generic|react-vite|custom>
--blueprint <auto|task-management|financial-dashboard|custom>
--deploy <none|prepare|live>    Default none.
--yes                           Accept safe defaults and record assumptions.
--interactive                   Ask blocking HITL questions in terminal.
--agent <none|packet-only|orchestrator|auto>
--max-repair-cycles <n>
--dry-run
--json
```

The desktop app should expose this as a guided Build/Deliver flow rather than
forcing users to know the CLI. The CLI remains useful for tests, headless runs,
and installed sidecar validation.

## Bridge State And Evidence

Bridge-owned state lives under `.signalos/product/`:

```text
.signalos/product/
  DELIVERY_STATE.json
  INTENT.json
  ASSUMPTIONS.json
  BLUEPRINT.json
  ACCEPTANCE_MATRIX.json
  GENERATION_MANIFEST.json
  VALIDATION_PLAN.json
  CLOSEOUT.json
  CLOSEOUT.md
  agent-runs/
  proof/
```

All state files must be deterministic JSON unless markdown is specifically for
human readout.

`DELIVERY_STATE.json` minimum fields:

```json
{
  "schema_version": "signalos.delivery_state.v1",
  "phase": "intent",
  "mode": "greenfield",
  "repo_root": "",
  "prompt_sha256": "",
  "profile": "",
  "blueprint": "",
  "wave": "",
  "status": "running",
  "updated_at": ""
}
```

## Bridge Phases

### P0 - Asset Inventory And Coverage Lock

Goal: create a machine-readable inventory of current assets and how the bridge
uses them.

Output:

```text
python/signalos_lib/product/bridge/assets.json
```

Required fields:

```json
{
  "id": "verify-product",
  "kind": "cli-command",
  "path": "python/signalos_lib/commands/verify_product.py",
  "bridge_status": "used",
  "bridge_phase": "validation",
  "proof": "python/test_product_delivery_bridge.py",
  "notes": "Canonical product verification command."
}
```

Minimum categories:

- CLI commands;
- Rust IPC routes;
- Tauri permissions;
- profile manifests;
- validators;
- orchestrator and worktree assets;
- prompts and bundled skills;
- preview runtime;
- deploy and release commands;
- handoff/export surfaces;
- proof tests.

Definition of done:

- every public sidecar command appears in the inventory;
- every product-relevant IPC route appears;
- every bundled category is classified as used, required, optional, deprecated,
  or blocked;
- no unknown bridge status remains.

### P1 - Product Intent Model

Goal: convert prompt and repo context into structured product intent.

Add or extend:

```text
python/signalos_lib/product/intent.py
python/signalos_lib/product/questions.py
python/signalos_lib/product/assumptions.py
```

Minimum model:

```json
{
  "product_name": "",
  "product_type": "",
  "target_users": [],
  "primary_workflows": [],
  "entities": [],
  "entity_relationships": [],
  "ux_surfaces": [],
  "api_surfaces": [],
  "data_sources": [],
  "integrations": [],
  "auth_requirements": [],
  "permissions": [],
  "audit_requirements": [],
  "security_constraints": [],
  "performance_expectations": [],
  "deployment_intent": "none",
  "stack_preferences": [],
  "unknowns": [],
  "assumptions": [],
  "out_of_scope": []
}
```

Rules:

- deterministic extractor first;
- no network or LLM required for base path;
- adoption merges repo-detected surfaces from `adoption.py`;
- if ambiguity blocks scaffold, tests, or code, ask the user or record an
  explicit assumption;
- write final intent to `.signalos/product/INTENT.json`;
- append an audit event.

Proof:

- task-management prompt extracts task entities and workflows;
- financial-dashboard prompt extracts metrics, data sources, charts, and
  dashboard surfaces;
- vague prompt produces questions or assumptions;
- adoption adds detected repo surfaces to intent.

### P2 - Blueprint Registry

Goal: remove product-type hardcoding from generation and planning.

Add:

```text
python/signalos_lib/product/blueprints/
  registry.json
  task-management/
    blueprint.json
    api.json
    ui.json
    tests.json
    seed.json
    acceptance.json
  financial-dashboard/
    blueprint.json
    api.json
    ui.json
    tests.json
    seed.json
    acceptance.json
```

Blueprint schema:

```json
{
  "id": "",
  "display_name": "",
  "intent_match": {},
  "required_intent_fields": [],
  "entities": [],
  "workflows": [],
  "api": [],
  "ui": [],
  "tests": [],
  "seed_data": [],
  "security": [],
  "quality_profile": "",
  "default_deferrals": [],
  "profile_support": []
}
```

Rules:

- Python may load, validate, and render blueprints;
- Python and UI prompts must not embed task-management business content as the
  only product path;
- adding a product type should be a blueprint addition plus tests;
- unknown product types use `custom` synthesized from intent and require
  stronger HITL confirmation.

Proof:

- task-management generation uses blueprint files;
- financial-dashboard generation works without task-management strings;
- invalid blueprint schema fails validation;
- guard fails if domain strings are reintroduced into core generator code.

### P3 - Profile And Stack Adapter Contract

Goal: make scaffold, generation, validation, and preview profile-aware.

Extend:

```text
python/signalos_lib/profiles/
```

Add:

```text
python/signalos_lib/product/stacks.py
```

Adapter contract:

```python
class StackAdapter:
    id: str
    def detect(self, context): ...
    def scaffold(self, context): ...
    def resolve_targets(self, context): ...
    def validation_plan(self, context): ...
    def preview_plan(self, context): ...
```

Required adapters:

- `react-vite`: real package manifest, source tree, tests, Vite preview;
- `generic`: governance and evidence only, cannot close UI product delivery
  without explicit no-runtime assumption;
- `existing-repo`: detects and respects current layout.

Future adapters:

- `next`;
- `minimal-node-api`;
- `python-fastapi`;
- `tauri`.

Rules:

- bridge must prefer a real scaffold over loose files;
- scaffold cannot be marked complete unless profile-required manifests exist;
- adapter must report blockers instead of silently falling back.

Proof:

- `react-vite` scaffold creates package/project files;
- `generic` cannot claim runnable UI delivery;
- existing repo detection does not overwrite source files;
- selected profile is written to `.signalos/profile.json`.

### P4 - Repo Lifecycle

Goal: target product repo is real, preserved, and evidence-backed.

Use existing:

- `signalos init`;
- `init --keep-existing`;
- `init --refresh-bundle`;
- workspace switching IPC;
- git checkpoint and rollback helpers.

Add bridge behavior:

- greenfield mode creates target folder and runs init;
- git initialization remains best-effort but closeout records whether `.git`
  exists;
- first commit after governance shell when git is available;
- second commit after scaffold/generation when git is available;
- final commit after proof/handoff when git is available;
- clean tree status captured at closeout;
- commit SHAs written to closeout when available.

Definition of done:

- target repo path is explicit;
- active workspace points at the product repo, not the app repo;
- `.signalos` exists;
- `.signalos/product/DELIVERY_STATE.json` exists;
- git status is captured in closeout.

### P5 - Real Scaffold

Goal: create actual product structure before product generation.

Greenfield logic:

1. create or select target repo;
2. run `signalos init` with selected profile;
3. capture source prompt;
4. instantiate governance and G0 where the existing UI flow supports it;
5. run adapter scaffold for selected profile;
6. run scaffold postflight;
7. set phase to `scaffolded`.

Rules:

- no product source generation before scaffold completion;
- `--profile auto` must explain the selected profile;
- if no runnable profile is available, closeout must say partial and blocked;
- scaffold proof cannot be only `.signalos` files.

Proof:

- e2e fails if no app manifest exists for a runnable profile;
- e2e fails if only governance files exist;
- `react-vite` and `generic` have separate proof tests.

### P6 - TDD And Acceptance Plan

Goal: tests and acceptance criteria are generated before delivery can close.

Inputs:

- `.signalos/sources/initial-intent.json`;
- `.signalos/product/INTENT.json`;
- blueprint test and acceptance files;
- wave plan/tasks;
- selected profile.

Outputs:

```text
.signalos/product/ACCEPTANCE_MATRIX.json
src/**/*.test.tsx or profile equivalent
tests/ or profile equivalent
```

Rules:

- tests must reference task IDs and acceptance IDs where practical;
- dry-run quality rows are not product closure;
- UI profiles require test or UX proof;
- the bridge must run at least build or test validation for runnable profiles;
- missing test command is a blocker for strict product delivery unless explicitly
  waived with human-visible limitation.

Proof:

- generated tests run for `react-vite`;
- failing fixture proves closeout rejects unexecuted tests;
- acceptance matrix maps prompt to task to test to result.

### P7 - Generic Product Generation

Goal: generate product files from intent, blueprint, stack adapter, and approved
wave scope.

Add:

```text
python/signalos_lib/product/generation.py
python/signalos_lib/product/manifest.py
python/signalos_lib/product/templates.py
```

Generation flow:

1. load intent;
2. select blueprint;
3. resolve profile targets;
4. render tests first;
5. render implementation files;
6. render route/module registration changes;
7. write `.signalos/product/GENERATION_MANIFEST.json`;
8. append audit event.

Manifest fields:

```json
{
  "product": "",
  "blueprint": "",
  "profile": "",
  "wave": "",
  "task_ids": [],
  "files": [
    {
      "path": "",
      "kind": "",
      "task_id": "",
      "acceptance_id": "",
      "sha256_lf": "",
      "overwrite_mode": ""
    }
  ],
  "validation_commands": []
}
```

Rules:

- no generated file without manifest metadata;
- no orphan generated file outside the manifest;
- no overwrite unless the file is bridge-owned, user-approved, or an existing
  patch plan allows it;
- writes in the desktop app go through Rust workspace file guards;
- existing repos prefer patch plans over blind writes.

Proof:

- task-management and financial-dashboard both generate from blueprints;
- generated files land inside profile target paths;
- manifest includes every generated file;
- reserved path write attempts are rejected.

### P8 - Agent Execution Bridge

Goal: allow Codex, Claude, Cursor, or the existing orchestrator to build missing
product-specific pieces while SignalOS controls scope and evidence.

Add:

```text
python/signalos_lib/product/agent_packets.py
python/signalos_lib/product/repair_loop.py
```

Packet output:

```text
.signalos/product/agent-runs/<run-id>/
  PACKET.md
  scope.json
  files-allowed.txt
  commands-allowed.txt
  validation-plan.json
  result.schema.json
  RESULT.json
```

Packet includes:

- source prompt;
- approved intent;
- selected blueprint;
- wave tasks;
- acceptance matrix;
- profile targets;
- allowed file paths;
- forbidden actions;
- validation commands;
- required result schema.

Modes:

- `none`: deterministic generation only;
- `packet-only`: write packet for user or external agent;
- `orchestrator`: use existing SignalOS orchestrator tasks;
- `auto`: use supported local mode or fall back to packet-only.

Repair loop:

```text
run validation
if fail:
  create repair packet with logs
  run supported agent mode or pause
  rerun validation
  stop at max cycles with evidence
```

Rules:

- unowned agent output is non-binding until validated;
- T3 and reserved surfaces remain blocked;
- every repair cycle stores logs and changed files;
- no hidden agent work outside packet scope.

Proof:

- packet-only mode works without external agent;
- fake-agent fixture repairs a seeded build failure;
- max repair cycles stop with clear failure;
- forbidden file modification is rejected.

### P9 - Product Validation Profile

Goal: prove product quality, not only governance wiring.

Extend:

```text
python/signalos_lib/commands/verify_product.py
python/signalos_lib/profiles/
```

Validation plan keys:

```json
{
  "install": [],
  "build": [],
  "test": [],
  "lint": [],
  "qa": [],
  "e2e": [],
  "runtime_smoke": [],
  "ux_smoke": [],
  "security": []
}
```

Minimum pass for local bridge e2e:

- workspace exists;
- profile loads;
- scaffold-required files exist;
- build command runs for runnable profile;
- product tests run or explicit blocker is recorded;
- frontend preview starts for UI profile;
- smoke proof exists for primary route;
- evidence JSON summarizes real executed checks.

Rules:

- `--dry-run` validates wiring but cannot close delivery;
- product closeout cannot pass with only skipped checks;
- missing toolchain is an infra blocker, not success;
- profile `generic` cannot close a UI product as ready.

Proof:

- `verify-product` rejects a broken build;
- `verify-product` records skipped generic checks honestly;
- release readiness consumes verification evidence.

### P10 - Runtime And UX Proof

Goal: prove the generated product can be used.

Backend or preview proof:

- start profile preview or runtime on a local port;
- wait for known route or health signal;
- capture logs and exit status;
- stop the process cleanly when the proof ends.

Frontend proof:

- open product route through preview runtime;
- capture screenshot or DOM proof;
- verify no blank page;
- verify primary workflow surface is visible.

Blueprint-specific smoke:

- task-management: create/list/complete task where supported;
- financial-dashboard: seed/read metric and render dashboard summary where
  supported.

Output:

```text
.signalos/product/proof/runtime/
  preview.log
  smoke.json
  ux-smoke.json
  screenshots/
```

Proof:

- e2e fails if screenshot or DOM proof is absent for UI products;
- e2e fails if generated product route is not reachable;
- e2e fails if primary surface is blank.

### P11 - Deploy And Publish Decision

Goal: use release and deploy assets without hidden deployment.

Use existing:

- `signalos release-readiness --json`;
- deploy record commands in `python/signalos_lib/commands/deploy.py`;
- publish command surfaces where applicable.

Bridge deploy decision:

- default: `none`;
- `prepare`: create deploy notes/package/evidence only;
- `live`: requires explicit user request and supported provider path.

Rules:

- no network deploy in default e2e;
- handoff states whether live deploy happened;
- live deploy requires target-specific proof and secrets availability;
- release readiness can say ready, blocked, or published, but does not silently
  publish.

Proof:

- no-deploy mode records explicit no-deploy decision;
- prepare mode creates evidence;
- live deploy remains blocked until a provider-specific safe path is implemented.

### P12 - Product Handoff Closeout

Goal: final app message and files summarize real product state.

Add:

```text
.signalos/product/CLOSEOUT.json
.signalos/product/CLOSEOUT.md
.signalos/handoffs/product-summary.md
.signalos/handoffs/test-evidence.md
.signalos/handoffs/operator-runbook.md
```

Closeout fields:

```json
{
  "schema_version": "signalos.product_closeout.v1",
  "product_name": "",
  "repo_path": "",
  "repo_git_head": "",
  "source_prompt_sha256": "",
  "blueprint": "",
  "profile": "",
  "generated_files": [],
  "tests_executed": [],
  "build_status": "",
  "runtime_status": "",
  "ux_status": "",
  "security_status": "",
  "deploy_status": "",
  "known_limitations": [],
  "how_to_run": [],
  "what_next": []
}
```

User-facing final message must include:

- repo path;
- product name;
- what was built;
- how to run it;
- tests/checks that passed;
- deploy status;
- known limitations;
- next action.

It must not claim ready when validation is partial.

Proof:

- closeout JSON and markdown exist;
- final UI/harness output includes repo path and run command;
- closeout refuses ready when build/test/preview proof is partial or missing.

## Required Proof Tests

Add tests under the existing test systems:

```text
python/test_product_bridge_inventory.py
python/test_product_intent_extraction.py
python/test_product_blueprints.py
python/test_product_stack_adapters.py
python/test_product_delivery_greenfield.py
python/test_product_delivery_adoption.py
python/test_product_generation_manifest.py
python/test_product_agent_packets.py
python/test_product_validation_profile.py
python/test_product_closeout.py
src/components/views/BuildView.productDelivery.test.tsx
src/services/productDelivery.test.ts
src-tauri product delivery IPC/permission tests
```

Strict closeout assertions:

- target path is not the app repo unless explicitly testing app self-hosting;
- generated repo has `.signalos`;
- runnable profile has real app manifest/workspace files;
- generated files are inside profile target paths;
- product tests were executed when profile requires them;
- build or runtime proof exists for ready closeout;
- UI products have UX proof;
- handoff summarizes actual evidence;
- no hardcoded task-management path is required.

## Migration From Current Build Flow

### Step 1 - Strict Honesty

Keep existing chat plan and orchestrator flow, but mark output as partial unless
`verify-product` and preview proof pass.

Required changes:

- show verification status after wave completion;
- do not say "done" if verification fails or is skipped for a runnable profile;
- write partial closeout with blockers.

### Step 2 - Intent And Blueprint

Extend source intent capture into product intent and blueprint selection.

Required changes:

- persist `.signalos/product/INTENT.json`;
- add blueprint registry;
- include blueprint in plan context.

### Step 3 - Real Scaffold

Ensure greenfield delivery creates a runnable profile scaffold before product
files are generated.

Required changes:

- extend profiles with scaffold templates or adapter code;
- block generation if scaffold postflight fails.

### Step 4 - Tests First

Generate acceptance matrix and tests before implementation.

Required changes:

- extend plan schema/task prompt for acceptance IDs;
- require test artifacts for product closeout.

### Step 5 - Runtime And UX Proof

Run preview and UX smoke after verification.

Required changes:

- capture runtime/preview logs;
- add screenshot or DOM proof;
- include in closeout.

### Step 6 - Professional Handoff

Generate structured closeout and handoff files from evidence.

Required changes:

- closeout command or bridge phase;
- UI summary card;
- release readiness link.

## Workstreams

### W1 - Bridge Orchestrator

Owns:

- `signalos deliver`;
- delivery state machine;
- command sequencing;
- dry-run behavior;
- final closeout.

Primary files:

- `python/signalos_lib/commands/deliver.py`;
- `python/signalos_lib/product/delivery.py`;
- `python/signalos_lib/cli.py`.

### W2 - Intent And Blueprint

Owns:

- product intent model;
- questions and assumptions;
- blueprint registry;
- task-management blueprint;
- financial-dashboard blueprint.

Primary files:

- `python/signalos_lib/intent.py`;
- `python/signalos_lib/product/intent.py`;
- `python/signalos_lib/product/blueprints/`.

### W3 - Profile And Scaffold

Owns:

- profile adapters;
- `react-vite` real scaffold;
- existing repo target resolution;
- scaffold postflight.

Primary files:

- `python/signalos_lib/profiles/`;
- `python/signalos_lib/product/stacks.py`;
- profile fixture JSON.

### W4 - Generation And Manifest

Owns:

- renderer;
- generated file manifest;
- overwrite policy;
- route/module registration;
- trace metadata.

Primary files:

- `python/signalos_lib/product/generation.py`;
- `src-tauri/src/ipc.rs` only if write contract needs metadata support.

### W5 - TDD And Validation

Owns:

- acceptance matrix;
- test generation;
- verification integration;
- strict skipped-check policy;
- release readiness evidence.

Primary files:

- `python/signalos_lib/commands/verify_product.py`;
- `python/signalos_lib/commands/release_readiness.py`;
- `python/signalos_lib/tdd_runner.py`;
- `python/signalos_lib/e2e_runner.py`.

### W6 - Runtime And UX Proof

Owns:

- preview smoke;
- screenshot/DOM proof;
- product route checks;
- proof artifact storage.

Primary files:

- `src-tauri/src/runtime.rs`;
- `src/services/preview.ts`;
- new Python proof helpers if CLI-driven.

### W7 - Agent Bridge

Owns:

- packet-only mode;
- orchestrator integration;
- repair packets;
- scoped output validation.

Primary files:

- `python/signalos_lib/orchestrator.py`;
- `python/signalos_lib/product/agent_packets.py`;
- `python/signalos_lib/product/repair_loop.py`.

### W8 - UI Delivery Experience

Owns:

- guided Build/Deliver state;
- validation result UI;
- partial/ready closeout display;
- profile and blueprint transparency;
- no hidden deployment.

Primary files:

- `src/components/views/BuildView.tsx`;
- `src/services/approvePlan.ts`;
- `src/services/waveEngineClient.ts`;
- `src/state.ts`;
- new `src/services/productDelivery.ts`.

### W9 - Proof And Regression

Owns:

- strict bridge e2e tests;
- no-hardcoding guard;
- no-duplicate guard;
- existing repo adoption proof;
- release-scenario coverage.

Primary files:

- `python/test_product_*.py`;
- `python/test_factory_release_scenarios.py`;
- `src/**/*.test.tsx`;
- `src-tauri` tests.

## Completion Levels

### Level 0 - Current State

Governed product repo, plan emission, orchestrator, preview runtime, product
verification, and release readiness exist. Product delivery can still be
overstated when proof is partial.

### Level 1 - Strict Skeleton Honesty

The app no longer claims product completion unless scaffold/build/test/preview
evidence supports it. Partial delivery is explicit.

### Level 2 - Real App Scaffold

Greenfield delivery creates a real product repo and real profile scaffold.

### Level 3 - Blueprint-Based Generation

Task management is blueprint-driven and at least one second blueprint generates.

### Level 4 - Product Tests And Build Proof

Generated product tests execute and build proof is captured.

### Level 5 - Runtime And UX Proof

Generated app starts through preview/runtime and smoke proof passes.

### Level 6 - Agent Repair Loop

Failures produce scoped repair packets and are fixed or reported with evidence.

### Level 7 - Professional Handoff

Closeout summarizes actual product evidence, run commands, limitations, and
deploy status.

### Level 8 - Generic Product System

At least two greenfield blueprints and one existing-repo adoption pass strict
end-to-end tests without product-specific hardcoding.

## Final Definition Of Done

The Product Delivery Bridge is complete when:

- `signalos deliver` exists and is registered;
- the desktop app has a guided delivery flow backed by the same state machine;
- task management is a blueprint, not hardcoded generator logic;
- at least one second product blueprint exists;
- greenfield e2e produces a real runnable product repo;
- existing repo adoption e2e passes without overwriting source files;
- generated product has profile-required project/workspace files;
- generated product tests execute when profile requires tests;
- product build or runtime proof exists;
- UI products have UX proof;
- verification and release readiness run or fail with explicit blockers;
- deploy is opt-in and evidence-backed;
- handoff is evidence-derived;
- all bridge proof tests pass;
- stale skeleton-only flow cannot pass as product delivery.

## Immediate Next Wave Recommendation

Start with Level 1 because it is the smallest high-leverage correction:

1. Add a strict delivery status model: `not_started`, `planned`, `partial`,
   `blocked`, `verified`, `ready`.
2. After a wave completes, run `signalos verify-product --json` for the active
   workspace when a profile exists.
3. Surface verification result in the Build view.
4. Write `.signalos/product/CLOSEOUT.json` even for partial results.
5. Refuse ready/complete language unless build/test/preview requirements pass.

This makes current behavior honest before adding the larger blueprint and
scaffold system.
