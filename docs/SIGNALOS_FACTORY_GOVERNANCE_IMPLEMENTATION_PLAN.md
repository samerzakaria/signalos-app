# SignalOS Factory Governance Implementation Plan

Status: Drafted
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
| Workspace switching | Not started | None | None recorded |
| Tauri capability grants | Not started | None | None recorded |
| New product repo creation | Not started | None | None recorded |
| Existing repo adoption | Not started | None | None recorded |
| Layer 1 factory inputs | Not started | None | None recorded |
| Stack/profile system | Not started | None | None recorded |
| Layer 1 structural validator | Not started | None | None recorded |
| CI/template validation | Not started | None | None recorded |
| Layer 2 gate flow | Not started | None | None recorded |
| Product artifact generation | Not started | None | None recorded |
| Build/test evidence | Not started | None | None recorded |
| Release readiness gate | Not started | None | None recorded |
| Release test suite | Not started | None | None recorded |

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

- Active UI work must target `src/`, not the parallel legacy `src_old/` tree. Do not edit `src_old/` unless a dedicated cleanup decision removes or archives it.
- Every new Tauri IPC command must be granted in `src-tauri/capabilities/default.json` or the app can pass local development checks and still fail in production builds.
- The factory should extend the existing `python/signalos_lib/commands/intent.py` command for prompt and source-intent capture instead of creating a disconnected intent module.
- `signalos release-readiness --json` must gate and compose with the existing `signalos-publish` command surface. It should not silently replace publish behavior.
- New UI must avoid raw inline `onclick=` and raw inline `style=` in hand-written HTML. React/Preact `onClick` and `style={{...}}` props are acceptable; raw inline handlers require the existing CSP bootstrap approach.

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
| Add workspace status command | Return active path, exists, is SignalOS repo, product name, phase, gate summary, and missing requirements | Rust IPC plus Python validator call | UI can show valid, invalid, missing, or unavailable workspace states |
| Add Tauri ACL grants | Grant `clear_workspace`, workspace status, and any new workspace IPC commands in capabilities | `src-tauri/capabilities/default.json` | Commands work in production builds, not only dev |

### Tests

- Rust unit test: set workspace, persist, reload, clear.
- Rust unit test: invalid path is rejected.
- JS unit test: boot rehydrates workspace.
- UI/E2E test: create/select workspace, restart app, workspace remains active.
- UI/E2E test: forget workspace leaves app with no active product repo.
- Static or integration check: every new workspace IPC command has a Tauri capability grant.

### Phase Definition Of Done

- [ ] Workspace persists, restores, switches, and clears correctly.
- [ ] Workspace status is available to the UI.
- [ ] Required Tauri capability grants are present and tested.

## Phase 2: New Product Repo Creation

### Goal

The New Project flow must create a real dedicated product repo and embed SignalOS into it.

### Required Changes

| Task | Implementation | Likely Files | Acceptance |
|---|---|---|---|
| Create folder | New Project must create the selected directory if missing and reject unsafe paths | `src/components/NewProjectModal.tsx`, `src/js/app-v2.js`, Rust IPC | Empty target path becomes a real folder |
| Use product name | Pass name into backend init and store it in product metadata | JS create flow, Python init/factory command | Product name appears in status and generated governance metadata |
| Run init | Replace modal-only `set_workspace` flow with shared `initWorkspace(path, options)` | `src/services/workspace.ts`, `src/js/app-v2.js` | New product always contains `.signalos` after creation |
| Fill governance | Run governance placeholder fill after init | `src/services/workspace.ts` | Soul, Constitution, and Decision DNA are populated or explicitly blocked |
| Sign G0 | Call signing flow after governance fill | `instantiateGovernanceAndSignG0()` and sign command | New product has an auditable G0 decision |
| Refresh status | After creation, run Layer 1 validator and update UI | Workspace status service | UI shows ready, blocked, or needs-human-step |
| Wire browse button | Folder picker must populate target path | `NewProjectModal.tsx` | User can browse instead of typing paths |

### Tests

- JS unit test: create project calls init, sign, validate in order.
- Rust test: create path rejects file path and unsafe parent traversal.
- Python integration test: init with `--name` writes product metadata.
- E2E test: New Project from empty folder produces a Layer 1 valid repo.

### Phase Definition Of Done

- [ ] New Project creates or selects a real target folder.
- [ ] Product name, source intent, selected profile, init, G0 signing, and validation are wired.
- [ ] Browse path behavior is implemented and covered.

## Phase 3: Existing Product Repo Adoption

### Goal

SignalOS can adopt an existing product repo without destroying existing work.

### Required Changes

