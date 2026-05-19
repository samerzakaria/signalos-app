<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized: VS Code attack-surface step retargeted to webview/Tauri threats. -->

---
name: security-audit
description: "Comprehensive security audit following OWASP Top 10, STRIDE threat modeling, and webview / Tauri desktop attack surfaces. USE FOR: reviewing code for vulnerabilities, hardening implementations, preparing for security review, auditing input handling, file access, shell execution, webview implementations, authentication patterns, dependency security. DO NOT USE FOR: general code quality review (use comprehensive-code-review), writing fix implementations (use scope-implement), penetration testing execution, compliance certification."
license: MIT
---
# Security Audit Skill

## When to Use
- Before merging security-sensitive code
- Auditing input handling, file access, or shell execution
- Reviewing webview / Tauri IPC implementations
- Checking authentication/authorization patterns
- Evaluating dependency security

## Procedure

1. **Map attack surface** — identify all trust boundaries and inputs
2. **STRIDE analysis** — check each threat category ([reference](./references/stride-model.md))
3. **OWASP check** — verify against Top 10 ([checklist](./references/owasp-checklist.md))
4. **Webview / Tauri specific** — desktop-app attack vectors ([reference](./references/webview-threats.md))
5. **Generate report** — findings with severity, PoC, and fix ([template](./assets/report-template.md))

## Severity Classification

| Level | Criteria | SLA |
|-------|----------|-----|
| CRITICAL | RCE, data exfiltration, auth bypass | Fix before merge |
| HIGH | Privilege escalation, injection possible | Fix within 24h |
| MEDIUM | Information disclosure, DoS possible | Fix within wave |
| LOW | Defense-in-depth improvement | Track as defer |

## Output Format

```markdown
## Security Audit Report
Audited: [scope]
Date: [date]
Risk Level: CRITICAL / HIGH / MEDIUM / LOW

### Executive Summary
[1-2 sentences of overall risk assessment]

### Findings
[Ordered by severity]

### Recommendations
[Prioritized hardening steps]
```
