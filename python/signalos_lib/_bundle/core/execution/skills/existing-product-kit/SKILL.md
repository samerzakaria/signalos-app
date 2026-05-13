---
name: existing-product-kit
description: "Extracts product surface, metrics, and stakeholder map from an existing product."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Skill — existing-product-kit

## One-liner

The end-to-end onboarding ceremony: orchestrate stakeholder-interview → product-surface-mapping → belief-seed-generation, then produce the six first-run artifacts (Soul Document, product-Constitution draft, Surface Inventory, permanently-T3 list, seed Belief, draft Role Activation Card).

## Trigger phrase

`existing-product-kit` — invoked only from `/signal-onboard`. Not callable from any later phase.

## Owning agent

**Onboarding agent** (`core/execution/agents/onboarding.md`).

## Inputs

- Read access to the full target codebase.
- Discovery Briefs at `core/strategy/discovery-briefs/wave-0-session-*.md`.
- The meta-Constitution at `core/governance/Governance/CONSTITUTION.md` (as seed).
- All templates under `*/Templates/`.

## Outputs

Six drafts, each on its own draft branch (never main):

1. `core/governance/Governance/SOUL-DOCUMENT.md` (one page)
2. `core/governance/Governance/CONSTITUTION.md` (product-scoped draft)
3. `core/execution/SURFACE_INVENTORY.md`
4. `core/execution/PERMANENTLY_T3.md`
5. `core/strategy/BELIEF.md` (seed — 2-week falsifiable)
6. `core/execution/ROLE_ACTIVATION_CARD.md` (draft; PO re-signs at Gate 1)

Plus an audit artifact:

7. `core/execution/onboarding-report.md` — what was read, what was skipped, every assumption.

## Orchestration order

1. **stakeholder-interview** — confirm at least one Discovery Brief exists; prompt PO to file more if coverage is thin.
2. **product-surface-mapping** — read the codebase, produce Surface Inventory + permanently-T3 list.
3. **belief-seed-generation** — derive the first falsifiable Belief from surfaces + stakeholder signal.
4. **Assembly** — fill Soul Document, draft product-Constitution, fill draft Role Activation Card.
5. **Audit** — write `onboarding-report.md` listing every read file, every skipped file, and every assumption.

## Refusal conditions

- Live production incident detected in the codebase → stop, page PE + PO.
- Zero Discovery Briefs present → refuse, prompt PO to interview at least one stakeholder first.
- Codebase too large to audit in one run → emit partial Surface Inventory flagged `coverage: partial`, escalate for scoping.
- Any of the six output artifacts would fail its individual quality bar (Soul Document > 1 page, Belief not falsifiable in 2 weeks, Surface Inventory has unclassified rows) → refuse.

## Quality bar

Every output must be **small enough to sign**. A 30-page product-Constitution draft is a refusal condition — prune to ≤ 10 rules. A 200-row Surface Inventory is fine; a surface classified `unknown` is not.

## Handoff

PO at Gate 0 (Soul Document sign) and Gate 1 (Belief + Role Activation Card + Constitution sign). The skill **does not self-sign** anything.
