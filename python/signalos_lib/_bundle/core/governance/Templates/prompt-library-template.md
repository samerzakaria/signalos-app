<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Prompt Library — {Product Name}

`Canonical path: core/governance/Governance/PROMPT-LIBRARY.md · Authored by: PE + any role · Updated: every time an AI session surfaces a reusable pattern or a new project-specific gotcha`

> A living catalogue of reusable skill prompts scoped to **this product**. Different from `core/governance/Templates/gotcha-skill-template.md` (which is the blank template): this file is the *instance* for one product, populated over time. Every entry is a skill — a named, re-invokable unit of AI work — with its own Gotchas section grown from Wave Debriefs.

---

## How to use this file

- **Before writing a new AI prompt**, scan this file for an existing skill that fits.
- **After a session produces a reusable pattern**, promote it to a skill entry here.
- **After a Wave Debrief surfaces a project-specific mistake**, add a Gotcha line to the relevant skill's Gotchas section.
- Skills grow; they are never deleted. If a skill becomes obsolete, mark it `ARCHIVED` with the Wave it retired in.

---

## Index

| Skill | Purpose | Last updated | Owner |
|---|---|---|---|
| `memory-search` | Query prior decisions before a new ticket | YYYY-MM-DD | PE |
| `write-belief` | Draft a falsifiable Belief | YYYY-MM-DD | PO |
| `tdd-red-first` | Write failing test before implementation | YYYY-MM-DD | PE |

*(Seed the first 3 at `/signal-init`. Grow as the product runs.)*

---

## Entry template

```markdown
## Skill: {skill-slug}

**Purpose:** {one sentence}
**Trigger:** {when to invoke — keywords or situations}
**Owner:** {role}
**Last updated:** YYYY-MM-DD · Wave {N}

### Context required

Before running this skill, have loaded:
- [ ] `core/governance/Governance/SOUL-DOCUMENT.md`
- [ ] `core/governance/Governance/DECISION-DNA.md` — search for entries related to {area}
- [ ] {any other artifact}

### Steps

1. {step}
2. {step}
3. {step}

### Output

{What this skill produces}

### Examples

#### Good
```
{example}
```

#### Anti-pattern
```
{what NOT to do — and why}
```

---

### Gotchas
<!-- Populated during Wave Debriefs. Never delete entries. -->
<!-- Format: **What happened** → **Why it was wrong** → **Prevention** -->

- *(No gotchas yet — add the first one after Wave 1 Debrief)*
```

---

## Skills

*(Append new skills below this line. Use the template above.)*

### Skill: memory-search

**Purpose:** Query the Decision DNA and Prompt Library before starting any ticket so prior decisions and gotchas inform new work.
**Trigger:** At the top of every Build, Plan, or Review session.
**Owner:** PE
**Last updated:** 2026-04-16 · Wave 0

See full skill: `core/execution/skills/memory/SKILL.md`.

---

### Skill: write-belief

**Purpose:** Draft a Belief that is falsifiable, specific, and time-bound.
**Trigger:** At `/signal-pre-wave` when the PO is composing a Wave's hypothesis.
**Owner:** PO
**Last updated:** 2026-04-16 · Wave 0

Template form:

> "We believe that {USER} wants {OUTCOME} because {INSIGHT}. We'll know we're right if {METRIC} moves by {AMOUNT} within {TIMEFRAME}."

Reject if any one of: unfalsifiable, no time window, no user named, no signal quantified.

---

### Skill: tdd-red-first

**Purpose:** Ensure every Build task opens with a failing test (RED).
**Trigger:** Before any production code is written in a Build ticket.
**Owner:** PE
**Last updated:** 2026-04-16 · Wave 0

See full skill: `core/execution/build/test-driven-development/SKILL.md`.

---

*(Add Wave-learned skills below.)*
