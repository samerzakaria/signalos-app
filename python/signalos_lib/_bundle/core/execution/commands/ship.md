---
description: "Verify Gate 5 ship readiness, optionally create a local tag, and append audit."
---

# ship - Deterministic Ship Ceremony

Runs the app-native Ship stage as an executable local ceremony. It verifies
Gate 5, release readiness, release proof, closeout honesty, and working-tree
state before a release is described as shipped.

## Usage

```text
signalos ship <wave>
signalos ship <wave> --dry-run --json
signalos ship <wave> --no-tag
signalos ship <wave> --tag-format release-{W}
```

Add `--repo-root <path>` to evaluate a different workspace. Use `--allow-dirty`
only when the dirty worktree is intentionally part of the local handoff.

## Rules

- The wave directory `.signalos/waves/<WNN>/` must exist.
- Gate 5 `core/governance/QUALITY_CHECK.md` must be signed, non-draft, and
  hash-valid.
- Agent self-signatures such as `agent`, `claude`, `copilot`, or `llm` are
  refused.
- The git worktree must be clean unless `--allow-dirty` is explicit.
- The quality check must not contain FAIL marks.
- Passing `release-readiness.json` evidence is required.
- Passing `release-proof.json` evidence is required only when an artifact
  handoff is being claimed and the evidence exists.
- Product closeout honesty is checked when `.signalos/product/CLOSEOUT.json`
  exists.

## Local Boundary

`signalos ship` may create a local annotated tag. It never pushes, publishes,
deploys, or mutates a remote service. Those actions require separate explicit
operator approval.

## Evidence

Ship evidence is written to `.signalos/evidence/<wave>/ship.json`. Live runs
append `ship-confirmed` or `ship-blocked` to `.signalos/AUDIT_TRAIL.jsonl`.
