<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. Replaced editor-extension rows with Tauri IPC / webview test rows. -->

# Test Type Decision Matrix

## Which test type do I need?

| Scenario | Test Type | Template |
|----------|-----------|----------|
| Pure function, no deps | Unit | `unit-test.ts` |
| Multiple components interacting | Integration | `integration-test.ts` |
| File system operations | Integration | `integration-test.ts` |
| User input handling | Security | `security-test.ts` |
| Path/command injection | Security | `security-test.ts` |
| Tauri IPC command handler | Integration | `integration-test.ts` |
| Webview ↔ Rust IPC payload validation | Security | `security-test.ts` |
| State machine transitions | Unit | `unit-test.ts` |
| Cache behavior | Integration | `integration-test.ts` |
| Error recovery | Unit + Integration | Both |

## Test Pyramid

```
        /\          E2E (few, slow, fragile)
       /  \         
      /    \        Integration (moderate)
     /      \       
    /        \      
   /          \     Unit (many, fast, stable)
  /____________\    
```

## Priority Order

1. **Security tests first** — any input from outside the system
2. **Unit tests for business logic** — core algorithms and rules
3. **Integration tests for boundaries** — file I/O, API calls, Tauri IPC, sidecar stdin/stdout
4. **UX tests last** — command behavior, user feedback, view rendering

## When NOT to Test

- Simple getters/setters with no logic
- Framework-generated code (unless customized)
- Private implementation details
- Purely declarative configuration
