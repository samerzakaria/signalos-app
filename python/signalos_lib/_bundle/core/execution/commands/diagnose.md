---
description: "Runtime diagnostic snapshot: daemon state, audit trail, worktrees, gate status, pending T2 items. (W3.5, AMD-CORE-018)."
---

# diagnose — Runtime Diagnostic Snapshot (W3.5 · AMD-CORE-018)

Collects a point-in-time snapshot of runtime state: daemon heartbeat, recent
audit-trail entries, live worktrees, gate signing status (G0–G5), and pending
T2 pause items. Output is always JSON (stdout or `--output` file).

## Usage
```
signalos diagnose [--repo-root <path>] [--wave <id>] [--output <path>] [--json]
```

## Keys in snapshot
- `generated_at` — ISO-8601 timestamp
- `repo_root` — absolute path used
- `wave` — filter applied (or null)
- `daemon_state` — last heartbeat data or null
- `audit_trail` — last 5 entries (filtered by wave if given)
- `worktrees` — list of active worktrees
- `gate_status` — map of G0–G5 → `{artifact, signed}`
- `pending_t2` — T2 tasks in pending/waiting state
