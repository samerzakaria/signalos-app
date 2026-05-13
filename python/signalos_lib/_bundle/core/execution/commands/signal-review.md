---
description: "Phase 4 review. Runs validators, generates QA evidence pack."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# /signal-review — Phase 4: Code Review

Owner: QC / reviewing agent. Execution phase.

## Your first action
Read `core/execution/Plans/wave-{N}-plan.md` and `core/execution/Plans/wave-{N}-acceptance-criteria.md`.
Confirm Integration Checkpoint passed.

## Two-stage review

**Stage A — AI code review**
- Pass 1: Spec compliance — does implementation match PLAN.md?
- Pass 2: Quality — readability, test coverage, security surface
- Apply declared Trust Tier (T1 Proceed / T2 Propose / T3 Suggest) from Decision DNA

**Stage B — Human senior review**
Review the AI's review report, not just the code. Spot what the AI missed.

## Acceptance criteria verification
Open `core/execution/Plans/wave-{N}-acceptance-criteria.md`.
For each criterion: mark ✅ PASS or ❌ FAIL with evidence.
Critical failures block merge. Minor findings → add to backlog as `status: raw`.

## QC pass
- Evidence pack: test results, coverage report, security scan output
- All findings captured in `core/governance/Governance/DECISION-DNA.md`

## Exit criteria

- [ ] No critical findings remain
- [ ] All acceptance criteria verified with evidence
- [ ] Review notes captured in Decision DNA
- [ ] Ready to ship to real users — not merely staging

## Gate 5: Quality Check
Merge is blocked on any critical finding.

## Next phase
Run `/signal-ship` when gate passes.
