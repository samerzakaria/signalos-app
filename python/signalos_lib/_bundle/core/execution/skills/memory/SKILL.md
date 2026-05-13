---
name: memory
description: "Run before every ticket in Phase 3 Build. Searches memory, surfaces prior context."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Memory Search Skill
# 
# Run before every ticket in Phase 3 Build.

## Trigger
Automatically invoked when starting a new ticket in Phase 3 Build.
Manually invoked anytime you begin work in an unfamiliar area of the codebase.

## Steps

### Step 1 — Identify the ticket topic
State in one sentence what this ticket is about:
> "This ticket implements [feature/fix] in [component/area]."

### Step 2 — Query Decision DNA
Open `governance/DECISION-DNA.md` and search for entries related to:
- The component or module being touched
- The technical pattern being used (auth, data model, API design, caching, etc.)
- Any entity names (table names, service names, domain terms) from the ticket

**Query format:**
> "Search DECISION-DNA.md for any entry mentioning: [component], [pattern], [entity names]"

If a matching entry is found:
- Read it in full before touching any file
- Note the decision owner and reopen conditions
- If the decision is marked "reopen if: [condition]" — check whether this ticket triggers that condition

### Step 3 — Query Prompt Library Gotchas
Open `governance/PROMPT-LIBRARY.md` and find the skill(s) most relevant to this ticket.
Read the `## Gotchas` section of each relevant skill.

**Common skills to check:**
- `auth-patterns` — if ticket touches authentication or sessions
- `data-model` — if ticket touches database schema or ORM
- `api-design` — if ticket defines or modifies API endpoints
- `frontend-state` — if ticket touches client-side state management
- `testing` — always

### Step 4 — Declare memory search complete
State the result before writing any test:

```
Memory search complete.
Relevant Decision DNA entries found: [list titles or "none"]
Relevant Gotchas found: [list or "none"]
Proceeding to RED with this context loaded.
```

### Step 5 — Post-ticket update (after GREEN + REFACTOR)
If this ticket produced a new architectural decision:
- Add an entry to `governance/DECISION-DNA.md` immediately
- Format:

```markdown
## Decision: [short title]
**Date:** YYYY-MM-DD
**Wave:** N
**Ticket:** wave-N/ticket-ID
**Context:** [what situation led to this decision]
**Options considered:** [what alternatives were evaluated]
**Decision:** [what was chosen and why]
**Owner:** [who made the call]
**Consequences:** [what this locks in or rules out]
**Reopen if:** [what condition would make us revisit this]
```

If this ticket exposed a new Gotcha:
- Add to the relevant skill's `## Gotchas` section in `governance/PROMPT-LIBRARY.md`

## Gotchas
- Do not skip this step when context is high — that is exactly when you most need it
- If DECISION-DNA.md does not exist yet, create it now with just the header
- A "no results" search is still a valid search — document that you checked
