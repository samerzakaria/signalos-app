---
description: "Independent cross-model second-opinion review on a plan, diff, or decision (W15, AMD-CORE-036)."
---

# /signal-second-opinion — Second Opinion (W15, AMD-CORE-036)

**Phase:** execution  
**AMD:** AMD-CORE-036  
**Wave:** W15

## Purpose
Routes a plan, diff, or decision to a fresh second-opinion record. The second model call
must use a **new system prompt** — it is explicitly forbidden from reading the prior session
preamble to prevent anchoring bias. Structured output: `agree` / `disagree` / `risk-identified`.
Disagree verdicts and identified risks are recorded in DECISION-DNA.

## Usage

```
# Request a new second opinion
signalos signal-second-opinion "Proposal: migrate auth to JWT" --wave W15 [--note N] [--json]

# Record the verdict after independent review
signalos signal-second-opinion-record so-001 --verdict agree|disagree|risk-identified \
  [--new-risk TEXT] [--decision-dna-ref REF] [--json]
```

## Iron-Law: Fresh System Prompt
The reviewer model session **must not** have access to the original session's preamble or
prior context. The subject passed to `/signal-second-opinion` is the only input.

## Verdicts
| Verdict | Meaning |
|---------|---------|
| `agree` | Second model concurs with the plan/decision |
| `disagree` | Second model identifies a fundamental flaw |
| `risk-identified` | Second model flags a new risk not covered in the original |
| `pending` | Review has been requested but verdict not yet recorded |

## Storage
`.signalos/second-opinion/index.jsonl` — append-only record store  
Sequential IDs: `so-001`, `so-002`, …

## DECISION-DNA integration
When verdict is `disagree` or `risk-identified`, the caller should record the finding
in `DECISION-DNA.md` using the `--decision-dna-ref` flag.
