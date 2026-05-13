---
description: "Lock a directory against writes by creating a freeze record (W14, AMD-CORE-035)."
---

# /signal-freeze — Freeze Directory (W14, AMD-CORE-035)

**Phase:** execution  
**AMD:** AMD-CORE-035  
**Wave:** W14

## Purpose
Creates a freeze record for a target directory, signalling that it is locked against changes. Use `signal-guard` to enforce the freeze gate before writes.

## Usage
`signalos signal-freeze <target> --wave W [--note N] [--json]`

## Storage
`.signalos/safety/freeze/<hash>.json`
