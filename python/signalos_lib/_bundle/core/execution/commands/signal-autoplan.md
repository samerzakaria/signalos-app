---
description: "Auto-generate PLAN task list from a feature description (W11, AMD-CORE-032)."
---

# /signal-autoplan — AutoPlan (W11, AMD-CORE-032)

**Phase:** execution  
**AMD:** AMD-CORE-032  
**Wave:** W11

## Purpose
Eliminates manual PLAN.tasks.yaml authoring. Parses a free-form feature description and generates a structured task list with sequential IDs, T2 tier, and 0.5 effort-day estimates.

## Subcommands

| Subcommand | Description |
|---|---|
| `generate <description> --wave W` | Parse description → structured AutoPlanTask list |
| `list --wave W` | List saved tasks for a wave |

## Storage
`.signalos/plans/autoplan-<wave>.yaml`
