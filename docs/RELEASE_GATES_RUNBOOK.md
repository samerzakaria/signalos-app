# Release Gates Runbook — what I (Claude) cannot do and you must

This file lists the three release gates that the v3.2.3-internal.2 push **cannot** finish without credentials, hardware, or accounts that live with you, plus the exact commands to run when you do them.

Last updated: 2026-05-15 — after the Wave 1–5 merge to `main` (PR #1).

---

## Two ship paths

| Path | When | Doc |
|---|---|---|
| **Internal testing** (attested by name, not signed) | Shipping to known testers now, certs not yet acquired | [INTERNAL_TESTING_BUILD.md](INTERNAL_TESTING_BUILD.md) |
| **Signed public release** (Authenticode + notarized + minisign-stamped) | Public download / auto-updater required | this file (R1–R3 below) |

The current v3.2.3-internal.2 ship plan is the **internal testing** path. R1–R3 below remain blocked on your credentials. Run `scripts/build-internal.ps1` (or `.sh`) to produce the internal artifacts.

The implementation gates (G0–G4) are signed in `.signalos/AUDIT_TRAIL.jsonl`. The remaining gates are the operational/external ones: code signing, notarization, updater signatures, clean-machine validation.

---

## Gate R1 — Fill minisign signatures in `distribution/update-manifest/*.json`

**Why I can't do this:** the minisign private key for `signalos-app` is held by you (and possibly your CI secrets store). Releasing without filling these makes the Tauri updater refuse to install the build, which is by design.

**What's currently empty:**

```bash
$ grep -c '"signature": ""' distribution/update-manifest/*.json
distribution/update-manifest/beta.json:4
distribution/update-manifest/latest.json:4
```

4 empty signatures in each file (one per platform: darwin-aarch64, darwin-x86_64, windows-x86_64, linux-x86_64).

**Procedure:**

1. Build and bundle the installers locally or via CI:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass `
     -File scripts/verify-release.ps1 -BuildInstaller
   ```

   This produces (for Windows):
   ```
   src-tauri/target/release/bundle/nsis/Foundry_3.2.3-internal.2_x64-setup.exe
   src-tauri/target/release/bundle/msi/Foundry_3.2.3-internal.2_x64_en-US.msi
   ```

2. For each installer, run `minisign -S` with the project private key:

   ```bash
   minisign -S -s "$TAURI_PRIVATE_KEY_PATH" \
     -m src-tauri/target/release/bundle/nsis/Foundry_3.2.3-internal.2_x64-setup.exe
   ```

   The signature is written to `<file>.sig` and the base64-encoded payload is what goes into the manifest.

3. Update both `beta.json` and `latest.json` so each platform stanza has:

   ```json
   "windows-x86_64": {
     "url": "https://github.com/samerzakaria/signalos-app/releases/download/v3.2.3-internal.2/Foundry_3.2.3-internal.2_x64-setup.exe",
     "signature": "<base64-minisign-signature-here>"
   }
   ```

4. Verify locally:

   ```powershell
   powershell -File scripts/verify-release.ps1 -ValidateRemoteReleaseUrls
   ```

5. After commit + push, the Rust `check_for_updates` IPC will stop reporting `signatures_missing: true` (currently it falls back to "no update available" with that flag set; see `src-tauri/src/ipc.rs::check_for_updates`).

**Acceptance:** all 8 lines no longer match `"signature": ""`.

---

## Gate R2 — macOS notarization

**Why I can't do this:** requires a macOS machine with `xcrun notarytool`, an Apple Developer ID Application certificate in the system keychain, and your Apple ID + App-Specific Password (or a notary API key).

**Procedure (on a Mac):**

1. Build + bundle the macOS installer:

   ```bash
   cargo tauri build --target aarch64-apple-darwin
   # and / or
   cargo tauri build --target x86_64-apple-darwin
   ```

2. Sign the `.app` bundle with your Developer ID certificate (Tauri's bundler does this automatically if the cert is in your keychain and `tauri.conf.json` has the right team identifier). To verify:

   ```bash
   codesign -dv --verbose=4 \
     src-tauri/target/release/bundle/macos/SignalOS.app
   ```

3. Zip the `.app` for upload (notarytool only accepts archives):

   ```bash
   ditto -c -k --keepParent \
     src-tauri/target/release/bundle/macos/SignalOS.app \
     /tmp/SignalOS.zip
   ```

4. Submit for notarization:

   ```bash
   xcrun notarytool submit /tmp/SignalOS.zip \
     --apple-id "$APPLE_ID" \
     --team-id "$APPLE_TEAM_ID" \
     --password "$APPLE_APP_SPECIFIC_PASSWORD" \
     --wait
   ```

   `--wait` blocks until Apple's verdict (typically 1–10 minutes). On success it prints `status: Accepted`.

5. Staple the notarization ticket so the app passes Gatekeeper offline:

   ```bash
   xcrun stapler staple src-tauri/target/release/bundle/macos/SignalOS.app
   ```

6. Re-zip the stapled bundle, upload to the GitHub Release.

7. Verify on a clean Mac:

   ```bash
   spctl -a -vvv -t install SignalOS.app
   # Should report: source=Notarized Developer ID
   ```

**Acceptance:** `spctl` reports `source=Notarized Developer ID` and Gatekeeper allows the app without a right-click bypass on a clean machine.

---

## Gate R3 — Clean-machine VM validation

**Why I can't do this:** I have no hypervisor access. This needs a fresh Windows 11 VM (or two — one Win11, one macOS) with no SignalOS dev environment installed.

**Procedure (per [docs/CLEAN_MACHINE_VALIDATION.md](CLEAN_MACHINE_VALIDATION.md)):**

1. Spin up a fresh Windows 11 VM. Do **not** install Node, Rust, Python, Cargo, Tauri, or any IDE extension related to SignalOS.

2. Copy the signed installer onto the VM:

   ```
   Foundry_3.2.3-internal.2_x64-setup.exe   (or the .msi)
   ```

3. Run the installer. Confirm:
   - No SmartScreen warning (or only the "Unknown publisher" prompt if Authenticode reputation is still building — that's expected for early signed builds)
   - The app appears in Start Menu and Add/Remove Programs
   - Launching from Start Menu opens the SignalOS window
   - First-run wizard appears

4. Walk the wizard:
   - Step 1 (Welcome) — Continue
   - Step 2 (Folder) — pick a fresh empty folder; verify the folder-check renders ✓ exists / ✓ writable / ✓ empty
   - Step 3 (Init) — pick "Keep my files" — verify Continue enables
   - Step 4 (Identity) — type a name, pick a role
   - Step 5 (AI) — paste a real API key, pick a model, click `Test connection` — verify the green check with a real chat response
   - Step 6 (Budget) — set $10, leave privacy defaults
   - Step 7 (Done) — click Start building

5. After wizard closes:
   - Confirm `.signalos/` folder appears in the chosen workspace
   - Confirm `core/`, `integrations/`, `README.md` etc. are written (Keep mode preserves user files)
   - Confirm `.signalos/identity.json` exists with name + role
   - Confirm `.signalos/AUDIT_TRAIL.jsonl` exists with a `workspace:set` entry

6. Pick the **Build** view, type "build a todo app with priorities", pick `react-vite`, click **Build app**:
   - Watch the 4-phase progress strip animate substeps
   - Watch AI tokens stream into the phase message
   - When the diff modal appears, click Apply
   - Watch files write
   - Watch the right-pane preview iframe load `localhost:5173`
   - Click the running app — it should respond

7. Sign the gates:
   - Open the Project view → Steps
   - Sign G0 — should succeed (PO role default)
   - Sign G1 — should refuse without a test ref; add a test path and retry
   - Sign G2 — should succeed

8. Test cost meter, secrets pane (add + reveal + delete), enforcement override, freeze wave, audit trail JSONL entries.

9. Close the app. Re-open. Confirm:
   - Wizard does NOT re-appear (state persisted)
   - Project re-loads at the right folder
   - Previous build files still there

10. Run `scripts/test-installer.ps1` from `signalos-app/` on the **dev** machine (NOT the VM) to log the manual checklist results.

**Acceptance:** wizard completes, build runs, gates sign with role enforcement, secrets manager works, audit trail shows the actions, app survives a restart. No console errors visible in `Win+R → eventvwr.msc → Application` related to SignalOS.

---

## Why these are off the implementation gates

The deep review (`docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md`) classified these as **release gates**, not implementation gates:

> *"The signing work is intentionally not done at this stage. Everything below treats signing, notarization, release signatures, and platform reputation as release gates outside the app implementation pass."*

The reason: signing and notarization require credentials and machines that are not part of the source repository, and clean-machine validation requires a fresh OS install which is meaningfully different from a developer machine.

After all three gates pass, the product moves from `signed-ready installer candidate` (current state) to `signed public beta`.

---

## What's left for me (Claude) after this branch merges

1. Watch the CI run on PR #1 and fix any platform-specific surprises (most likely: Ubuntu's Tauri build needs `libwebkit2gtk-4.1-dev` and `librsvg2-dev`).
2. Once green, merge to `main`.
3. Nothing further on the release-gate track until you complete R1–R3 above.

---

*Last reviewed: Wave 5 closeout, 2026-05-15.*
