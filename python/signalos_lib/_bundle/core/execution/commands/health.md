---
description: "System health check: git, Python runtime, jq, wiring guard, daemon heartbeat. Exits 0/1/2 (OK/DEGRADED/DOWN). (W3.5, AMD-CORE-018)."
---

# health — System Health Check (W3.5 · AMD-CORE-018)

Runs a structured health check across git, Python runtime, optional tools (jq),
wiring guard, and daemon heartbeat. Reports OK / DEGRADED / DOWN per component
and exits with code 0 (all OK), 1 (degraded), or 2 (any DOWN).

## Usage
```
signalos health [--repo-root <path>] [--json]
```

## Output modes
- Default: aligned table with status icons and detail strings.
- `--json`: machine-readable array of `{name, status, detail}` items plus `overall`.

## Exit codes
| Code | Meaning |
|------|---------|
| 0    | All components OK |
| 1    | At least one DEGRADED, none DOWN |
| 2    | At least one DOWN |
