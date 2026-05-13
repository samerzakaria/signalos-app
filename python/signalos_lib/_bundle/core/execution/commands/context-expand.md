---
description: "Expand compressed scope IDs back to canonical on-disk content."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.3 — /context-expand command spec (AMD-CORE-005). -->

# /context-expand — Fetch byte-identical on-disk content for a compressed scope

Owner: PE. Operational lever — **not a Gate**.

## What it is

`context-expand` is the decompression-on-demand complement to the four-layer compressor documented in `core/execution/skills/compress-context/SKILL.md`. When the compressor has reduced a prior Wave, belief, or amendment to a summary or headline, an agent mid-session can call:

```bash
signalos context expand --scope <wave-id|belief-id|amendment-id>
```

and receive the **original on-disk bytes** back — never a reconstruction from the summary. The summary is a read-side convenience; the file on disk is still authoritative.

## What it is NOT

- **Not a cache layer.** No stored state; every call re-reads the file from disk.
- **Not a rewrite.** The output is byte-identical to the source file. No formatting, no banners (unless the Wave directory contains multiple files — see Scope resolution below), no trailing-newline massage.
- **Not a search.** The scope id must resolve to exactly one entity; otherwise you get a resolution error.

## CLI surface

```bash
# Fetch a Wave's full docs directory.
signalos context expand --scope W1.3

# Fetch a belief by its belief-id (filename without .md).
signalos context expand --scope belief-retry-budget

# Fetch an amendment row by AMD#.
signalos context expand --scope AMD-CORE-005
```

The command writes the resolved content to stdout with no additional framing. Redirect to a file to capture it:

```bash
signalos context expand --scope W1.3 > /tmp/w13-snapshot.md
```

## Arguments

| Flag | Required | Purpose |
|---|---|---|
| `--scope <id>` | yes | Wave id, belief id, or amendment id (AMD-CORE-*). |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Scope resolved; bytes written to stdout. |
| 1 | Usage error (missing `--scope`, scope cannot be resolved, not inside a SignalOS repo). |

(There is no exit code 2 or 3 for `expand`: this subcommand is read-only and never interacts with the disk-truth invariants.)

## Scope resolution

Lookup proceeds in the following order; the first hit wins:

| Order | Shape | Path pattern | Returns |
|---|---|---|---|
| 1 | Wave id | `core/governance/Retro/waves/<scope>/` | The single file if one exists; otherwise all files concatenated, each preceded by a `<!-- <rel-path> -->` banner, in filename-sorted order. |
| 2 | Belief id | `core/governance/Beliefs/<scope>.md` | The exact bytes of the file. |
| 3 | Amendment id | Row in `core/governance/Retro/AMENDMENTS.md` starting with `| <scope> ` | The full markdown table row plus a trailing newline. |

If the scope matches none of these, the command exits 1 with a descriptive stderr message.

## Why the output is byte-identical to disk

The compressor in `cli/signalos_lib/context.py` never writes to a governance or retro file. It only reads a transcript and emits a summary. `expand_scope()` walks back to the **original file** and returns its bytes verbatim — so an agent that was handed a HEADLINE of a prior belief can always recover the full text, and a diff of the expansion against the source will show zero bytes changed.

This is the W1.3 instantiation of the **disk-truth invariant**: the on-disk record is the truth; in-context summaries are a convenience that can always be resolved back to it.

## Prior art

The concept of "compressed view with decompress-on-demand" is borrowed from `a5c-ai/babysitter` (MIT). Babysitter compresses Node conversation buffers and re-expands from its own store; Core compresses transcripts while leaving the filesystem as the source of truth. Attribution is tracked in `core/CREDITS.md`.
