---
description: "Cross-wave review. Compares actuals vs Expectation Map."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# /signal-wave-review — Phase 6: Wave Review

Owner: PO + Client. SIGNAL phase.

## Your first action
Read `core/governance/Governance/signal-logs/wave-{N}-signal.md`. Confirm Signal Window is closed.

## Client presentation
Present to client:
1. What was built (demo or screenshot)
2. The Signal result — with actual numbers, not qualitative summaries
3. Whether the Belief was confirmed, refuted, or inconclusive

## Classify every client reaction

| Reaction type | Action |
|--------------|--------|
| Positive | Log verbatim in Client Signal Log |
| Concern | Log verbatim, score urgency |
| Direction Change | Flag immediately — trigger Blast Radius classification |
| New Requirement | Add to backlog as `status: raw` |
| Decision | Log in Decision DNA |

## Blast-radius classification for every CR
For any Direction Change or New Requirement, run the CR Classifier before the session ends.
Template: `core/governance/Templates/cr-classifier.md`

| Tier | Meaning | Action |
|------|---------|--------|
| Contained | Affects one feature only | Add to backlog, normal Pre-Wave |
| Cross-Cutting | Affects multiple features or APIs | Add to backlog, flag for Architecture review in next Plan |
| Foundation | Affects data model, auth, or core architecture | Mandatory Constitution review before next Pre-Wave begins |

## Discovery Brief
If a surprise or direction change emerged, complete `core/strategy/Templates/discovery-brief-template.md` immediately after session, while memory is fresh.

## Exit criteria

- [ ] Client reactions logged verbatim in `core/governance/Governance/CLIENT-SIGNAL-LOG.md`
- [ ] Every CR blast-radius classified
- [ ] Any Foundation CR triggers Constitution review flag
- [ ] Soul Document updated if direction changed

## Post-ship Signal Checkpoint
Nothing proceeds to Debrief until client reactions are fully logged.

## Next phase
Run `/signal-debrief`.
