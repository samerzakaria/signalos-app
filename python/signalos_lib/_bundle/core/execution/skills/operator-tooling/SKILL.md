---
name: operator-tooling
description: "Operator tooling: health check, runtime diagnostics, validator suite, hook lifecycle, and checkpoint recovery commands."
---

# Operator Tooling Skill (W3.5 · AMD-CORE-018)

Provides health checking, runtime diagnostics, validator suite execution,
hook lifecycle management, and checkpoint recovery for SignalOS Core operators.

## Commands

### health
```
signalos health [--repo-root <path>] [--json]
```
Checks git, Python version, jq availability, wiring guard, and daemon
heartbeat. Returns a structured report and exits 0/1/2.

### diagnose
```
signalos diagnose [--repo-root <path>] [--wave <id>] [--output <path>]
```
Produces a JSON snapshot of daemon state, audit trail (last 5 entries),
worktrees, gate signing status, and pending T2 pauses.

### validate
```
signalos validate [--repo-root <path>] [--validator <name>] [--json]
```
Runs the full validator suite or a named validator. Severity tiers:
HALT > BLOCK_MERGE > WARN.

### hooks test
```
signalos hooks test [--hook <name>] [--repo-root <path>]
```
Dry-runs all registered hooks (or a named hook) with
`SIGNALOS_DRY_RUN=1 SIGNALOS_HOOK_TEST=1`.

### recover
```
signalos recover [--repo-root <path>] [--resume] [--json]
```
Lists available checkpoints. With `--resume`, triggers `deliver.sh resume`
from the most recent checkpoint.

## Module layout
```
cli/signalos_lib/
  health.py          # HealthStatus, HealthItem, HealthReport, run_health()
  diagnose.py        # build_diagnose(), _read_* helpers
  validate_cmd.py    # ValidatorResult, run_validators(), VALIDATOR_SEVERITY
  commands/
    health.py        # CLI entrypoint for signalos health
    diagnose.py      # CLI entrypoint for signalos diagnose
    validate_cmd.py  # CLI entrypoint for signalos validate
    hooks.py         # CLI entrypoint for signalos hooks
    recover.py       # CLI entrypoint for signalos recover
```
