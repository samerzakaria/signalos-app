# SignalOS Desktop — Signing Checklist

The app is **signing-ready**. All configuration placeholders are in place.
When you have the required accounts and certificates, follow this checklist in order.

---

## 1. macOS (Apple notarization)

**What you need:**
- Apple Developer account ($99/yr) — https://developer.apple.com
- Developer ID Application certificate (issued from Apple)
- App-specific password for notarytool — https://appleid.apple.com → Security → App-specific passwords

**CI secrets to set (GitHub Actions → Repo Settings → Secrets):**

| Secret name                    | Value |
|-------------------------------|-------|
| `APPLE_CERTIFICATE`           | Base64-encoded .p12 certificate: `base64 -i cert.p12 | pbcopy` |
| `APPLE_CERTIFICATE_PASSWORD`  | Password used when exporting the .p12 |
| `APPLE_SIGNING_IDENTITY`      | e.g. `Developer ID Application: Samer Zakaria (TEAMID)` |
| `APPLE_ID`                    | Your Apple ID email |
| `APPLE_PASSWORD`              | App-specific password (not your Apple ID password) |
| `APPLE_TEAM_ID`               | 10-char team ID from developer.apple.com |
| `APPLE_PROVIDER_SHORT_NAME`   | Provider short name (same as TEAM_ID for individuals) |

**What happens when secrets are set:**
The release workflow's `if: env.APPLE_CERTIFICATE != ''` guard becomes true and the signing + notarization steps activate automatically. No code changes needed.

---

## 2. Windows (SmartScreen removal)

**What you need:**
- EV Code Signing Certificate — from DigiCert, Sectigo, or SSL.com (~$300–500/yr)
- Hardware token (most EV certs require a physical USB token for the private key)
- Some providers (SSL.com) support cloud-based signing which works in CI without a token

**Recommended:** SSL.com eSigner (cloud, works in GitHub Actions without physical token)

**CI secrets to set:**

| Secret name                       | Value |
|----------------------------------|-------|
| `WINDOWS_CERTIFICATE`            | Base64-encoded .pfx: `base64 -w 0 cert.pfx` |
| `WINDOWS_CERTIFICATE_PASSWORD`   | .pfx password |
| `WINDOWS_CERTIFICATE_THUMBPRINT` | SHA-1 thumbprint of the certificate |

**What happens when secrets are set:**
The `certificateThumbprint` in `tauri.conf.json` is populated and Windows signs the .exe during packaging.

---

## 3. Tauri updater signing

**Required for auto-updates to verify integrity.**

```bash
# Generate the signing keypair (run once, store securely)
npx tauri signer generate -w ~/.tauri/signalos-updater.key

# This outputs a public key — paste it into tauri.conf.json → bundle.updater.pubkey
# Store the private key as a CI secret:
```

| Secret name           | Value |
|----------------------|-------|
| `TAURI_PRIVATE_KEY`  | Contents of `~/.tauri/signalos-updater.key` |
| `TAURI_KEY_PASSWORD` | Password set during keygen |

---

## 4. Release workflow activation

The file `.github/workflows/release.yml` is already written with all signing steps gated behind secret-presence checks:

```yaml
if: env.APPLE_CERTIFICATE != ''   # macOS signing block
if: env.WINDOWS_CERTIFICATE != '' # Windows signing block
```

**Without secrets:** builds unsigned binaries, uploads to GitHub Releases.
**With secrets:** builds signed + notarized binaries, uploads to GitHub Releases.

Zero code changes required. Just add the secrets.

---

## 5. Priority order

If you need to pick one first: **macOS signing** has the worse user-facing failure (complete block with "App is damaged" and no bypass path). Windows is a softer block (one extra click). Do macOS first.

Estimated setup time once you have the accounts: **2–3 hours** per platform.
