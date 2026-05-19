<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. Editor-agnostic. -->

---
name: test-generation
description: "Generate well-structured tests with vitest + factory data + AAA layout + table-driven cases. USE FOR: writing new tests for services, IPC commands, validators, security boundaries; choosing the right test type (unit/integration/security); applying mock-at-boundaries patterns. DO NOT USE FOR: writing the implementation (use scope-implement), reviewing existing tests (use comprehensive-code-review), running test suites (use the project's test runner)."
license: MIT
---
# Test Generation Skill

## When to Use
- Adding tests for newly implemented services or IPC handlers
- Hardening input boundaries with security tests
- Capturing regression scenarios after a bug fix
- Building up coverage for state-machine or parser logic

## Procedure

1. **Pick the test type** — see [decision matrix](./references/test-type-matrix.md) (unit / integration / security)
2. **Pick a template** — start from one of:
   - [unit-test.ts](./assets/unit-test.ts) — pure functions, no deps
   - [integration-test.ts](./assets/integration-test.ts) — component interactions, real fs/temp dirs
   - [security-test.ts](./assets/security-test.ts) — input validation, injection, XSS, prototype pollution
3. **Apply patterns** — [test-patterns](./references/test-patterns.md): factory functions, AAA layout, table-driven cases, spy-at-boundaries, async patterns
4. **Co-locate the test** — `foo.ts` → `foo.test.ts` next to it (project convention)
5. **Run with vitest** — `npm test -- foo.test.ts` to confirm green before commit

## Conventions in This Project

- Test runner: **vitest** with **jsdom** environment (set per file via `// @vitest-environment jsdom` if needed)
- Files matched: `**/*.test.ts` and `**/*.test.tsx`
- Service tests live next to the service file under `src/services/`
- No mocking the Python sidecar in service tests — stub `window.__TAURI__.invoke` at the boundary instead
- Security-test category gets priority CI weight: a failing security test should block merge

## Output Format

When asked to generate tests, produce:

```
<filename.test.ts>
// (full vitest file ready to drop in)
```

Then a 1-sentence note explaining which template you started from and why.
