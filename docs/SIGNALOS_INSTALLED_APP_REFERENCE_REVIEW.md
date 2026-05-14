# SignalOS Installed-App Reference Review

Date: 2026-05-14

Scope: this review is for `signalos-app`, not the mother SignalOS repository. The target user has only the installer and a project folder. They do not have this repo, the SignalOS Core repo, Python, Rust, Node, Cargo, or Tauri.

This document is the standing reference for what the installed app must do, what is implemented, what is still app-side missing, which external release gates still require real proof, and how an end user should operate it.

## Executive Verdict

SignalOS App is now a ready-to-sign installer candidate, not the earlier prototype state.

The signing work is intentionally not done at this stage. Everything below treats signing, notarization, release signatures, and platform reputation as release gates outside the app implementation pass.

The app-side journey now exists:

- Choose a project folder.
- Connect AI with a saved key or local Ollama.
- Fetch model names and select one, or choose "Other model" and type it.
- Chat with the selected AI provider.
- Run `/signal-*` commands through the bundled engine.
- Set up and check a project.
- Build a first app through the SignalOS path: setup/status, build brief, selected stack files, and status refresh.
- See setup/status results instead of only a generic "Done".
- Inspect created project artifacts.
- Use Dashboard, Guide, Notes, History, Settings, and gate signing.
- Restart or diagnose the SignalOS engine.
- Keep secrets out of prompts and raw UI display.

The remaining work is release proof, not app implementation: install the built app on a clean machine where this repository is not present, sign/notarize the packages, and validate updates from signed release assets.

## Current Classification

```text
Ready-to-sign installer candidate
```

Do not call it:

```text
Signed public beta
Production release
Clean-machine proven release
```

It can be used on the next project as an internal controlled trial after the unsigned installer is installed and tested on a clean Windows machine. It can be shared externally only after signing and installer-only validation pass.

## What Was Missing Before This Pass

These were the major product gaps found during the installed-app review:

- The main screen could feel locked because the workspace did not scroll correctly.
- The old Guide kept previous phases taking most of the screen instead of behaving like tabs.
- Model names had to be typed manually instead of fetched and selected.
- Settings looked read-only and did not expose enough operational controls.
- Users could not easily find where secrets or saved AI key state lived.
- A saved key could look connected even when the provider was not actually tested.
- Setup could eventually say "Done" without showing what happened.
- Users had no clear next action after setup.
- Users could ask "where do I chat?" because Chat was not dominant enough.
- Command progress was weak.
- Command capability was confusing because runnable, advanced, and preview commands were not clearly labeled.
- The sidecar engine was too hidden when it failed.
- Gate signing existed below the UI but was not productized.
- Dashboard claims were ahead of what the installed UI actually showed.
- Release readiness was too manual.
- The updater endpoint and checked-in manifest paths were not aligned.
- Public docs and the reference review were stale compared with the app.

## Implemented In This App Pass

Completed in `signalos-app` on 2026-05-14:

- Workspace scrolling was fixed so the user can move through the screen.
- The first screen is now Build, not a crowded all-in-one guide.
- Build turns a plain app request into real project files instead of dumping scaffold text into chat.
- Build no longer forces plain HTML. It supports React / Vite, Next.js, Node / Express, Python / Flask, plain HTML, and Auto stack selection.
- Build now runs SignalOS preparation before generation: `/signal-init` when needed, `/signal-status`, a scoped build plan/evidence brief in `.signalos/builds/`, a saved decision note, file writing, and a final status refresh.
- Project, Chat, Secrets, Dashboard, Settings, and History are separate surfaces.
- Chat now routes build requests back to Build instead of pretending a text answer created files.
- `signalos signal-*` input is routed as a command instead of normal AI chat.
- Chat and command results persist per project folder.
- Slash commands show running/progress state.
- Long-running command work can be stopped by restarting the engine.
- Setup/status results inspect the selected project after the command finishes.
- Project artifacts are shown when present:
  - `.signalos/`
  - `core/strategy/PLAN.md`
  - command definitions
  - IDE integration files
  - README
  - app dependency manifest
  - generated app entry
