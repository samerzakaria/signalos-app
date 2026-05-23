# SignalOS Factory Governance Implementation Plan

Status: Implemented and verified
Owner: SignalOS app
Last updated: 2026-05-23
Source: Current code reality review, not product claims or external documents.

## Purpose

This plan closes the gaps between the current SignalOS app code and the required end state:

- Layer 1 creates or adopts a dedicated governed product repo.
- The desktop app switches active work from the general SignalOS app repo into that product repo.
- The product repo contains the SignalOS governance runtime, command surfaces, agents, hooks, validators, audit trail, templates, CI gates, and phase status checks.
- Layer 2 uses that embedded SignalOS runtime to turn intent into a real product with scope, artifacts, gates, implementation, tests, evidence, and release readiness.

The plan is implementation-oriented. Each section includes the current gap, the required change, likely files or modules, acceptance criteria, and release-test coverage.

## Non-Negotiable Release Rule

The app is not ready for a new release test until every item in this document is either:

- Implemented and verified.
- Explicitly deferred with owner, reason, risk, and user-visible fallback.

The Definition of Done at the end of this file must be satisfied, including status updates and a final commit.

## Status Tracking Protocol

Every implementation PR or wave must update the status table below before completion.

| Area | Status | Evidence | Blockers |
|---|---|---|---|
| Workspace switching | Verified | Agent 1 IPC contract implemented persisted active workspace, recent workspace storage, `clear_workspace`, and `get_workspace_status`; Agent 6 switches the app into the newly created product repo after factory creation; cleanup wave added Settings recent product switcher UI, `window.switchWorkspace(path)`, status hydration for `recent_workspaces`, active workspace profile fallback, and focused UI coverage. Verified by latest gates: `npm test` = 161 passed; `npm run build` passed; `cargo test --manifest-path src-tauri/Cargo.toml` = 48 passed; `python -m pytest -q` = 490 passed, 0 skipped, 5 subtests passed | None recorded |
| Tauri capability grants | Verified | Added `workspace-core` permission set for `clear_workspace` and `get_workspace_status`; verified by `cargo test --manifest-path src-tauri/Cargo.toml` | None recorded |
| Legacy `src_old/` cleanup | Verified | Confirmed no active build imports, removed `src_old/`, verified by `npm run build` | None recorded |
| Parallel execution coordination | Verified | Contract-first, full parallel, and final release-readiness/test-release lanes landed cleanly: `9bb46d2`, `890fc44`, `3298313`, `2c50a01`, `85aefa2`, `546307b`, `02e5be7`, `a533d1d`, `937d2ae`, `d755305`, `520e41d`, `6496f3e`, `c47bb7e`, `d42d031`; combined source gates and installed-build smoke passed after integration | None recorded |
| New product repo creation | Verified | Agent 6 wired Browse to existing `pickWorkspaceFolder()`, new project creation to `createSignalosProject()`, folder creation, existing `initWorkspace()`, `instantiateGovernanceAndSignG0()`, workspace status refresh, and user-visible failure state; cleanup wave added New Project profile selector and `createSignalosProject(path, name, profile)` to `signal-init --profile`. Verified by latest gates: `npm test` = 161 passed; `npm run build` passed; focused `python/test_adoption_init.py` profile init coverage passed | None recorded |
| Existing repo adoption | Implemented | Agent 7 added `init --keep-existing` adoption scanner output: `.signalos/adoption/surface-inventory.json`, `.signalos/adoption/unknowns.json`, `.signalos/adoption/onboarding-draft.md`, `.signalos/adoption/next-steps.md`, and `.signalos/sources/initial-intent.json`; verified by focused tests and combined source gates | Adoption report UI remains downstream |
| Layer 1 factory inputs | Implemented | Agent 8 added intent/PRD source capture on the existing `signalos intent` path: prompt sources persist to `.signalos/sources/initial-intent.json`, PRD/spec/document files are copied and fingerprinted under `.signalos/sources/`; verified by focused tests and combined source gates | Unified factory/new-project/adoption flows still need to call this source-ingestion path |
| Stack/profile system | Verified | Agent 3 added `python/signalos_lib/profiles/` loader/schema plus `generic` and `react-vite` fixtures; cleanup wave added New Project and Settings profile selectors, `signalos init --profile`, `.signalos/profile.json`, `.signalos/profile-validation.json`, profile CI/template emission, `validate --group layer1` profile check, and profile-driven preview defaults. Verified by latest gates: focused profile tests = 31 passed, 0 skipped, 5 subtests passed; `npm test` = 161 passed; `npm run build` passed; `python -m pytest -q` = 490 passed, 0 skipped, 5 subtests passed | None recorded |
| Layer 1 structural validator | Verified | Agent 2: `signalos validate --group layer1 --json`; cleanup wave added the `layer1-profile` validator so Layer 1 now checks selected profile metadata and generated profile/CI outputs. Verified by focused profile/layer1 tests = 31 passed, 0 skipped, 5 subtests passed; `python -m pytest -q` = 490 passed, 0 skipped, 5 subtests passed | None recorded |
| CI/template validation | Verified | Agent 9 added profile CI/template validation helpers and focused tests; cleanup wave wired those helpers into `signalos init --profile` and `signalos validate --group layer1`. Verified by focused profile tests and full Python gate: `python -m pytest -q` = 490 passed, 0 skipped, 5 subtests passed | None recorded |
| Layer 2 gate flow | Implemented | Agent 10 added shared G0-G5 gate timeline UI, normalized `current` gate status handling, dashboard/sidebar gate rendering, and `wave:begin` inspection publishing into existing gate signals; verified by `npm test`, `npm run build`, and combined source gates | Reject/request-changes verdict actions remain disabled until backend verdict support is exposed |
| Product artifact generation | Verified | Agent 4 added shared artifact helper and compatibility export; cleanup wave added `python/signalos_lib/gate_artifacts.json` as the shared G0-G5 artifact manifest, loaded it from Python artifact helpers, and consumed the same manifest from Rust `get_project_artifacts()`. Verified by `python/test_artifacts.py`, `src-tauri` artifact manifest coverage, `cargo test --manifest-path src-tauri/Cargo.toml` = 48 passed, and `python -m pytest -q` = 490 passed, 0 skipped, 5 subtests passed | None recorded |
| Build/test evidence | Implemented | Agent 11 added `signalos verify-product --json` command surface, normalized `.signalos/evidence/<wave>/verify-product.json` output, profile command execution, QA/E2E runner composition, TDD runner detection metadata; verified by focused tests and combined source gates | IPC route, Tauri ACL grant, UI display, and full generated-product E2E remain downstream |
| Release readiness gate | Verified | Agent 12 added `signalos release-readiness --json`, Layer 1 validation evidence at `.signalos/evidence/layer1/validate-layer1.json`, release evidence at `.signalos/evidence/<wave>/release-readiness.json`, sidecar route `signal-release-readiness`, dashboard readiness card, and focused pass/fail/blocker UI tests; latest source gates: `python -m pytest -q` = 490 passed, 0 skipped, 5 subtests passed; `cargo test --manifest-path src-tauri/Cargo.toml` = 48 passed; `npm test` = 161 passed; `npm run build` passed | Publish itself remains the existing explicit `signalos publish` action; readiness gates relationship as `blocked`, `ready-to-publish`, or `published` but does not auto-publish |
| Release test suite | Verified | Agent 13 added Phase 12 release-scenario tests for empty repo creation, existing repo adoption, prompt/PRD traceability, workspace switch/clear route coverage, gate timeline rendering, verify-product evidence shape, release-readiness contract, and installed-artifact preflight. Latest source gates passed: `python -m pytest -q` = 490 passed, 0 skipped, 5 subtests passed; `cargo test --manifest-path src-tauri/Cargo.toml` = 48 passed; `npm test` = 161 passed; `npm run build` passed. Installed checks passed: `powershell -File scripts/check-installed-artifact-preconditions.ps1 -Json -RequireInstallers` = READY_FOR_SMOKE; `powershell -File scripts/smoke-installed-build.ps1 -CloseRunning` passed release executable launch, bundled sidecar product validation, and MSI administrative extraction; `scripts/build-internal.ps1` now rebuilds the sidecar before bundling to avoid stale packaged Python routes | `npm run tauri build` produced the release executable, MSI, and NSIS installer but exited 1 because updater signing requires `TAURI_SIGNING_PRIVATE_KEY`; WebView2 DevTools interactivity canary skipped in this environment because port 9223 was unreachable |
| TDD enforcement | Not started | None | None recorded |
| Design+UX enforcement | Not started | None | None recorded |
| Hardening borrows (Phase 13) | Not started | None | None recorded |

