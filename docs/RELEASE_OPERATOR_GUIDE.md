# SignalOS App Release Operator Guide

Date: 2026-05-14

This guide is for the person preparing SignalOS App for installer-only use.

## Local Verification

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1
```

Required result:

- JavaScript syntax checks pass.
- Python safety tests pass.
- Rust compile check passes.
- Rust tests pass.
- Tauri config points at checked-in update manifests.
- Bundled sidecar exists for the host target.

## Build Unsigned Installers

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -BuildInstaller -SmokeInstalledBuild -InstallNsisSmoke
```

Expected Windows outputs:

```text
src-tauri/target/release/bundle/nsis/SignalOS_0.0.7_x64-setup.exe
src-tauri/target/release/bundle/msi/SignalOS_0.0.7_x64_en-US.msi
```

## Clean-Machine Validation

Before moving to a separate clean machine, run the local unsigned package smoke:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -SmokeInstalledBuild -InstallNsisSmoke
```

This verifies the release executable, bundled sidecar launch, MSI extraction, NSIS silent install, installed-app launch, and NSIS uninstall on the development machine.

If the smoke command reports that SignalOS is already running, close SignalOS and retry. The smoke script only closes the app instances it launches itself.

Then run the installer-only runtime smoke:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -InstalledRuntimeSmoke
```

This installs the NSIS package to a temp folder, uses a fresh temp project outside this repo, runs setup/status through the bundled engine, verifies secret redaction, verifies Brain, verifies gate status, and uninstalls.

Run the manual checklist on a clean Windows VM or physical machine:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-installer.ps1
```

The test machine must not have:

- `signalos-app` source repo.
- SignalOS mother repo.
- Python requirement for the user path.
- Rust, Node, Cargo, or Tauri requirement for the user path.

## Signing Gate

Signing is not simulated by local scripts. A signed release requires:

- Windows code-signing certificate.
- macOS Developer ID certificate.
- macOS notarization credentials.
- Tauri updater signing key.
- CI secrets configured for signed release builds.

## Update Gate

A release update pass requires:

- A real older signed build.
- A real newer signed build.
- Signed update manifests.
- Release assets uploaded to the URLs in the manifests.
- App update check returning a meaningful result.

Validate local and remote release URLs:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -ValidateRemoteReleaseUrls
```

After pushing to `main` and enabling GitHub Pages, require remote URLs:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -ValidateRemoteReleaseUrls -RequireRemoteReleaseUrls
```

## Provider Gate

Validate local Ollama and any cloud providers with keys in the environment:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -LiveProviderValidation
```

When cloud provider keys are available, require at least one cloud provider:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -LiveProviderValidation -RequireCloudProviderKeys
```

## Release Decision

Do not call the build a signed public beta until:

- Clean-machine install passes.
- Windows signing validation passes.
- macOS signing and notarization pass.
- Signed update validation passes.
- The first installed-user trial is recorded.
