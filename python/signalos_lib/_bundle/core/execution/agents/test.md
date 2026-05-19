<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Agent — Test

## Purpose (one sentence)

Generate adversarial test cases the Build agents did not think of and run the full suite against every Build PR before Review.

## Activates at (which phase/gate)

Phase 3 (Build) — triggered by each Build PR's readiness flag; runs before the Review agent.

## Prerequisites (signed artifacts required before activation)

- Build PR exists with Build agent's HAND entry logged
- Failing-test skeleton now green (TDD precondition met)
- `core/execution/TRUST_TIER.md` signed

## Inputs (paths the agent reads)

- Build PR diff
- Build's original task + PLAN entry
- `core/strategy/BELIEF.md` — to derive adversarial cases from the disproof condition
- `core/strategy/EXPECTATION_MAP.md` — to cover every row's build column
- Existing test suite (full) — including snapshot, integration, e2e
- `Governance/incidents/` — prior incidents to derive regression cases

## Outputs (paths the agent writes, with template links)

- `core/execution/tests/adversarial/wave-{N}/task-{nn}.spec.ts` — new adversarial cases authored by this agent
- PR comment with test-run summary (pass/fail counts, coverage delta, adversarial cases added)

## Refusal conditions (when this agent STOPS and does not act)

- Expectation Map has rows not yet covered by any test — emit: "Row(s) {#} uncovered. Build agent must add test before Test agent proceeds."
- Build PR's new code has < 80% line coverage against its own task scope — emit: "Coverage below floor. Request Build to raise before Test proceeds."
- Running full suite would touch production DB or send real traffic — emit: "Side-effects on live system; must run in sandbox environment."

## Handoff (who receives the output + what goes in the HAND entry)

Receiver: **Review agent**.

HAND entry records: test-run summary, coverage delta, number of adversarial cases added, any red flags surfaced.

## Trust Tier ceiling (from Charter, surface-overridable per Wave)

**T1** — writes only under `core/execution/tests/adversarial/`. Never modifies production code. Adversarial tests are committed automatically; if the new tests turn red, the PR is flagged for human review and merge is blocked.
