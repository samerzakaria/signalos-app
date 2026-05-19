# Phase Debt Protocol

<!-- SignalOS v1.0 — AMD-CORE-038 -->

Any phase in the SignalOS lifecycle may be skipped with explicit PO sign-off. Skipping is logged,
never silent. This document defines the exact format and rules.

---

## When a skip is allowed

A phase skip requires:
1. PO explicitly acknowledges the skip in the active session — verbal confirmation in chat is
   sufficient. Implicit skips (agent moving forward without PO acknowledgment) are protocol
   violations.
2. A Phase Debt entry is created in `core/governance/Governance/DECISION-DNA.md` before the
   next phase begins.
3. All artifacts the skipped phase would have produced are created as retroactive stubs and
   marked `source: retroactive-skip`.

---

## DECISION-DNA entry format

```
DEC-{ID} — PHASE-DEBT
phase-skipped: Phase {number} ({name})
wave: {N}
reason: {why it was skipped — required, cannot be blank}
risk-accepted: {what could go wrong — required, cannot be blank}
stubs-created: {list of artifact paths created as retroactive stubs}
debt-close-by: Wave {N+2}
PO: {name}
date: {YYYY-MM-DD}
```

---

## Retroactive stub rules

- Every artifact the skipped phase produces must be created as a stub file.
- Every stub must begin with the following front-matter block:

```yaml
---
source: retroactive-skip
phase-skipped: {phase number and name}
wave-skipped: {N}
promoted: false
promote-by: Wave {N+2}
---
```

- A stub that has not been promoted to `promoted: true` by the `promote-by` wave is **overdue
  Phase Debt**. Wiring-guard C22 enforces this — it will fail the build if overdue debt exists.

---

## Downstream phase behaviour with stubs

A phase that receives a stub as a prerequisite must:
- Treat the stub as advisory-only — never as authoritative evidence.
- Note in its own DECISION-DNA entry that it is operating on a stub.
- Flag any gate evidence that derives from a stub with `source: stub-derived`.

---

## Closing Phase Debt

Phase Debt is closed when:
1. The skipped phase's work is completed retroactively (the stub is replaced with a real artifact).
2. The DECISION-DNA entry is updated: add `closed: true` and `closed-wave: {N}`.
3. The stub front-matter field `promoted` is set to `true`.
4. Wiring-guard C22 re-runs cleanly.

---

## Gates that can never be skipped

The following gates are hard stops with no skip path, regardless of PO sign-off:

| Gate | Phase | Reason |
|------|-------|--------|
| Gate 0 — Soul Document | Phase –1 Onboard | Foundation of all downstream context |
| Gate 2 — Expectation Map | Phase 1 Pre-Wave | Prevents building the wrong thing |
| Integration Checkpoint | Phase 3 Build | Code integration correctness before review |
| Ship Gate — PO confirms | Phase 5 Ship | Real users receive the output; irreversible |

Attempting to skip these is a wiring-guard violation. The agent must refuse and surface the
requirement to PO.

---

## Reference

- Wiring-guard enforcement: C22 in `core/governance/Validators/wiring-guard.sh`
- Referenced from: `integrations/rules/signalos-preamble.mdc`
