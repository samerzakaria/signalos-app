---
description: "Count, reconcile, and harvest deferred work markers."
---

# defer - Source Deferral Reconciliation

Counts source `DEFER:` markers, reconciles wave-targeted markers with
`.signalos/PRD_TRACEABILITY.md`, and can harvest deferred work into the wave
backlog.

## Usage

```text
signalos defer count [--repo-root <path>] [--json]
signalos defer harvest --wave <n> [--repo-root <path>] [--json]
```

## Count Rules

- `DEFER:` markers are scanned across source-like files while skipping vendored
  and generated directories.
- Wave-targeted markers such as `// DEFER: W02+ ...` are reconciled against
  `.signalos/PRD_TRACEABILITY.md` rows containing `DEFER`.
- A marker is reconciled when a PRD DEFER row cites its target wave, relative
  source path, or file name.
- `count` exits `100` when any wave-targeted marker is unreconciled. This is a
  review-needed signal, not a runtime crash.
- Non-wave DEFER notes remain countable and harvestable but do not require PRD
  reconciliation.

## Harvest Rules

- `harvest` writes `.signalos/backlog/wave-<n>.yaml`.
- Harvested items use `status: raw` and preserve the source file and line.
- Harvest appends a `defer-harvest` row to `.signalos/AUDIT_TRAIL.jsonl`.

## Governance

Do not leave `DEFER: W<NN>+` markers as invisible source debt. Either add a
matching PRD traceability row, harvest the item into a wave backlog, or remove
the marker when it is no longer valid.
