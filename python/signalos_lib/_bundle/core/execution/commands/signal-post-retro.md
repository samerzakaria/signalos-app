---
description: "Run the post-retro Phase-8 closure gate for a wave."
---

# /signal-post-retro - Post-Retro Closure Gate

**Phase:** execution  
**Wave:** Phase-8 closure

## Purpose
Runs the installed `core/execution/hooks/post-retro` hook for a completed wave.
The hook blocks closure unless the wave retro artifact exists and the
Constitution delta is recorded with PO and PE signatures.

## Usage
`signalos signal-post-retro <wave-id> [--json]`

## Enforcement
Fails closed when the hook is missing, bash is unavailable, or the hook reports
a retro/Constitution delta violation.
