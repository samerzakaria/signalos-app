---
description: "Enable/disable/check careful mode — extra caution flag for high-risk operations (W14, AMD-CORE-035)."
---

# /signal-careful — Careful Mode (W14, AMD-CORE-035)

**Phase:** execution  
**AMD:** AMD-CORE-035  
**Wave:** W14

## Purpose
Sets a `careful.flag` that signals to all tooling that extra caution is active. Use before high-stakes operations (production deploys, schema migrations, bulk deletions).

## Subcommands
- `enable [--note N]` — activate careful mode
- `disable` — deactivate careful mode
- `status` — check current careful mode state

## Storage
`.signalos/safety/careful.flag`