Allowed status values:

- Not started
- In progress
- Blocked
- Implemented
- Verified
- Deferred

Evidence must be concrete: test command, commit hash, screenshot path, generated artifact path, or validation output path.

## Current Code Reality Summary

The app already has useful pieces:

- Rust IPC can set an active workspace path.
- Signal commands can run with `cwd` set to the active workspace.
- Workspace file reads and writes are path-guarded.
- Python init can copy the SignalOS bundle, create `.signalos`, initialize runtime state, preserve existing files, and initialize git.
- The wave engine can route gate-aware interactions.
- Chat can inject SignalOS protocol context.
- Plan approval can write plan files, sign G2, and start orchestration.

The major gaps are:

- The active workspace is not persisted across app restarts.
- `forgetWorkspace()` attempts to clear the workspace by sending an empty path, but Rust rejects non-directory paths.
- The New Project modal does not create a new repo, does not use the project name, does not run init, and does not sign G0.
- Existing repo adoption has no complete scan, surface map, unknowns list, or onboarding draft generation.
- The factory does not yet handle prompt, PRD/spec, empty repo, and existing repo through one reliable flow.
- There is no hard Layer 1 validator that blocks completion when required files, templates, commands, CI, or gates are broken.
- Stack/profile selection is missing.
- Layer 2 gates and artifacts are only partially wired.
- Release readiness is not enforced by one machine-readable command and UI state.

## Existing Repo Conventions To Respect

- Phase 1 must delete or archive the parallel legacy `src_old/` tree after confirming it is not imported by the build. Until then, active UI work must target `src/`, not `src_old/`.
- Every new Tauri IPC command must be granted in `src-tauri/capabilities/default.json` or the app can pass local development checks and still fail in production builds.
- The factory should extend the existing `python/signalos_lib/commands/intent.py` command for prompt and source-intent capture instead of creating a disconnected intent module.
- `signalos release-readiness --json` must gate and compose with the existing `signalos-publish` command surface. It should not silently replace publish behavior.
- New UI must avoid raw inline `onclick=` and raw inline `style=` in hand-written HTML. React/Preact `onClick` and `style={{...}}` props are acceptable; raw inline handlers require the existing CSP bootstrap approach.

## Existing Code To Extend

Do not create parallel implementations for capabilities that already exist. The work below must extend or consolidate these code surfaces.

| Capability | Existing Code | Required Use |
|---|---|---|
| Validation CLI | `python/signalos_lib/commands/validate_cmd.py` | Add a Layer 1 validator group/scope to existing `signalos validate`; do not create a separate validate command |
| Init flags | `python/signalos_lib/commands/init.py` | Reuse existing `--name`, `--keep-existing`, `--force`, `--refresh-bundle`, `--minimal`, and `--no-git`; add only missing profile/factory behavior |
| Existing repo preservation | `init.py` protected files and `--keep-existing` | Treat adoption as an extension of init preserve mode, not a separate first-class CLI |
| Governance fill and G0 sign | `src/services/workspace.ts` `instantiateGovernanceAndSignG0()` | Invoke existing flow from product creation; do not rewrite it |
| Gate model/status | `src-tauri/src/governance.rs`, `src-tauri/src/ipc.rs` `get_gate_status()` | Build gate UI on existing gate status data |
| Sign verdicts | `python/signalos_lib/commands/sign.py` | Extend verdict values for rejected/requested changes instead of adding a new signing command |
| Wave/scope drift | `src/services/waveEngineClient.ts`, `python/signalos_lib/wave_engine.py` | Wire UI to existing typed client and engine events |
| Gate artifact paths | `sign.py` `GATE_MAP`, `ipc.rs` `get_project_artifacts()` | Consolidate into one source of truth instead of adding a third map |
| Artifact templates | Packaged `_bundle` governance and execution templates | Populate existing templates from intent; do not treat templates as absent |
| Evidence capture | `qa_runner.py`, `e2e_runner.py`, `tdd_runner.py` | Compose existing runners under product verification |
| Audit trail | Rust `audit()` helper, `governance.rs` `AuditEntry`, `get_audit_trail` IPC | Use existing audit path for new actions |
| Intent capture | `python/signalos_lib/commands/intent.py` and intent router code | Extend existing intent capture for prompt and PRD/spec sources |
| Folder picker | `pickWorkspaceFolder()` and existing `dialog:default` capability | Wire the Browse button to existing picker; no new dialog capability needed |

