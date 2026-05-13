---
description: "Phase 0 initialization. Creates repo scaffold, CONSTITUTION.md, agent roster."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# /signal-init — Phase 0: Init

Owner: Orchestrator. Runs once per project.

## Your first action
Read `core/governance/Governance/SOUL-DOCUMENT.md` if it exists. If it does not exist, create it
now using the Soul Document template at `core/governance/Templates/soul-document-template.md`.

## Mandatory actions (complete in order)

1. **Seed Soul Document**
   - Stack and tech constraints
   - Closed decisions (things that will not change)
   - Open questions (things not yet decided)
   - Current Wave number (start at Wave 1)

2. **Confirm the product-Constitution**
   - Confirm the product-Constitution is signed and locked at `core/governance/Governance/CONSTITUTION.md` (Gate 0 prerequisite — the SignalOS meta-Constitution governs how it's authored).
   - See `core/governance/Templates/example-product-constitution.md` for the reference instance.

3. **Initialise git worktree structure**
   - Create branch naming convention: `wave-{N}/ticket-{ID}`
   - Document convention in Soul Document under "Conventions"

4. **Seed Prompt Library**
   - Create `core/governance/Governance/PROMPT-LIBRARY.md` with first 3 reusable patterns you know you will need
   - Use the skill template from `core/governance/Templates/gotcha-skill-template.md`

## Exit criteria (do not proceed to Pre-Wave until all are true)

- [ ] Soul Document is pastable into any AI session in under 5 seconds
- [ ] `core/governance/Governance/CONSTITUTION.md` committed and has at least 5 rules
- [ ] Worktree + branch naming convention documented
- [ ] Prompt Library seeded with at least 3 skills

## Gate: Constitution Check
Before exiting, confirm: does every rule in CONSTITUTION.md have a clear
violation condition and a clear consequence? If not, strengthen the weak rules now.

## Next phase
Run `/signal-pre-wave` when exit criteria are all checked.
