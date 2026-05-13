---
description: "Run the full validator suite (HALT/BLOCK_MERGE/WARN tiers) or a single named validator. (W3.5, AMD-CORE-018)."
---

# validate — Validator Suite Runner (W3.5 · AMD-CORE-018)

Executes the full validator suite (or a single named validator) and reports
pass/fail/skip status per validator. Mirrors the HALT / BLOCK_MERGE / WARN
severity tiers defined in deliver.sh.

## Usage
```
signalos validate [--repo-root <path>] [--validator <name>] [--json]
```

## Severity tiers
| Tier | Validators |
|------|-----------|
| HALT | gate-signature-guard · constitution-amendment-guard · ownership-guard |
| BLOCK_MERGE | trust-tier-guard · tier-sheet-guard · artifact-shape-guard · path-consistency-guard · expectation-redline-guard |
| WARN | decision-dna-guard · client-signal-verbatim-guard · metrics-config-validator |

## Exit codes
| Code | Meaning |
|------|---------|
| 0    | All validators pass (or only WARN failures) |
| 1    | At least one HALT validator fails |
| 2    | At least one BLOCK_MERGE validator fails (no HALT) |
