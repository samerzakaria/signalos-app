---
description: "Iron-law systematic debugging protocol: reproduce → log → regression → fix (W15, AMD-CORE-036)."
---

# /signal-investigate — Iron-Law Debugging Protocol (W15, AMD-CORE-036)

**Phase:** execution  
**AMD:** AMD-CORE-036  
**Wave:** W15

## Purpose
Enforces the five iron laws of systematic debugging. Every investigation must pass through
each law in order. Produces `INVESTIGATION.md` with hypothesis / evidence / conclusion.

## Five Iron Laws
1. **Reproduce first** — no hypothesis accepted without a confirmed reproduction step
2. **One variable at a time** — each test changes exactly one thing
3. **Log everything** — every action recorded in `INVESTIGATION.md`
4. **No assumption without evidence** — claims must cite specific observations
5. **Write regression before fix** — the test must fail before any patch is applied

## Usage

```
# Open a new investigation
signalos signal-investigate open --title "Login 500 on empty email" --wave W15 [--json]

# Confirm the bug reproduces (Iron Law 1)
signalos signal-investigate confirm-reproduction inv-001 --wave W15 [--json]

# Confirm the regression test is written and failing (Iron Law 5)
signalos signal-investigate confirm-regression inv-001 --wave W15 [--json]

# Close after fix is applied and test passes
signalos signal-investigate close inv-001 --wave W15 [--json]

# List investigations
signalos signal-investigate list --wave W15 [--json]
```

## Document
Each investigation produces:
`.signalos/investigations/{id}-INVESTIGATION.md` — hypothesis, reproduction steps,
evidence table, regression test, conclusion.

## Storage
`.signalos/investigations/index.jsonl` — append-only record store  
Sequential IDs: `inv-001`, `inv-002`, …
