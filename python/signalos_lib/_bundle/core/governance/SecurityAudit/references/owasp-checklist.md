<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized: SecretStorage reference replaced with OS keychain via Tauri; webview lines kept (apply to us too). -->

# OWASP Top 10 Audit Checklist

## A01: Broken Access Control
- [ ] File access restricted to workspace root
- [ ] Path traversal (`..`) rejected in all file operations
- [ ] Symlinks resolved and re-validated
- [ ] No unauthorized file write outside workspace
- [ ] Workspace-trust state respected for dangerous operations (e.g. running scripts)

## A02: Cryptographic Failures
- [ ] No secrets in source code or committed files
- [ ] Tokens stored in the OS keychain via Tauri keychain IPC (NOT `localStorage`, NOT signals persisted to disk)
- [ ] `crypto.randomUUID()` for identifiers (not `Math.random`)
- [ ] Sensitive data not logged to the audit trail in plaintext
- [ ] No weak hashing (MD5, SHA1 for security purposes)

## A03: Injection
- [ ] No `child_process.exec()` / `subprocess.run(..., shell=True)` with string interpolation
- [ ] Shell arguments as arrays via `execFile` / `Command::new()`
- [ ] No `eval()` or `new Function()` with user input
- [ ] Template literals don't include unsanitized input
- [ ] Regex input escaped or validated (no ReDoS)

## A04: Insecure Design
- [ ] Input validated at every trust boundary (chat composer, IPC, sidecar stdin)
- [ ] Fail-secure defaults (deny by default)
- [ ] Rate limiting for expensive operations (e.g. orchestrator dispatch)
- [ ] Defense in depth (multiple validation layers)
- [ ] Principle of least privilege in file access

## A05: Security Misconfiguration
- [ ] Webview CSP is restrictive (`default-src 'self'`)
- [ ] No debug code in production
- [ ] Tauri allowlist scoped to commands actually used
- [ ] File watchers use specific globs (not `**/*`)
- [ ] Error messages don't reveal system information

## A06: Vulnerable Components
- [ ] All dependencies up to date (`npm audit`, `cargo audit`, `pip-audit`)
- [ ] No high/critical vulnerabilities in transitive deps
- [ ] Minimal dependency tree (prefer stdlib)
- [ ] Lock file committed and verified
- [ ] No dependencies with known supply chain attacks

## A07: Identification & Authentication Failures
- [ ] API tokens have expiration where the vendor supports it
- [ ] Tokens refreshed securely
- [ ] Failed auth attempts limited (slow down + log, don't crash)
- [ ] Session state cleaned on app exit
- [ ] No token reuse across different services

## A08: Software & Data Integrity Failures
- [ ] Configuration files validated against schema (e.g. PLAN.tasks.yaml)
- [ ] `JSON.parse` results type-checked before use
- [ ] No deserialization of untrusted complex objects
- [ ] Package integrity verified (lock file)
- [ ] Auto-updater signatures verified (minisign / Apple notarization)

## A09: Security Logging & Monitoring Failures
- [ ] Security-relevant events logged (access denied, validation failure, gate signs)
- [ ] Sensitive data not included in logs (redaction layer in harness)
- [ ] Log injection prevented (newlines sanitized)
- [ ] Destructive operations traceable to a signed gate
- [ ] Anomalous patterns detectable

## A10: Server-Side Request Forgery (SSRF)
- [ ] URLs from user input validated against allowlist
- [ ] No internal network access from user-supplied URLs
- [ ] Redirect chains limited/blocked
- [ ] DNS resolution validated
- [ ] Protocol restricted (https only for external)
