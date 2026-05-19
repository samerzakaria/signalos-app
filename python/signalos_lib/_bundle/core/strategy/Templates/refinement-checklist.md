<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Backlog Refinement Checklist
# 
# Run this to promote a backlog item from status:raw to status:refined

---

**Item ID:** _______________  
**Title:** _______________  
**Current status:** raw  
**Refinement date:** {YYYY-MM-DD}  
**Refined by:** {Discovery Agent / PO name}

---

## Refinement checklist

### Belief
- [ ] Problem stated: {what is broken or missing?}
- [ ] Bet stated: {what change do we believe will fix it?}
- [ ] Signal threshold stated: {how will we know it worked? Must be measurable.}

### Scoring
- [ ] Risk score (1–5): ___  *(how bad if we're wrong?)*
- [ ] Impact score (1–5): ___ *(how good if we're right?)*
- [ ] Test Cost (1–5): ___ *(how expensive to find out?)*
- [ ] **Bet Score = (Risk × Impact) / Test Cost = ___**

> Items with Bet Score < 2: deprioritise. Items > 8: consider splitting.

### Blast Radius
- [ ] Classified using `governance/templates/cr-classifier.md`
- [ ] Result: Contained / Cross-Cutting / Foundation

### Acceptance Criteria
- [ ] Minimum 3 criteria written
- [ ] Each criterion is independently testable
- [ ] No criterion uses vague language ("looks good", "works correctly")
- [ ] Happy path, edge cases, and security covered

### Expectation Map Delta
- [ ] What does the client expect?
- [ ] What will we actually build?
- [ ] Is the delta documented and ready to discuss?

### Foundation gate (if applicable)
- [ ] N/A — item is Contained or Cross-Cutting
- [ ] Constitution review completed — `governance/CONSTITUTION.md` updated if needed

---

## Result

- [ ] All criteria met → change `status: raw` to `status: refined` in BACKLOG.yaml
- [ ] Criteria not met → item stays `status: raw`. Note what is missing:
  > {missing items}

---

## Raw → refined promotion ceremony

*The promotion is not a status-string edit. It is a small ceremony that makes the item safe to pick up at Pre-Wave without needing the original author present. Run these steps, in order, only after every checklist item above is ticked.*

1. **Author reads the item aloud.** If it does not parse as a Belief in one sentence ("We believe … because … we'll know by …"), it is not ready.
2. **A second human (PE or PO — whichever did not author it) restates the Belief in their own words.** If the restatement materially differs, the item is not ready — the ambiguity surfaced here is cheaper to fix than the same ambiguity surfacing at Gate 2.
3. **Bet Score recomputed in the presence of both people.** Scores that only one person endorses are a smell — scoring is a shared exercise, not a solo judgement.
4. **Blast Radius reconfirmed.** Foundation items trigger Constitution review before promotion, not after.
5. **Append a one-line entry to `governance/DECISION-DNA.md`** of shape: `DEC-{ID} — Refined {Item ID} — {one-sentence Belief headline}`. Promotion without a Decision DNA entry is a protocol violation — it destroys audit trail for why this bet was chosen over the dozens left in `raw`.
6. **Flip `status: raw` → `status: refined` in BACKLOG.yaml** and commit with message `refine({Item ID}): promote to refined`.
7. **Notify the PO** if the PO did not run the ceremony themselves — they need to know what the next Pre-Wave may pull from.

A refined item is not a committed Wave. It is an item that has passed the minimum bar to be *picked* at Pre-Wave without re-litigating the basics. The ceremony exists so that the picker inherits a trustworthy package, not a shell.

---

*Refinement completed by:* _______________
*Ceremony witnessed by:* _______________
*DECISION-DNA entry ID:* _______________