| Task | Implementation | Likely Files | Acceptance |
|---|---|---|---|
| Add adoption command | Implement `signalos adopt <path> --profile <profile> --json` or a factory command with adoption mode | `python/signalos_lib/commands/`, IPC route | Existing repo can be adopted by one backend command |
| Preserve existing files | Default to no overwrite; generated files should be namespaced or require explicit confirmation | Python init/adopt logic, Rust write guards | Existing source files remain byte-for-byte unchanged unless user confirms |
| Surface inventory | Scan routes, package scripts, APIs, tests, CI, docs, env files, deployment files, data stores, and commands | New Python scanner module | `.signalos/adoption/surface-inventory.json` exists |
| Unknowns list | Record missing or ambiguous adoption facts | Python adoption module | `.signalos/adoption/unknowns.json` exists |
| Onboarding drafts | Create draft scope, risks, test strategy, governance notes, and next human step | Python adoption module | Draft artifacts exist without pretending they are final |
| Adoption report UI | Show what was found, what was embedded, what is unknown, and what needs approval | App UI status/adoption panel | User sees adoption state and blockers |

### Tests

- Python test: existing repo with README/package files is preserved.
- Python test: adoption writes inventory and unknowns.
- E2E test: existing repo adoption embeds `.signalos` and leaves app code unchanged.
- E2E test: adoption blockers are visible in UI.

### Phase Definition Of Done

- [ ] Existing repo adoption is non-destructive by default.
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

| Task | Implementation | Acceptance |
|---|---|---|
| Profile manifest | Add machine-readable profile manifests listing required templates, CI, scripts, validators, and preview behavior | `python/signalos_lib/profiles/` | Validator can inspect selected profile |
| UI selector | Add profile selector during create/adopt flow | New Project/Factory UI | User can choose or accept detected profile |
| Backend support | Add `--profile` to init/factory/adopt commands | Python commands and IPC | Profile is stored in product metadata |
| Detection | Infer likely profile from files for existing repos | Adoption scanner | Existing repo gets suggested profile |
| Preview compatibility | Preview service reads profile before trying npm commands | Preview service | Generic repos do not fail because npm is absent |

### Tests

- Python test: each profile manifest loads.
- Python test: validator fails on missing required profile files unless explicitly disabled.
- UI test: selecting a profile changes init options.
- E2E test: generic profile avoids Node preview assumptions.

### Phase Definition Of Done

- [ ] Supported profiles have manifests.
- [ ] Create/adopt UI can select or accept a detected profile.
- [ ] Preview and validation behavior is profile-aware.

## Phase 6: Layer 1 Structural Validator

### Goal

Layer 1 must not finish with a broken product repo. The validator is the hard gate.

### Required Command

```bash
signalos validate-layer1 --json
```

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
| Artifact resolver | Expected artifact paths resolve inside workspace |
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
- Any IPC route that calls `signalos validate-layer1 --json` must be granted in `src-tauri/capabilities/default.json`.

### Tests

- Python unit tests for each validator check.
- Snapshot test for validator JSON schema.
- E2E test: intentionally remove a required file and verify UI blocks completion.
- E2E test: valid created repo passes.
- E2E test: valid adopted repo passes.
- Static or integration check: validator IPC route has a Tauri capability grant.

### Phase Definition Of Done

- [ ] `signalos validate-layer1 --json` exists and returns a stable JSON schema.
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
| Placeholder scan | Detect unresolved template placeholders in required files | Validator | Layer 1 blocks unresolved placeholders |
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
| Gate timeline | Add UI panel for G0-G5 status, current gate, signer, evidence, and blockers | App UI, wave state | User sees governance state at all times |
| Gate actions | Add sign, reject, request changes, and add evidence actions per gate | UI + sign IPC | Gate state changes are auditable |
| Structured wave events | Wave engine responses should update app state, not only chat bubbles | `waveEngineClient`, app state | UI state reflects engine state |
| Scope drift actions | Wire scope drift choices to continue current repo or create a new product repo | Wave UI + factory flow | Scope drift can actually create/switch repos |
| Gate evidence | Require evidence links for gates that need them | Sign command/UI | Gate cannot pass without required evidence |
| CSP-safe UI implementation | Build gate UI with framework event handlers and CSS classes; avoid raw inline handlers/styles in hand-written HTML unless using the CSP bootstrap pattern | Gate timeline UI files | Production CSP remains intact |

### Tests

- Python test: wave engine gate state transitions.
- JS unit test: wave events update gate timeline.
- UI test: sign/reject/request-changes actions render and call backend.
- E2E test: scope drift creates a new product repo and switches active workspace.

### Phase Definition Of Done

- [ ] G0-G5 status is visible in the app.
- [ ] Gate actions are auditable.
- [ ] Gate UI is CSP-safe and scope drift can create or switch product repos.

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
| Artifact resolver | Add central resolver for artifact paths and required phase | Commands use same path map |
| Artifact generation | Add commands/services to create draft artifacts from source intent | Product repo gets concrete artifacts |
| Human-needed markers | Unknowns must be explicit blockers, not silent blanks | Validator reports unknowns with next action |
| Traceability links | Every generated task references source intent and target artifacts | Release readiness can trace work |

