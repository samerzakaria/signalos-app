<!-- SignalOS v1.0 — Locked 2026-04-16 -->
<!-- SEED FILE: copy to your product repo at Governance/SOUL-DOCUMENT.md and populate during /signal-init. This file in the SignalOS distro exists only as a template-seed reference. -->

# Soul Document — {Product Name}

`Canonical path: Governance/SOUL-DOCUMENT.md · Authored by: PO · Signed at: Gate 0 · Cadence: evergreen — amended with signatures · Template: core/governance/Templates/soul-document-template.md`

> The Soul Document is the **product's one-page truth** that every Wave re-reads before it starts. Stack, constraints, closed decisions, open questions, conventions — the context an agent needs to stop asking "what is this product?" and start asking "is this Belief right?"

---

## 1. The point (two sentences)

> *{What this product does, for whom, and why it exists — in language the PO would use out loud at a bar, not in a PRD.}*

---

## 2. Stack

| Layer | Tool / service | Version / plan | Why this, not the alternative |
|---|---|---|---|
| Language | | | |
| Framework | | | |
| Database | | | |
| Hosting | | | |
| Analytics | | | |
| CI | | | |
| Auth | | | |

---

## 3. Constraints (non-negotiable)

*Technical, business, regulatory, budgetary, or team constraints that will shape every Wave. If a Belief proposes violating one of these, it blocks at Gate 1.*

- {e.g. "Data cannot leave the EU — GDPR + contractual"}
- {e.g. "We ship to web only in v1.0; mobile after product-market fit"}
- {e.g. "Runway is 9 months at current burn — no hiring until next round"}

---

## 4. Closed decisions (do not re-litigate)

*Debates that have already happened and are done. A new agent reading this should see the answer without opening the Decision DNA log.*

| Decision | The answer | Date closed | Who closed it |
|---|---|---|---|
| | | YYYY-MM-DD | |

*(Detailed rationale lives in `Governance/DECISION-DNA.md`. This table is the headline.)*

---

## 5. Open questions (live — expect Waves to resolve)

*Questions the PO does not yet have an answer to, ranked by how much they block current work.*

| # | Question | Blocks | Target Wave |
|---|---|---|---|
| 1 | | | Wave {N} |
| 2 | | | |

---

## 6. Conventions

- **Naming:** {file, component, test, branch, commit-message conventions}
- **Style:** {linters, formatters, pre-commit hooks}
- **Review:** {how PRs are sized, who reviews what}
- **Docs:** {where ADRs / CRs / READMEs live}

---

## 7. Non-goals

*What this product is **not** — to kill scope creep early.*

- {e.g. "Not a CRM. Not an analytics dashboard. Not a full-stack marketing suite."}

## 8. Security surfaces

security_surfaces:
  - webview
  - ipc
  - sidecar
  - filesystem
  - network

---

## Gate 0 signature

**I affirm this Soul Document reflects the current truth of the product. Any Wave that contradicts Sections 3 or 4 must first amend this file with my signature.**

Signed (PO): __________  *Date: __________*

---

## Amendment history

| Date | What changed | Rationale | Signer |
|---|---|---|---|
| YYYY-MM-DD | Initial Soul Document at Gate 0 | Product launch | PO |
