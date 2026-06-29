---
description: "Evaluate a request against active wave scope before building."
---

# feature-gate - Mid-Wave Scope Drift Refusal

Evaluates a new request against the active wave's approved scope before build
work or product generation proceeds.

## Usage

```text
signalos feature-gate "<request>" [--q1 yes|no] [--q2 yes|no] [--repo-root <path>] [--json]
```

## Inputs

- `.signalos/wave.json` must name an `ACTIVE` wave.
- Backlog scope is read from `.signalos/waves/<wave>/BACKLOG.yaml`,
  `.signalos/BACKLOG.yaml`, and app-native `.signalos/backlog/wave-<n>.yaml`.
- Expectation scope is read from `.signalos/waves/<wave>/EXPECTATION_MAP.md`
  plus app-native fallback expectation-map paths.
- PRD scope is read from `.signalos/PRD_TRACEABILITY.md` rows containing
  `BUILD`.

## Verdicts

- `BUILD`: the request matched scope, or Q1/Q2 justified immediate build.
- `DEFER`: Q1 and Q2 are both `no`; record the work as deferred rather than
  building it now.
- `NEEDS_ANSWERS`: the request is out of scope and Q1/Q2 were not supplied.
- `WAVE_NOT_ACTIVE`: no active wave pointer exists.

Exit code `100` means Q1/Q2 answers are needed. Exit code `2` means the active
wave pointer is missing or not active.

## Delivery Bridge

`signalos deliver` writes `.signalos/product/FEATURE_GATE.json` before
generation. If no wave pointer exists, the evidence records that the gate was
skipped because no active wave was available. If a wave pointer exists, delivery
must receive a non-blocking Feature Gate result before writing generation
packets.
