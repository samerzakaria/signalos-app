---
description: "Persistent AI memory: put/search/list/prune/export/upgrade against .signalos/brain/index.jsonl with pure-Python BM25."
---

# /signalos brain — Knowledge Brain (W9, AMD-CORE-030)

**Phase:** execution  
**AMD:** AMD-CORE-030  
**Wave:** W9

## Purpose
Persistent AI memory index with BM25 search. Stores artifacts, decisions, QA evidence, and session notes in `.signalos/brain/index.jsonl`. No runtime third-party dependencies — pure-Python BM25.

## Subcommands

| Subcommand | Description |
|---|---|
| `put <content>` | Add an entry to the brain index |
| `search <query>` | BM25 search; returns top_n ranked entries |
| `list` | List all active entries, optionally filtered |
| `prune <id>` | Soft-delete an entry (tombstone record) |
| `export --out <path>` | Export active entries to portable JSONL |
| `upgrade --embeddings` | Opt-in Anthropic embeddings (falls back to BM25) |

## Flags — put
- `--source PATH` — source file path
- `--gate G0–G5` — associated gate
- `--wave NN` — associated wave
- `--product-id ID` — product namespace (default: core)
- `--type artifact|decision|qa|session|note`
- `--weight FLOAT` — relevance weight (default: 1.0)
- `--repo-root PATH`
- `--json` — JSON output

## Flags — search
- `--top N` — number of results (default: 5)
- `--wave NN`, `--gate G`, `--type TYPE` — pre-filters
- `--repo-root PATH`, `--json`

## Entry schema
```json
{
  "id": "brain-001",
  "product_id": "core",
  "gate": "G2",
  "wave": "09",
  "type": "artifact",
  "content": "...",
  "source_path": "core/strategy/PO_BRIEF.md",
  "ts": "2026-05-01T00:00:00Z",
  "weight": 1.0,
  "embedding": []
}
```

## Storage
`.signalos/brain/index.jsonl` — append-only; pruned entries get a tombstone record.

## Exit codes
- `0` — success
- `1` — entry not found (prune) or missing required flag
- `2` — config / path error
