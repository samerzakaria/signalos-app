# Internal Testing Build — attested by name, not signed

This file documents how to produce a v1.0 build for **internal testing**
without code-signing certificates, Apple Developer ID, or minisign release
keys. The artifact is **attested by your name** (via git config + a
machine-readable JSON), not signed by a CA. Distribute only to named
internal testers.

When you're ready to ship to the public, follow [RELEASE_GATES_RUNBOOK.md](RELEASE_GATES_RUNBOOK.md)
instead — that path produces signed + notarized + minisign-stamped artifacts.

---

## When to use this path

| Situation | Path |
|---|---|
| Hand it to your team / known beta testers / yourself on another machine | **This file** |
| Publish to GitHub Releases for the public | `RELEASE_GATES_RUNBOOK.md` |
| Need the auto-updater to install across versions | `RELEASE_GATES_RUNBOOK.md` (needs minisign) |
| Cannot get certs / notarization yet but need to ship anyway | **This file** |

The internal path produces a **working installer that triggers OS warnings
about unknown publishers**. That's expected. Internal testers know to click
through. Public users would (rightly) be alarmed.

---

## What "attested by name" means

When you run `scripts/build-internal.ps1`, the script:

1. Reads your name from `git config user.name`. **Email is set to a
   project-level noreply address** (`noreply@signalos.app`) rather
   than the maintainer's personal Gmail — testers report bugs via the
   distribution-channel link in `distribution_notes`, not by emailing
   the builder. Set `SIGNALOS_BUILDER_EMAIL` if you want a different
   address in the attestation.
2. Captures the git commit SHA, branch, clean/dirty state, and timestamp.
3. Builds the unsigned installer via `cargo tauri build --bundles nsis,msi`.
4. Computes SHA-256 of every installer file produced.
5. Writes `distribution/internal/attestation-<short-commit>.json` —
   the **attestation file**. Schema:

   ```json
   {
     "schema": "signalos.attestation.v1",
     "release_type": "internal-testing-unsigned",
     "product": "SignalOS",
     "version": "0.0.9",
     "builder": {
       "name": "Samer Zakaria",
       "email": "noreply@signalos.app"
     },
     "built_at": "2026-05-15T18:30:00Z",
     "git": {
       "commit": "5284391...",
       "branch": "main",
       "clean": true
     },
     "artifacts": [
       {
         "path": "src-tauri/target/release/bundle/nsis/SignalOS_0.0.9_x64-setup.exe",
         "size": 22118400,
         "sha256": "abc123..."
       }
     ],
     "distribution_notes": [
       "Unsigned installer. SmartScreen will warn 'Unknown publisher'…",
       "DO NOT publish to the public landing page…"
     ]
   }
   ```

6. Appends an audit entry to `.signalos/AUDIT_TRAIL.jsonl` recording the
   build (`action: "build:internal-attest"`).

The attestation file is what tells internal testers:
- **Who built it** (so they can ask you questions / report bugs)
- **From what source state** (commit + clean flag → reproducible if they want)
- **Exactly which bits** (SHA-256 → tampering is detectable)
- **What it isn't** (`release_type: internal-testing-unsigned` is unambiguous)

It is **not** a cryptographic signature. A tester who verifies the SHA-256
in the attestation matches the SHA-256 of the file they downloaded knows
the file matches what you built, but cannot prove the attestation itself
wasn't fabricated. For an internal pool of people who already trust you,
that's sufficient. For the public, it isn't — use the runbook instead.

---

