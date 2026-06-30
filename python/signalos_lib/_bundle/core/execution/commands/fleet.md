---
description: "Governed agent fleet — detect runtimes, list them, and GC isolated task workspaces."
---

# fleet - Governed Agent Fleet Runtime (Foundation)

Detects the agent CLIs SignalOS can dispatch governed work to, lists the
registered/detected runtimes, and garbage-collects isolated per-task
workspaces. Every agent hand-off in the fleet passes through a governance
admission check first (an active wave/gate or a valid agent packet); SignalOS
enforces, never advises, so an ungoverned dispatch fails closed.

This command is the FOUNDATION around a live executor. The autonomous,
server-backed runtime that actually claims, spawns, heartbeats, and streams a
CLI is roadmap — see `docs/GOVERNED_FLEET_RUNTIME_DESIGN.md`.

## Usage

```text
signalos fleet detect [--repo-root <dir>] [--json] [--no-evidence]
signalos fleet list   [--repo-root <dir>] [--json]
signalos fleet gc     [--repo-root <dir>] [--tasks-root <dir>]
                      [--done-ttl <s>] [--orphan-ttl <s>] [--artifact-ttl <s>]
                      [--now-ts <epoch>] [--json] [--no-evidence]
```

`detect` is the default action when no subcommand is given.

## Runtime detection

`detect` scans PATH for the agent CLIs SignalOS emits tool adapters for —
`claude-code` (claude), `codex`, `cursor` (cursor-agent), `github-copilot`
(copilot), `windsurf`, `gemini`, `antigravity`, `vs-code` (code), and the
built-in `harness`. Each known runtime is reported as
`{id, cli, executable, kind, detected}`. Evidence is written to
`.signalos/evidence/fleet/runtimes.json` unless `--no-evidence` is passed.

## Governed dispatch admission

Before any agent runs, the runtime requires EITHER an active wave/gate OR a
structurally-valid agent packet (the same packet contract the agent-result
validator enforces). With neither, admission is REFUSED (`admitted: false`) and
no agent runs. Every decision — admitted or refused — appends an evidence row
to `.signalos/evidence/fleet/dispatch.jsonl` and a
`fleet-dispatch-admitted` / `fleet-dispatch-refused` audit row.

## Workspace GC

`gc` prunes task workspaces under `.signalos/fleet/tasks/` (override with
`--tasks-root`) on TTLs:

- whole task dirs that are done/idle past `--done-ttl` are removed;
- heavy artifact dirs (`node_modules`, `.next`, `.turbo`) past `--artifact-ttl`
  are pruned from otherwise-kept tasks, while source, `.git`, and `logs` are
  preserved;
- orphan dirs with no `.gc_meta.json` past `--orphan-ttl` are removed.

GC is housekeeping, not a gate: it writes evidence to
`.signalos/evidence/fleet/gc.json`, appends a `fleet-gc` audit row, and exits
non-zero only on a real error (e.g. a removal that failed).
