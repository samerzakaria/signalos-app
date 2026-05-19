<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Signature Specification

`Canonical path: core/governance/Governance/SIGNATURE_SPEC.md · Enforced by: gate-signature-guard.sh`

> Every gate artifact in SignalOS requires a human signature before promotion. This spec defines the machine-parseable signature format that validators enforce.

---

## Signature block format

Every signed artifact must contain a signature block matching this exact pattern:

```markdown
## Signatures

```yaml
- signer: {Full Name}
  role: {PO | PE | QA | DevOps}
  date: {YYYY-MM-DD}
  gate: {Gate 0 | Gate 1 | Gate 2 | Gate 3 | Gate 4 | Gate 5 | Phase-8}
  artifact_hash: {sha256 of artifact content above the Signatures section}
  verdict: {APPROVED | APPROVED-WITH-CONDITIONS | WAIVED}
  conditions: "{optional — required if verdict is APPROVED-WITH-CONDITIONS}"
```
```

Multiple signers append to the same YAML list (e.g., PO + PE co-sign on Constitution deltas).

---

## Rules

1. **Identity is the typed name + role.** In an LLM-agentic context, the human types the signature into the artifact through the AI tool. The git commit author + the typed name together form the identity chain. Cryptographic signatures are a v1.1 concern.

2. **artifact_hash is mandatory.** The signer (or agent acting on their behalf) computes `sha256` of the artifact content above the `## Signatures` heading and records it. This prevents post-signature tampering.

3. **Date must be ISO 8601.** `2026-04-16`, not `April 16` or `16/04/2026`.

4. **Role must match the RACI table.** If the RACI table in `ENGAGEMENT_MODEL.md` says QA is the Accountable signer for Gate 5, a PE signature alone is insufficient.

5. **Counter-signatures are separate entries.** A PO counter-sign on a PE-proposed Trust Tier declaration is a second YAML entry, not an inline note.

6. **Agents cannot sign.** An agent may draft a signature block with `signer: DRAFT — awaiting {Role}`, but the human must replace DRAFT with their name. A signature block containing "DRAFT" is treated as unsigned by the validator.

7. **Waiver requires conditions.** `verdict: WAIVED` must include a `conditions:` field explaining why and who approved the waiver (e.g., QA waiver on Quick-track Stage-2).

---

## Validator enforcement

`gate-signature-guard.sh` parses the `## Signatures` section of every touched gate artifact and checks:

- At least one `signer:` entry with a non-DRAFT, non-empty name
- `role:` matches the expected signer for this gate (from RACI)
- `date:` is valid ISO 8601
- `artifact_hash:` matches the computed hash of content above `## Signatures`
- No `DRAFT` entries remain in a "signed" artifact

Failure on any check = FAIL (exit 1). The artifact is unsigned until all checks pass.

---

## Audit trail integration

Every successful signature validation appends a line to `.signalos/AUDIT_TRAIL.jsonl`:

```json
{"ts":"2026-04-16T14:30:00Z","actor":"Jane Smith","role":"PO","action":"sign","gate":"Gate 1","artifact":"core/strategy/BELIEF.md","hash":"abc123...","verdict":"APPROVED"}
```

See `core/governance/Governance/AUDIT_TRAIL_SPEC.md` for the full schema.