### Tests

- Python test: artifact resolver keeps paths inside workspace.
- Python test: missing required artifact blocks the right phase.
- E2E test: prompt-based product creates all Layer 2 planning artifacts.
- E2E test: traceability links original prompt to plan tasks.

### Phase Definition Of Done

- [ ] Required artifacts have schemas or typed structures.
- [ ] Artifact resolver keeps all paths inside the product workspace.
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
| Evidence capture | Save command output, exit code, duration, and environment summary | `.signalos/evidence/<wave>/` contains logs |
| Task-to-evidence links | Each completed task links to validation evidence | Quality evidence is traceable |
| Failure reporting | Failed commands return actionable blockers | UI shows exact failing command and log path |
| Manual evidence | Allow manual check records when automation is not available | Release readiness can include manual evidence |
| Add Tauri ACL grant | Grant any IPC route that invokes `signalos verify-product --json` | `src-tauri/capabilities/default.json` | Verification works in production builds |

### Tests

- Python test: verify-product captures passing and failing command results.
- E2E test: generated React/Vite product runs build/test and captures evidence.
- UI test: failed verification displays command and evidence path.
- Regression test: workspace write guards still prevent path escape.
- Static or integration check: verify-product IPC route has a Tauri capability grant.

### Phase Definition Of Done

- [ ] `signalos verify-product --json` exists and captures build/test results.
- [ ] Evidence is saved under `.signalos/evidence/`.
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
| No unresolved release blockers | Yes |

### Required UI

- Release readiness card.
- Pass/fail state.
- Blocking checks.
- Evidence links.
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
- Static or integration check: release-readiness IPC route has a Tauri capability grant.

### Phase Definition Of Done

- [ ] `signalos release-readiness --json` exists and gates publish.
- [ ] UI shows pass/fail, blockers, evidence, next action, and publish relationship.
- [ ] Release-readiness IPC route is granted in Tauri capabilities.

## Phase 12: Release Test Suite

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

- [ ] Source tests and build checks pass.
- [ ] Built artifact installs or launches successfully.
- [ ] Installed app can run the sidecar and validate a product repo.

## Implementation Order

1. Add persistent workspace settings and `clear_workspace`.
2. Add workspace status command and UI status model.
3. Replace New Project modal behavior with real create, init, G0 sign, validate flow.
4. Add recent product switcher.
5. Add Layer 1 validator command and UI blocking behavior.
6. Add profile manifests and profile selector.
7. Add CI/template manifests and placeholder validation.
8. Add existing repo adoption scanner and adoption report UI.
9. Add unified factory pipeline for prompt, PRD/spec, empty repo, and existing repo.
10. Add gate timeline and full gate action UI.
11. Add artifact schemas, artifact resolver, and completeness validation.
12. Add product verification command and evidence capture.
13. Add release-readiness command and UI card.
14. Add full automated and E2E test coverage.
15. Run source tests and source build checks.
16. Build the release artifact, install or launch it, and verify sidecar/product-repo validation from the installed app.
17. Update this document's status table with evidence.
18. Commit the implementation with a clear message.

## Definition Of Done

The implementation is done only when all of the following are true:

- The app can create a new dedicated product repo from the UI.
- The app can adopt an existing repo without destroying existing files.
- The app switches active work from the SignalOS app repo to the selected product repo.
- The active product repo persists across app restart.
- The user can switch between recent product repos.
- The user can clear the active product repo.
- New product creation uses product name, target path, selected profile, and source intent.
- New product creation runs SignalOS init, governance fill, G0 signing, and Layer 1 validation.
- Existing repo adoption produces surface inventory, unknowns, onboarding drafts, and validation output.
- Prompt, PRD/spec, empty repo, and existing repo inputs all flow through the same factory pipeline.
- Every supported stack/profile has a manifest, required templates, validator rules, and release-test coverage.
- Layer 1 cannot be marked complete unless `signalos validate-layer1 --json` passes.
- CI/templates cannot be emitted in a broken or placeholder-only state.
- Layer 2 shows G0-G5 gate state in the UI.
- Gate sign/reject/request-changes actions are auditable.
- Scope drift can continue current work or create/switch to a new product repo.
- Product scope, Soul, Beliefs, traceability, surface inventory, plan, design, trust tier, test strategy, and quality evidence are generated or explicitly blocked by human-needed unknowns.
- Product implementation writes only inside the active product repo.
- Product verification captures build/test evidence.
- `signalos release-readiness --json` exists and blocks release readiness when required evidence is missing.
- Release readiness composes with `signalos-publish` and blocks publish until ready or explicitly overridden with audit evidence.
- UI shows release-readiness pass/fail, blockers, evidence links, and next action.
- Every new Tauri IPC command is granted in `src-tauri/capabilities/default.json`.
- Implementation edits target the active `src/` tree, not `src_old/`, unless a separate cleanup decision removes or archives `src_old/`.
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
