---
description: "Trace governance ticket ids to source, proof, and SignalOS files."
---

# trace - Ticket-To-Code Evidence Lookup

Finds source, test, proof, and governance files that cite a backlog ticket id.

## Usage

```text
signalos trace ticket --id T-W<NN>-NNN [--repo-root <path>] [--json]
```

## Rules

- Ticket ids must use the canonical `T-W<NN>-NNN` form, for example
  `T-W04-001`.
- The command scans app-native implementation paths: `src`, `test`, `tests`,
  `proof`, `.signalos`, and `core`.
- Vendored/generated directories such as `node_modules`, `bin`, `obj`,
  `target`, `dist`, and `build` are skipped.
- Exit code `100` means the ticket id is valid but no file references it.

Use this before closeout when a ticket claims implementation evidence. A ticket
with no traceable file reference is not proof of completed work.
