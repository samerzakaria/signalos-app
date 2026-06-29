---
description: "Validate the Wave status card, Journey blockers, and signed gate evidence."
---

# validate-wave-status - Wave Status Validator

Validates the current SignalOS Wave from local app files. This is the blocking
counterpart to advisory `signalos status`.

## Usage

```text
signalos validate-wave-status [--wave W01] [--repo-root <path>] [--json]
```

Compatibility flags are accepted:

```text
signalos validate-wave-status --api-url <url> [--token <bearer>]
```

Remote status API validation is intentionally blocked in the app runtime until a
technology-independent runtime API exists. Passing `--api-url` returns a
blocker instead of silently trusting a non-app source.

## What It Proves

- `.signalos/` scaffold is present.
- `.signalos/AUDIT_TRAIL.jsonl` exists and is parseable.
- Gate artifacts G0 through G5 exist.
- Gate artifacts have non-draft signatures.
- Signature hashes still match current artifact content.
- Every signed artifact has an approved audit row linking artifact, gate,
  verdict, hash, and optional wave.
- Signature roles match the app's gate artifact manifest.
- The generated Journey snapshot exposes structured blockers and next action.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Wave status validation passed |
| 1 | Wave status validation found blockers |

## Output

`--json` emits `signalos.validate_wave_status.v1` with:

- `source`, `wave`, `state`, `signed_gate_count`, `next_gate`;
- `audit_status`, `scaffold_status`, `has_blocking_issue`;
- per-gate validation checks;
- `journey.structured_blockers`;
- rendered advisory `status_card` for UI reuse.
