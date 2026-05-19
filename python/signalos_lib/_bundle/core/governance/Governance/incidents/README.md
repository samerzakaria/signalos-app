<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Incidents

`Folder: Governance/incidents/ · Authored by: PE + QA · Cadence: one file per incident · Naming: YYYY-MM-DD-{short-tag}.md`

> Every production incident or near-miss caused by (or caught by) the SignalOS process produces a file here. The log is the product's **institutional memory of what goes wrong** — so the next time a Belief is written, a Trust Tier is declared, or a permanently-T3 surface is about to be touched, the squad can grep its own scar tissue first.

---

## When to open an incident file

- A Wave shipped and broke production (any severity).
- A Gate passed but should have blocked — the process failed.
- A permanently-T3 surface was changed with the wrong hands on the keyboard.
- A Signal Window verdict proved wrong within 30 days (false Keep or false Kill).
- A near-miss: something would have broken except a human caught it at PR review.

---

## File structure

Each incident file follows a fixed shape:

```
# Incident — YYYY-MM-DD — {short tag}

## Summary
## Timeline
## Blast radius
## Root cause (technical)
## Root cause (process)
## What the process missed
## Amendments
## Sign-off
```

The **What the process missed** section is the critical one — it names the Constitution clause, Gate, or Validator that should have caught this. If none applies, it names the gap in SignalOS itself and queues an amendment against the Constitution (§13 amendment path).

---

## Relationship to Wave Debrief

- A **Wave Debrief** (core/execution) closes a Wave — covers what was learned, what we'd do differently.
- An **incident file** (here) covers a specific breakage — narrower, more forensic, tied to a calendar date.

A single Wave can produce zero incident files (healthy) or multiple (unhealthy). A pattern of multiple incidents per Wave is a Product Belief disproof signal in daemon mode.

---

## Privacy

Incident files may reference customers, vendors, or specific users. Redact PII in the public-facing copy; keep the full-fidelity version in the private governance repo if the organization separates them.
