---
name: stakeholder-interview
description: "Guides structured stakeholder interviews for product discovery."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Skill — stakeholder-interview

## One-liner

Structured interview script for the PO to run against each stakeholder during onboarding; emits a Discovery Brief per session that the Onboarding agent reads for signal.

## Trigger phrase

`stakeholder-interview` — invoked by the Onboarding agent during `existing-product-kit`, or manually by the PO before `/signal-onboard` runs.

## Owning agent

**Onboarding agent** (`core/execution/agents/onboarding.md`) — but the interview is **run by the PO as a human**. This skill is a script, not an autonomous action.

## Inputs

- Stakeholder name and role.
- 30–60 minute slot with the stakeholder.
- Access to `core/strategy/Templates/discovery-brief-template.md`.

## Outputs

- `core/strategy/discovery-briefs/wave-0-session-{S}.md` — one Discovery Brief per interview.
- Optional verbatim transcript at `Governance/conversations/wave-0-session-{S}-{tag}.md` (recommended; the Discovery Brief is the distillation).

## Interview script (fixed shape)

Run the questions in order. Write the stakeholder's words verbatim where possible.

1. **Framing (2 min).** "This is an interview, not a meeting. I'll ask, you answer. I'll type your words."
2. **What the product does (3 min).** "In one sentence, what does {Product Name} do?" — If the answer exceeds one sentence, the product has a soul problem, not a feature problem.
3. **Who it's for (3 min).** "Who is the single most important user, today? Not the ideal future user — today." — Pluralities here are a red flag; log them.
4. **What's broken (8 min).** "Name three things about this product that are currently broken, painful, or lying to us. Rank them." — Listen for hesitations.
5. **Last-resort decisions (5 min).** "What's the last decision you made that you'd take back if you could?" — This is where closed-decisions appear that don't want to be closed.
6. **Guardrails (5 min).** "What must we never break? Not 'try not to break' — never." — This feeds permanently-T3 and closed-decisions.
7. **Measurement (5 min).** "If we shipped a change this week, how would you know whether it worked?" — This is where Signal Window candidates emerge.
8. **Surprise (5 min).** "What would surprise you if we learned it about your users this month?" — This is where Brainstorm-ready Beliefs seed themselves.
9. **Close (2 min).** "Anything I should have asked and didn't?"

## Refusal conditions

- Stakeholder declines to speak verbatim → log the refusal; do not paraphrase.
- Stakeholder is clearly describing a different product than the codebase reveals → log the contradiction; do **not** resolve it in-interview — that is Onboarding-agent + PO work later.
- Fewer than 3 stakeholders interviewed before `/signal-onboard` — the Onboarding agent will still run, but flag coverage as `thin` in the audit report.

## Quality bar

One Discovery Brief per interview. Every Brief's Field 2 (*What surprised you?*) must be filled — if nothing surprised the PO, the interview was not run well and should be re-done.

## Handoff

Each Brief lands at `core/strategy/discovery-briefs/wave-0-session-{S}.md`. The Onboarding agent reads every `wave-0-session-*.md` during `product-surface-mapping` and `belief-seed-generation`.