## Phase 1: Workspace Switching Must Become Durable

### Goal

The SignalOS desktop app remains the shell, but every product has its own active product repo. The app can switch between product repos, remember the active repo, clear it safely, and prove the active repo is valid.

### Required Changes

| Task | Implementation | Likely Files | Acceptance |
|---|---|---|---|
| Persist active workspace | Add app settings storage for `activeWorkspacePath`, recent workspaces, last opened timestamp, and product metadata | `src-tauri/src/ipc.rs`, new Rust settings module, `src/state.ts`, `src/js/state.js` | Restarting the app restores the last active product repo |
| Rehydrate backend state | On app boot, load persisted workspace and set Rust `WorkspaceState` before UI status checks | `src-tauri/src/ipc.rs`, `src/js/app-v2.js` | `get_workspace` returns the restored path after restart |
| Clear workspace safely | Add explicit `clear_workspace` IPC command instead of passing `""` to `set_workspace` | Rust IPC, JS workspace service | Forget workspace clears backend state, UI state, and persisted state |
| Add recent product switcher | Store and display recently used product repos with name, path, last opened, and validity | App shell/sidebar or workspace modal | User can switch repos without restarting |
| Compose workspace status | Build one UI status model from existing `get_wave_state`, `get_gate_status`, `get_project_artifacts`, and the Layer 1 validator result instead of adding a fourth state source | Rust IPC, JS workspace service | UI can show valid, invalid, missing, or unavailable workspace states |
| Add Tauri ACL grants | Grant `clear_workspace`, workspace status, and any new workspace IPC commands in capabilities | `src-tauri/capabilities/default.json` | Commands work in production builds, not only dev |
| Remove legacy `src_old/` | Delete or archive `src_old/` after confirming it is not imported by `vite.config.ts`, package scripts, or build entrypoints | Repo root, build config | Contributors cannot edit the wrong UI tree |

### Tests

- Rust unit test: set workspace, persist, reload, clear.
- Rust unit test: invalid path is rejected.
- JS unit test: boot rehydrates workspace.
- UI/E2E test: create/select workspace, restart app, workspace remains active.
- UI/E2E test: forget workspace leaves app with no active product repo.
- Static or integration check: every new workspace IPC command has a Tauri capability grant.
- Static check: `src_old/` is removed/archived or explicitly excluded with a documented reason.

### Phase Definition Of Done

- [ ] Workspace persists, restores, switches, and clears correctly.
- [ ] Workspace status is available to the UI.
- [ ] Required Tauri capability grants are present and tested.
- [ ] Legacy `src_old/` ambiguity is removed or formally archived.

## Phase 2: New Product Repo Creation

### Goal

The New Project flow must create a real dedicated product repo and embed SignalOS into it.

### Required Changes

| Task | Implementation | Likely Files | Acceptance |
|---|---|---|---|
| Create folder | New Project must create the selected directory if missing and reject unsafe paths | `src/components/NewProjectModal.tsx`, `src/js/app-v2.js`, Rust IPC | Empty target path becomes a real folder |
| Use product name | Pass name into existing init support and store it in product metadata | `src/js/app-v2.js`, `python/signalos_lib/commands/init.py` | Product name appears in status and generated governance metadata |
| Wire existing init flow | Replace modal-only `set_workspace` flow with existing `initWorkspace(path)`; reuse existing init flags instead of creating new ones | `src/services/workspace.ts`, `src/js/app-v2.js`, `init.py` | New product always contains `.signalos` after creation |
| Invoke existing governance/G0 flow | Call existing `instantiateGovernanceAndSignG0()` after init; do not reimplement placeholder fill or signing | `src/services/workspace.ts` | Soul, Constitution, Decision DNA, and G0 are filled/signed or explicitly blocked |
| Refresh status | After creation, run Layer 1 validator and update UI | Workspace status service | UI shows ready, blocked, or needs-human-step |
| Wire browse button | Connect Browse to existing `window.pickWorkspaceFolder()`; dialog capability and window exposure already exist, so no new module wiring is required | `NewProjectModal.tsx`, `src/services/workspace.ts` | User can browse by clicking a modal button wired as `onClick={() => window.pickWorkspaceFolder()}` |

### Tests

- JS unit test: create project calls init, sign, validate in order.
- Rust test: create path rejects file path and unsafe parent traversal.
- Python integration test: init with `--name` writes product metadata.
- E2E test: New Project from empty folder produces a Layer 1 valid repo.

### Phase Definition Of Done

- [ ] New Project creates or selects a real target folder.
- [ ] Product name, source intent, selected profile, existing init flow, existing G0 signing, and validation are wired.
- [ ] Browse path behavior uses the existing picker and is covered.

## Phase 3: Existing Product Repo Adoption

### Goal

SignalOS can adopt an existing product repo without destroying existing work.

### Required Changes

| Task | Implementation | Likely Files | Acceptance |
|---|---|---|---|
| Extend init preserve mode | Extend existing `signalos init --keep-existing` with adoption metadata and scanner output; do not create a separate `signalos adopt` command for the first implementation | `python/signalos_lib/commands/init.py`, scanner module | Existing repo can be adopted through init preserve mode |
| Preserve existing files | Reuse `_PROTECTED_FILES`, `--keep-existing`, and existing Rust write guards | `init.py`, Rust write guards | Existing source files remain byte-for-byte unchanged unless user confirms |
| Surface inventory | Add scanner for routes, package scripts, APIs, tests, CI, docs, env files, deployment files, data stores, and commands | New Python scanner module called by init preserve mode | `.signalos/adoption/surface-inventory.json` exists |
| Unknowns list | Record missing or ambiguous adoption facts from the scanner | Scanner module and init preserve mode | `.signalos/adoption/unknowns.json` exists |
| Onboarding drafts | Populate existing bundle templates into draft scope, risks, test strategy, governance notes, and next human step | Init preserve mode and template fill logic | Draft artifacts exist without pretending they are final |
| Adoption report UI | Show what was found, what was embedded, what is unknown, and what needs approval | App UI status/adoption panel | User sees adoption state and blockers |

### Tests

- Python test: existing repo with README/package files is preserved.
- Python test: adoption writes inventory and unknowns.
- E2E test: existing repo adoption embeds `.signalos` and leaves app code unchanged.
- E2E test: adoption blockers are visible in UI.

### Phase Definition Of Done

