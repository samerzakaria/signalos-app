---
description: "Record a DevEx metric — TTHW (Time To Hello World) or custom (W13, AMD-CORE-034)."
---

# /signal-devex — DevEx Measure (W13, AMD-CORE-034)

**Phase:** execution  
**AMD:** AMD-CORE-034  
**Wave:** W13

## Purpose
Records developer experience metrics, primarily Time To Hello World (TTHW) in seconds. Provides a historical trail for DX regressions.

## Usage
`signalos signal-devex TTHW 120.5 --wave W [--note N] [--json]`

## Storage
`.signalos/devex/metrics.jsonl` — append-only metrics index
