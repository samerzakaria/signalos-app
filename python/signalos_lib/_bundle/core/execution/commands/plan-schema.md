---
description: "Machine-readable task schema commands: render PLAN.md, validate PLAN.tasks.yaml, list tasks (W3.4, AMD-CORE-017)."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.0.3 — plan command (AMD-CORE-017). -->

# /plan — Machine-Readable Task Schema

Owner: PE · QA. Authoritative task list is `PLAN.tasks.yaml`; `PLAN.md` is generated from it.

## Purpose

Manages `PLAN.tasks.yaml` — a ULID-keyed, dependency-aware, typed task list that is
the orchestrator's authoritative source. `PLAN.md` is a *rendered view* only and
must not be edited directly.

## Usage

```
signalos plan render [--input <path>] [--output <path>]
signalos plan validate [--input <path>] [--json]
signalos plan list [--input <path>] [--status <status>] [--json]
```

## Actions

| Action | Description |
|--------|-------------|
| `render` | Generate `PLAN.md` from `PLAN.tasks.yaml` |
| `validate` | Check `PLAN.tasks.yaml` against the JSON Schema and dependency graph |
| `list` | Print tasks, optionally filtered by status |

## Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--input <path>` | `./PLAN.tasks.yaml` | Input file path |
| `--output <path>` | `./PLAN.md` | render: output path |
| `--status <status>` | _(all)_ | list: filter by status |
| `--json` | false | validate/list: emit JSON |

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success / valid |
| 1 | Validation errors found |
| 2 | File not found / usage error |

## Task schema

Each task in `PLAN.tasks.yaml` must have:

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `id` | ✓ | ULID (26 chars) | Generate with `make_ulid()` |
| `title` | ✓ | string | Short imperative title |
| `status` | ✓ | enum | `pending` / `in_progress` / `done` / `blocked` / `skipped` |
| `tier` | ✓ | enum | `T1` / `T2` / `T3` |
| `owner` | — | string | Role or agent name |
| `depends_on` | — | list[ULID] | IDs that must be `done` first |
| `effort_days` | — | float | Estimated effort (default 1.0) |
| `prompt_file` | — | path | Relative path to agent prompt |
| `wave` | — | string | Wave override |
| `branch` | — | string | Git branch name |
| `notes` | — | string | Free-form notes |

## Example PLAN.tasks.yaml

```yaml
wave: W3.4
tasks:
  - id: 01HX1234567890ABCDEFGHIJKL
    title: Write plan schema
    status: done
    tier: T1
    effort_days: 0.5
  - id: 01HX1234567890ABCDEFGHIJKM
    title: Write CLI commands
    status: in_progress
    tier: T1
    depends_on:
      - 01HX1234567890ABCDEFGHIJKL
```

## Validation rules

1. Every `id` matches `[0-9A-Z]{26}` (ULID pattern).
2. IDs are unique within the document.
3. `status` ∈ `{pending, in_progress, done, blocked, skipped}`.
4. `tier` ∈ `{T1, T2, T3}`.
5. Every entry in `depends_on` references an `id` in the same document.
6. No dependency cycles (DFS check).

## JSON Schema

`core/execution/plan/PLAN_SCHEMA.json` — JSON Schema draft-07, referenced by the
wiring guard Check 10.

## Implementation

`cli/signalos_lib/plan.py` — `Task`, `PlanDoc`, `load_tasks`, `validate_tasks`, `render_plan_md`, `dump_tasks`, `make_ulid`.
`cli/signalos_lib/commands/plan.py` — CLI entry point.
`core/execution/skills/task-schema/SKILL.md` — agent skill guide.

## Next command

After render: open `PLAN.md` for human review.
After validate: fix any errors listed, then re-run.