- Project artifact paths can be opened from the UI.
- The command catalog labels ready, advanced, and preview commands.
- Dashboard is now a real view with project, AI, engine, next action, gates, and files.
- Help/Guide is available in-app for first-run and recovery.
- Gate signing is visible in the UI and accepts a signer name.
- AI setup supports provider selection, key save, fetched models, model picker, and manual "Other model" entry.
- AI readiness requires a real provider test.
- Settings exposes provider, model, key replacement, saved-key delete, engine diagnostics, updates, and budget controls.
- Secrets has a dedicated page for saving `.env.local`, `.env`, or `.env.development` values without exposing raw values.
- Project secret values are written locally through a sandboxed app command and are not sent to AI prompts.
- Engine diagnostics include status, ping, restart, and redacted diagnostic copy.
- Session and monthly budget controls were added.
- The updater endpoint now points to beta/latest manifests hosted through GitHub Pages, so private-repo raw GitHub URLs are not required by installed apps.
- Sidecar request IDs are more collision-resistant.
- `scripts/verify-release.ps1` runs release readiness checks and can build unsigned installer bundles.
- Command output cards now structure status/setup results instead of relying only on raw text.
- Dashboard now includes a first-project proof checklist for project, AI, setup, status, first note, and first gate action.
- Issue reports can be exported as redacted Markdown files inside `.signalos/issue-reports/`.
- Team handoff reports can be exported as Markdown files inside `.signalos/handoffs/`.
- History now renders audit entries as a timeline and supports handoff export from the installed UI.
- Settings now includes beta/stable update-channel preference and channel-aware update checks.
- Help now includes project templates, workflow recipes, and local privacy mode guidance.
- Provider errors are translated into user-facing recovery messages for key, model, quota, network, and local Ollama failures.
- Advanced and preview commands now get structured command cards showing command class, output mode, and next step.
- Public-doc source files are ready in `docs/USER_GUIDE.md`, `docs/RELEASE_OPERATOR_GUIDE.md`, `docs/PROVIDER_VALIDATION_GUIDE.md`, and `docs/CLEAN_MACHINE_VALIDATION.md`.
- The download landing page now reflects the real ready-to-sign candidate state without claiming signed public beta readiness.
- `scripts/smoke-installed-build.ps1` verifies the unsigned release executable launches, the bundled engine starts, MSI extraction works, and NSIS silent install/launch/uninstall works.
- `scripts/validate-installed-runtime.ps1` installs the NSIS package to a temp folder, drives the bundled engine through JSON IPC, creates a fresh project outside this repo, runs setup/status, verifies secret redaction, verifies Brain, verifies gate status, and uninstalls.
- `scripts/validate-live-providers.ps1` validates live provider model fetch/chat when provider keys are present and validates local Ollama when it is running.
- `scripts/validate-release-urls.ps1` validates local update manifests and can require public remote release/docs URLs.
- GitHub Pages is enabled and publishes the landing page, docs, and update manifests from `.github/workflows/pages.yml`.
- The release workflow now verifies Linux package artifacts when the Linux release job runs.
- Manual release dispatch now uses the requested version for release notes, asset names, release tags, and update manifests instead of deriving release version from the branch name.
- Local Windows unsigned installer build passed:
  - `src-tauri/target/release/bundle/nsis/SignalOS_0.0.7_x64-setup.exe`
  - `src-tauri/target/release/bundle/msi/SignalOS_0.0.7_x64_en-US.msi`
