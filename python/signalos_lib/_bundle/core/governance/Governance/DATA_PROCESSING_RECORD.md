# Data Processing Record — GDPR Article 30

`Canonical path: core/governance/Governance/DATA_PROCESSING_RECORD.md`
`AMD: AMD-CORE-026 · Wave: W6.2 · Authored: 2026-04-28`

> Machine-readable record of processing activities maintained under GDPR Article 30. This document covers all personal data processed by the SignalOS execution runtime — primarily operator identity captured in the audit trail and session journal.

---

## 1. Controller

| Field | Value |
|---|---|
| **Name** | SignalOS Product Owner (per product Constitution) |
| **Contact** | As defined in `CONSTITUTION.md` §1 — Pillar owner identity |
| **DPO** | Not appointed (product runtime — not a public-facing data processor at scale) |

---

## 2. Purposes of Processing

| Purpose | Legal Basis | Notes |
|---|---|---|
| **Delivery governance** | Legitimate interests (Article 6(1)(f)) | Gate signatures, wave progress, hook-fire records required for Change-Management audit trail |
| **Security and access control** | Legitimate interests (Article 6(1)(f)) | Agent write blocks, secret-scan events, pre-commit blocks needed for SOC2 CC7.2 logging |
| **Reproducibility and traceability** | Legitimate interests (Article 6(1)(f)) | Step events, session journals, worktree lifecycle records needed for incident investigation |
| **Actor identity verification** | Legitimate interests (Article 6(1)(f)) | HMAC-keyed actor identity (AMD-CORE-025) to detect forged audit entries |

---

## 3. Categories of Data Subjects

| Category | Description |
|---|---|
| **Product team operators** | PO, PE, QA, DevOps roles who sign gates, run steps, or interact with the CLI |
| **System actors** | Automated hooks, validators, and daemon processes (role = `system`) |

---

## 4. Categories of Personal Data

| Field | Location | Notes |
|---|---|---|
| `actor` | `.signalos/AUDIT_TRAIL.jsonl` and `.signalos/sessions/*/journal.jsonl` | Free-text name string provided by the operator (e.g. `"Samer Zakaria"`) |
| `actor_identity` | Same files, when AMD-CORE-025 HMAC signing is active | Plaintext `"name\|role\|session_id"` string used to compute the HMAC |
| `actor_hmac` | Same files | HMAC-SHA256 of `actor_identity` keyed with `install.secret` — not personal data per se, but binds identity to an installation |
| Email addresses | Any file under `.signalos/` | Captured only if redaction fails (redact.py Rule 3 pattern; should not appear; PII minimisation control) |
| IP addresses | Any file under `.signalos/` | Captured only if redaction fails (redact.py Rule 4 pattern; same caveat) |

**Sensitive data:** None processed by design. SignalOS does not request or store health, religious, ethnic, or other special-category data.

---

## 5. Recipients

| Recipient | Purpose | Transfer mechanism |
|---|---|---|
| **LLM provider (Anthropic Claude API)** | Step execution — prompts are sent for completion | `harness.py` with pre-send `_redact_text()` sanitisation (AMD-CORE-011); actor names may appear in prompt context if included in the wave PLAN.md |
| **Git remote** | Audit trail is committed to the product repo | Standard git push; access governed by repo ACLs |
| **No third parties beyond the above** | — | — |

---

## 6. Transfers Outside the EEA

| Transfer | Safeguard |
|---|---|
| Prompts sent to Anthropic Claude API (US-hosted) | Standard Contractual Clauses apply per Anthropic's DPA. Operators must ensure their Anthropic agreement covers their jurisdiction. |
| Git remote (if hosted outside EEA) | Organisation's own data transfer agreement with their git hosting provider. |

---

## 7. Retention Periods

| Data | Default retention | Override |
|---|---|---|
| **Session journals** (`.signalos/sessions/*/journal.jsonl`) | 90 days from session end | `SIGNALOS_SESSION_RETENTION_DAYS` env var |
| **Audit trail** (`.signalos/AUDIT_TRAIL.jsonl`) | Follows product git repo lifetime | No default rotation; requires Constitutional amendment to truncate |
| **HMAC install secret** (`.signalos/install.secret`) | Persists until repo is archived | Rotate by re-running `install.sh` |

All retention periods are measured from the timestamp in the `ts` field of each entry.

---

## 8. Technical and Organisational Measures

| Measure | Implementation |
|---|---|
| **Pseudonymisation** | `actor_hmac` (AMD-CORE-025) — HMAC binding prevents forgery while keeping the audit trail verifiable without exposing the raw secret |
| **Redaction at ingestion** | `redact.py` Rules 1–16 strip API keys, JWTs, emails, IPs, connection strings from prompts and tool-content before any write (AMD-CORE-011) |
| **Pre-LLM sanitisation** | `harness.py` calls `_redact_text()` before every `provider.call()` to prevent PII leaving the local process (AMD-CORE-011) |
| **Hash-chain integrity** | `prev_hash` field on each audit-trail entry (AMD-CORE-013 D8); tamper-evident chain detected by scenario 78 |
| **Append-only audit trail** | `.signalos/AUDIT_TRAIL.jsonl` is never overwritten, only appended; `data-protection-guard.sh` enforces this at the agent write boundary |
| **Access control** | File-system permissions on `.signalos/`; repo-level ACLs for git remote |
| **Data subject erasure** | `signalos data purge --subject <name> --reason "GDPR Article 17"` redacts all string fields containing the subject name and logs the purge action (AMD-CORE-026) |
| **Data subject access** | `signalos data export --subject <name>` produces a JSON export of all entries referencing the subject (AMD-CORE-026) |

---

## 9. Data Subject Rights Procedure

### Right of access (Article 15)

```bash
signalos data export --subject "Jane Smith" --json > dsar_jane_smith.json
```

Returns all journal and audit-trail entries where `"Jane Smith"` appears in any string field.

### Right to erasure (Article 17)

```bash
signalos data purge --subject "Jane Smith" --reason "GDPR Article 17 erasure request"
```

Replaces all occurrences of `"Jane Smith"` in `.signalos/**/*.jsonl` with `[REDACTED:GDPR17]` and appends a `gdpr-purge` record to `AUDIT_TRAIL.jsonl` confirming the action.

**Note:** Because the audit trail is committed to git, erasure from the working tree does not remove data from git history. A `git filter-repo` pass is required to remove data from the full commit history. This is outside the scope of the CLI and requires human operator action.

### Right to rectification (Article 16)

Journal entries are append-only by design. Corrections are appended as new entries with `action: "amendment"` rather than modifying existing entries, preserving the hash-chain integrity.

---

## 10. Review Schedule

This record must be reviewed:
- When a new personal-data field is added to the audit trail schema
- When a new LLM provider is added (`cli/signalos_lib/providers/`)
- Annually, at minimum

Last reviewed: **2026-04-28** by Samer Zakaria (PO) + Mohammed Shaban (PE)
