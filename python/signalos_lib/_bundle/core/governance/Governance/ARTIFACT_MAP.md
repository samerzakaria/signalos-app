# Artifact Map — SignalOS v1.0

> **Rule.** This is the canonical map of every artifact SignalOS v1.0 produces. If an artifact is not in this table, it does not exist under v1.0 governance.
> **Layout.** SignalOS organises artifacts into three Pillars — **Strategy** (Belief, Soul, Map), **Execution** (Plan, Trust Tier, Acceptance, Cards), **Governance** (Constitution, Decision-DNA, Inventory). The Pillars are the physical folder structure; the artifact table below maps every artifact to its Pillar folder and its signing gate.
> **Provenance.** Pre-v1.0 drafts used an `Artifacts/NN_*` folder layout inherited from the Agency methodology. That layout is retired — see `archive-assets/legacy-proofs/legacy-signal/signal-source/Agency/Governance/ARTIFACT_MAP.md` for the provenance copy. Do not cite the legacy map.

---

## The 15 canonical Signal artifacts

| # | Artifact | Pillar | Canonical path | Owner | Signed at gate | Per-Wave or persistent |
|---|----------|--------|----------------|-------|----------------|-----------------------|
| A-1 | `SOUL-DOCUMENT.md` | Governance | `core/governance/Governance/SOUL-DOCUMENT.md` | PO + PE | Gate 0 | Persistent (once per product) |
| A-2 | `CONSTITUTION.md` *(product)* | Governance | `core/governance/Governance/CONSTITUTION.md` *(in the product repo)* | PO + PE | Gate 0 | Persistent |
| A-3 | `SURFACE_INVENTORY.md` | Governance | `core/governance/Governance/SURFACE_INVENTORY.md` | PE | Gate 0 | Persistent (edited at retros) |
| A-4 | `PERMANENTLY_T3.md` | Governance | `core/governance/Governance/PERMANENTLY_T3.md` | PE | Gate 0 | Persistent |
| A-5 | `BELIEF.md` | Strategy | `core/strategy/BELIEF.md` | PO | Gate 1 | Per-Wave |
| A-6 | `EXPECTATION_MAP.md` | Strategy | `core/strategy/EXPECTATION_MAP.md` | PO + Client | Gate 2 | Per-Wave |
| A-7 | `ROLE_ACTIVATION_CARD.md` | Execution | `core/execution/ROLE_ACTIVATION_CARD.md` (current) + `core/execution/role-activation-cards/wave-{N}-card.md` (archive) | PO | Gate 1 | Per-Wave (archived) |
| A-8 | `DESIGN_NOTE.md` | Strategy | `core/strategy/DESIGN_NOTE.md` | PO | Gate 3 | Per-Wave |
| A-9 | `PLAN.md` | Execution | `core/execution/PLAN.md` | PE | Gate 3 | Per-Wave |
| A-10 | `ACCEPTANCE_CRITERIA.md` | Execution | `core/execution/ACCEPTANCE_CRITERIA.md` | PE | Gate 3 | Per-Wave |
| A-11 | `TRUST_TIER.md` | Execution | `core/execution/TRUST_TIER.md` | PE + PO | Gate 4 | Per-Wave |
| A-12 | `BUILD_EVIDENCE.md` | Execution | `core/execution/BUILD_EVIDENCE.md` | PE | Gate 4 | Per-Wave |
| A-13 | `QUALITY_CHECK.md` | Governance | `core/governance/QUALITY_CHECK.md` | QA | Gate 5 | Per-Wave |
| A-14 | `DECISION-DNA.md` | Governance | `core/governance/Governance/DECISION-DNA.md` | PO | rolling | Persistent (append-only) |
| A-15 | `BELIEF_MAP.md` | Strategy | `core/strategy/BELIEF_MAP.md` | PO | rolling | Persistent |

**Closed set.** v1.0 locks at 15 artifacts. Adding a new artifact requires a Constitution amendment through the retro channel (see `core/governance/Governance/CONSTITUTION.md` §6).

---

## Gate-to-artifact index

| Gate | Artifacts signed here | Signer(s) |
|------|----------------------|-----------|
| Gate 0 — Onboarding Integrity | A-1 Soul Document, A-2 product-Constitution, A-3 Surface Inventory, A-4 Permanently-T3 | PO + PE |
| Gate 1 — Belief signed | A-5 Belief, A-7 Role Activation Card | PO |
| Gate 2 — Expectation Map signed | A-6 Expectation Map | PO + Client |
| Gate 3 — Design Approval | A-8 Design Note, A-9 Plan, A-10 Acceptance Criteria | PO for Design Note; PE for Plan and Acceptance Criteria |
| Gate 4 — Trust Tier + Build Evidence | A-11 Trust Tier, A-12 Build Evidence | PE + PO for Trust Tier; PE for Build Evidence |
| Gate 5 — Quality Check | A-13 Quality Check | QA |
| rolling — retros | A-14 Decision-DNA, A-15 Belief Map | PO |

---

## Pillar folder map

```
SignalOS/
├── core/strategy/
│   ├── BELIEF.md                 ← A-5 (per Wave)
│   ├── EXPECTATION_MAP.md        ← A-6 (per Wave)
│   ├── DESIGN_NOTE.md            ← A-8
│   ├── BELIEF_MAP.md             ← A-15
│   └── Templates/                ← template copies
├── core/execution/
│   ├── ROLE_ACTIVATION_CARD.md   ← A-7 (current Wave)
│   ├── role-activation-cards/    ← A-7 archive
│   ├── PLAN.md                   ← A-9
│   ├── ACCEPTANCE_CRITERIA.md    ← A-10
│   ├── TRUST_TIER.md             ← A-11
│   ├── BUILD_EVIDENCE.md         ← A-12
│   ├── Agents/                   ← agent prompt stubs
│   ├── Commands/                 ← 10 slash-commands
│   └── Skills/                   ← skill library
└── core/governance/
    ├── Governance/
    │   ├── SOUL-DOCUMENT.md      ← A-1
    │   ├── CONSTITUTION.md       ← A-2 (meta-Constitution; product-Constitution lives in the product repo)
    │   ├── SURFACE_INVENTORY.md  ← A-3
    │   ├── PERMANENTLY_T3.md     ← A-4
    │   ├── DECISION-DNA.md       ← A-14
    │   └── ARTIFACT_MAP.md       ← this file
    ├── QUALITY_CHECK.md          ← A-13
    └── Templates/                ← artifact templates
```

---

## Notes on retired artifacts

Pre-v1.0 documents (Agency-era BRDs, SADs, sprint backlogs, phase handovers, deployment logs) are **not** v1.0 artifacts. Teams migrating from Agency should:

1. Keep legacy artifacts in place for provenance.
2. Re-derive v1.0 artifacts (Soul, Constitution, Belief) through `/signal-onboard`.
3. Reference legacy docs from the product-Constitution where useful — they are **inputs**, not artifacts.

A full provenance trail of the retired layout is preserved at `archive-assets/legacy-proofs/legacy-signal/signal-source/Agency/Governance/ARTIFACT_MAP.md`.
