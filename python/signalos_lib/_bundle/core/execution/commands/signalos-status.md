---
description: "Render the Wave status card — gates, tasks, belief, and next action."
---

<!-- SignalOS Core v2.1 — /signalos-status command spec (AMD-CORE-008). -->

# /signalos-status — Wave Status Card

Owner: any role. Observability surface — **not a Gate**.

## What it is

`signalos status` renders a structured ASCII status card for the current
Wave. It reads all state from disk (no LLM call, no network, stdlib only)
and prints:

- **Wave ID and current delivery phase** (ONBOARDING / BELIEF / PLANNING /
  DESIGN / BUILD / REVIEW / DONE)
- **First line of the problem statement** from `BELIEF.md`
- **Scale track** and **delivery mode**
- **Gate status** (G0–G5, ✓ = passed, ○ = open)
- **Active tasks** from `.signalos/worktree-state.json` with trust tier
  and current status
- **Next blocking action** for the appropriate role

## Card format

```
╔══════════════════════════════════════════════════════════╗
║  SignalOS · Wave W2.1 · BUILD                            ║
╠══════════════════════════════════════════════════════════╣
║  Belief  Users can track delivery velocity without ...   ║
║  Track   wave · Mode: fresh-wave                         ║
╠══════════════════════════════════════════════════════════╣
║  GATES                                                   ║
║  ✓ G0 Onboarding  ✓ G1 Belief     ✓ G2 Planning          ║
║  ✓ G3 Design      ✓ G4 Build      ○ G5 Review            ║
╠══════════════════════════════════════════════════════════╣
║  TASKS                           TIER    STATUS          ║
║  ⟳  T-001                        T1      ACTIVE          ║
║  ⏸  T-002                        T2      PAUSED          ║
║  ✓  T-003                        T1      COMPLETED       ║
╠══════════════════════════════════════════════════════════╣
║  NEXT ACTION                                             ║
║  PE → signalos pause resume T-002                        ║
╚══════════════════════════════════════════════════════════╝
```

Task status icons:
- `⟳` — running / active
- `⏸` — paused (T2 awaiting human review)
- `✓` — completed or merged
- `✗` — failed

## CLI surface

```bash
# Render status from current directory (walks up to find .signalos/)
signalos status

# Render status for a specific repo root
signalos status --repo-root /path/to/product-repo
```

## Arguments

| Flag | Required | Purpose |
|---|---|---|
| `--repo-root <path>` | no | Override repo root. Default: walk up from cwd to find `.signalos/`. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Always — status display is advisory, never blocks. |

## Gate detection logic

| Gate | File checked | Condition |
|---|---|---|
| G0 | `core/governance/Governance/SOUL-DOCUMENT.md` | Exists and is not a blank template |
| G1 | `core/strategy/BELIEF.md` or `BELIEF_LITE.md` | Either file exists |
| G2 | `core/strategy/EXPECTATION_MAP.md` | File exists |
| G3 | `core/strategy/DESIGN_NOTE.md` | Design note exists for status; signing validator requires Design Note, Plan, and Acceptance Criteria |
| G4 | `core/execution/BUILD_EVIDENCE.md` | Build evidence exists for status; signing validator requires both Build Evidence and Trust Tier |
| G5 | `core/governance/QUALITY_CHECK.md` | File exists |

## Next action logic

| Condition | Role | Action |
|---|---|---|
| Any task is PAUSED | PE | `signalos pause resume <step-id>` |
| Any task FAILED | PE | `signalos harness status <step-id>` |
| All tasks done, G5 open | QA | sign `QUALITY_CHECK.md` |
| G0 not passed | PO | `signalos signal-onboard` |
| G1 not passed | PO | `signalos signal-pre-wave` |
| No blocking condition | — | No blocking action |

## Data sources

| Data | Source |
|---|---|
| Tasks / status | `.signalos/worktree-state.json` |
| Wave ID | `.signalos/worktree-state.json` → `wave_id` field |
| Belief line | First non-empty line after `## Problem` in `BELIEF.md` |
| Scale track | `scale_track:` front-matter in `BELIEF.md` or `BELIEF_LITE.md` |
| Delivery mode | `delivery_mode:` in `SOUL-DOCUMENT.md` or `CONSTITUTION.md` |
| Gate status | File existence checks (see table above) |

All reads are local filesystem — no network call, no LLM. Exit is always 0.
