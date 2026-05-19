<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized: VS Code Extension Context column replaced with Desktop App / Webview Context throughout. -->

# STRIDE Threat Model

## Categories

### S — Spoofing Identity
**Question**: Can an attacker pretend to be someone/something else?

| Check | Desktop App / Webview Context |
|-------|--------------------------|
| Token validation | Are API tokens validated before use by the provider client? |
| Origin verification | Are webview-to-Rust IPC messages verified by command name + arg shape? |
| Cross-process impersonation | Can another local process invoke our Tauri IPC handlers (only same-origin can)? |
| Sidecar spoofing | Is the bundled sidecar binary verified before spawn? |

### T — Tampering with Data
**Question**: Can an attacker modify data they shouldn't?

| Check | Desktop App / Webview Context |
|-------|--------------------------|
| File integrity | Are config files (PLAN.tasks.yaml, SOUL-DOCUMENT.md) validated after read? |
| Audit-trail integrity | Are AUDIT_TRAIL.jsonl entries append-only and signed? |
| Cache poisoning | Can stale or maliciously crafted IPC responses be replayed? |
| Sidecar payload tampering | Is the sidecar stdin/stdout protocol bounded (size + shape) per line? |

### R — Repudiation
**Question**: Can an attacker deny performing an action?

| Check | Desktop App / Webview Context |
|-------|--------------------------|
| Audit logging | Are destructive operations (file writes, gate signs) logged with timestamps + actor? |
| Attribution | Is the signer name + role recorded for every gate signature? |
| Timestamp integrity | Are timestamps generated server-side (sidecar) and not client-controlled? |

### I — Information Disclosure
**Question**: Can an attacker access information they shouldn't?

| Check | Desktop App / Webview Context |
|-------|--------------------------|
| Error messages | Do errors leak file paths, API keys, or stack traces to the user-facing chat? |
| Log output | Are secrets printed to the audit trail or sidecar log? |
| Webview exposure | Does HTML source / dev-tools reveal sensitive data? |
| Provider responses | Do redaction layers strip env-var values and keys from harness output? |

### D — Denial of Service
**Question**: Can an attacker make the system unavailable?

| Check | Desktop App / Webview Context |
|-------|--------------------------|
| Resource exhaustion | Can a crafted PLAN.tasks.yaml cause unbounded memory use in the orchestrator? |
| CPU starvation | Can regex (e.g. in `_extract_files_from_response`) cause catastrophic backtracking? |
| File-watcher floods | Can bulk file changes trigger infinite refresh loops? |
| Event loop blocking | Can a sync IPC call freeze the UI thread? |
| Sidecar overload | Can rapid `/signal-*` invocations queue without backpressure? |

### E — Elevation of Privilege
**Question**: Can an attacker gain unauthorized capabilities?

| Check | Desktop App / Webview Context |
|-------|--------------------------|
| Path traversal | Can a malformed task `files: [...]` write outside the workspace? |
| Command injection | Can a slash command argument execute arbitrary shell? |
| Trust boundary | Does untrusted workspace content gain Tauri IPC access? |
| Tauri allowlist | Are commands scoped to the minimum surface needed? |
| Gate bypass | Can the orchestrator dispatch without a signed plan (G2)? |

## STRIDE per Interaction

For each data flow in the system:
1. Identify source, destination, data, and channel
2. Apply all 6 STRIDE categories
3. Rate likelihood (1-5) × impact (1-5) = risk score
4. Prioritize by risk score