## Run it (Windows)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/build-internal.ps1
```

Optional flags:
- `-SkipBuild` — re-attest existing artifacts (no rebuild)
- `-Strict` — fail if the working tree is dirty or HEAD is behind origin/main

Expected outputs:
```
src-tauri/target/release/bundle/nsis/SignalOS_0.0.9_x64-setup.exe
src-tauri/target/release/bundle/msi/SignalOS_0.0.9_x64_en-US.msi
distribution/internal/attestation-<short-commit>.json
```

## Run it (macOS / Linux)

```bash
bash scripts/build-internal.sh
```

Optional flags: `--skip-build`, `--strict`.

Outputs by platform:
- macOS: `src-tauri/target/release/bundle/dmg/SignalOS_0.0.9_*.dmg`
- Linux: `src-tauri/target/release/bundle/{deb,appimage}/SignalOS_0.0.9_*.{deb,AppImage}`

---

## Distribute to internal testers

1. Copy the installer to wherever your testers can grab it (Slack DM,
   private GitHub Release marked "internal", a shared drive, scp).
2. Copy the matching `distribution/internal/attestation-<short-commit>.json`
   alongside it so testers can verify SHA-256.
3. Send testers this checklist (template at
   [distribution/internal/TESTER_README.md](../distribution/internal/TESTER_README.md)
   — generated on first run if missing).

### What to tell testers

> Hi — this is an **internal testing build** of SignalOS v0.0.9.
> It is **not code-signed**, so the OS will warn you that the publisher
> is unknown. That's expected. The build was attested by **Samer Zakaria
> <samer.zakaria@gmail.com>** on **2026-05-15** from commit `5284391...`.
>
> **On Windows:** double-click `SignalOS_0.0.9_x64-setup.exe`. SmartScreen
> will say "Windows protected your PC — Unknown publisher". Click **More info**,
> then **Run anyway**.
>
> **On macOS:** open the .dmg, drag SignalOS.app to Applications. On first
> launch Gatekeeper will say "cannot be opened because the developer cannot
> be verified". **Right-click → Open → Open**.
>
> **On Linux:** `chmod +x SignalOS_0.0.9_amd64.AppImage && ./SignalOS_…AppImage`,
> or install the .deb with `sudo dpkg -i …`.
>
> If you want to verify the build wasn't tampered with in transit:
>
> ```bash
> sha256sum SignalOS_0.0.9_x64-setup.exe
> # compare against the "sha256" field in attestation-<short-commit>.json
> ```
>
> **Report bugs to:** samer.zakaria@gmail.com
> **What works:** the first-run wizard, AI provider connect (you need your
> own key), Builder, file diff preview, run-and-preview pane, secrets
> manager, gate signing, audit trail.
> **Known gaps:** see `docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md` §12.8.

---

## What this path explicitly does NOT do

- ❌ No Authenticode signature → Windows SmartScreen warning will appear.
- ❌ No Apple Developer ID + notarization → macOS Gatekeeper will block first launch.
- ❌ No minisign signatures filled in `distribution/update-manifest/*.json` →
  the auto-updater will report "no update available" with `signatures_missing: true`.
- ❌ Not for public download landing → do not link from `distribution/landing/index.html`.

When any of these change (you get the certs / Apple ID / minisign key),
swap to `scripts/build-signed.ps1` (does not yet exist — produce it
alongside the signing step when you have the materials) and follow
[RELEASE_GATES_RUNBOOK.md](RELEASE_GATES_RUNBOOK.md).

---

## Re-verifying an old internal build

Each attestation file is self-contained. To verify a build a tester received:

```bash
# 1. Tester downloads SignalOS_0.0.9_x64-setup.exe + attestation-5284391.json
# 2. They compute the local hash
sha256sum SignalOS_0.0.9_x64-setup.exe
# 3. They compare against the attestation:
jq -r '.artifacts[] | select(.path | endswith("setup.exe")) | .sha256' attestation-5284391.json
# 4. If equal → the file matches what the builder produced. Identity comes
#    from out-of-band trust (the builder told them about this build).
```

---

## Why this satisfies "signing-ready, not signed"

- The **build infrastructure** (cargo tauri build, bundle config, externalBin
  resolution, capability allow-spawn) is the same path a signed release will
  use. Adding a signing step later means injecting `signtool sign` / `xcrun
  notarytool submit` / `minisign -S` calls into a `scripts/build-signed.ps1`
  derived from this script. The CI workflow's L1/L2 jobs already validate
  the unsigned-build path on every PR.
- The **attestation schema** (`signalos.attestation.v1`) is forward-compatible:
  the same JSON gets an extra `signatures` block when signing lands.
- The **audit trail** is the same regardless: `build:internal-attest` for
  this path vs `build:signed-release` for the cert path. Both land in
  `.signalos/AUDIT_TRAIL.jsonl`.

You're not building a separate product. You're shipping the same product
without the signing layer, marking it clearly, and gating it to people
who know to expect that.
