# Changelog

## [Unreleased] - 2026-05-14

### Installed-app hardening

- Reworked the first-run journey around Chat, Dashboard, Guide, Settings, Notes, History, and gate signing instead of a single locked guide screen.
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
