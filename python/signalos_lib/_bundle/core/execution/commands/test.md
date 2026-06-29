---
description: "Run the technology-neutral 12-phase SignalOS test automation umbrella."
---

# test - Test Automation Umbrella

Runs or evaluates the 12 SignalOS test-automation phases without assuming
.NET, ABP, Go, Node, Python, or any other product technology.

## Usage

```text
signalos test <phase> [--repo-root <path>] [--profile <id>] [--emit-audit]
signalos test all --repo-root <path> --json
signalos test unit --dry-run
```

Phases:

- `unit`
- `integration`
- `contract`
- `e2e`
- `visual`
- `performance`
- `security`
- `chaos`
- `production-monitor`
- `data`
- `pipeline`
- `governance`
- `all`

## Behavior

- `--dry-run` validates command wiring and writes evidence without invoking
  stack tools.
- Real runs use app-native evidence and profile commands where available.
- Missing evidence is `pending` or `blocked`, never silently passed.
- Threshold/evidence violations exit with code `8`.
- `--emit-audit` appends the phase audit row, such as
  `test.unit.executed` or `test.governance.executed`.
- Evidence is written under `.signalos/quality/test-automation/` and the
  latest result is mirrored to `.signalos/product/TEST_AUTOMATION_RESULT.json`.

## Technology Neutrality

The command may run Node, Python, .NET, Go, Rust, Java, or other stack tools
only when the selected product profile or evidence asks for them. It must not
force a generated product into .NET or Go, and it must not treat a missing
toolchain as success.
