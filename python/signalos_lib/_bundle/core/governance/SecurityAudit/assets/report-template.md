<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. -->

# Security Audit Report Template

## Audit Metadata
- **Target:** {file/module/feature}
- **Date:** {YYYY-MM-DD}
- **Auditor:** @security-auditor
- **Scope:** {what was examined}

## Attack Surface Map
| Entry Point | Trust Level | Data Flow |
|-------------|-------------|-----------|
| {user input} | Untrusted | {where it goes} |

## STRIDE Analysis
| Threat | Applicable | Finding | Severity |
|--------|-----------|---------|----------|
| Spoofing | Yes/No | {detail} | {level} |
| Tampering | Yes/No | {detail} | {level} |
| Repudiation | Yes/No | {detail} | {level} |
| Info Disclosure | Yes/No | {detail} | {level} |
| Denial of Service | Yes/No | {detail} | {level} |
| Elevation of Privilege | Yes/No | {detail} | {level} |

## Findings

### Finding 1: {title}
- **Severity:** CRITICAL / HIGH / MEDIUM / LOW
- **Category:** {OWASP category or STRIDE threat}
- **Location:** `{file}:{lineRange}`
- **Description:** {what the vulnerability is}
- **Proof of Concept:**
```typescript
// How it could be exploited
```
- **Recommendation:**
```typescript
// Secure implementation
```
- **SLA:** {Fix before merge / Fix within 24h / Fix within wave / Track as defer}

## Summary
| Severity | Count |
|----------|-------|
| CRITICAL | {n} |
| HIGH | {n} |
| MEDIUM | {n} |
| LOW | {n} |

## Verdict
- [ ] PASS — No critical/high findings
- [ ] CONDITIONAL — Fix high findings before merge
- [ ] FAIL — Critical vulnerabilities found, block merge
