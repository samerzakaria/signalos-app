---
description: "Validate read-only git WorktreeSync snapshot state."
---

# validate-worktree-sync - WorktreeSync Validator

Validates that the current git worktree can be read safely and, optionally,
that non-SignalOS worktree files are clean.

## Usage

```text
signalos validate-worktree-sync [--repo-root <path>] [--require-clean] [--json]
```

## What It Proves

- A git repository exists at or above the target path.
- HEAD has a readable commit.
- Branch, commit SHA, head message, dirty file count, and timestamp can be
  captured without mutating git.
- A snapshot is persisted to `.signalos/worktree-sync/snapshots.jsonl`.
- With `--require-clean`, dirty non-SignalOS files are blockers.
- Validation evidence is written under `.signalos/evidence/worktree-sync/`.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | WorktreeSync validation passed |
| 1 | WorktreeSync validation found blockers or git state was unreadable |
