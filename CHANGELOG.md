# Changelog

## [Unreleased] - 2026-05-16

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
