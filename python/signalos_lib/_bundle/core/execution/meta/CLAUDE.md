<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# SignalOS — Execution Pillar Context

You are operating inside the **Execution Pillar** of SignalOS v1.0.

## What lives here

- `Commands/` — 10 slash-commands (`/signal-init`, `/signal-onboard`, `/signal-plan`, `/signal-build`, `/signal-review`, `/signal-ship`, `/signal-observe`, `/signal-debrief`, `/signal-pre-wave`, `/signal-wave-review`).
- `Agents/` — 10 agent prompt slots (Onboarding, Brainstorm, Plan, Build ×N, Test, Review, Worktree-Sync, Security, Release, Observability).
- `Skills/` — 8 invocable skills (belief-seed-generation, context, design, existing-product-kit, memory, product-surface-mapping, review, stakeholder-interview).
- `Hooks/` — 3 lifecycle hooks (session-start, pre-commit, pre-merge).
- `Build/` — Worktree management and build orchestration scripts.
- `Meta/` — This file and extension guidelines.

## Rules to follow

1. **Respect gates.** Never skip a gate. If a prerequisite artifact is missing, stop and report — do not fabricate it.
2. **Respect trust tiers.** Check the declared trust tier before acting. T1 = advisory only, T2 = act with review, T3 = autonomous within scope.
3. **Respect the Constitution.** `core/governance/Governance/CONSTITUTION.md` is the law. If a command or skill contradicts the Constitution, the Constitution wins.
4. **Handoff, don't hoard.** Every agent action ends with an explicit handoff to the next phase or human reviewer. Write the HAND entry.
5. **Audit trail.** Every significant action must produce a traceable artifact under `core/governance/Governance/Audit/`.
