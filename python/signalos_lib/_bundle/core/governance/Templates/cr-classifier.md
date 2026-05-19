<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# CR Classifier — Change Request Blast-Radius Assessment
# 
#
# Run this for EVERY change request raised in Phase 6 Wave Review.
# Complete before the client session ends.

---

## Change Request

**CR ID:** CR-{wave}-{sequence}  
**Date:** {YYYY-MM-DD}  
**Raised by:** {client name / role}  
**Description:** {verbatim client statement — do not paraphrase}

---

## Decision tree

Answer each question. Stop at the first YES.

### Q1 — Does this change affect the database schema or data model?
- [ ] YES → **Foundation** — stop here
- [ ] NO → continue to Q2

### Q2 — Does this change affect authentication, authorisation, or security architecture?
- [ ] YES → **Foundation** — stop here
- [ ] NO → continue to Q3

### Q3 — Does this change affect more than one feature, module, or API endpoint?
- [ ] YES → **Cross-Cutting** — stop here
- [ ] NO → continue to Q4

### Q4 — Does this change affect a shared component used by 3 or more screens/flows?
- [ ] YES → **Cross-Cutting** — stop here
- [ ] NO → **Contained**

---

## Classification result

**Blast Radius:** Contained / Cross-Cutting / Foundation  ← circle one

---

## Required actions by tier

### If Contained
- [ ] Add to `governance/BACKLOG.yaml` as `status: raw`
- [ ] Note in Client Signal Log
- [ ] Normal Discovery refinement in next Pre-Wave

### If Cross-Cutting
- [ ] Add to `governance/BACKLOG.yaml` as `status: raw` with flag: `cross_cutting: true`
- [ ] Note in Client Signal Log with impact summary
- [ ] PO must review during Phase 2 Plan of affected Wave
- [ ] Update Soul Document: "Cross-cutting CR in backlog — affects [areas]"

### If Foundation
- [ ] Add to `governance/BACKLOG.yaml` as `status: raw` with flag: `foundation: true`
- [ ] Note in Client Signal Log with full impact description
- [ ] **MANDATORY: Constitution review before next Pre-Wave begins**
  - Which Constitution rules are affected or invalidated?
  - What new rules are needed?
  - Update `governance/CONSTITUTION.md` only after explicit agreement with client
- [ ] Update Soul Document immediately: "Foundation CR pending — next Pre-Wave blocked until Constitution reviewed"
- [ ] Expectation Map for next wave must explicitly address the Foundation change

---

## Estimated delivery wave

Based on blast radius and current backlog:
- Contained: Wave {N+1} likely
- Cross-Cutting: Wave {N+1} or {N+2} depending on scope
- Foundation: Wave {N+2} minimum — needs architecture planning wave first

**Communicated to client:** YES / NO  
**Client acknowledgement:** {verbatim or summary}

---

## Sign-off

PO: _______________  
Date: _______________