- Local Windows unsigned package smoke passed on 2026-05-14.
- Local installer-only runtime smoke passed on 2026-05-14. Evidence: `docs/release-evidence/installed-runtime-local.md`.
- Local Ollama live-provider validation passed on 2026-05-14. Evidence: `docs/release-evidence/live-providers-local.md`.
- Local release URL validation passed for checked-in files and recorded the pre-publish private-raw 404s on 2026-05-14. Evidence: `docs/release-evidence/release-urls-local.md`.
- Remote release URL validation passed on 2026-05-14 after moving update manifests to GitHub Pages. Evidence: `docs/release-evidence/release-urls-remote.md`.
- CI release workflow passed for `version=0.0.7`, `channel=beta` on 2026-05-14. Evidence: `docs/release-evidence/ci-release-0.0.7-beta.md`.
- CI Smoke workflow passed for commit `70256765be70c623085c32a4d17faa408bca41ab` on 2026-05-14. Run: `https://github.com/samerzakaria/signalos-app/actions/runs/25865410092`.
- CI Pages workflow passed for commit `70256765be70c623085c32a4d17faa408bca41ab` on 2026-05-14. Run: `https://github.com/samerzakaria/signalos-app/actions/runs/25865409991`.

## What Is Still Missing In App Code

None.

The app-side installed-user journey is implemented in this repository. The items below are external proof gates or live validation gates. They are not acceptable to fake with placeholders and cannot be truthfully marked complete without the real installer environment, signing credentials, release assets, or live provider accounts.

## External Release Proof Gates

- Physical or VM clean-machine Windows UI validation from only the generated installer. The installer-only runtime path is proven locally; manual UI clicking still needs a separate clean machine.
- Windows setup/chat/update-check/upgrade/uninstall validation outside the development machine.
- Signed Windows installer and Windows reputation validation.
- macOS signed and notarized build validation.
- Signed release manifests generated from a real tagged release.
- Auto-update validation from an older signed build to a newer signed build.
- Real next-project trial using only the installer, with no source repo available. A synthetic temp-project runtime trial passed locally; the human trial remains external.
- Final copy polish based on the first real user trial.
- Real cloud-provider validation against live accounts for each configured cloud provider. Local Ollama passed; no cloud API keys are present in this machine environment.

## Release Gates

### Gate 1: Local App Verification

Required command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1
```

Pass criteria:

- Frontend JavaScript syntax checks pass.
- Rust checks pass.
- Rust tests pass.
- Python tests pass.
- Tauri config and manifest sanity checks pass.

### Gate 2: Unsigned Installer Build

Required command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -BuildInstaller -SmokeInstalledBuild -InstallNsisSmoke
```

Pass criteria:

- NSIS installer is generated.
- MSI installer is generated.
- Release executable launches on the development machine.
- Bundled SignalOS engine starts.
- MSI administrative extraction works.
- NSIS silent install works.
- NSIS installed app launches.
- NSIS silent uninstall works.

### Gate 2A: Unsigned Package Smoke

Required command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -SmokeInstalledBuild -InstallNsisSmoke
```

If this reports that SignalOS is already running, close the app and run the command again. The script only closes the app instances that it launches during the smoke test.

Pass criteria:

- Release executable launches.
- Bundled SignalOS engine starts.
- MSI administrative extraction works.
- NSIS silent install works.
- NSIS installed app launches.
- NSIS silent uninstall works.

### Gate 2B: Installer-Only Runtime Smoke

Required command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -InstalledRuntimeSmoke
```

Pass criteria:

- NSIS package installs to a temp folder.
- Runtime project is outside the `signalos-app` repository.
- Bundled engine starts through JSON IPC.
- `/signal-init` creates `.signalos/`, `core/strategy/PLAN.md`, and the command library.
- Secret scan reports variable names without leaking values.
- `/signal-status` returns a next action.
- Brain note add/search works.
- Gate status returns six gates.
- NSIS package uninstalls.

### Gate 2C: Live Provider Validation

Required local-provider command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -LiveProviderValidation
```

Required cloud-provider command when keys are available:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -LiveProviderValidation -RequireCloudProviderKeys
```

Pass criteria:

