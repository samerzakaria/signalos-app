<!-- SignalOS v1.0 — Locked 2026-04-16 -->
<!-- SEED FILE: copy to your product repo at Governance/PROMPT-LIBRARY.md. Product-instance skill catalog. Template: core/governance/Templates/prompt-library-template.md -->

# Prompt Library — {Product Name}

`Canonical path: Governance/PROMPT-LIBRARY.md · Authored by: any agent; PE curates · Cadence: evergreen — skills added/retired in-line`

> The product's **skill catalog**. Every agent-callable skill for this product has an entry here with its trigger phrase, inputs, outputs, and side-effects. Agents read this file during `/signal-init` and on activation; humans read it to know what the squad can already do without new prompting.

---

## How this differs from `core/execution/skills/`

- `core/execution/skills/` (SignalOS distro) ships the **canonical** skill definitions — the source of truth, versioned with SignalOS.
- `Governance/PROMPT-LIBRARY.md` (this file, in each product repo) is the **product's enabled set** — which canonical skills are active, plus product-specific skills layered on top.
- A skill that is in the distro but NOT listed here is considered disabled for this product.

---

## Enabled canonical skills

| Skill | Trigger | Purpose | Owner agent | Last verified |
|---|---|---|---|---|
| `memory-search` | `search memory for {query}` | Read prior Waves' artifacts to avoid re-deciding a closed decision | Any agent | YYYY-MM-DD |
| `write-belief` | `draft Belief for Wave {N}` | Produce a falsifiable Belief matching core/strategy/Templates/belief-template.md | PO | YYYY-MM-DD |
| `tdd-red-first` | `write failing test for {behavior}` | Author the failing test before implementation, per Constitution §6 | Any engineer agent | YYYY-MM-DD |

*(Extend as more canonical skills ship. Remove a row to disable a skill for this product.)*

---

## Product-specific skills

| Skill | Trigger | Purpose | Owner agent | Location of skill file |
|---|---|---|---|---|
| | | | | |

*(Product-specific skills live at `Governance/skills/{skill-name}/SKILL.md` following the gotcha-skill structure.)*

---

## Retired skills

*Skills that were active and have been retired. Kept here so a grep on an old trigger phrase still finds its disposition.*

| Skill | Trigger | Retired on | Why | Replacement |
|---|---|---|---|---|
| | | YYYY-MM-DD | | |

---

## Curation rules

1. **One skill, one trigger phrase** — no aliases. Ambiguous triggers produce inconsistent agent behavior.
2. **Every skill names its owner agent** — so activation cards can reference the right seat.
3. **Skills that touch permanently-T3 surfaces** (Constitution §2.2) must declare so in their SKILL.md and will always activate at T3 ceiling, regardless of the agent's default tier.
4. **PE reviews additions** — new rows in this table require PE's initial in the row's "Owner agent" column.
