---
description: "Human-facing Brain interface: paginated review, BM25 search, prune stale entries, portable export."
---

# /signal-learn — Human-facing Brain Interface (W9, AMD-CORE-030)

**Phase:** execution  
**AMD:** AMD-CORE-030  
**Wave:** W9

## Purpose
Human-readable interface to the Knowledge Brain. Provides paginated review, BM25 search, stale-entry pruning, and portable export.

## Subcommands

| Subcommand | Description |
|---|---|
| `review` | Paginated list of brain entries by wave/gate |
| `search <query>` | BM25 search with human-readable output |
| `prune <id>` | Soft-delete a stale entry |
| `export --out <path>` | Export active entries to portable JSONL bundle |

## Flags — review
- `--wave NN` — filter by wave
- `--gate G0–G5` — filter by gate
- `--page-size N` — entries per page (default: 10)
- `--repo-root PATH`, `--json`

## Flags — search
- `--top N` — number of results (default: 5)
- `--repo-root PATH`, `--json`

## Flags — prune
- `entry_id` — brain-NNN id
- `--repo-root PATH`, `--json`

## Flags — export
- `--out PATH` — output .jsonl file (required)
- `--repo-root PATH`, `--json`

## Exit codes
- `0` — success
- `1` — entry not found / missing flag
