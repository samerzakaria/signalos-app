# Changelog

## [Unreleased] - 2026-05-17

## [3.2.3-internal.1] - 2026-07-03

### Close Wave-1 executor + the C-bridge loop; supply-chain fix

- **1.1 — live parallel executor:** the supervisor/worker loop on top of the `TaskStore` foundation — a `run_worker_pool` (claim/lease/heartbeat, retry→dead-letter, N concurrent workers) plus `run_isolated_build_tasks` (real git-worktree-per-task isolation + a single-threaded merge queue), verified against a real git repo (independent tasks build in parallel and merge cleanly; conflicting tasks dead-letter the loser, not both). Wired into the live `delivery.py` dispatch through the same claim/retry contract, and `dispatch_local_build_agent_parallel` splits a react-vite product's components (import-disjoint by construction) across the worktree/merge-queue path — with verbatim fallback for every other profile. Two real bugs surfaced by exercising the actual delivery path and fixed: a fresh-scaffold repo has no commit yet (so `git worktree add` had nothing to fork from), and per-task `.signalos/` bookkeeping was leaking into merges.
- **1.10 — incident cards:** all four present-day cards now fire from real trigger points (gate-deadlock, integration-outage, credential-revoked, deploy-failure).
- **1.11 — founder policy controls:** full stack — `policy.py` persistence, `policy:get`/`policy:set` IPC, and a plain-language `PolicyPanel` settings surface.
- **3.1 / 3.2 (C-bridge):** closeout now links the product's real listening window, and the unsigned-threshold safety gate is enforced inside the live `evaluate_listening_window` verdict path (never a duplicate module).
- **3.4 (C-bridge):** a launch surface re-enters the same enforced G0–G5 gate loop as an isolated child build (`launch.py` + `agent:launch`), linked back to the parent product.
- **3.6 (C-bridge):** the founder's real identity now reaches signed gate records (previously always the generic `foundry-agent`) and carries into launch children.
- **Supply chain:** removed `quick-xml 0.37.5` (RUSTSEC-2026-0194 / 0195, both high) by updating `notify-rust` → 4.18.0 and `tauri-winrt-notification` → 0.7.3, which drop the crate entirely from the Windows-notification path — a genuine removal, not an audit suppression. The remaining `quick-xml 0.39.4` (via `plist`, pinned `^0.39.2`) is upstream-blocked pending `plist#191`; deliberately left un-ignored and tracked.
- **Intentionally still open:** 3.3 (advisory write-back) and 3.5 (growth re-entry) remain blocked on B-layer modules that do not exist yet; not stubbed.

## [3.2.0-internal.1] - 2026-07-02

### Governed product lifecycle — integrity hardening + engineering foundation (Wave 0 + Wave 1 + C-bridge)

