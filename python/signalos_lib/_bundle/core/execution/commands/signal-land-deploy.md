---
description: "Mark a deployment as landed (W12, AMD-CORE-033)."
---

# /signal-land-deploy — Land Deploy (W12, AMD-CORE-033)

**Phase:** execution  
**AMD:** AMD-CORE-033  
**Wave:** W12

## Purpose
Transitions a deployment record from `setup` → `landed`, confirming the deploy completed successfully.

## Usage
`signalos signal-land-deploy <deploy_id> [--json]`

## Storage
`.signalos/deploy/index.jsonl`
