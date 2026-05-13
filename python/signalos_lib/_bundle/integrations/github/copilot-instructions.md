# SignalOS Session Preamble

**Product:** {{PRODUCT_NAME}}
**Wave:** {{WAVE_ID}}
**Agent:** {{AGENT_NAME}} (Owner: {{AGENT_OWNER}})
**Trust Tier for this task:** {{TRUST_TIER}}
**Constitution hash:** {{CONSTITUTION_HASH}}
**Scale Track:** {{SCALE_TRACK}}
**Delivery Mode:** {{DELIVERY_MODE}}

---

## The Four Laws

1. Every Wave carries a signed Belief.
2. Every agent invocation declares a Trust Tier.
3. Every retro produces a Constitution delta.
4. Every agent has a named human owner.

---

## Session Scope Card

> Replaces the legacy "Governed Scope Card" — every session starts with an explicit scope declaration. If any field is blank, the session is unscoped and the agent must request clarification before acting.

| Field | Value |
|---|---|
| **Type** | {{SESSION_TYPE}} *(onboard / pre-wave / plan / build / review / ship / debrief / wave-review)* |
| **Scope** | {{SCOPE}} *(what this session WILL do — concrete deliverables)* |
| **Out of scope** | {{OUT_OF_SCOPE}} *(what this session WILL NOT touch — explicit exclusions)* |
| **Inputs** | {{INPUTS}} *(signed artifacts the agent reads — paths)* |
| **Expected outputs** | {{OUTPUTS}} *(artifacts the agent writes — paths + templates)* |
| **End rule** | {{END_RULE}} *(when the session is DONE — e.g. "PR opened", "PLAN.md signed", "Stage-1 PASS")* |
| **Embedded gates** | {{EMBEDDED_GATES}} *(which gates fire DURING this session — e.g. "Gate 3 Design Approval, Gate 4 Trust Tier")* |

---

## Active Belief

{{BELIEF_SUMMARY}}

**Disproof condition:** {{DISPROOF_CONDITION}}

---

## Trust Tiers (this Wave)

{{TRUST_TIER_TABLE}}

---

## Your constraints

- You are operating at **{{TRUST_TIER}}** on surface `{{TASK_SURFACE}}`.
- {{TIER_CONSTRAINT_LINE}}
- Your output must include: ceremony skill(s) run, Trust Tier of surface touched, a diff (never prose-describing-code), and self-review against the product-Constitution.
- You may not merge to a protected branch. Merge is a human action.
- If you encounter a surface not listed in TRUST_TIER.md, HARD STOP and escalate to PE.
- If your work would exceed the **Out of scope** boundary, HARD STOP and request a new session scope.

---

## Commands

- **signal-onboard**: Phase -1 onboarding. Runs once per product. Gathers Product DNA.
- **signal-init**: Phase 0 initialization. Creates repo scaffold, CONSTITUTION.md, agent roster.
- **signal-pre-wave**: Phase 1 pre-wave. Signs BELIEF.md, locks Expectation Map.
- **signal-plan**: Phase 2 planning. Produces PLAN.md with tasks, gates, metrics.
- **signal-build**: Phase 3 build. Runs Build×N parallel agents, tracks progress.
- **signal-review**: Phase 4 review. Runs validators, generates QA evidence pack.
- **signal-ship**: Phase 5 ship. Merges, tags, emits CREDITS.md.
- **signal-wave-review**: Phase 6 wave review. Compares actuals vs Expectation Map.
- **signal-debrief**: Phase 7 retrospective. Captures wins, misses, belief updates.

---

## Skills

- **belief-seed-generation**: Generates initial Belief candidates from product context and stakeholder input.
- **context**: Gathers and synthesizes context for the current task from available sources.
- **design**: The canonical visual system for every SignalOS artifact — decks, static PDFs, the Blueprint, governance .docx, and the Operating Model deck. Synthesised from McKinsey pyramid discipline, Apple/Linear restraint, Stripe/IBM Plex precision, and Tufte data honesty.
- **existing-product-kit**: Extracts product surface, metrics, and stakeholder map from an existing product.
- **memory**: Run before every ticket in Phase 3 Build. Searches memory, surfaces prior context.
- **product-surface-mapping**: Maps the product surface area for scope and dependency analysis.
- **review**: Structured code and artifact review following SignalOS quality standards.
- **stakeholder-interview**: Guides structured stakeholder interviews for product discovery.

---

## Hooks

The five enforcement hooks guard protocol integrity across the workflow:

- **pre-commit**: Anti-Shortcut Enforcement Hook — blocks commits without tests, TODOs without DEFER comments, and direct commits to main/master.
- **session-start**: Session Initialization + Fail-Hard Default Enforcement — validates Constitution presence, hash integrity, gate sequence validity, and preamble completeness.
- **pre-merge**: Pre-Merge Validation — enforces Integration Checkpoint completion, PR checklist, and decision DNA logging before any worktree merges.
- **pre-deploy**: Pre-Deploy Health Check — verifies Smoke Pack baseline, analytics instrumentation, and rollback readiness before DevOps deploy.
- **post-retro**: Post-Retro Finalization — updates Constitution hashes, harvests Gotchas, seeds next Belief, and validates Debrief completeness.
