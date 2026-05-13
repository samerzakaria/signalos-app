---
description: "Cross-product retrospective: query brain index for insights across waves (W13, AMD-CORE-034)."
---

# /signal-retro-global — Global Retro (W13, AMD-CORE-034)

**Phase:** execution  
**AMD:** AMD-CORE-034  
**Wave:** W13

## Purpose
Queries the SignalOS brain index for cross-wave retrospective insights. Falls back gracefully if brain index is absent.

## Usage
`signalos signal-retro-global "query string" --wave W [--json]`

## Storage
Reads `.signalos/brain/index.jsonl` (read-only)