- [ ] Existing repo adoption extends init preserve mode and is non-destructive by default.
- [ ] Surface inventory, unknowns, and onboarding drafts are generated.
- [ ] Adoption report is visible in the app.

## Phase 4: Unified Layer 1 Factory Inputs

### Goal

Prompt, PRD/spec/document, empty repo, and existing repo all enter one factory pipeline and produce one validated product repo state.

### Factory Pipeline

1. Collect input source.
2. Resolve target product repo path.
3. Select or infer stack/profile.
4. Create or adopt workspace.
5. Save source intent.
6. Initialize or embed SignalOS.
7. Generate initial artifacts or draft blockers.
8. Run Layer 1 validator.
9. Set active workspace.
10. Show next required human step.

### Required Changes

| Input | Implementation | Acceptance |
|---|---|---|
| Plain prompt | Extend `python/signalos_lib/commands/intent.py` to store prompt in `.signalos/sources/initial-intent.json` and seed scope/unknowns | Prompt-only product can reach Layer 1 ready |
| PRD/spec/document | Extend `intent.py` and the source import flow to copy, fingerprint, and translate source files into draft scope/unknowns | PRD source is traceable |
| Empty repo | Create folder, initialize git optionally, embed SignalOS, validate | Empty repo becomes governed product repo |
| Existing repo | Run adoption, preserve files, embed SignalOS, validate | Existing repo becomes governed without damage |

### Tests

- E2E test: each input type reaches a validator result.
- Python test: source intent is saved and fingerprinted.
- UI test: each input route shows the same final status model.

### Phase Definition Of Done

- [ ] Prompt, PRD/spec, empty repo, and existing repo inputs share one factory pipeline.
- [ ] Existing `intent.py` owns source-intent persistence.
- [ ] Every factory path ends in a Layer 1 validator result.

## Phase 5: Stack And Profile System

### Goal

Factory output must be tailored to the product stack instead of assuming one generic Node-like shape.

### Required Profiles

| Profile | Minimum Use |
|---|---|
| `react-vite` | Frontend product app |
| `next` | Next.js app |
| `node-api` | Node backend/API |
| `python` | Python service/tooling |
| `tauri` | Desktop app |
| `generic` | Unknown or manually governed repo |

### Required Changes

| Task | Implementation | Likely Files | Acceptance |
|---|---|---|---|
| Profile manifest | Add machine-readable profile manifests listing required templates, CI, scripts, validators, and preview behavior | `python/signalos_lib/profiles/` | Validator can inspect selected profile |
| UI selector | Add profile selector during create/adopt flow | New Project/Factory UI | User can choose or accept detected profile |
| Backend support | Add only missing `--profile` support to existing init/factory surfaces; reuse existing init flags | Python commands and IPC | Profile is stored in product metadata |
| Detection | Infer likely profile from files for existing repos | Adoption scanner | Existing repo gets suggested profile |
| Preview compatibility | Preview service reads profile before trying npm commands | Preview service | Generic repos do not fail because npm is absent |
| Enforcement defaults | Extend profile manifest schema with `design_required` (bool), `ux_required` (bool), and `tdd_threshold` (0.0-1.0) so Phase 6/8/11 validators read enforcement strictness from the profile instead of hard-coding it | `python/signalos_lib/profiles/profile.schema.json`, `generic.yaml`, `react-vite.yaml`, profile loader | Each shipped profile declares enforcement defaults; backend profiles set `ux_required: false`, frontend profiles set `ux_required: true` |

### Tests

- Python test: each profile manifest loads.
- Python test: validator fails on missing required profile files unless explicitly disabled.
- Python test: profile manifest schema rejects missing `design_required` / `ux_required` / `tdd_threshold` fields.
- UI test: selecting a profile changes init options.
- E2E test: generic profile avoids Node preview assumptions.

### Phase Definition Of Done

- [ ] Supported profiles have manifests.
- [ ] Create/adopt UI can select or accept a detected profile.
- [ ] Preview and validation behavior is profile-aware.
- [ ] Profile manifest declares `design_required`, `ux_required`, `tdd_threshold` and these values drive Phase 6/8/11 validators.

## Phase 6: Layer 1 Structural Validator

### Goal

Layer 1 must not finish with a broken product repo. The validator is the hard gate.

### Required Command Surface

```bash
signalos validate --group layer1 --json
```

The implementation must extend the existing `validate_cmd.py` command surface, severity model, and validator registry. It should add a Layer 1 group or scope selector and register the checks below. Do not create a parallel `validate-layer1` CLI.

### Required Validation Checks

| Check | Required Result |
|---|---|
| Workspace root | Exists, is directory, and is the active workspace |
| `.signalos` runtime | Required runtime dirs/files exist |
| Product metadata | Product name, source input, profile, created/adopted mode, and current phase exist |
| Governance docs | Soul, Constitution, Decision DNA present or explicitly blocked by human-needed unknown |
| Audit trail | Audit file exists and can append a test event safely |
| Gates | G0-G5 state can be read and current gate is known |
| Commands | Required command surfaces are resolvable |
| Agents | Required gate agents are available |
| Artifact resolver | Expected artifact paths resolve inside workspace using the consolidated gate/artifact map |
| Templates | Required profile templates exist |
| CI | CI files exist or are explicitly disabled by profile |
| Hooks | Hook registration status is known |
| Validators | Required validators are callable |
| Source traceability | Prompt, PRD, or adoption source is saved |
| Unknowns | Unknowns are recorded with next human action |
| Safety | No generated path resolves outside workspace |

### Required App Behavior

- App cannot mark Layer 1 complete unless validator passes.
- App must show blocking errors with file paths and next actions.
- App must store latest validation output under `.signalos/evidence/layer1/`.
- Any IPC route that calls `signalos validate --group layer1 --json` must be granted in `src-tauri/capabilities/default.json`.

Layer 1 validation evidence lives at `.signalos/evidence/layer1/`. Per-wave implementation, build, and test evidence lives at `.signalos/evidence/<wave>/`. Do not introduce a third evidence location without updating the artifact resolver and release-readiness checks.

### Tests

- Python unit tests for each validator check.
- Snapshot test for validator JSON schema.
- E2E test: intentionally remove a required file and verify UI blocks completion.
- E2E test: valid created repo passes.
- E2E test: valid adopted repo passes.
- Static or integration check: validator IPC route has a Tauri capability grant.

### Phase Definition Of Done

