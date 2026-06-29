---
description: "Validate one gate's artifact presence, non-draft signature, hash, audit link, and optional wave link."
---

# validate-gate - Gate Artifact Validator

Validates a single SignalOS gate using app-native artifact definitions.

## Usage

```text
signalos validate-gate --gate G5 [--wave W01] [--repo-root <path>] [--json]
```

`--gate` accepts `G0` through `G5`, or numeric `0` through `5`.
`--wave` is optional; when present, the matching audit row must carry the same
normalized wave id.

## What It Proves

- Required gate artifacts exist.
- Artifacts have non-draft signatures.
- Declared signature hashes still match current artifact content.
- `.signalos/AUDIT_TRAIL.jsonl` exists.
- Each signed artifact has a matching approved audit row linking artifact,
  gate, verdict, hash, and optional wave.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Gate validation passed |
| 1 | Gate validation failed |
