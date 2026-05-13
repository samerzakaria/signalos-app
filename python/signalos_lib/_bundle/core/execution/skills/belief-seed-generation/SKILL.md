---
name: belief-seed-generation
description: "Generates initial Belief candidates from product context and stakeholder input."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Skill — belief-seed-generation

## One-liner

Derive the first falsifiable Belief for a newly-onboarded product — deliberately small, proveable or killable within 2 weeks — from the Surface Inventory and the Discovery Briefs.

## Trigger phrase

`belief-seed-generation` — invoked by the Onboarding agent during `existing-product-kit`, after `product-surface-mapping` has completed.

## Owning agent

**Onboarding agent** (`core/execution/agents/onboarding.md`).

## Inputs

- `core/execution/SURFACE_INVENTORY.md`
- `core/strategy/discovery-briefs/wave-0-session-*.md` (every brief filed)
- `core/strategy/Templates/belief-template.md` or `belief-lite-template.md`
- `core/strategy/SIGNAL_CONCEPTS.md` §2 (Belief Statement shape) and §3 (Bet Score)

## Outputs

- `core/strategy/BELIEF.md` — seed Belief for Wave 1, draft state, PO to sign at Gate 1.

## Generation rules (fixed)

The seed Belief must satisfy **all** of:

1. **Falsifiable within 2 weeks.** If the Signal Window requires longer than 14 days, the Belief is too ambitious for onboarding — propose a smaller one that uses the same signal source.
2. **Touches a T1 or T2 surface only.** A first Belief that lands on a permanently-T3 surface is a refusal condition.
3. **Bet Score ≥ 1.0.** (Risk × Impact) / Test Cost, per SIGNAL_CONCEPTS §3.
4. **Grounded in a Discovery Brief.** The Belief must cite at least one Brief's Field 2 (*What surprised you?*) or Field 5 (*Signal to watch*) as its origin — not invented out of the surface inventory alone.
5. **Writable in one sentence.** If the candidate Belief exceeds one sentence in the SIGNAL_CONCEPTS §2 shape, it's not a seed — it's a Wave 3 bet wearing seed clothes. Try again smaller.

## Refusal conditions

- No Discovery Brief cites a usable signal → refuse; escalate to PO to run at least one more interview.
- Every candidate Belief requires > 2-week Signal Window → refuse; propose the smallest proxy signal (e.g. engagement with a precursor screen, rather than the final conversion).
- Only T3 surfaces yield a plausible Belief → refuse; first Wave must practise the ceremony on a safer surface.
- Bet Score < 1.0 on every candidate → refuse; the first Belief is not where we take big swings.

## Quality bar

- The seed Belief is **boring on purpose**. Its job is to exercise the ceremony (Gates 0–5, Signal Window, Wave Debrief) on a real product, not to deliver strategic value. Strategic Beliefs begin at Wave 2.
- Bet Score cited explicitly with the three numbers shown.
- At least one Discovery Brief cited by ID.

## Handoff

PO reviews, edits, signs at Gate 1 (optionally deferred to the first `/signal-pre-wave`). The Belief lands in `core/strategy/BELIEF.md` draft; the first Role Activation Card is filled assuming this Belief.
