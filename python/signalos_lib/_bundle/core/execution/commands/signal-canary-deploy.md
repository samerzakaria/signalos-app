---
description: "Post-deploy canary check: verify deploy records exist for a wave (W12, AMD-CORE-033)."
---

# /signal-canary-deploy — Canary Deploy Check (W12, AMD-CORE-033)

**Phase:** execution  
**AMD:** AMD-CORE-033  
**Wave:** W12

## Purpose
Checks whether deploy records exist for a given wave, acting as a post-deploy health canary.

## Usage
`signalos signal-canary-deploy --wave W [--json]`
