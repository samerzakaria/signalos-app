<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. -->

---
name: code-review
description: "Comprehensive code review checking type safety, SOLID violations, security vulnerabilities, performance issues, and clean code patterns. USE FOR: reviewing PRs, validating implementations, auditing code quality, checking TypeScript strict mode compliance, spotting injection risks, identifying performance bottlenecks. DO NOT USE FOR: generating new code (use scope-implement), writing tests (use test-generation), security-focused deep dive (use security-audit), refactoring suggestions without review context."
license: MIT
---
# Code Review Skill

## When to Use
- Reviewing code before committing
- Validating implementation quality
- Checking for security vulnerabilities
- Auditing compliance with project standards

## Review Procedure

1. **Type Safety** — Check [type rules](./references/type-safety-rules.md)
2. **Security** — Run through [security checklist](./references/security-review.md)
3. **Clean Code** — Apply [quality metrics](./references/quality-metrics.md)
4. **Performance** — Check for common [bottleneck patterns](./references/performance-patterns.md)
5. **Summarize** — Produce actionable review with severity ratings

## Severity Scale
- **CRITICAL**: Security vulnerability or data loss risk — must fix before merge
- **HIGH**: Bug, type unsafety, or significant code smell — should fix
- **MEDIUM**: Improvement opportunity — better patterns available
- **LOW**: Style nitpick — optional improvement

## Output Format

```
## Review Summary
Files reviewed: N
Issues found: N (X critical, Y high, Z medium)

### Critical Issues
[file:line] — description + fix

### High Issues
[file:line] — description + fix

### Recommendations
- [improvement suggestions]
```