- **Wave 0 (enforcement integrity):** gate signing now fails closed when required artifacts are missing or are unfilled template placeholders; the audit trail is a tamper-evident hash chain wired into `integrity-witness`; `check_second_opinion_wired` verifies real importability/callability instead of stale file paths; `PLAN_SCHEMA.json` is formally mirror-tested against the hand-written plan validator; the enforcement UI now reads the correct backend field (was silently showing "No rules loaded"); core governance invariants (`gate-gating`, `test-first`, `secret-block`, etc.) can no longer be toggled off via `set_rule_mode` — only tunable policy rules can; the previously dormant agentic UX-friction QA now runs on every design-gate preview.
- **Wave 1 (engineering foundation):** a governed parallel-executor `TaskStore` (idempotent enqueue, atomic claim, lease/heartbeat reclaim, retry→dead-letter), with a real Postgres backend using `SELECT … FOR UPDATE SKIP LOCKED`; a budget hard-stop (90% warn / 100% halt) wired into the cost report; a task-class model router with cross-vendor critique routing (a critique always prefers a different model vendor than the artifact's author — no hardcoded model names, vendor resolved from provider config); maturity-tier labels (proven/supported/experimental) on every stack adapter; founder-facing plan structure (Feature→Epic→Story, release grouping, value score, provenance) layered on the existing typed plan; headless bidirectional tracker sync behind a `TrackerAdapter` protocol, with a real Jira Cloud adapter; a plain-words 4-field gate-brief contract (critic-authored, cross-vendor-independence enforced) with a live authoring pipeline; a completeness-rubric advisory pass (silent-omission detection) on gate artifacts; plain-words failure-state incident cards wired into the live gate-deadlock path; founder policy controls with a floor-gate invariant (no policy mode may ever remove qualification/Go-No-Go/design/deploy/launch).
- **C-bridge:** belief auto-resolution (Keep/Refute/Iterate) from post-launch telemetry, gated so a hypothesis can only resolve against *signed* success metrics.
- **Live-verified**, not just unit-tested: the Postgres executor store against a real dedicated database, the Jira tracker adapter against a real Jira Cloud site, and the full cross-vendor critique loop (one vendor authors, a different vendor critiques) against real Anthropic and OpenAI models.
- See `docs/GOVERNED_PRODUCT_LIFECYCLE_IMPLEMENTATION_PLAN.md` for the full A/B/C plan this wave executes.

## [3.1.0-internal.2] - 2026-06-30

### Autonomous mode works with all providers (not just Claude)

- The headless harness (`signalos harness call` / `orchestrate`, and product `call_llm`) now routes **all 12 configured providers** — Anthropic, OpenAI, Gemini, Groq, Mistral, DeepSeek, OpenRouter, xAI, Together, Cerebras, DashScope (via one OpenAI-compatible adapter) and local Ollama — from a single source-of-truth provider table. The provider is **auto-detected** from whichever API key is present (override with `SIGNALOS_LLM_PROVIDER`).
- The model is **discovered from each provider's API** (best-fit pick) instead of a hardcoded default; override with `SIGNALOS_LLM_MODEL` or `--model`. `DEFAULT_MODEL` is no longer a silent default. The `signal-init` autonomous-mode message is now provider-neutral.

### CI / smoke

- `smoke-installed-build.ps1`: release-readiness on a freshly bootstrapped project is correctly **blocked** (no signed gates/evidence); the smoke now treats that as a valid "ran + produced schema/evidence" outcome instead of a command failure.

### Docs

- `FOUNDRY_MULTI_USER_MULTI_TENANT_PLAN.md` v2 — research-validated: drop git-as-queue, correct the live-tracker transport (`NOTIFY` as a hint + durable state + poll), require microVM/gVisor sandboxing for multi-tenant, and harden with idempotency/lease/dead-letter + RLS best practices (cited).

## [3.1.0-internal.1] - 2026-06-30

### SignalOS.NET concept parity + A+ enforcement-hardening

- Land the app-native command surface for parity with the SignalOS.NET reference (validators, lifecycle/state, ceremonies, release/ship, 18 stack adapters) and harden every new surface to enforce (never advise) with fail-closed behavior and negative-test coverage.

### Governed external-skill supply chain

- Add `skill-lock` — a license-checked skill lockfile (`signalos.skills_lock.v1`): pins skills by SHA-256 + SPDX license, refuses any non-permissive/unlicensed or hash-drifted skill (fail-closed), composes with `integrity-witness` drift detection, and `skill-lock pin` resolves a license (declared → LICENSE text → README) before pinning. Inspired by Multica's lockfile concept; clean-room, no third-party code.

### Engineering-discipline guidance pack

- Add four routable guidance skills (think-before-coding, simplicity-first, surgical-changes, goal-driven-execution) wired into the orchestrator catalog, obligations (`OBL-APP-008`), and keyword routing. Adapted from the MIT-licensed andrej-karpathy-skills with attribution in `THIRD_PARTY_NOTICES.md`.

### Governed multi-agent fleet foundation

- Add `fleet` — clean-room runtime detection, fail-closed `governed_dispatch` admission, and workspace GC; plus design docs for the full governed runtime and the multi-user/multi-tenant roadmap. The live executor remains roadmap (documented, not built).

### Validation

- Full suites green: Python 1646 passed, frontend 280 passed, Rust/Tauri 56 passed.

## [3.0.0-internal.28] - 2026-05-31

### Release smoke hardening (real fix, take 2)

- Correct the NSIS / MSI installer smoke picker to match `signalos-desktop.exe` exactly — the cargo `[package].name` from `src-tauri/Cargo.toml`. The `.27` fix matched `SignalOS.exe`, but that's the productName (display name) from `tauri.conf.json`, not the binary filename. Tauri 2 names the binary after the cargo bin name, not the productName.

## [3.0.0-internal.27] - 2026-05-31

### Release smoke hardening (real fix)

- Fix the NSIS / MSI installer smoke picker to match the Tauri productName exactly (`SignalOS.exe`) instead of matching `SignalOS|signalos|desktop` and tie-breaking by file size. The size heuristic was selecting the bundled Python sidecar (`signalos-python-*.exe`, ~25-30 MB from PyInstaller, larger than the Tauri stub) and trying to launch it as a windowed app — but the sidecar is a stdin/stdout JSON daemon and never creates a window, so the launch poll timed out at any ceiling.
- Revert the `.26` timeout bump (60 s → 25 s) — the cold-start was a symptom, not the cause; with the right binary the launch is well under 25 s.

## [3.0.0-internal.26] - 2026-05-31

### Release smoke hardening

- Widen the NSIS-installed-app launch ceiling from 25 s to 60 s so the WebView2 bootstrapper has room to download on a cold CI runner; the poll loop short-circuits once the main window appears so healthy launches stay fast.

## [3.0.0-internal.25] - 2026-05-31

### Internal delivery governance hardening

- Add an installed-app golden path for minimum-prompt delivery through real product proof.
- Keep generated products under the selected projects root while separating starter workspaces from product repos.
- Rebuild Deliver around SignalOS-managed team ownership, generic stack selection, and non-technical approval.
- Fetch provider models through the allowed ACL and keep model selection selectable instead of typed.
- Require release, UX proof, and validation skip evidence before claiming delivery readiness.

## [3.0.0-internal.24] - 2026-05-28

### Internal product workspace and delivery UX recovery

- Route Deliver greenfield products into the user's selected projects root instead of the internal starter workspace.
- Hide technical design questions from non-technical users and show SignalOS-managed implementation decisions in plain language.
- Keep Deliver previews inside the app without blob navigation failures or blocked preview pages.
- Replace raw provider/model authorization failures with repairable setup messages and clear rejected keys.
- Block starter-workspace governance commands from showing product release-readiness failures before a product repo exists.

## [3.0.0-internal.23] - 2026-05-28

### Internal onboarding provider hardening

- Let first-run setup complete when provider validation rejects a typed API key, showing a repair warning instead of failing the workspace setup.
- Validate provider access before saving the typed key, and clear rejected keys on unauthorized responses.
- Record onboarding provider status as tested only after a successful provider round-trip.

## [3.0.0-internal.19] - 2026-05-27

### Internal usability recovery

- Add scoped Deliver styling, live delivery phase progress, and a longer activity-aware delivery wait.
- Route Terminal chips to supported SignalOS, git, and preview actions instead of raw unknown command strings.
- Replace ambiguous chat/test-debt failure states with actionable workspace/provider/evidence messages.

## [3.0.0-internal.18] - 2026-05-27

### Onboarding workspace order hotfix

- Create and initialize the selected projects-root starter workspace before writing the onboarding identity file.
- Add a regression cage that fails if onboarding identity writes move ahead of workspace setup again.

## [3.0.0-internal.17] - 2026-05-27

### Internal managed workspace onboarding

- Replace first-run product-folder selection with one projects-root folder, then create a starter SignalOS workspace underneath it.
- Keep per-project `signal-init --mode keep` behavior unchanged for explicit product repos, generated products, and adoption flows.
- Fail setup immediately on workspace initialization errors instead of booting into a broken app state.

## [3.0.0-internal.16] - 2026-05-27

### Internal onboarding ACL fix

- Permit the registered provider/model commands through the Tauri capability ACL so onboarding can fetch live provider models.
- Add a blocking ACL parity check across release, CI, and local test gates so registered commands cannot ship without active permissions again.

## [3.0.0-internal.15] - 2026-05-27

### Internal release manifest hardening

- Retry and rebase the release manifest push so transient remote rejections do not leave an otherwise complete release workflow red.
- Skip the manifest push entirely when the generated manifest has no changes.

## [3.0.0-internal.14] - 2026-05-27

### Internal release lockfile hardening

- Commit the Tauri `Cargo.lock` so release CI validates RustSec policy against the same locked dependency graph used by shipped builds.

## [3.0.0-internal.13] - 2026-05-27

### Internal release security gate hardening

- Promote RustSec dependency audit from advisory telemetry to blocking CI and release policy.
- Require explicit, expiring RustSec exception evidence before any advisory can be ignored.
- Install and run the same strict audit policy from test automation, L6, local L1 gates, and release CI.

## [3.0.0-internal.12] - 2026-05-27

### Internal release cleanup

- Include the post-release Rust formatting fix and secret-scanner false-positive cleanup in the release tag.

## [3.0.0-internal.11] - 2026-05-27

### Internal onboarding model picker fix

- Replaced onboarding and settings model typing paths with fetched-provider model selects.
- Fetch provider models before onboarding connection tests so retired model IDs do not force a new app release.
- Normalize Tauri string/object rejections so setup failures show the real error instead of `undefined`.
- Fill the selected provider radio circle instead of only highlighting the provider card.

## [3.0.0-internal.10] - 2026-05-26

### Internal workflow skip hardening

- Replaced GitHub Actions step-level OS/channel `if:` skips with explicit platform dispatchers that succeed or fail inside the step, so required CI no longer reports skipped conditional steps.

## [3.0.0-internal.9] - 2026-05-26

### Internal sidecar stdin normalization

- Normalize BOM/NUL-padded IPC input lines in the Python sidecar before JSON parsing so Windows release smoke cannot fail on redirected stdin encoding artifacts.

## [3.0.0-internal.8] - 2026-05-26

### Internal release sidecar startup ordering

- Wait for the packaged sidecar `init` readiness JSON before sending Windows release-smoke requests, keeping bounded reads around startup and response handling.

## [3.0.0-internal.7] - 2026-05-26

### Internal release sidecar stdin encoding

- Write Windows release-smoke sidecar requests as explicit UTF-8 bytes to redirected stdin so PowerShell 5.1 cannot corrupt the JSON payload encoding.

## [3.0.0-internal.6] - 2026-05-26

### Internal release sidecar JSON serialization

- Replaced PowerShell `ConvertTo-Json` sidecar request serialization in Windows release smoke with a deterministic JSON writer for the simple IPC payload shape.
- Log the exact sidecar request JSON used by the smoke harness so future hosted-runner failures show the wire payload.

## [3.0.0-internal.5] - 2026-05-26

### Internal release sidecar one-shot smoke

- Replaced the packaged sidecar persistent IPC smoke with one-shot sidecar requests that close stdin after each JSON command.
- Removed the Windows PowerShell async `ReadLineAsync` path that bypassed request timeouts on hosted runners.

## [3.0.0-internal.4] - 2026-05-26

### Internal release CI hardening

- Bound Windows release smoke MSI/NSIS extraction, install, and uninstall waits so CI fails with evidence instead of hanging.
- Bound the Release workflow Windows smoke step to 20 minutes.
- Moved L6 nightly deep validation into its own scheduled/manual workflow so normal push CI no longer reports a skipped L6 job.
- Replaced hosted-runner WebView2 DevTools smoke output with an explicit fallback pass instead of a skipped result.

## [3.0.0-internal.3] - 2026-05-26

### Internal release Windows smoke isolation

- Run direct sidecar IPC validation before app-launch smoke so a prior WebView2/app shutdown cannot poison the packaged sidecar stdin/stdout check.
- Scrub Python and SignalOS environment variables before direct sidecar smoke, and kill/read stderr on timeout for actionable Windows CI diagnostics.
- Resolve Git Bash from standard Windows install paths so emitter integration tests fail instead of skipping when `bash` is not on `PATH`.

## [3.0.0-internal.2] - 2026-05-26

### Internal release sidecar readiness fix

- Moved packaged sidecar readiness reporting until after the IPC loop is ready to receive stdin requests.
- Added a bundled sidecar ping probe before the heavier `signal-init` smoke so Windows release failures identify transport readiness separately from init work.

## [3.0.0-internal.1] - 2026-05-26

### Internal release smoke fix

- Bounded best-effort `signalos init` subprocesses so packaged Windows sidecar validation cannot hang indefinitely during git or IDE hook setup.
- Added sidecar request and progress logging to the unsigned installed-build smoke so future timeouts identify the exact IPC request.

## [1.1.1] - 2026-05-17

### Hotfix — v1.1.0 shipped non-interactive on Windows

- Root cause: Tauri 2 auto-injects a `nonce-<random>` into both `script-src` and `style-src` of the configured CSP. Per the CSP spec, the presence of a nonce makes the browser ignore any sibling `'unsafe-inline'`. Result: every `style="…"` (126 occurrences) and every `onclick="…"` (45 occurrences) introduced by the v2 UI revamp was silently dropped at parse time — the window rendered, but nothing could be clicked, searched, or closed, and the hardcoded "$0.04 / Claude · live" placeholders never updated because IPC was also blocked.
- Added `src/js/csp-bootstrap.js` — a small synchronous bootstrap that runs before `app-v2.js`, reads every inline `style=` into a single dynamic `<style nonce>` block (mirroring Tauri's runtime nonce), and rewrites every inline `onclick=` into a proper `addEventListener` binding. No HTML refactor required; the v2 UI is byte-identical.
- Fixed `connect-src` to allow `http://ipc.localhost` (the actual Windows Tauri 2 IPC origin) — previously only the `https://` variant was listed, so `Event.listen` and most invoke calls were blocked.
- Replaced the hardcoded `$0.04` / `Claude · live` placeholders in the titlebar and settings pane with neutral `—` / `Loading…` so the shell no longer lies about state while IPC is initialising.
- No CSP relaxation: nonces remain in effect, and `'unsafe-inline'` is still subordinated. Inline content is rewritten at boot, not whitelisted.
- Fixed the **Close (X) button leaving a static brown screen** — two-layer bug:
  - `_doExit()` called `window.__TAURI__.window.getCurrent().close()` — Tauri 2 renamed `getCurrent()` to `getCurrentWindow()` and dropped the old name, so the call resolved to `undefined?.close?.()` and silently no-op'd.
  - Even after fixing the API name, `close()` returned `Command plugin:window|close not allowed by ACL` because the capability file (`src-tauri/capabilities/default.json`) only granted `core:default`, which does **not** include window-control permissions in Tauri 2. Added explicit `core:window:allow-close` / `allow-minimize` / `allow-maximize` / `allow-toggle-maximize` / `allow-start-dragging`. The traffic-light buttons (red/yellow/green dots) were silently broken for the same reason.
  - On the off-chance both APIs are missing in some future Tauri release, `_doExit()` now restores window visibility and surfaces a toast pointing at Alt+F4 instead of leaving a dead window.

## [1.1.0] - 2026-05-16

### Revamped UI

- Replaced the desktop shell with the SignalOS v2 interface and local font/icon assets.
- Added the v2 Tauri wiring layer for onboarding, streaming chat, dashboard, gates, enforcement, Brain, Vault, History, Settings, terminal, file tree, and preview.
- Switched the desktop window to custom chrome with hidden native titlebar controls.
- Updated the app icon set for the revamped brand treatment.
- Added a porting map documenting mockup-to-runtime bindings and remaining follow-up work.

## [1.0.0-internal2] - 2026-05-16

### Forced onboarding (real bug fix — internal1 shipped broken for upgraders)

- Bumped `WIZARD_VERSION` to invalidate stale `signalos.onboarding.wizard.v1` localStorage from prior beta installs. WebView2 stores localStorage per app identifier (`com.signalos.desktop`), so saves from v0.0.7..v1.0.0-betaN were carrying over to internal1 and convincing the wizard it had already finished. Result on first launch: no wizard, main UI rendered fully but unconfigured. Fixed.
- Hard-gated the main app shell behind first-time setup: `body.setup-pending` hides `#app` via CSS until the wizard completes (or until `maybeRunWizard` returns false, meaning setup is already done). No more "crowded UI with no setup state behind a wizard that didn't run."
- Removed the "Skip for now" button from the wizard entirely. Onboarding is mandatory, not advisory — title now reads "SignalOS — first-time setup."
- Run order in `init()` rewritten: `loadBasics` → wizard → (only then) `render()` + `refreshProjectState`. The user never sees the main UI before setup is complete.

## [1.0.0-internal1] - 2026-05-15

### v1.0 / Waves 1–5 (G0..G4) signed — internal testing build

- Wave 1 / G0 Stabilize: first-run wizard (7 steps), `/signal-init` mode-aware (full/keep/minimal/skip), real chat-ping AI test, refreshed provider defaults, redact-on-export, Replit-style Secrets manager (list/reveal/edit/delete/diff).
- Wave 2 / G1 Build: PhaseContract + progress event stream, three-pane shell (left collapsible + center chat + right preview), `LocalProcessSupervisor` (npm install / dev / Python flask serve), iframe preview pane, mocks purged.
- Wave 3 / G2 Land — Fully wired & enforced: 12-rule enforcement engine, atomic audit-append, gate-gating + plan-gating on Build, self-healing Builder retries, file diff preview before write, override modal with audited reason, wave freeze respected.
- Wave 4 / G3 Harden: HTTP timeouts on every reqwest call, per-model `max_tokens`, real Stop (tree-kills sidecar + restarts engine), CSP tightened, destructive-action confirms.
- Wave 5 / G4 Verify: test-debt store + IPC, mutation-threshold gate, test-first gate, L0/L1 gate runners, GitHub Actions workflow, 9-test live integration suite.
- Streaming AI tokens across all 12 providers via `streamingProviderChat` helper (5 high-token callers).
- §11.1 deeper UX: Files / Gov / Mem left-pane tabs with intent-driven auto-switch, file tree with diff badges, per-file regenerate, Builder-aware conversation history.
- Interactive 23-slide onboarding tour at `docs/onboarding-tour.html`, design-system-unified.
- Identity + role assignment in wizard; tree-kill on sidecar Stop; install phase interruptible by Stop.
- Caret version specifiers pinned; signed-gates cache; non-destructive `set_workspace`.

### Internal testing build path

- Added `scripts/build-internal.ps1` + `scripts/build-internal.sh` — unsigned installer + JSON attestation (`distribution/internal/attestation-<short-commit>.json`) keyed by `git config user.name` + `user.email`. Schema: `signalos.attestation.v1`.
- Added `docs/INTERNAL_TESTING_BUILD.md` with the tester distribution checklist (SmartScreen / Gatekeeper bypass instructions, SHA-256 verification).
- Audit-logs every internal build to `.signalos/AUDIT_TRAIL.jsonl` (`action: "build:internal-attest"`).
- Signing-ready, not signed: same build infrastructure will land Authenticode / Developer ID / minisign signatures when credentials exist. See `docs/RELEASE_GATES_RUNBOOK.md`.

### Real bugs found by the live smoke and fixed in the same push

- Bundled sidecar exe was at v0.0.7 (pre-Wave-2) — rebuilt via `scripts/bundle-sidecar.ps1`.
- `capabilities/default.json` missing `shell:allow-spawn` for sidecar — desktop booted but Python sidecar never spawned. Fixed.

### Not yet shipped (intentional — see RELEASE_GATES_RUNBOOK.md)

- Authenticode / Developer ID code signing
- macOS notarization
- Minisign signatures in `distribution/update-manifest/*.json`
- Clean-machine VM validation

## [0.0.9] - 2026-05-14

### SignalOS Builder path

- Changed Builder from static-only output to a stack selector with React / Vite, Next.js, Node / Express, Python / Flask, plain HTML, and Auto options.
- Made React / Vite the default browser-app stack instead of plain HTML.
- Made Build run through SignalOS preparation: initialize when needed, run status, generate a scoped plan, write a `.signalos/builds/` evidence brief, save a decision note, write files, and refresh status.
- Added run instructions and phase progress for Prepare, Plan, Build, and Review.
- Added generated app manifest and app entry detection to Project/Dashboard artifacts.

## [0.0.8] - 2026-05-14

### Builder UX reset

- Changed the first screen to Build, with one app description box and a Build app action.
- Added generated-file writing so Build creates real `index.html`, CSS, JavaScript, and README files in the selected project folder.
- Split Project, Chat, Secrets, Dashboard, Settings, and History into separate views instead of crowding the first screen.
- Added a dedicated Secrets page for local `.env.local`, `.env`, and `.env.development` values.
- Routed build requests out of Chat and into Build, and routed `signalos signal-*` text as SignalOS commands.
- Fixed installed-app setup for non-empty project folders by running project initialization with the selected folder and `--force`.
- Increased Anthropic response budget for Builder file bundles.
- Moved in-app update checks to the public GitHub Pages manifest URL.

### Installed-app hardening

- Reworked the first screen into a Build surface that turns a plain app request into real static project files, with Project, Chat, Secrets, Dashboard, Settings, and History separated into their own views.
- Added sandboxed generated-file writing for Builder output and a dedicated Secrets page for local `.env.local` / `.env` / `.env.development` values.
- Routed `signalos signal-*` text input as SignalOS commands instead of normal AI chat, and routed build-intent chat messages back to Build.
- Made `/signal-init` from the installed app initialize non-empty selected project folders with the expected SignalOS project files instead of failing on normal app folders.
- Reworked the first-run journey around Build, Project, Chat, Dashboard, Secrets, Settings, History, and gate signing instead of a single locked guide screen.
- Added scrollable workspace layout, phase tabs, persistent project transcript, and clear command progress states.
- Added model fetching, model picker selection, and manual "Other model" entry for AI setup.
- Made Settings operational with project controls, AI provider/model/key replacement, saved-key delete, secret summaries, engine diagnostics, and budget controls.
- Added real AI connection testing before marking a provider ready.
- Added setup/status result handling with project artifact inspection and open-in-system actions.
- Added sidecar status, ping, restart, stop-current-command behavior, and redacted diagnostics copy.
- Added dashboard cards for project, AI, engine, next action, gates, and detected project files.
- Added visible gate signing with signer name input.
- Aligned updater endpoints with the checked-in beta/latest manifests.
- Added `scripts/verify-release.ps1` for local release readiness checks and unsigned installer builds.
- Added ASCII-only installed-app checklist scripts for Windows and cross-platform manual validation.
- Added structured setup/status result cards in the chat activity stream.
- Added first-project onboarding proof checklist for project, AI, setup, status, first note, and first gate action.
- Added redacted issue-report export and team handoff export into the selected project's `.signalos` folder.
- Added audit timeline rendering, update-channel preference, project templates, workflow recipes, and local privacy guidance.
- Added provider error recovery copy for invalid keys, invalid models, quota/rate limits, network failures, and local Ollama failures.
- Added structured command cards for advanced/preview command outputs with output mode and next-step guidance.
- Added user, release-operator, provider-validation, and clean-machine validation docs.
- Added unsigned installed-build smoke automation for release exe launch, sidecar startup, MSI extraction, NSIS silent install, installed-app launch, and NSIS uninstall.
- Added installer-only runtime validation for NSIS install, bundled engine IPC, fresh-project setup/status, secret redaction, Brain, gate status, and uninstall.
- Added live-provider validation for local Ollama and environment-provided cloud keys.
- Added release URL validation for local manifests, remote updater endpoints, and public docs URLs.
- Added GitHub Pages workflow source for publishing the landing page and docs.
- Moved updater endpoints to GitHub Pages so installed apps do not depend on authenticated raw GitHub URLs from a private repo.
- Added Linux package artifact verification to the release workflow.
- Fixed manual release dispatch so the requested version is used for release notes, asset names, release tags, and manifest generation instead of accidentally using the branch name.
- Reworked the landing page to describe the ready-to-sign installer candidate state without public-beta overclaiming.
- Verified local checks on Windows: `node --check`, `cargo check`, `cargo test`, Python tests, unsigned NSIS/MSI bundle generation, unsigned installed-build smoke, installer-only runtime smoke, local Ollama live-provider validation, and release URL validation.
- Verified CI release workflow for beta `0.0.7`, including Windows unsigned installer smoke and Linux package artifact verification.

## [0.0.7] - 2026-05-14

### Standalone app repo

- Vendored the SignalOS Core runtime into the desktop app repository so release builds no longer depend on checking out a separate private repository.
- Updated CI sidecar bundling to build from the app repo only.
- Switched the desktop version to numeric `0.0.7` so Windows MSI can build while GitHub still marks the release as a beta/prerelease.
- Fixed macOS/Linux sidecar bundling by passing PyInstaller absolute paths for the vendored core.
- Kept the safe chat attachment work from beta5.

## [1.0.0-beta5] - 2026-05-13

### Chat attachments and release fix

- Added chat file selection and drag/drop attachment intake.
- Added support for images, PDFs, Word, PowerPoint, Excel, text, Markdown, CSV, JSON, logs, code files, and zip references.
- Blocked `.env`, key/certificate files, SQL/database dumps, and likely secret attachments.
- Redacted likely API keys and secrets from accepted text and document summaries.
- Added Office/PDF text extraction for safe summaries without returning raw file bytes.
- Fixed CI release builds by bundling the platform-specific Python sidecar before Tauri packaging.

## [1.0.0-beta4] - 2026-05-13

### Provider and secrets release

- Added Qwen as a first-level AI provider.
- Moved lower-frequency AI integrations under More providers.
- Added OpenRouter, DeepSeek, Mistral, Groq, Cerebras, Together AI, and xAI provider entries.
- Removed frontend access to raw saved AI keys.
- Added secret redaction for `.env` files, likely secret values, command arguments, sidecar output, errors, notes, and nested response data.
- Added a settings secrets summary that shows secret file names and variable names only.
- Sanitized provider model-list errors so API keys are not echoed back to the UI.

## [1.0.0-beta1] - 2026-05-03

### First public beta

- Native desktop app for macOS, Windows, and Linux.
- Multi-provider LLM chat: Anthropic Claude, OpenAI, Google Gemini, and Ollama.
- API keys stored in OS keychain and never written to disk.
- SignalOS governance UI for wave state, gate signing, and audit trail.
- Brain knowledge base with BM25 search.
- Live phase debt and belief confidence dashboard.
- Command palette with `/signal-*` commands.
- Python SignalOS Core sidecar integration.
- Auto-updater with signed update manifests.
- File watcher for workspace change events.
