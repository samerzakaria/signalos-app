---
description: "Plan DevEx work in three modes: EXPANSION, POLISH, TRIAGE (W13, AMD-CORE-034)."
---

# /signal-devex-plan — DevEx Plan (W13, AMD-CORE-034)

**Phase:** execution  
**AMD:** AMD-CORE-034  
**Wave:** W13

## Purpose
Structures DevEx planning into three focused modes: EXPANSION (new capability), POLISH (quality), and TRIAGE (fire-fighting). Each mode generates a targeted work item list.

## Usage
`signalos signal-devex-plan --mode EXPANSION|POLISH|TRIAGE --wave W [--json]`

## Modes
- **EXPANSION** — new onboarding flows, API discoverability, quickstart guides
- **POLISH** — error message clarity, CLI output formatting, docs consistency  
- **TRIAGE** — blocking bugs, P0 friction audit, hotfix pipeline

## Storage
`.signalos/devex/plans.jsonl` — append-only DevEx plan index
