---
name: task-schema
description: "Authoring and validating PLAN.tasks.yaml — the ULID-keyed, typed, dependency-aware task list that replaces prose markdown checklists as the orchestrator's source of truth. Use when creating/editing tasks for a wave, populating fields, resolving signalos plan validate errors, or writing automation that reads task state."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->

# Task Schema Skill — PLAN.tasks.yaml Guide (W3.4, AMD-CORE-017)

## When to invoke

Use this skill when:
- Creating or editing `PLAN.tasks.yaml` for a new wave.
- Deciding what fields to populate for a given task.
- Resolving validation errors from `signalos plan validate`.
- Writing automation that reads task state from disk.

## ULID generation

Generate IDs with:
```python
from signalos_lib.plan import make_ulid
new_id = make_ulid()  # e.g. 01HWXYZ1234567890ABCDEFGH
```

Or via shell: `python3 -c "from signalos_lib.plan import make_ulid; print(make_ulid())"`

Every task must have a ULID that is:
- 26 characters, uppercase alphanumeric (Crockford base32: `[0-9A-HJKMNP-TV-Z]`).
- Unique within the document.
- Never reused, even after a task is removed.

## Field selection guide

| Situation | Fields to fill |
|-----------|---------------|
| Automated T1 task | `id`, `title`, `status: pending`, `tier: T1`, `prompt_file` |
| Human-reviewed T2 task | Add `owner`, `notes` explaining why human sign-off is required |
| Dependent task | Add `depends_on: [<parent-id>]` |
| Parallel tasks | `depends_on` points to the same parent; no further flag needed |
| Long task (> 2 days) | Set `effort_days`, add `notes` with breakdown |
| Multi-wave task | Set `wave` to override the document-level wave |

## Status transitions

```
pending → in_progress → done
pending → blocked      (external dependency)
pending → skipped      (descoped)
blocked → pending      (unblocked)
```

The orchestrator reads `status` to decide dispatch order. Only `pending` tasks with
all `depends_on` satisfied are eligible for dispatch.

## Validation checklist

Before running `signalos plan render`, confirm:
1. Every `id` is a 26-character ULID: `[0-9A-Z]{26}`.
2. IDs are unique across the file.
3. `status` ∈ `{pending, in_progress, done, blocked, skipped}`.
4. `tier` ∈ `{T1, T2, T3}`.
5. Every `depends_on` entry references a task `id` in the same file.
6. No circular dependencies.

Run `signalos plan validate --json` to get a machine-readable report.

## Reading task state from Python

```python
from signalos_lib.plan import load_tasks, validate_tasks

doc = load_tasks("PLAN.tasks.yaml")
errors = validate_tasks(doc)
if errors:
    raise RuntimeError(f"Plan invalid: {errors}")

pending = [t for t in doc.tasks if t.status == "pending"]
ready   = [t for t in pending if not t.depends_on]
```

## Rendered output

`signalos plan render` writes `PLAN.md` alongside `PLAN.tasks.yaml`.
The markdown is a *derived* artefact — never edit it directly.
It contains a summary table and one section per task.

## CLI quick reference

```bash
signalos plan validate                         # check PLAN.tasks.yaml
signalos plan render                           # write PLAN.md
signalos plan list                             # all tasks
signalos plan list --status pending            # pending only
signalos plan list --json                      # JSON output
signalos plan render --input other.yaml        # custom input
```
