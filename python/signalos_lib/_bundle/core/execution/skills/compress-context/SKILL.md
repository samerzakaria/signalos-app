<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
---
name: compress-context
description: "Rule-based context-window compressor (AMD-CORE-005). Use before handing off a long session transcript to a new editor or sub-agent, or when the in-context window is about to overflow. Compression is rule-based (regex + heuristics only, no LLM call). Disk-truth (journal.jsonl, metrics.jsonl, AUDIT_TRAIL.jsonl) is never touched. Read this before editing cli/signalos_lib/context.py or core/execution/hooks/pre-session-compress/."
---

<!-- SignalOS Core v1.3 — W1.3 compress-context skill. T2 for the layers; T3 for the never-compress enforcement. -->

# Skill — compress-context

## One-liner

SignalOS compresses the **in-context projection** of a session transcript — never the on-disk audit trail — using four deterministic layers. Rule-based, stdlib-only, Python 3.11+.

## When to invoke

- Handing a long session off to a new editor or sub-agent and the transcript is about to blow the context window.
- A batch job is about to ingest dozens of prior steps and wants a summarized view without re-running the Anthropic API.
- The `pre-session-compress` hook has fired and is asking for the canonical compression output.

Do **not** invoke this skill when:
- The input is `.signalos/sessions/<id>/journal.jsonl` or `metrics.jsonl`, or `.signalos/AUDIT_TRAIL.jsonl`. The shell guard `core/execution/hooks/pre-session-compress/pre-session-compress.sh` and the Python `_reject_disk_truth_input()` helper both refuse these.
- You need a semantic summary. This skill is rule-based (regex + first-sentence heuristics). For model-based summarization, wait for the v1.4 candidate that was deferred from W1.3.

## The four layers

The compressor walks the transcript from most-recent to least-recent. Age `1` is the last turn, age `2` is the second-to-last, and so on.

| Turn age | Layer | What remains of the turn |
|---|---|---|
| 1–2 | **VERBATIM** | The original content, unchanged (secret scrubbing still applies). |
| 3–10 | **SUMMARY** | First sentence + any `##` / `###` heading line + any line starting with `Gate:` / `Result:` / `Outcome:` / `Verdict:` / `TODO` / `DONE` (case-insensitive). Truncated to 400 chars. |
| 11+ | **HEADLINE** | First sentence only, truncated to 120 chars. |
| any age, content ≥ 8 KB | **DISCARD** | Replaced with `[TOOL-OUTPUT DISCARDED — N bytes]`. Size measured on the original UTF-8 byte length. |
| any age, content matches `redact.py` secret patterns | **DISCARD** | Secrets replaced with `[REDACTED]`. If the whole turn was secret, the turn becomes `[REDACTED]`. |

The compressor emits JSONL identical in shape to the input, with one added field `_compression_layer` recording which layer fired.

## The disk-truth invariant

**Disk audit trail is NEVER compressed.** The module refuses three path shapes at every public entrypoint:

```
.signalos/sessions/<session-id>/journal.jsonl
.signalos/sessions/<session-id>/metrics.jsonl
.signalos/AUDIT_TRAIL.jsonl
```

Rejection is layered:

1. `core/execution/hooks/pre-session-compress/pre-session-compress.sh` refuses at the shell-guard level (exit code 1).
2. `cli/signalos_lib/context.py::_reject_disk_truth_input()` refuses at the Python level (raises `DiskTruthRefused`; CLI returns exit code 2).

Either layer is sufficient on its own. Both fire together so a bypass of one is caught by the other. See AMD-CORE-001 for the append-only journal invariant this protects.

## The never-compress allowlist (T3)

Certain transcript-metadata keys are **pass-through verbatim** regardless of layer. The Python allowlist is hardcoded in `_never_compress()`:

- `gate_exit_criteria`
- `trust_tier_sheet`
- `active_constitution`
- `pending_amendments`

A transcript's leading `{"role":"metadata", ...}` record is inspected: if any key shadows an allowlisted name (e.g. `compressed_gate_exit_criteria`), the compressor raises `NeverCompressViolation` (CLI exit code 3) and writes nothing.

This enforcement is **T3**. A bug here could hide Gate criteria from an agent — changing the list requires an AMD-CORE amendment, PO + PE co-sign, and a new proof scenario.

## CLI surface

```bash
# Compress a transcript, report sizes to stdout.
signalos context compress .signalos/transcripts/session-42.jsonl

# Compress to a file.
signalos context compress input.jsonl --out compressed.jsonl

# Expand a scope id to its byte-identical on-disk content.
signalos context expand --scope W1.3
signalos context expand --scope belief-retry-budget
signalos context expand --scope AMD-CORE-005
```

`signalos context compress` prints a summary dict:

```json
{
  "compressed_bytes": 2048,
  "layers": {
    "discarded_turns": 0,
    "headline_turns": 40,
    "summary_turns": 8,
    "verbatim_turns": 2
  },
  "original_bytes": 8192,
  "ratio": 0.75
}
```

`ratio` is `1 - compressed/original`, so `0.75` means "compressed file is 25% the size of the original".

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | Usage error (missing arg, unknown subcommand, scope not found). |
| 2 | Disk-truth rejection. The input path matched a journal/metrics/audit pattern. |
| 3 | Never-compress allowlist violation. |

## Related

- `cli/signalos_lib/context.py` — the compressor implementation (stdlib-only).
- `cli/signalos_lib/commands/context.py` — the CLI argparse wrapper.
- `core/execution/hooks/pre-session-compress/pre-session-compress.sh` — the shell-side disk-truth guard.
- `core/execution/commands/context-expand.md` — operator doc for `signalos context expand`.
- `core/governance/Retro/AMENDMENTS.md` — AMD-CORE-005 (context compression ratified).
- `proof/scenarios/36_compression_ratio.sh` through `39_decompression_roundtrip.sh` — ratified behaviour, run from the repo root.

## Prior art

The four-layer compression concept is borrowed from `a5c-ai/babysitter` (MIT). Babysitter mutates live Node buffers at conversation boundaries; Core summarises markdown transcripts on disk. The implementation is entirely different: no source code was copied. Attribution is tracked in `core/CREDITS.md`.
