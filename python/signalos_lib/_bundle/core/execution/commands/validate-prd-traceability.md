---
description: "Validate that PRD claims resolve to BELIEF, BUILD, DEC, or DEFER destinations."
---

# validate-prd-traceability - PRD Traceability Validator

Validates `.signalos/PRD_TRACEABILITY.md` using app-native local files.

## Usage

```text
signalos validate-prd-traceability [--matrix-path <path>] [--repo-root <path>] [--json]
```

## What It Proves

- The PRD traceability matrix exists and has data rows.
- Every row has a PRD section, claim, destination, and target.
- Destination is one of `BELIEF`, `BUILD`, `DEC`, or `DEFER`.
- `BELIEF` targets resolve to product Belief files.
- `BUILD` targets are scoped under an existing parent Belief.
- `DEC` targets resolve to blocks in `DECISION-DNA.md`.
- `DEFER` targets use `DEFER -> WNN` or `DEFER -> never`; `never` requires notes.
- Principle-shaped claims do not point to unmeasurable Beliefs.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | PRD traceability validation passed |
| 1 | PRD traceability validation found blockers |
