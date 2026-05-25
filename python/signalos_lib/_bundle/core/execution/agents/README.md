<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Agent Prompt Slots

`Folder: core/execution/agents/ · Authored by: PE · Signed at: Gate 0 · Cadence: one file per agent seat, versioned with SignalOS`

> One `.md` file per agent seat. Each file is the **system prompt + activation contract** for that seat — the thing a Claude instance is initialized with when it fills the role for a Wave. The Charter (`docs/Team-Charters/AGENTIC_TEAM_CHARTER.md`) defines **what** each agent does; the files in this folder define **exactly how** each agent activates, what it reads, what it refuses, and how it hands off.

---

## One file per seat

| # | File | Seat | Half | Owning human | Default Trust Tier |
|---|---|---|---|---|---|
| 1 | `onboarding.md` | Onboarding | Discovery | PO | T2 |
| 2 | `brainstorm.md` | Brainstorm | Discovery | PO | T1 |
| 3 | `plan.md` | Plan | Delivery | PE | T1 |
| 4 | `build.md` | Build ×N | Delivery | PE | T2 (T3 only suggests) |
| 5 | `test.md` | Test | Delivery | QA | T1 |
| 6 | `review.md` | Review | Delivery | QA | T2 |
| 7 | `worktree-sync.md` | Worktree-Sync | Delivery | PE | T1 |
| 8 | `security.md` | Security | Delivery | PE | T2 (advisory) |
| 9 | `release.md` | Release | Delivery | DevOps | T3 (advisory) |
| 10 | `observability.md` | Observability | Delivery | PO | T1 |

---

## File shape (contract)

Every agent prompt file MUST follow this structure:

```
# Agent — {Seat Name}

## Purpose (one sentence)
## Expertise frame (senior role and quality bar the agent must inhabit)
## Activates at (which phase/gate)
## Prerequisites (signed artifacts required before activation)
## Inputs (paths the agent reads)
## Outputs (paths the agent writes, with template links)
## Success criteria (what must be true before claiming done)
## Evidence required (proof paths, logs, screenshots, diffs, or blocker records)
## Forbidden rules (hard walls; violations are never accepted)
## Repair/rework policy (retry, regenerate, or escalate behavior)
## Refusal conditions (when this agent STOPS and does not act)
## Handoff (who receives the output + what goes in the HAND entry)
## Trust Tier ceiling (from Charter, surface-overridable per Wave)
```

Missing sections fail `artifact-shape-guard` (Enforcement-Layer 3) and block the SignalOS release.

---

## Why the separation from the Charter

- The Charter is **narrative** — readable by humans, covers purpose/authority/escalation.
- These files are **operational** — read by the agent model at activation, machine-parseable, strictly-shaped.
- Changing authority or concurrency edits the Charter; changing activation logic or refusal conditions edits the file here.
