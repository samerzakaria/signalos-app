<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Product Tier Sheet — {Product Name}

`Canonical path: core/execution/PRODUCT_TIER_SHEET.md · Authored by: PE · Signed by: PE + PO · Cadence: quarterly · Used only in delivery_mode: daemon`

> In **daemon mode** (Constitution §12), the per-Wave Trust Tier Declaration is replaced by a **persistent product-wide Tier Sheet**. Surfaces and their tiers are stable across Waves; items drawn from the queue inherit the surface's tier from this sheet. Re-affirmed quarterly or whenever the surface list changes.

---

## Entry criteria

A Product Tier Sheet exists only after the daemon-mode entry criteria (Constitution §12.2) are satisfied:

- [ ] 3 consecutive Waves returned "Keep" on the same Wave-level Belief
- [ ] T3 surface list has been stable for 3 Waves
- [ ] Backlog is queue-like (refined items with acceptance lines)
- [ ] Product Belief + Product Expectation Map signed

*If any box is unchecked, the product stays in `fresh-wave` mode and this file is not authored.*

---

## Product surface inventory

| # | Surface (file / module / route) | Owner agent | Tier | Why |
|---|---|---|---|---|
| 1 | {e.g. `services/billing/*`} | Backend Engineer | T3 | Payments — Constitution §2.2 |
| 2 | {e.g. `services/auth/*`} | Backend Engineer | T3 | Auth — Constitution §2.2 |
| 3 | {e.g. `db/migrations/*`} | Backend Engineer | T3 | Migrations — Constitution §2.2 |
| 4 | {e.g. `components/Dashboard/*`} | Frontend Engineer | T2 | Customer-facing UI |
| 5 | {e.g. `components/icons/*`} | Frontend Engineer | T1 | Pure-presentational |
| 6 | {e.g. `tests/unit/*`} | QA Engineer (human) | T1 | Isolated, low-blast-radius |
| 7 | {e.g. `infra/terraform/*`} | DevOps Engineer | T3 | IaC — Constitution §2.2 |

---

## Permanently-T3 surfaces (never downgrade — Constitution §2.2)

- Auth / session / RBAC
- Payments / billing / invoices
- Data migrations / schema changes
- Secrets / credentials / key rotation
- Infrastructure-as-Code
- Constitution or Governance files

*Every row in the inventory above that maps to one of these categories MUST be T3. A CI validator rejects the sheet if any such row is below T3.*

---

## Per-item tier inheritance

*When an item is drawn from the queue, its touched surfaces inherit tiers from this sheet. No per-item Trust Tier file is authored unless the item introduces a new surface.*

- New surface not in this sheet → PE adds a row here before merge; this counts as a sheet amendment and requires re-signature.
- Item-specific downgrade requested → amend this sheet (not the item); the amendment re-opens the quarterly cadence.

---

## Enforcement hooks

- CI `tier-sheet-guard` validator (Enforcement-Layer 3) cross-references every PR's touched files against this sheet's tier declarations.
- PR cannot merge if it touches a surface not in the sheet.
- PE types the diff for every T3 surface touched, regardless of which agent drafted.

---

## Active since

| Field | Value |
|---|---|
| Entered daemon mode | YYYY-MM-DD |
| Last quarterly retro | YYYY-MM-DD |
| Next quarterly retro due | YYYY-MM-DD |
| Product Belief | `core/strategy/PRODUCT_BELIEF.md` |
| Product Expectation Map | `core/strategy/PRODUCT_EXPECTATION_MAP.md` |

---

## Signatures

**We affirm the above surface inventory and tier declarations accurately reflect this product's blast-radius map. The permanently-T3 constraint is honored. The quarterly retro cadence is current.**

Signed (PE): __________  *Date: __________*  
Signed (PO): __________  *Date: __________*

*(An overdue quarterly retro forces exit to fresh-wave — Constitution §12.4.)*

---

## Amendment history

| Date | Quarter | What changed | Signers |
|---|---|---|---|
| YYYY-MM-DD | Q{N} | Initial daemon-mode sheet | PE + PO |
