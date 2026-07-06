---
description: "Phase 1 pre-wave. Signs BELIEF.md, locks Expectation Map."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# /signal-pre-wave — Phase 1: Pre-Wave

Owner: Product Owner. SIGNAL phase.

## Your first action
Read `core/governance/Governance/SOUL-DOCUMENT.md` and `core/governance/Governance/CONSTITUTION.md`.
Then read the latest retrospective entry if one exists in `core/governance/Governance/RETROSPECTIVE.md`.

## Scale check
Before doing anything else, declare the Scale Track for this wave:
- **Quick** — bug fix or small enhancement, under 4 hours. Skip to `/signal-plan` directly.
- **Wave** — standard feature delivery, 1–14 days. Continue with this phase.
- **Campaign** — multi-wave initiative. Run Pre-Wave once, then repeat Plan→Ship for each sub-wave.

If Quick: summarise the Belief in one sentence and run `/signal-plan`.

## Mandatory actions (Wave and Campaign only)

1. **Harvest DEFER comments** from last wave's code (`git grep -n "DEFER"`)
2. **State the Belief** = Problem + Bet + Signal threshold (measurable, falsifiable)
3. **Score candidate beliefs** using Bet Score = (Risk × Impact) / Test Cost
4. **Write Expectation Map** — what client expects vs what is being built; make the delta explicit
5. **Fill Role Activation Card** — Active / AI-heavy / Minimal / Deferred / External per agent seat (see `docs/Team-Charters/AGENTIC_TEAM_CHARTER.md` for the 10-seat roster)
6. **Run Discovery Brief** if a client session happened — use template at `core/strategy/Templates/discovery-brief-template.md`

## Exit criteria

- [ ] One falsifiable Belief chosen and scored
- [ ] Expectation Map written and ready for client signature
- [ ] Smallest testable build defined
- [ ] Measurable signal threshold documented
- [ ] Scale Track declared

## Gate 2: Expectation Map signed
Client must sign Expectation Map before any code is written.
No exceptions. "Building the wrong thing perfectly" is prevented here.

## Next phase
Run `/signal-plan` when client has signed.
