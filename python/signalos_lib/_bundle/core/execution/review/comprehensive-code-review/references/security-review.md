<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized: replaced VS Code-specific "Common Vulnerabilities" section with Tauri/webview equivalents. -->

# Security Review Checklist

## Injection Vectors
| Vector | Check |
|--------|-------|
| Command injection | No `exec()` with string interpolation; use `execFile()` / Tauri `Command::new()` with explicit arg arrays |
| Path traversal | All paths resolved + checked against workspace root |
| Template injection | No user input in code-generation templates |
| Regex DoS | No unbounded quantifiers on user input |
| Prototype pollution | No `Object.assign` from untrusted JSON |

## Trust Boundaries
- Chat composer input → validate before sending to provider
- File system reads → check existence + size before parsing
- `JSON.parse` → type-guard the result
- Webview ↔ Rust IPC → validate command name + arg shapes on the Rust side
- Sidecar stdin/stdout → parse known event kinds only; ignore unknown
- Environment variables → validate format before use

## Common Vulnerabilities in Tauri Desktop Apps
1. Writing user input directly to files without sanitization (file tree, secret values)
2. Using workspace path in shell commands without escaping (orchestrator subprocess calls)
3. Webview loading external resources without CSP — Tauri's `app.security.csp` must be locked down
4. Storing tokens in `localStorage` / app state instead of the OS keychain (`tauri-plugin-keyring` / our `keychain.rs`)
5. Filesystem watchers on overly broad globs causing DoS or surprise reads
6. IPC commands accepting arbitrary file paths without validating they're inside the active workspace
7. `__TAURI__` global exposed to webviews that load untrusted content
8. Sidecar subprocess inheriting parent env variables (leaks API keys to unrelated tools)
