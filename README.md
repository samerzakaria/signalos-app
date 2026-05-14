# SignalOS App

SignalOS App is the native desktop shell for the vendored SignalOS Core runtime. It is built for an installer-first workflow: an end user installs the app, chooses a separate project folder, connects an AI provider or local model, and uses SignalOS without this repository.

Current status: ready-to-sign installer candidate. The app-side first-run journey is implemented; code signing, notarization, release signatures, and clean-machine installer validation are release gates.

## What It Does

- Chat-first workspace for plain AI questions and `/signal-*` commands.
- Guided project setup with visible setup/status results and project artifacts.
- Dashboard for project readiness, AI state, engine health, next action, gates, and files.
- Gate signing UI with signer name and audit-oriented command output.
- First-project proof checklist for project, AI, setup, status, first note, and first gate action.
- Notes and Brain search for project memory.
- Multi-provider AI setup with model fetch, model picker, and manual "Other model" entry.
- OS keychain storage for API keys; raw keys are not shown after save.
- Secret summary that shows risky file names and variable names without exposing values.
- Engine diagnostics with ping, status, restart, and redacted diagnostic copy.
- Redacted issue-report export and team handoff export into the selected project.
- Project templates, workflow recipes, and local privacy guidance in the in-app guide.
- Beta/stable update-channel preference for release checks.
- Cost controls for session spend and monthly budget.
- Release readiness script for local checks and unsigned installer builds.

## End User Requirements

An installed user should only need:

- The SignalOS installer for their operating system.
- A writable project folder.
- An AI provider key, or Ollama running locally with a pulled model.
- Network access for cloud AI providers.

They should not need:

- This `signalos-app` repository.
- The separate SignalOS core repository.
- Python installed system-wide.
- Rust, Node, Cargo, Tauri, or build tools.

## Architecture

```text
signalos-app/
|-- src/                         Frontend app shell
|   |-- index.html               HTML/CSS UI
|   `-- js/
|       |-- app.js               Workspace behavior
|       `-- ipc.js               Tauri IPC wrapper and browser mock
|-- src-tauri/                   Rust backend (Tauri 2)
|   |-- src/
|   |   |-- main.rs              App entry point and command registration
|   |   |-- ipc.rs               Project, AI, notes, command, and artifact IPC
|   |   |-- provider.rs          AI provider integration
|   |   |-- sidecar.rs           Python SignalOS Core sidecar manager
|   |   `-- keychain.rs          OS credential storage
|   |-- Cargo.toml
|   `-- tauri.conf.json          App, bundle, signing, and updater config
|-- python/                      Vendored SignalOS Core runtime
|-- distribution/
|   |-- landing/                 Download landing page
|   `-- update-manifest/         Beta/latest updater manifests
|-- docs/
|   `-- SIGNALOS_INSTALLED_APP_REFERENCE_REVIEW.md
|-- scripts/
|   |-- bundle-sidecar.ps1       Build the bundled Python sidecar
|   |-- verify-release.ps1       Local release readiness checks
|   |-- test-installer.ps1       Windows manual installed-app checklist
|   `-- test-installer.sh        Cross-platform manual installed-app checklist
|-- SIGNING.md                   Signing and notarization checklist
`-- README.md
```

Tauri owns the native window, menus, IPC, updater, and OS integration. The Rust backend launches the bundled Python sidecar and keeps AI keys in the OS credential store. The webview frontend provides the installed-user journey.

## Development

### Prerequisites

- Rust stable toolchain.
- Node.js 18+.
- Python 3.11+ for sidecar bundling and local development.
- Tauri CLI: `cargo install tauri-cli`.

### Run The App

```powershell
cargo tauri dev
```

The browser mock can be opened directly from `src/index.html`, but real app behavior should be verified through Tauri because keychain, sidecar, updater, and file opening are native features.

### Verify Release Readiness

Run the local readiness checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1
```

Build unsigned local installer bundles:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -BuildInstaller -SmokeInstalledBuild -InstallNsisSmoke
```

Expected Windows outputs:

```text
src-tauri/target/release/bundle/nsis/SignalOS_0.0.7_x64-setup.exe
src-tauri/target/release/bundle/msi/SignalOS_0.0.7_x64_en-US.msi
```

Run the local unsigned package smoke again without rebuilding, if needed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -SmokeInstalledBuild -InstallNsisSmoke
```

If the smoke command reports that SignalOS is already running, close SignalOS and retry.

Run the installer-only runtime smoke:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -InstalledRuntimeSmoke
```

Run live provider validation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -LiveProviderValidation
```

Validate local and remote release URLs:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -ValidateRemoteReleaseUrls
```

Run the manual installed-app checklist on a clean Windows machine or VM:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-installer.ps1
```

## Signing

The product is ready to sign, but signing is intentionally not completed in this stage. See [SIGNING.md](SIGNING.md) for certificates, notarization, updater signing, and CI secrets.

Do not describe a build as public beta until signed packages and signed update manifests have been validated from a tagged release.

## Distribution

Updater manifest source files live in:

```text
distribution/update-manifest/beta.json
distribution/update-manifest/latest.json
```

The installed app reads the public copies deployed through GitHub Pages:

```text
https://samerzakaria.github.io/signalos-app/update-manifest/beta.json
https://samerzakaria.github.io/signalos-app/update-manifest/latest.json
```

The Tauri updater is wired to those checked-in manifest paths. Release signatures must be populated during the signed release process.

## Reference

The installed-app product review, user guide, roadmap, and acceptance checklist are in [docs/SIGNALOS_INSTALLED_APP_REFERENCE_REVIEW.md](docs/SIGNALOS_INSTALLED_APP_REFERENCE_REVIEW.md).

Additional operating docs:

- [User Guide](docs/USER_GUIDE.md)
- [Release Operator Guide](docs/RELEASE_OPERATOR_GUIDE.md)
- [Provider Validation Guide](docs/PROVIDER_VALIDATION_GUIDE.md)
- [Clean-Machine Validation](docs/CLEAN_MACHINE_VALIDATION.md)

## License

Proprietary - Copyright 2026 SignalOS
