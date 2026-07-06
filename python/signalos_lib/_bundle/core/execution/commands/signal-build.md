---
description: "Phase 3 build. Runs Build×N parallel agents, tracks progress."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# /signal-build — Phase 3: Build (TDD loop)

Owner: Dev subagents. Execution phase.

## Your first action
Read `core/governance/Governance/SOUL-DOCUMENT.md`, `core/execution/PLAN.md`,
`core/execution/ACCEPTANCE_CRITERIA.md`, and `core/execution/TRUST_TIER.md`.
Confirm Gate 3 is signed before writing tests or implementation.

## Memory search (before every ticket)
**Before starting any ticket**, run this query against `core/governance/Governance/DECISION-DNA.md`:
> "Are there any prior decisions, Gotchas, or architectural constraints related to [ticket topic]?"

If a match is found: read it in full before writing any test.
If no match: proceed. Add a new entry after the ticket completes if an architectural decision was made.

## Context budget check
Check your current context usage before starting:
- **0–60%** → work freely
- **60–80%** → run `/compact` before starting the next ticket
- **80%+** → hard stop. Archive current state to Decision DNA. Start a fresh session with Soul Document loaded.

## TDD loop (per ticket)

1. **RED** — write a failing test. Run it. Watch it fail. Do not write implementation yet.
2. **GREEN** — write minimal code to pass. No more than needed.
3. **REFACTOR** — clean up, keep tests green, commit.
4. **Spec re-check** — before picking up the next ticket, re-read `core/execution/ACCEPTANCE_CRITERIA.md`. If specs or priorities have changed, re-scope before coding resumes.

## Feature Gate (mid-wave)
Every new idea that surfaces mid-wave must pass two questions:
1. Is it required for the current Belief?
2. Is it a safety or security concern?

If neither → add to `core/governance/Governance/BACKLOG.yaml` as `status: raw`. Do not build it now.

## Anti-shortcut enforcement
The pre-commit hook at `core/execution/hooks/pre-commit` will block any commit that:
- Has no associated test file
- Skips the PR checklist at `core/governance/Templates/pr-checklist.md`
- Contains a TODO without a DEFER comment

## Parallel subagents
Dispatch independent tasks to parallel worktrees. Each worktree = one branch = one ticket.
Format: `wave-{N}/ticket-{ID}-{short-description}`

## Integration Checkpoint (before Phase 4)

> **This is the SignalOS Integration Checkpoint** — the single named moment between parallel Build worktrees and Review where the pieces are forced to meet each other on a real integration branch. No Wave may enter Phase 4 Review without it passing. Skipping it is a protocol violation surfaced by the pre-commit hook.

After the last ticket closes and before routing to Review:
- Merge all worktrees
- Run contract tests (OpenAPI / schema diff)
- Run FE ⇄ BE smoke interactions
- Run DB migration dry-run
- Run dependency version reconciliation

Failure → route back into Phase 3.
Pass → proceed to `/signal-review`.

## Exit criteria

- [ ] Every task green in CI
- [ ] DEFER comments harvested into `core/governance/Governance/BACKLOG.yaml` as `status: raw`
- [ ] No scope creep beyond signed Expectation Map
- [ ] Integration Checkpoint passed
- [ ] Memory search was run for every ticket
- [ ] `core/execution/BUILD_EVIDENCE.md` records the red/green/build commands, results, touched files, and blockers

## Next phase
Run `/signal-review` when Integration Checkpoint passes.
