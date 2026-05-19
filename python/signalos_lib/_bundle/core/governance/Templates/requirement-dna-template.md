<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Requirement DNA — {Product Name}

`Canonical path: core/governance/Governance/REQUIREMENT-DNA.md · Authored by: PO · Updated: every requirement change, every Wave close`

> The Requirement DNA tracks the **evolution of each requirement** across Waves. Decision DNA records *why we chose architecture A over B*; Requirement DNA records *how "user can register" evolved from a one-liner into a multi-Wave feature with three pivots*. It is the genealogy of product intent — queried when a requirement resurfaces, when scope disputes arise, or when a Belief references a feature the squad thought was settled.

---

## How to use this file

**Before writing a Belief**, the PO and Brainstorm agent query this file: *"Has this requirement appeared in a prior Wave? What happened last time? Was it completed, deferred, or killed?"*

**After a Wave closes**, the PO confirms every requirement that was in-scope has an up-to-date entry. Missing entries are flagged at Phase-8 Retro.

**At scope disputes**, this file is the arbitration record. If a client says "we asked for export six Waves ago," the entry either confirms or refutes the claim with dates, Belief IDs, and verdicts.

---

## Entry template

```markdown
## REQ-{NNNN} · {short requirement title}

- **Created:** YYYY-MM-DD · Wave {N}
- **Author:** {PO name}
- **Source:** {client request / internal discovery / incident / retro action / competitor observation}
- **Current status:** raw | refined | in-progress | done | deferred | cancelled
- **Related Decision DNA:** DEC-{NNNN}, … (or "none")
- **Related Belief:** BEL-{YYYYMMDD-N}, … (or "none")

### Original statement

{Verbatim — what the requester actually said, not a rewrite. Quote source.}

### Evolution log

| Wave | What happened | Verdict | Belief ID |
|---|---|---|---|
| {N} | {e.g. "First appeared in Discovery Brief. Refined to acceptance criteria."} | Refined | BEL-{…} |
| {N+1} | {e.g. "Built email/password. OAuth deferred."} | Partial — done (email), deferred (OAuth) | BEL-{…} |
| {N+3} | {e.g. "OAuth built in Wave 3. Social login still deferred."} | Partial — done (OAuth), deferred (social) | BEL-{…} |

### Current acceptance criteria

{Copy from the latest backlog entry or Expectation Map. This is what "done" means right now — not what it meant in Wave 1.}

- {criterion 1}
- {criterion 2}

### Scope boundaries (what this requirement is NOT)

{Explicit exclusions. Prevents scope creep via ambiguity.}

- {e.g. "Not SSO. SSO is REQ-{MMMM}."}
- {e.g. "Not password-reset. Password-reset is REQ-{MMMM}."}

### Dependencies

| Dependency | Type | Status |
|---|---|---|
| {e.g. "Identity provider contract signed"} | External | Done |
| {e.g. "REQ-{MMMM} — user profile table exists"} | Internal | Done |

### Risk notes

{Anything the PO or PE flagged as risky about this requirement's trajectory — not the technical risk (that's Decision DNA), but the product/scope risk.}

- {e.g. "Client changed their mind twice on auth method. May change again."}
```

---

## REQ-0001 · {first requirement title}

*(Populate as requirements are captured. Newest at top.)*
