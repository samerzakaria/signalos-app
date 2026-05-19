# Wave 09 — Proof Scenarios

| ID | Scenario | Pass Criteria |
|---|---|---|
| 108 | brain put + search (BM25 ranking) | `brain put` returns brain-001; `brain search` returns it ranked first |
| 109 | brain list + prune + tombstone | Pruned entry absent from `brain list`; tombstone in raw index |
| 110 | brain export produces valid JSONL | Export file has same count as `brain list`; each line parses as JSON |
| 111 | auto-ingest hook graceful skip | With no signalos on PATH, `brain-auto-ingest.sh` exits 0 |
| 112 | session-start inject (no index) | With no index, `brain-session-inject.sh` exits 0 with no output |
| 113 | signal-learn review + search | `signal-learn review` lists entries; `signal-learn search` returns BM25 results |
| 114 | embeddings upgrade — BM25 fallback | Without API key, `brain upgrade --embeddings` returns backend=bm25 |
| 115 | wiring-guard C15 WARN path | With index present and hook absent → C15 warns; hook present → C15 passes |
