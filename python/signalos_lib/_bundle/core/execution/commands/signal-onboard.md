---
description: "Phase -1 onboarding. Runs once per product. Gathers Product DNA."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# /signal-onboard — Phase -1: Product Onboarding

Owner: PO. Runs **exactly once per product**, before `/signal-init`. Invokes the Onboarding agent (Team Charter §1).

> `/signal-init` assumes a product-Constitution already exists. `/signal-onboard` is what produces that Constitution (and the Soul Document, the Surface Inventory, the seed Belief) when SignalOS is being dropped onto an existing codebase. Greenfield products may run `/signal-onboard` with an empty repo to get the same scaffolding; most will skip straight to `/signal-init`.

## Your first action

Confirm this product has never had `/signal-onboard` run before. Check for:
- Absence of `core/governance/Governance/SOUL-DOCUMENT.md`
- Absence of `core/execution/SURFACE_INVENTORY.md`
- Absence of `core/execution/PERMANENTLY_T3.md`

If any of those exist and `/signal-onboard` was not logged in `core/governance/Governance/DECISION-DNA.md` as a prior run → **stop**. The product has partial onboarding state; resolve with PO before continuing.

## Prerequisites

- PO has run at least one structured stakeholder interview and filed a Discovery Brief at `core/strategy/discovery-briefs/wave-0-session-1.md`.
- Repo read access for the Onboarding agent.
- No live production incidents in flight.

## Mandatory actions (complete in order)

1. **Activate Onboarding agent** with the `existing-product-kit` skill. The agent reads the repo, reads all Wave-0 Discovery Briefs, and produces draft artifacts at the paths listed in `core/execution/agents/onboarding.md`.

2. **PO reviews every draft.** The PO is expected to edit, not rubber-stamp. The agent is T2 — nothing lands without PO eyes on each line.
   - `SOUL-DOCUMENT.md` — must fit on one page
   - `CONSTITUTION.md` — product-scoped; at minimum the meta-Constitution clauses §1, §2, §3, §4 are retained
   - `SURFACE_INVENTORY.md` — every surface classified T1/T2/T3
   - `PERMANENTLY_T3.md` — auth, payments, PII, billing, migrations enumerated
   - `BELIEF.md` — falsifiable within 2 weeks
   - Draft `ROLE_ACTIVATION_CARD.md` — PO re-signs at Gate 1

3. **Log the onboarding run** in `core/governance/Governance/DECISION-DNA.md` as:
   `DEC-{ID} — Onboarded product {name} under SignalOS v1.0 — agent run: {run-id} — PO: {name} — date: {YYYY-MM-DD}`

4. **Sign Gate 0** (Soul Document) before proceeding. Gate 1 (Belief) can be signed next or deferred to the first `/signal-pre-wave`.

## Exit criteria (do not proceed to `/signal-init` until all are true)

- [ ] `SOUL-DOCUMENT.md` signed (Gate 0)
- [ ] `CONSTITUTION.md` product-draft signed by PO + PE
- [ ] `SURFACE_INVENTORY.md` complete — zero unclassified rows
- [ ] `PERMANENTLY_T3.md` enumerated and acknowledged by PE
- [ ] Seed `BELIEF.md` drafted (Gate 1 may be deferred to first Pre-Wave)
- [ ] Draft `ROLE_ACTIVATION_CARD.md` present
- [ ] Decision DNA entry logged

## Gate: Onboarding Integrity

Before exiting, confirm the onboarding report at `core/execution/onboarding-report.md` shows:
- What the agent read vs. skipped
- Every assumption it made
- Any contradiction between stakeholder interviews and code — logged, not resolved in-place

If the integrity report is missing or incomplete → do not exit.

## Next phase

Run `/signal-init` when exit criteria are checked. (`/signal-init` on an onboarded product is thinner — it primarily confirms what Onboarding already produced and seeds the Prompt Library.)
