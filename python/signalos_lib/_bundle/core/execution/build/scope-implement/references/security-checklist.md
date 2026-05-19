<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized: VS Code SecretStorage references replaced with OS-keychain-via-Tauri equivalents; webview section retargeted to Tauri webview specifics. -->

# Security Checklist

## Input Validation
- [ ] All user input validated at system boundary
- [ ] File paths resolved and checked against workspace root
- [ ] Reject paths containing `..`
- [ ] Type guards for unknown data (`JSON.parse` results, IPC payloads)
- [ ] String length limits enforced

## Injection Prevention
- [ ] No `child_process.exec()` / `subprocess.run("...", shell=True)` with interpolation
- [ ] Shell arguments passed as array, not string
- [ ] No `eval()` or `new Function()`
- [ ] Template literals don't include unsanitized user input

## Secrets
- [ ] No hardcoded credentials
- [ ] Tokens in OS keychain via Tauri keychain IPC (not `localStorage`, not app-state signals)
- [ ] Secrets masked in log output and audit trail
- [ ] `.env` files in `.gitignore`
- [ ] Sidecar subprocess receives only the env vars it needs (no parent-process leakage)

## Webviews (Tauri)
- [ ] `app.security.csp` set to a strict policy (`default-src 'self'`, no `'unsafe-inline'`, no `'unsafe-eval'`)
- [ ] `tauri.conf.json` allowlist restricted to commands actually used
- [ ] `withGlobalTauri` set only when JSX needs the dialog/shell globals
- [ ] No `innerHTML` with unsanitized content (Preact JSX is safe by default; legacy `app-v2.js` paths must validate)
- [ ] IPC commands validate caller-provided paths against the active workspace

## IDs & Randomness
- [ ] `crypto.randomUUID()` for identifiers (JS)
- [ ] `secrets.token_urlsafe()` for tokens (Python sidecar)
- [ ] Never `Math.random()` for anything security-related

## File Operations
- [ ] ENOENT / FileNotFoundError handled gracefully
- [ ] Atomic writes (temp file → rename)
- [ ] File size checked before reading
- [ ] Permissions checked before writing (avoid silent overwrites of user-owned files)
