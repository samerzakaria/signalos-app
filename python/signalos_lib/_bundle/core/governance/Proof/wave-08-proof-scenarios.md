<!-- SignalOS v1.0 — W8 Design Pipeline -->

# Wave 08 Proof Scenarios

Wave: 08 — Design Pipeline
Belief: A structured design scoping ceremony with 6 forcing questions, taste memory, and an 8-dimension review rubric produces higher-quality product decisions than ad-hoc design choices.

---

## Overview

Four proof scenarios cover the W8 deliverables end-to-end. Scenarios 104–107 are executable as bash scripts in `proof/scenarios/`.

| # | Scenario | What it proves | Notes |
|---|----------|----------------|-------|
| 104 | `pre_design_modes` | All 4 PreDesignMode values accepted; PO_BRIEF.md written with correct mode + 6 Q&A entries; DECISION-DNA appended | stdlib only |
| 105 | `design_gate_lock` | `/signal-design explore` blocked when PO_BRIEF.md unsigned; unblocked when signed | stdlib only |
| 106 | `taste_decay` | `decay_weight()` at 10 weeks = 0.95^10 ≈ 0.5987; approved/rejected trait injection correct | stdlib only |
| 107 | `wiring_guard_c13_c14` | C13 PASS when DESIGN_NOTE absent; C13 FAIL when DESIGN_NOTE present without signed PO_BRIEF; C14 PASS when no DESIGN_NOTE | bash |

---

## Scenario 104 — Pre-design modes + PO_BRIEF.md output

**File:** `proof/scenarios/104_pre_design_modes.sh`

**Proves:**
- `cli/signalos_lib/design.py` is importable
- All 4 `PreDesignMode` values are valid
- `generate_po_brief()` writes `core/strategy/PO_BRIEF.md`
- Written file contains all 6 forcing questions
- Written file contains the correct mode line
- `append_decision_dna()` creates or appends to DECISION-DNA.md

**Pass condition:** All assertions pass within 10 seconds.

---

## Scenario 105 — Design gate lock (PO_BRIEF.md signed)

**File:** `proof/scenarios/105_design_gate_lock.sh`

**Proves:**
- `check_po_brief_signed()` returns False when PO_BRIEF.md absent
- `check_po_brief_signed()` returns False when PO_BRIEF.md has no signature block
- `check_po_brief_signed()` returns False when only DRAFT signer present
- `check_po_brief_signed()` returns True when a valid non-DRAFT signer present
- `generate_variants()` produces 3 HTML files + index.html comparison board
- `review_variant()` PASS when overall ≥ 7.0, FAIL when < 7.0
- `generate_production_html()` produces output.html in production dir

**Pass condition:** All assertions pass within 15 seconds.

---

## Scenario 106 — Taste memory decay

**File:** `proof/scenarios/106_taste_decay.sh`

**Proves:**
- `decay_weight()` at 0 weeks = 1.0
- `decay_weight()` at 10 weeks ≈ 0.5987 (0.95^10, tolerance ±0.001)
- `decay_weight()` at 52 weeks < 0.08 (effectively expired)
- `record_taste()` writes to design-taste.jsonl
- `load_taste_context()` returns approved and rejected trait sections
- Multiple entries ordered by decayed weight

**Pass condition:** All assertions pass within 10 seconds.

---

## Scenario 107 — Wiring guard C13 + C14

**File:** `proof/scenarios/107_wiring_guard_c13_c14.sh`

**Proves:**
- `wiring-guard.sh --check C13` PASS when DESIGN_NOTE.md absent
- `wiring-guard.sh --check C13` FAIL when DESIGN_NOTE.md present but PO_BRIEF.md missing
- `wiring-guard.sh --check C14` PASS when DESIGN_NOTE.md absent
- All prior checks C1–C12 still pass (no regression)

**Pass condition:** All exit-code assertions match expected values.