- [ ] `signalos validate --group layer1 --json` exists through the existing validator command and returns a stable JSON schema.
- [ ] UI blocks Layer 1 completion on validator failure.
- [ ] Validator IPC route is granted in Tauri capabilities.

## Phase 7: CI And Template Validation

### Goal

No generated product repo should contain CI or templates that fail because SignalOS forgot to emit required files.

### Required Changes

| Task | Implementation | Acceptance |
|---|---|---|
| Template manifest | Each profile declares templates and destination paths | Profile manifest | Validator checks every template |
| CI manifest | Each profile declares CI files and expected commands | Profile manifest | Validator can prove CI is coherent |
| Placeholder scan | Reuse existing substitution/token knowledge from `applySubstitutions()` and scan required files for unresolved placeholders | Validator | Layer 1 blocks unresolved placeholders |
| Dry run | Add factory dry-run command for each profile | Python command | Release test can verify profiles without UI |
| CI disabled state | Profiles may explicitly disable CI with reason | Profile metadata | Missing CI is visible, not silent |

### Tests

- Python test: all profile templates exist.
- Python test: unresolved placeholders fail validation.
- Python test: dry-run succeeds for every supported profile.
- Release test: validate every profile from an empty temp repo.

### Phase Definition Of Done

- [ ] Profile templates and CI manifests are complete.
- [ ] Placeholder-only output fails validation.
- [ ] Dry-run validates every supported profile.

## Phase 8: Layer 2 Gate Flow

### Goal

Layer 2 must visibly drive governed product work through gates, waves, artifacts, implementation, tests, and evidence.

### Required Changes

| Task | Implementation | Likely Files | Acceptance |
|---|---|---|---|
| Gate timeline | Add UI panel on top of existing `Gate` data and `get_gate_status()` for G0-G5 status, current gate, signer, evidence, and blockers | App UI, `governance.rs`, IPC gate status | User sees governance state at all times |
| Gate actions | Build UI on existing signing IPC and extend `sign.py` verdicts with `REJECTED` and `CHANGES-REQUESTED`; do not create a new signing command | UI + sign IPC + `sign.py` | Gate state changes are auditable |
| Structured wave events | Wire existing `waveEngineClient.ts` responses into app state, not only chat bubbles | `waveEngineClient.ts`, app state | UI state reflects engine state |
| Scope drift actions | Use existing wave engine scope-drift detection and typed client results to continue current repo or create a new product repo | Wave UI + factory flow | Scope drift can actually create/switch repos |
| Gate evidence | Use `sign.py` `GATE_MAP` as the source of truth for required gate artifacts | Sign command/UI | Gate cannot pass without required evidence |
| CSP-safe UI implementation | Build gate UI with framework event handlers and CSS classes; avoid raw inline handlers/styles in hand-written HTML unless using the CSP bootstrap pattern | Gate timeline UI files | Production CSP remains intact |
| G3 TDD coverage prerequisite | Add `validate_tdd_coverage(wave, profile)` Layer 2 validator. Reads task plan, counts `is_tdd_task(task)` matches, fails when ratio < profile's `tdd_threshold`. Wire into G3 sign as a blocking prerequisite. Override path: `signal-sign G3 --waive-tdd "<reason, min 20 chars>"` records `action=tdd-waiver` in `AUDIT_TRAIL.jsonl` with the failing task IDs named in the entry | `python/signalos_lib/sign.py`, new `python/signalos_lib/validators/tdd_coverage.py`, `validate_cmd.py` registry | G3 cannot be signed without TDD coverage at profile threshold unless an audited waiver is recorded |
| G3 design+UX prerequisite | Add `validate_design_reviewed(wave, profile)` Layer 2 validator. When profile has `design_required: true`, requires `.signalos/design/variants/wave-<n>/review.json` with `verdict=approved`. When profile has `ux_required: true`, the review must also include the UX rubric. Wire into G3 sign as a blocking prerequisite. UX-specific override path: `signal-sign G3 --no-ux-changes "<reason>"` records `action=ux-skip` (does not skip design-review — design is still required even when UX is waived). The skip is scoped to the current wave; the next wave that touches UI files must re-add the UX-reviewed design | `python/signalos_lib/sign.py`, new `python/signalos_lib/validators/design_reviewed.py`, `validate_cmd.py` registry, `design.py` `check_design_reviewed` | G3 cannot be signed without an approved design review (with UX rubric when profile requires it) unless an audited UX-skip is recorded |

### Tests

- Python test: wave engine gate state transitions.
- Python test: `validate_tdd_coverage` fails the wave when below threshold and passes when a waiver entry exists.
- Python test: `validate_design_reviewed` fails when the review file is missing, fails when UX rubric is missing on a `ux_required` profile, and passes when an audited ux-skip is recorded.
- Python test: `--waive-tdd` and `--no-ux-changes` require non-empty reasons and write the expected audit-trail entries.
- JS unit test: wave events update gate timeline.
- UI test: sign/reject/request-changes actions render and call backend.
- UI test: G3 sign shows the waive-tdd and no-ux-changes affordances when the corresponding prerequisite fails, with reason capture.
- E2E test: scope drift creates a new product repo and switches active workspace.

### Phase Definition Of Done

- [ ] G0-G5 status is visible in the app.
- [ ] Gate actions use existing sign/gate IPC surfaces and are auditable.
- [ ] Gate UI is CSP-safe and scope drift can create or switch product repos.
- [ ] G3 sign blocks on TDD coverage and design-review prerequisites unless an audited waiver (`--waive-tdd`) or UX skip (`--no-ux-changes`) is recorded.

## Phase 9: Product Artifact Generation

### Goal

Layer 2 must produce real product artifacts, not only chat messages or loose documents.

### Required Artifacts

| Artifact | Required Behavior |
|---|---|
| Product scope | Generated from prompt/PRD/adoption state and confirmed by user when needed |
| Soul | Product purpose, constraints, and identity |
| Beliefs | Assumptions and operating principles |
| Traceability | Intent to artifacts to tasks to code/tests |
| Surface inventory | Screens, APIs, commands, jobs, data stores, integrations |
| Plan | Waves, tasks, dependencies, acceptance criteria |
| Design | UX/system design notes appropriate to profile |
| Trust tier | Risk level and evidence requirements |
| Test strategy | Required unit, integration, E2E, manual, security, and performance checks |
| Quality evidence | Build/test logs, screenshots, validation outputs, signoff records |

### Required Changes

