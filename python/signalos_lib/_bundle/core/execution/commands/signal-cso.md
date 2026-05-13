---
description: "Security Chief Officer — OWASP+STRIDE threat model, canary tokens, injection scan (W10, AMD-CORE-031)."
---

# /signal-cso — Security Chief Officer (W10, AMD-CORE-031)

**Phase:** execution  
**AMD:** AMD-CORE-031  
**Wave:** W10

## Purpose
Native security tooling: OWASP+STRIDE threat modelling, canary token deployment, and injection risk scanning without any external security scanner dependency.

## Subcommands

| Subcommand | Description |
|---|---|
| `scan <surface> --wave <W>` | Generate OWASP+STRIDE threat model for a surface |
| `canary plant [--label L]` | Plant a UUID canary token |
| `canary check [--label L]` | Check if canary token exists |
| `threats list [--wave W] [--category C]` | List threat entries |
| `threats export --out <path>` | Export threat index to JSONL |
| `inject-scan <path>` | Scan a file for injection risk patterns |

## Storage
`.signalos/security/threats.jsonl` — append-only threat index  
`.signalos/security/canary-<label>.json` — canary token files