- Ollama model fetch/chat passes when Ollama is running.
- Cloud providers fetch models and return chat content when their provider API keys are present in environment variables.
- Missing cloud keys are reported as skipped unless `-RequireCloudProviderKeys` is used.

### Gate 2D: Release URL Validation

Required local command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -ValidateRemoteReleaseUrls
```

Required remote-published command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -ValidateRemoteReleaseUrls -RequireRemoteReleaseUrls
```

Pass criteria:

- Local beta/latest manifests parse.
- Public GitHub Pages update manifest URLs return HTTP 200 after Pages deploys.
- GitHub Pages docs URLs return HTTP 200 after Pages is enabled and deployed.

### Gate 3: Clean-Machine Installer Test

Required environment:

- Clean Windows VM or physical machine.
- No `signalos-app` repository.
- No SignalOS mother repository.
- No Python/Rust/Node requirement for the user path.

Pass criteria:

- Install from NSIS or MSI.
- Launch SignalOS.
- Choose a fresh project folder.
- Connect AI.
- Fetch models.
- Select a model or type "Other model".
- Send a plain chat message.
- Run `/signal-init`.
- Confirm created files are visible in the app.
- Run `/signal-status`.
- Open Dashboard and Settings.
- Restart the app and confirm project/provider state persists.
- Confirm API key is saved but not displayed.
- Uninstall normally.

### Gate 4: Signing And Update Proof

Required proof:

- Windows package is signed.
- macOS package is signed and notarized.
- Update manifests include valid signatures.
- An older signed build updates to a newer signed build.
- Update failures are understandable to a normal user.

## End-User Guide

This guide assumes the user has only the installed app.

### 1. Install SignalOS

Install SignalOS with the installer for the operating system.

Expected result:

- SignalOS opens as a desktop app.
- No terminal is required.
- No source repo is required.

Unsigned internal builds may show operating-system security warnings. Signed release builds must not rely on the user bypassing scary warnings.

### 2. Choose A Project Folder

Choose the folder for the project SignalOS should guide.

Use a real writable project folder. Do not choose the SignalOS app repository.

Expected result:

- The project appears in the sidebar.
- The project path appears in Settings.
- SignalOS remembers the folder after restart.

### 3. Connect AI

For cloud AI:

1. Open Settings or the AI step.
2. Select the AI service.
3. Paste the API key once.
4. Fetch models.
5. Select a fetched model, or choose "Other model" and type one.
6. Save and test the connection.

Expected result:

- SignalOS says the provider responded.
- The raw key is not shown again.
- The top status moves toward ready when project and AI are valid.

For local AI:

1. Install and start Ollama.
2. Pull a model in Ollama.
3. Select Ollama in SignalOS.
4. Fetch models or type the local model name.
5. Save and test.

### 4. Chat

Use the Chat view for normal questions and slash commands.

Plain AI question:

```text
What should I do next in this project?
```

SignalOS command:

```text
/signal-status
```

Expected result:

- Plain text goes to the selected AI provider.
- Slash commands go to the SignalOS engine.
- Results remain visible after navigation and restart.

### 5. Set Up The Project

Run setup from the UI or type:

```text
/signal-init
```

Expected result:

- The app shows the command result.
- The app inspects the selected project.
- Created project artifacts are visible.
- The next action is clear.

Minimum artifacts to verify:

```text
.signalos/
core/strategy/PLAN.md
```

### 6. Check Status

Run:

```text
/signal-status
```

Expected result:

- Current phase/gate state is shown when available.
- Next action is shown when available.
- Status does not unexpectedly rewrite the project.

### 7. Use Dashboard

Use Dashboard to inspect:

- Project readiness.
- AI connection state.
- Engine state.
- Next action.
- Gate status.
- Detected project files.

### 8. Sign Gates

Open the gate signing section.

Expected result:

- The user can see available gates.
- The user enters a signer name.
- SignalOS records the signing command result.