| Task | Implementation | Acceptance |
|---|---|---|
| Artifact schema | Define JSON schema or typed structure for each required artifact | Validator can enforce presence and shape |
| Artifact resolver | Consolidate `sign.py` `GATE_MAP` and `get_project_artifacts()` into one shared artifact map instead of creating a third map | Commands use same path map |
| Artifact generation | Populate existing `_bundle` templates from source intent and record traceability | Product repo gets concrete artifacts |
| Human-needed markers | Unknowns must be explicit blockers, not silent blanks | Validator reports unknowns with next action |
| Traceability links | Every generated task references source intent and target artifacts | Release readiness can trace work |

### Tests

- Python test: artifact resolver keeps paths inside workspace.
- Python test: missing required artifact blocks the right phase.
- E2E test: prompt-based product creates all Layer 2 planning artifacts.
- E2E test: traceability links original prompt to plan tasks.

### Phase Definition Of Done

- [ ] Required artifacts have schemas or typed structures.
- [ ] Artifact resolver consolidates existing gate/artifact maps and keeps all paths inside the product workspace.
- [ ] Missing artifacts produce explicit human-needed blockers.

## Phase 10: Implementation, Build, And Test Evidence

### Goal

Layer 2 must build or modify product code, run verification, and capture evidence.

### Required Command

```bash
signalos verify-product --json
```

### Required Changes

| Task | Implementation | Acceptance |
|---|---|---|
| Profile commands | Each profile declares install, build, test, lint, and preview commands where applicable | Product verification knows what to run |
| Compose existing runners | Wrap existing `qa_runner.py`, `e2e_runner.py`, and `tdd_runner.py` under one product verification command instead of reimplementing test runners | `.signalos/evidence/<wave>/` contains runner output |
| Evidence capture | Normalize existing runner output plus command output, exit code, duration, and environment summary | `.signalos/evidence/<wave>/` contains logs |
| Task-to-evidence links | Each completed task links to validation evidence | Quality evidence is traceable |
| Failure reporting | Failed commands return actionable blockers | UI shows exact failing command and log path |
| Manual evidence | Allow manual check records when automation is not available | Release readiness can include manual evidence |
| Add Tauri ACL grant | Grant any IPC route that invokes `signalos verify-product --json` in `src-tauri/capabilities/default.json` | Verification works in production builds |

### Tests

- Python test: verify-product captures passing and failing command results.
- E2E test: generated React/Vite product runs build/test and captures evidence.
- UI test: failed verification displays command and evidence path.
- Regression test: workspace write guards still prevent path escape.
- Static or integration check: verify-product IPC route has a Tauri capability grant.

### Phase Definition Of Done

- [x] `signalos verify-product --json` exists and captures build/test results.
- [x] Product verification composes existing runner modules.
- [x] Evidence is saved under `.signalos/evidence/`.
- [ ] Verification IPC route is granted in Tauri capabilities.

## Phase 11: Release Readiness Gate

### Goal

The app must provide one machine-readable release readiness result.

Release readiness gates publish. It composes with the existing `signalos-publish` command surface by providing the required pre-publish pass/fail result, blockers, and evidence links. It must not silently replace publish behavior.

### Required Command

```bash
signalos release-readiness --json
```

### Required Passing Conditions

| Check | Required |
|---|---|
| Layer 1 valid | Yes |
| Active workspace valid | Yes |
| Source intent traceable | Yes |
| Product scope approved | Yes |
| Required governance artifacts present | Yes |
| Required gates signed | Yes |
| Build passes | Yes, unless profile explicitly disables build |
| Tests pass | Yes, unless profile explicitly records manual-only verification |
| Audit trail valid | Yes |
| Risks visible | Yes |
| Deployment path known | Yes, even if deployment is not executed |
| Required templates present | Yes |
| Required evidence captured | Yes |
| TDD coverage meets profile threshold | Yes, unless every shortfall is covered by an audited `tdd-waiver` audit entry |
| Design reviewed for every UI-touching wave | Yes, unless an audited `ux-skip` audit entry exists for that wave |
| No unresolved release blockers | Yes |

### Required UI

- Release readiness card.
- Pass/fail state.
- Blocking checks.
- Evidence links.
- Waiver/skip surfacing: `tdd-waived: N of M waves` and `ux-skipped: N of M waves` shown as visible (not blocking) signals on the readiness card so the operator sees the audited overrides at release time.
- Next action.
- Last run timestamp.
- Publish relationship: blocked, ready-to-publish, or published state when integrated with `signalos-publish`.
- Tauri capability coverage for any IPC route that invokes `signalos release-readiness --json`.

### Tests

- Python test: release-readiness fails when each required piece is missing.
- Python test: release-readiness passes for a fully valid fixture.
- UI test: readiness card renders pass/fail/blockers.
- E2E test: create product, run Layer 2, verify readiness.
- Integration test: publish flow is blocked until release-readiness passes or an explicit audited override is used.
- Static or integration check: release-readiness route uses existing sidecar `run_signal_command`; add a Tauri capability grant only if a dedicated Rust IPC route is introduced.

### Phase Definition Of Done

- [x] `signalos release-readiness --json` exists and gates publish relationship without replacing `signalos publish`.
- [x] UI shows pass/fail, blockers, evidence, next action, and publish relationship.
- [x] Release-readiness uses existing sidecar `run_signal_command`; no new Rust IPC route or Tauri capability grant was added.

## Phase 12: Release Test Suite

### Test Harness Prerequisites

The listed release commands are release requirements, but the test harness must exist before they can be treated as reliable gates.

| Prerequisite | Implementation | Acceptance |
|---|---|---|
| Rust test harness | Add or verify Cargo test configuration for the Tauri crate before relying on `cargo test --manifest-path src-tauri/Cargo.toml` as a release gate | Cargo test command runs predictably in CI/local release validation |
| Python pytest config | Add `pyproject.toml`, `pytest.ini`, or equivalent pytest configuration before relying on `python -m pytest python` as a release gate | Python tests discover the intended test set consistently |

### Required Commands

Before a release test build, run source tests and source build checks:

```bash
npm test
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
python -m pytest python
```

To create the release artifact for installed-app validation, also run:

```bash
npm run tauri build
```

Use the existing release scripts where applicable, but do not treat a script as sufficient unless it proves the new factory/governance flows.

Installed-app validation means installing or launching the built artifact, then proving the packaged app can start, reach the sidecar, initialize a product repo, and run the required validation commands.

Agent 13 added `scripts/check-installed-artifact-preconditions.ps1` as a deterministic preflight. It only checks artifact presence and records the manual/smoke command; it does not claim installed-app success. Installed-app success still requires running `scripts/smoke-installed-build.ps1` against a real `npm run tauri build` artifact.

