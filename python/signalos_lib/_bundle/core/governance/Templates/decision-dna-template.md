<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Decision DNA — {Product Name}

`Canonical path: core/governance/Governance/DECISION-DNA.md · Authored by: PE · Updated: every architectural decision, every Wave close`

> The Decision DNA is the product's architectural decision journal. Every meaningful choice — why we picked library A over B, why auth lives in middleware not at the edge, why this table uses soft-deletes — gets one entry. Queried **before** every Build ticket via the memory-search skill. Never edited — only appended. Entries that are reversed get a new entry pointing at the old one.

---

## How to use this file

**Before starting any ticket**, the Build agent and PE run the memory-search skill against this file. The query is: *"Any Decision DNA entry mentioning {component}, {pattern}, or {entity names}?"*

**After making a decision**, the author appends an entry using the template below. Typical entry authors: PE (architecture), QA (test strategy), DevOps (infra), PO (product scope decisions that shape architecture).

**At Wave Close**, the PE confirms every architectural decision made during the Wave has an entry. Missing entries block the Wave Debrief.

---

## Entry template

```markdown
## DEC-{NNNN} · {short decision title}

- **Date:** YYYY-MM-DD
- **Wave:** {N}
- **Author:** {name · role}
- **Trust Tier of surface touched:** T1 / T2 / T3
- **Related entries:** DEC-{NNNN}, … (or "none")
- **Reverses:** DEC-{NNNN} (or "none")

### Context

{2–5 sentences. What was the problem or choice? What alternatives were on the table?}

### Decision

{1–3 sentences. The actual choice, stated declaratively. Not "we considered …" but "we do …".}

### Rationale

{3–6 sentences. Why. Cite the product-Constitution clause, the Belief, or the incident that motivated this choice. If the decision has trade-offs, name them.}

### Consequences

- **Good:** {what this unblocks or simplifies}
- **Cost:** {what this makes harder or riskier}
- **Reversibility:** {trivial / hard / one-way door}

### Verification

{How we'd know this decision was wrong. Specific signal or incident type.}
```

---

## Entries

*(Append new entries below this line. Never delete or edit existing entries — reverse them with a new entry instead.)*

### DEC-0001 · {first decision}

- **Date:** YYYY-MM-DD
- **Wave:** 1
- **Author:** {name · role}
- **Trust Tier of surface touched:** T{1|2|3}
- **Related entries:** none
- **Reverses:** none

*(Fill in the first decision when the first architectural choice is made. This file is idempotent — creating it empty at `/signal-init` is fine.)*

---

*Last verified complete at Wave close:* __________  *PE — Date:* __________
