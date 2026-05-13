---
description: "Remove a directory freeze — allow writes again (W14, AMD-CORE-035)."
---

# /signal-unfreeze — Unfreeze Directory (W14, AMD-CORE-035)

**Phase:** execution  
**AMD:** AMD-CORE-035  
**Wave:** W14

## Purpose
Transitions a frozen directory's status to `unfrozen`, allowing writes to proceed.

## Usage
`signalos signal-unfreeze <target> [--json]`