### Required E2E Scenarios

| Scenario | Required Proof |
|---|---|
| Empty repo creation | New product repo reaches Layer 1 valid |
| Existing repo adoption | Existing files are preserved and SignalOS is embedded |
| Prompt input | Prompt source is saved and planning artifacts are created |
| PRD/spec input | Source document is saved and traceable |
| Workspace restore | Active workspace survives app restart |
| Workspace switch | User can switch between two product repos |
| Workspace clear | User can clear active workspace |
| Gate flow | G0-G5 state appears and transitions are auditable |
| Scope drift | New product repo can be created from drift action |
| Plan approval | Plan writes artifacts, signs G2, and starts orchestration |
| Product verification | Build/test evidence is captured |
| Release readiness | Readiness fails with blockers and passes when complete |
| Installed app | Installed app can run sidecar and initialize a product repo |

### Phase Definition Of Done

- [x] Rust and Python test harness configuration exists before those commands become required release gates.
- [x] Source tests and build checks passed after final integration: `python -m pytest -q` = 490 passed, 0 skipped, 5 subtests passed; `cargo test --manifest-path src-tauri/Cargo.toml` = 48 passed; `npm test` = 161 passed; `npm run build` passed.
- [x] Phase 12 release-scenario coverage exists for empty repo creation, existing repo adoption, prompt/PRD traceability, workspace switch/clear route coverage, gate timeline rendering, verify-product evidence shape, release-readiness contract, installed-artifact preflight, and installed-build smoke.
- [x] Built artifact launches successfully: `npm run tauri build` produced release executable, MSI, and NSIS artifacts; installed-artifact preflight returned READY_FOR_SMOKE; `scripts/smoke-installed-build.ps1 -CloseRunning` passed release executable launch and MSI administrative extraction.
- [x] Installed app can run the sidecar and validate a product repo: installed-build smoke passed bundled sidecar readiness, `signal-init`, `signal-release-readiness --json`, and evidence generation against a temp smoke product.

Release signing note: `npm run tauri build` currently exits 1 after bundle creation unless `TAURI_SIGNING_PRIVATE_KEY` is set for updater signing. Internal unsigned smoke passed; signed updater release requires that secret.

Release build hygiene note: `scripts/build-internal.ps1` rebuilds the PyInstaller sidecar before bundling so ignored/stale `src-tauri/bin/signalos-python-*` binaries cannot silently ship old Python command routes.

## Maximum Reliable Parallel Execution Plan

Maximum reliable parallelism: 10 implementation agents plus 1 integration owner.

Do not exceed 10 active code-writing agents at once. More than that creates too much contention around shared files such as `ipc.rs`, `app-v2.js`, `workspace.ts`, `validate_cmd.py`, `init.py`, `sign.py`, and the status/validator schemas.

Agent numbers identify ownership lanes across waves, not simultaneous headcount.

### Parallelism Rules

- Shared contracts land first: product metadata, workspace status JSON, profile manifest schema, validator JSON, artifact map, release-readiness JSON, audit event names, and Tauri capability grants.
- Each agent owns a narrow file set. Any file outside that set requires integrator approval before editing.
- No agent may introduce a duplicate command surface when an existing command can be extended.
- Every IPC-adding agent must update `src-tauri/capabilities/default.json` in the same change.
- Every agent must update this document's status table and phase DoD before handoff.
- The integration owner merges frequently, runs conflict checks, and keeps the implementation aligned with the "Existing Code To Extend" table.
- The integration owner merges agent handoffs within 24 hours; no agent branch may live longer than 5 working days without a rebase against `main` and the latest contract-pack changes.

### Contract-First Wave

These agents run first because they define shared shapes used by the rest of the work.

| Agent | Owns | Primary Files | Output |
|---|---|---|---|
| Integration owner | Shared schemas, branch discipline, merge order, status table, final commits | This plan, shared type/schema files | Contract pack merged before broad work starts |
| Agent 1: Workspace core | `src_old/` cleanup, workspace persistence, `clear_workspace`, workspace status composition, ACL grants | `src-tauri/src/ipc.rs`, `src-tauri/capabilities/default.json`, workspace state files | Durable workspace state and clear/switch contract |
| Agent 2: Validator core | Existing `signalos validate` Layer 1 group/scope and validator JSON schema | `validate_cmd.py`, validator registry/modules | Stable Layer 1 validator result |
| Agent 3: Profiles core | Profile manifest schema and first profile fixtures | `python/signalos_lib/profiles/`, profile tests | Shared profile contract for factory, validation, preview, CI |
| Agent 4: Artifact core | Consolidated artifact map from `GATE_MAP` and `get_project_artifacts()` | `sign.py`, artifact/status modules, IPC artifact probe | One source of truth for gate/artifact paths |
| Agent 5: Test harness core | Rust/Python test harness prerequisites and baseline runner documentation | `src-tauri/Cargo.toml`, `pyproject.toml` or `pytest.ini`, release test docs | Later agents have runnable test gates from day 1 |

Contract-first exit criteria:

- Workspace status JSON shape is documented and test-covered.
- Layer 1 validator JSON shape is documented and test-covered.
- Profile manifest schema has at least `generic` and `react-vite` fixtures.
- Artifact map is single-source or clearly wrapped from existing sources.
- Rust and Python test harness configuration exists before broad implementation starts.
- Tauri ACL changes for new IPC are present.
- This document's status table is updated.

### Full Parallel Wave

After the contract-first wave lands, run these agents in parallel.

| Agent | Owns | Depends On | Output |
|---|---|---|---|
| Agent 6: New Project factory | Create folder, wire Browse to `pickWorkspaceFolder()`, call existing `initWorkspace()` and `instantiateGovernanceAndSignG0()` | Workspace core, profiles core, test harness core | New product repo creation works end to end |
| Agent 7: Existing repo adoption | Extend `init --keep-existing` with scanner, surface inventory, unknowns, onboarding drafts | Profiles core, validator core, test harness core | Existing repo adoption is non-destructive and visible |
| Agent 8: Intent/PRD ingestion | Extend `intent.py` for prompt and PRD/spec source capture and traceability | Profiles core, artifact core, test harness core | Prompt and PRD sources are persisted and traceable |
| Agent 9: CI/template validation | Profile CI manifests, placeholder scan using existing substitution rules, dry-run validation | Profiles core, validator core, test harness core | Broken templates/CI are blocked |
| Agent 10: Gate UI/wave wiring | Gate timeline UI, sign/reject/request-changes verdict wiring, wave/scope drift UI | Workspace core, artifact core, test harness core | G0-G5 state and actions are visible and auditable |
| Agent 11: Product verification | `verify-product` command that composes existing QA/E2E/TDD runners and captures evidence | Profiles core, artifact core, test harness core | Build/test evidence is normalized and linked |

