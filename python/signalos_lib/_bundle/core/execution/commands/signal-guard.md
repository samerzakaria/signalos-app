---
description: "Check if a directory is frozen — gate that blocks writes when frozen (W14, AMD-CORE-035)."
---

# /signal-guard — Guard Check (W14, AMD-CORE-035)

**Phase:** execution  
**AMD:** AMD-CORE-035  
**Wave:** W14

## Purpose
Checks whether a directory has an active freeze record. Exit code 1 if frozen (blocks the operation), exit code 0 if clear.

## Usage
`signalos signal-guard <target> [--json]`
