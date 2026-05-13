---
description: "Set up a deployment record for a wave and stage (W12, AMD-CORE-033)."
---

# /signal-setup-deploy — Setup Deploy (W12, AMD-CORE-033)

**Phase:** execution  
**AMD:** AMD-CORE-033  
**Wave:** W12

## Purpose
Creates a deployment record marking the start of a deploy lifecycle for a given wave and stage. Records are stored append-only in the deploy index.

## Usage
`signalos signal-setup-deploy <wave> <stage> [--note N] [--json]`

## Storage
`.signalos/deploy/index.jsonl` — append-only deploy index