### Final Parallel Wave

These should start after the full parallel wave APIs stabilize.

| Agent | Owns | Depends On | Output |
|---|---|---|---|
| Agent 12: Release readiness | `release-readiness` command, publish gating, readiness UI card | Validator core, product verification, artifact core | Machine-readable release readiness and UI blockers |
| Agent 13: Test/release coverage | E2E scenarios, installed-app smoke path, release evidence collection | All implementation APIs, test harness core | Release suite proves factory/governance flows |

Agent 12 and Agent 13 may run in parallel, but Agent 13 should treat unstable APIs as blocked rather than inventing mocks that hide integration failures.

### Merge Order

1. Contract pack: workspace status, validator schema, profile schema, artifact map, ACL pattern, and Rust/Python test harness prerequisites.
2. New Project factory and intent ingestion.
3. Existing repo adoption and CI/template validation.
4. Gate UI/wave wiring and product verification.
5. Release readiness and publish gating.
6. Test/release harness and installed-app smoke.
7. Final status-table update, full verification, and commit.

### Reliability Guardrails

- Avoid same-file collisions by assigning `ipc.rs`, `app-v2.js`, `workspace.ts`, `init.py`, `validate_cmd.py`, and `sign.py` to one primary agent each.
- The `ipc.rs`, `init.py`, and `workspace.ts` ownership overlaps are watch-closely execution risks, not reasons to redesign the lane structure. Keep the lanes and route cross-lane edits through the integration owner.
- If two agents need the same shared file, the integration owner makes the edit or extracts a smaller shared helper first.
- Agents must prefer additive wrappers around existing code until tests prove behavior, then consolidate.
- No agent may delete or rewrite bundle templates unless the validator proves the replacement is compatible.
- All generated or adopted product-repo writes must remain inside the active workspace.
- A phase cannot be marked Verified unless its tests and status-table evidence are recorded in this document.

## Implementation Order

1. Delete or archive `src_old/` after confirming it is not imported by the active build.
2. Configure Rust/Python test harness prerequisites.
3. Add persistent workspace settings and `clear_workspace`.
4. Compose workspace status from existing wave, gate, artifact, and validator surfaces.
5. Replace New Project modal behavior with real create, existing init, existing G0 sign, validate flow.
6. Add recent product switcher.
7. Extend existing `signalos validate` with a Layer 1 group/scope and UI blocking behavior.
8. Add profile manifests and profile selector.
9. Add CI/template manifests and placeholder validation.
10. Extend init preserve mode with adoption scanner and adoption report UI.
11. Add unified factory pipeline for prompt, PRD/spec, empty repo, and existing repo.
12. Add gate timeline and gate action UI on existing gate/sign/wave surfaces.
13. Add artifact schemas, consolidate existing gate/artifact maps, and add completeness validation.
14. Add product verification command that composes existing runners and evidence capture.
15. Add release-readiness command and UI card that gate publish.
16. Add full automated and E2E test coverage.
17. Run source tests and source build checks.
18. Build the release artifact, install or launch it, and verify sidecar/product-repo validation from the installed app.
19. Update this document's status table with evidence.
20. Commit the implementation with a clear message.

## Definition Of Done

The implementation is done only when all of the following are true:

- The app can create a new dedicated product repo from the UI.
- The app can adopt an existing repo without destroying existing files.
- The app switches active work from the SignalOS app repo to the selected product repo.
- The active product repo persists across app restart.
- The user can switch between recent product repos.
- The user can clear the active product repo.
- New product creation uses product name, target path, selected profile, and source intent.
- New product creation invokes existing init, governance fill, G0 signing, and Layer 1 validation surfaces.
- Existing repo adoption extends init preserve mode and produces surface inventory, unknowns, onboarding drafts, and validation output.
- Prompt, PRD/spec, empty repo, and existing repo inputs all flow through the same factory pipeline.
- Every supported stack/profile has a manifest, required templates, validator rules, and release-test coverage.
- Parallel execution followed the contract-first model, stayed within the 10 active implementation-agent cap, and recorded coordination evidence in the status table.
- Layer 1 cannot be marked complete unless `signalos validate --group layer1 --json` passes through the existing validator command.
- CI/templates cannot be emitted in a broken or placeholder-only state.
- Layer 2 shows G0-G5 gate state in the UI.
- Gate sign/reject/request-changes actions extend existing sign/gate IPC surfaces and are auditable.
- New UI panels avoid raw inline handlers/styles and preserve the app's CSP-safe event/style pattern.
- Scope drift can continue current work or create/switch to a new product repo.
- Product scope, Soul, Beliefs, traceability, surface inventory, plan, design, trust tier, test strategy, and quality evidence are generated or explicitly blocked by human-needed unknowns.
- Product implementation writes only inside the active product repo.
- Product verification composes existing runner modules and captures build/test evidence.
- `signalos release-readiness --json` exists and blocks release readiness when required evidence is missing.
- Release readiness composes with `signalos-publish` and blocks publish until ready or explicitly overridden with audit evidence.
- UI shows release-readiness pass/fail, blockers, evidence links, and next action.
- Every new Tauri IPC command is granted in `src-tauri/capabilities/default.json`.
- Legacy `src_old/` ambiguity is removed by deleting or archiving the tree after confirming the active build does not import it.
- Rust and Python test harness configuration exists before `cargo test --manifest-path src-tauri/Cargo.toml` and `python -m pytest python` are treated as release gates.
- Rust IPC tests pass.
- Python tests pass.
- JS/unit tests pass.
- UI/E2E tests pass for create, adopt, switch, restore, clear, gate flow, product verification, and release readiness.
- Installed-app smoke test proves the sidecar can initialize and validate a product repo.
- This document's status table is updated with final statuses and evidence paths.
- The final implementation is committed at the end with a clear commit message.

## Final Commit Requirement

At the end of the implementation wave:

1. Run the required verification commands.
2. Update the status table in this document.
3. Record concrete evidence paths or command outputs.
4. Run `git status --short`.
5. Commit all intended changes.
6. Include the commit hash in the final handoff.

No implementation wave should be considered complete without the status update and final commit.
