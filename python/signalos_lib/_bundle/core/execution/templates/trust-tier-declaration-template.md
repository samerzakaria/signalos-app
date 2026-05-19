<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Trust Tier Declaration — Wave {N}

`Canonical path per Wave: core/execution/TRUST_TIER.md (current) or core/execution/trust-tiers/wave-{N}-trust-tier.md (archived) · Authored by: PE · Signed at: Gate 4 (PE + PO)`

> The Trust Tier Declaration is the per-Wave record of **who holds the keyboard for each surface** touched by the Wave. Trust is surface-by-surface, not persona-global. Constitution §2 defines the three tiers; this file is where the tier is committed, signed, and auditable for this Wave.

---

## Front-matter

```yaml
wave: {N}
scale_track: quick | wave | campaign
delivery_mode: fresh-wave | daemon
author: {PE name}
date: YYYY-MM-DD
co_signer: {PO name}
```

---

## Trust Tier definitions (reference)

| Tier | Authority | Who types the diff | Typical surfaces |
|---|---|---|---|
| **T1 Proceed** | Agent ships unsupervised inside the declared scope | Agent | Pure-function changes, tests for covered behavior, doc typos |
| **T2 Propose** | Agent drafts; human reviews & merges | Human (PO / PE / QA) reviews diff before merge | Feature code behind feature-flag, new tests for new behavior, isolated UI work |
| **T3 Suggest** | Agent advises; human types the diff | Human (PE) | Auth, payments, migrations, secrets, IaC, Constitution, permanently-T3 surfaces (§2.2) |

*A surface's tier may vary Wave to Wave, except the permanently-T3 surfaces enumerated in Constitution §2.2, which never downgrade.*

---

## Surfaces touched by this Wave

| # | Surface (file / module / route) | Owner agent | Default tier | Declared tier for this Wave | Justification (one line) |
|---|---|---|---|---|---|
| 1 | {e.g. `services/billing/charge.ts`} | {e.g. Backend Engineer} | T3 (permanently-T3: payments) | T3 | Constitution §2.2 — payments |
| 2 | {e.g. `components/ReportCard.tsx`} | {Frontend Engineer} | T2 | T2 | New component, behind flag |
| 3 | {e.g. `tests/report-card.spec.ts`} | {QA Engineer — human} | T1 | T1 | Test for T2 component; isolated |
| 4 | | | | | |

*If any row's **Declared tier** is below its **Default tier**, PE must add a one-paragraph rationale in the "Downgrade rationale" section below. Downgrades that touch permanently-T3 surfaces are **always blocked** — no rationale overrides §2.2.*

---

## Permanently-T3 surfaces touched (Constitution §2.2)

*List every permanently-T3 surface this Wave will touch. If any surface appears here, PE types the diff — no exceptions.*

- [ ] Auth (authentication / session / RBAC)
- [ ] Payments / billing / invoices
- [ ] Data migrations / schema changes
- [ ] Secrets / credentials / key rotation
- [ ] Infrastructure-as-Code / deployment pipelines
- [ ] Constitution or Governance files

*If none ticked, write "None — no permanently-T3 surface touched this Wave."*

---

## Downgrade rationale (if any)

*Required only if a row above has Declared tier below Default tier, AND the surface is not permanently-T3.*

| Surface | From → To | Rationale | Risk mitigation |
|---|---|---|---|
| | T3 → T2 | | |

*PO must acknowledge each downgrade by initialling the row.*

---

## Enforcement hooks

- PE verifies each surface's declared tier at PR-open (Gate 4 entry).
- PR template includes `trust-tier: T{n}` checkbox per touched surface — must match this file.
- CI `trust-tier-guard` validator rejects PRs where a permanently-T3 surface appears in the diff with tier ≠ T3 (Enforcement-Layer 3).
- On disagreement between agent-claimed tier and PE-declared tier, PE's declaration wins and the agent re-submits at the higher-trust ceiling.

---

## Gate 4 signatures

**I confirm that each surface this Wave touches has been classified, that no permanently-T3 surface has been downgraded, and that the PE will type the diff for every T3 surface.**

Signed (PE): __________  *Date: __________*  
Signed (PO): __________  *Date: __________*

*(Gate 4 is not satisfied without both signatures. An unsigned Trust Tier Declaration blocks merge at PR open.)*

---

## Amendment history

| Date | What changed | Signers |
|---|---|---|
| YYYY-MM-DD | Initial declaration | PE + PO |
