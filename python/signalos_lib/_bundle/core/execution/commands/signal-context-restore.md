---
description: "Checkpoint save/restore and doc drift detection (W11, AMD-CORE-032)."
---

# /signal-context-restore — Checkpoint & Doc Drift (W11, AMD-CORE-032)

**Phase:** execution  
**AMD:** AMD-CORE-032  
**Wave:** W11

## Purpose
Makes context compaction reversible (checkpoints) and surfaces stale documentation before it misleads future waves (doc drift).

## Subcommands

| Subcommand | Description |
|---|---|
| `save --wave W --label L --context PATH` | Save a context checkpoint |
| `list [--wave W]` | List checkpoints |
| `restore <id> --out PATH` | Restore a checkpoint to output path |
| `drift [--docs-dir D] [--max-age-days N]` | Detect stale documentation |

## Storage
`.signalos/checkpoints/index.jsonl` — append-only checkpoint index  
`.signalos/checkpoints/<id>/context.md` — per-checkpoint context snapshot
