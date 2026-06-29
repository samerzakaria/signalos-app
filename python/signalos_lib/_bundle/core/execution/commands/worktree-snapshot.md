---
description: "Take and query read-only git worktree snapshots."
---

# worktree-snapshot - Read-Only Worktree Snapshot

Captures app-native WorktreeSync rows from git without mutating the repository.

## Usage

```text
signalos worktree-snapshot take [--repo-root <path>] [--json]
signalos worktree-snapshot latest [--repo-root <path>] [--json]
signalos worktree-snapshot get <snapshot-id> [--repo-root <path>] [--json]
signalos worktree-snapshot list [--branch <name>] [--commit <sha>] [--repo-root <path>] [--json]
```

## What It Records

- Snapshot id.
- Git root path.
- Head commit SHA.
- Current branch.
- Head commit message.
- Dirty file count, excluding SignalOS bookkeeping under `.signalos/`.
- UTC timestamp.

Snapshots are appended to `.signalos/worktree-sync/snapshots.jsonl`.

## Rules

- The command reads git state only.
- It does not checkout, commit, stage, merge, reset, or write git metadata.
- It may write SignalOS evidence/snapshot JSON under `.signalos/`.
- Query commands resolve the git root so they work from subdirectories.
