---
name: product-surface-mapping
description: "Maps the product surface area for scope and dependency analysis."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Skill — product-surface-mapping

## One-liner

Walk an existing codebase, enumerate every user-or-data-facing surface, classify each into Trust Tiers (T1/T2/T3) and Blast Radius (Contained / Cross-Cutting / Foundation), and emit the permanently-T3 list separately.

## Trigger phrase

`product-surface-mapping` — invoked by the Onboarding agent during `existing-product-kit`.

## Owning agent

**Onboarding agent** (`core/execution/agents/onboarding.md`).

## Inputs

- Read access to the target codebase.
- `executive/Engagement-Model/TRUST_TIERS.md` (the spec) — classification rules.
- `core/governance/Templates/trust-tier-scoring.md` — scoring rubric.

## Outputs

- `core/execution/SURFACE_INVENTORY.md` — single table with columns: `Surface` · `Entry point(s)` · `Proposed Trust Tier` · `Blast Radius` · `Rationale`.
- `core/execution/PERMANENTLY_T3.md` — subset of surfaces that must **never** be delegated regardless of Wave state.

## Classification rules (fixed — not optional)

A surface is **permanently-T3** if it touches any of:

- Authentication or authorisation (who can do what)
- Payment handling, billing, pricing, or revenue recognition
- Personally Identifiable Information at rest or in transit
- Schema migrations on persistent data stores
- External contracts (APIs customers integrate with, partner endpoints)
- Secrets, credentials, KMS, cryptographic material
- Anything the product-Constitution marks as closed-decision critical

A surface is **T2** (Propose — agent drafts, human signs) by default.

A surface is **T1** (Proceed — agent may land) only if **all** of:

- No customer-visible behaviour change
- No schema change
- Fully covered by existing tests
- Blast Radius = Contained

**Blast Radius** classification:

- **Contained** — one service, one module, reversible in under 30 min.
- **Cross-Cutting** — multiple services or modules, still reversible in < 1 day.
- **Foundation** — infrastructure, platform, shared library, or Constitution-touching. Irreversible or multi-day revert.

## Refusal conditions

- Any surface cannot be classified with confidence → emit as `unknown` and flag the whole file as refused (Onboarding agent will escalate to PE).
- Codebase size exceeds what can be audited in one run → emit partial table with header `coverage: partial` and escalate for scoping.
- Surface appears permanently-T3 under rules above but reasonable doubt exists → default to permanently-T3; Onboarding agent does not downgrade T3 on its own.

## Quality bar

- Zero unclassified rows in `SURFACE_INVENTORY.md` in the final emission.
- Every permanently-T3 entry cites **which rule above** it matched.
- Every T1 entry cites **which four conditions** it satisfied.

## Handoff

PE reviews `SURFACE_INVENTORY.md` and `PERMANENTLY_T3.md` before PO signs the product-Constitution at Gate 0. PE may downgrade a permanently-T3 entry only by amending the product-Constitution per §13 — not in-place.