Signing a governance gate is not the same as code-signing the installer.

### 9. Use Notes And Brain

Use Notes for project memory:

- Decisions.
- Assumptions.
- QA evidence.
- Research summaries.
- Constraints.

Do not paste secrets, database dumps, or private credentials.

### 10. Review Settings

Settings should expose:

- Project path.
- AI provider.
- Model selection.
- Saved-key state.
- Replace/delete key controls.
- Secret file summary.
- Engine status and restart.
- Budget controls.

If Settings appears read-only, the installed build is stale.

## Troubleshooting

### Screen Feels Locked

Use the current build. The workspace is expected to scroll inside the desktop window.

### AI Key Is Saved But Not Connected

A saved key is not enough. Run provider test again:

1. Open Settings.
2. Fetch models.
3. Select a model.
4. Save and test AI.

### Model Is Not Listed

Choose "Other model" and type the model name manually.

### Setup Says Done But Nothing Is Visible

Run:

```text
/signal-status
```

Then check the project artifact list. If `.signalos/` and `core/strategy/PLAN.md` are missing, setup did not complete or the folder is not writable.

### Where Do I Chat?

Use Chat. It is the main app view and accepts both plain questions and slash commands.

### Engine Did Not Start

Open Settings and use:

- Ping.
- Restart engine.
- Copy diagnostics.

If restart fails in an installed build, reinstall and attach redacted diagnostics to the issue.

### Update Check Does Nothing

Unsigned local builds may not prove update behavior. A real update pass requires signed release assets and signed manifests.

## Command Reference

Start with:

```text
/signal-init
/signal-status
/signal-brain
```

Command labels matter:

- Ready commands execute normal installed-app work.
- Advanced commands call deeper SignalOS CLI behavior.
- Preview commands show a command brief and are not promised as full workflows.

The UI must keep these labels visible so users do not mistake a preview command for a broken feature.

## Roadmap

### P0: Release Candidate Proof

- Run physical or VM clean-machine Windows UI validation from the generated installer only.
- Sign Windows installer.
- Sign and notarize macOS build.
- Generate signed updater manifests from a real tagged release.
- Validate update from older signed build to newer signed build.
- Validate live cloud-provider error messages across Anthropic, OpenAI, Gemini, Qwen, OpenRouter, and other configured cloud providers with real provider accounts.

### P1: Real Beta Polish

- Observe the next-project trial using only the installer.
- Tighten copy where the user hesitates.
- Add richer issue-report metadata once signed installer build IDs are available.
- Use the first real user trial to tune templates, recipes, and onboarding language.

### P2: Product Maturity

- Expand hosted documentation with screenshots and release-channel screenshots.
- More project templates based on real usage.
- Richer audit filters and export formats.
- Team handoff import/restore.
- More local-only privacy mode validation.
- More workflow recipes for founders, engineers, QA, and product operators.

## Acceptance Checklist For Sharing

Before sharing externally, all of these must pass:

- Installer works on a clean machine.
- App launches without source repos.
- Project selection works.
- AI setup works with fetched model selection.
- Manual "Other model" works.
- Plain chat works.
- `/signal-init` works.
- `/signal-status` works.
- Setup result shows what changed.
- Dashboard is understandable.
- Settings is operational.
- Secrets are summarized without values.
- Engine restart/diagnostics works.
- Gate signing is visible.
- Issue report export works.
- Team handoff export works.
- First-project checklist reaches completion.
- Update channel preference is visible.
- State persists after restart.
- Uninstall works.
- Signed build avoids scary OS trust warnings.
- Auto-update works from a signed older build.

## Decision For The Next Project

Use SignalOS App on the next project as an internal installed-app trial after building and installing the current package. Keep this reference open and record every point where the user hesitates.

The app implementation should no longer be described as "not even beta prototype." The honest remaining limitation is release proof: signing, clean-machine install validation, and real installed-user trial evidence.
