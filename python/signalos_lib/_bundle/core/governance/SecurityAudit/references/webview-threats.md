<!-- Adapted from SignalGuard's vscode-threats.md (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized: retargeted to Tauri webview / desktop-app attack surface. -->

# Tauri Desktop / Webview Threat Vectors

## Attack Surface Map

```
┌────────────────────────────────────────────────────────┐
│                Tauri Application                         │
│                                                          │
│  ┌─────────────┐    ┌──────────────┐   ┌───────────┐   │
│  │ Webview UI  │◄──►│  Rust core   │◄─►│  FS       │   │
│  │ (Preact JS) │    │  (commands)  │   │           │   │
│  └─────────────┘    └──────┬───────┘   └───────────┘   │
│        ▲                   │                            │
│        │                   │                            │
│        │            ┌──────┴───────┐   ┌───────────┐   │
│        └────────────┤  Sidecar     │   │ Subprocess│   │
│                     │  (Python)    │◄─►│ (bash/git)│   │
│                     └──────────────┘   └───────────┘   │
│                                                          │
└────────────────────────────────────────────────────────┘
```

## Threat Categories

### 1. Malicious Workspace Content
**Vector**: User opens an untrusted workspace with crafted `.signalos/` files, PLAN.tasks.yaml, or governance docs.
**Impact**: Code execution via orchestrator, data exfiltration, audit-trail tampering.
**Mitigations**:
- Validate all parsed file content (YAML / Markdown / JSONL)
- Don't auto-execute based on file content alone — require gate signing
- Limit file size before parsing (50 KB on PLAN; 5 MB on bundle reads)
- Reject workspace paths that are symlinks pointing outside the chosen root

### 2. Webview Escape
**Vector**: Crafted content breaks the webview sandbox (CSP bypass, XSS).
**Impact**: Full Tauri IPC access from injected script — keychain reads, file writes, sidecar dispatch.
**Mitigations**:
- Strict CSP in `tauri.conf.json` (`default-src 'self'`, no `'unsafe-inline'`)
- Nonce or hash all inline scripts/styles
- Preact JSX is XSS-safe by default — DO NOT introduce `dangerouslySetInnerHTML`
- Validate all messages crossing the IPC boundary
- `withGlobalTauri` should only be `true` if no untrusted content ever loads

### 3. Command Injection via Slash Commands
**Vector**: Chat input "/signal-X $(rm -rf /)" reaches the sidecar shell.
**Impact**: Arbitrary command execution on the user's machine.
**Mitigations**:
- Slash routing parses tokens with `.split(/\s+/)` and dispatches via `run_signal_command` IPC (not via shell)
- Sidecar's `dispatch_cli` calls Python functions, not `os.system`
- When the sidecar shells out (e.g. worktree-manager.sh), use `subprocess.run([list, ...])` not `shell=True`

### 4. Path Traversal via Plan / Task Files
**Vector**: AI-generated PLAN.tasks.yaml lists `files: ["../../etc/passwd"]`.
**Impact**: Read/write arbitrary files on disk.
**Mitigations**:
- Plan schema validator rejects file paths with `..`, leading `/`, or Windows drive letters (`extractPlanWithErrors` in signalosPrompt.ts)
- Orchestrator's `_write_extracted_files` re-checks `target.resolve().relative_to(root)` before writing
- Defense in depth: both layers reject paths that escape the workspace

### 5. Cross-Process Attack via Sidecar
**Vector**: Malicious local process targets the Python sidecar's stdin or audit trail.
**Impact**: Bypass validation, inject false audit entries.
**Mitigations**:
- Sidecar reads from a pipe held by the Tauri app, not from a network socket
- Audit-trail file mode 0600 (owner read/write only)
- Process the user the Tauri app runs as has no special privileges

### 6. Supply Chain via Dependencies
**Vector**: Compromised npm / pip / cargo package.
**Impact**: Full system compromise.
**Mitigations**:
- Minimize dependencies (prefer stdlib + Tauri primitives)
- Pin exact versions in lock files (package-lock.json, Cargo.lock, requirements.txt)
- Regular `npm audit`, `cargo audit`, `pip-audit`
- Review dependency code for critical packages (esp. anything touching crypto or fs)

### 7. Denial of Service
**Vector**: Large/malformed input causes freeze.
**Impact**: App becomes unresponsive.
**Mitigations**:
- File-size limits before reading (in `_load_skill`, `read_workspace_file`)
- Timeout on harness LLM calls (`run_step` has internal timeout)
- Async processing for large datasets (file tree paginated by directory)
- Limit recursion depth (e.g. workspace file tree only loads top level on initial render)
- Sidecar IPC has bounded queue; backpressure surfaces as "Sidecar command queue full"

## Quick Reference: Safe Patterns

```typescript
// SAFE: Path validation in plan validator
function isSafePath(rel: string): boolean {
  if (rel.includes('..')) return false;
  if (rel.startsWith('/')) return false;
  if (rel.length > 2 && rel[1] === ':') return false; // drive letter
  return true;
}

// SAFE: IPC arg validation
async function ipcCall<T>(cmd: string, args: Record<string, unknown>): Promise<T> {
  // Reject objects with prototype-pollution-y keys before passing to Tauri
  for (const k of Object.keys(args)) {
    if (k === '__proto__' || k === 'constructor' || k === 'prototype') {
      throw new Error('refused suspicious arg key');
    }
  }
  return invoke(cmd, args);
}

// SAFE: JSON parsing with type guard
function safeJsonParse<T>(raw: string, guard: (v: unknown) => v is T): T | undefined {
  try {
    const parsed = JSON.parse(raw);
    return guard(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}
```

```python
# SAFE: workspace-rooted path resolution
def safe_resolve(root: Path, user_input: str) -> Path | None:
    if ".." in user_input.split("/"):
        return None
    candidate = (root / user_input).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate

# SAFE: subprocess invocation
import subprocess
subprocess.run(["git", "status", "--short"], cwd=root, check=False)
# DANGEROUS: subprocess.run(f"git {user_input}", shell=True, ...)
```
