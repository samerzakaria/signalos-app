---
description: "Validate product Belief authoring artifacts and source traceability."
---

# validate-traceability - Product Belief Traceability Validator

Validates product Belief artifacts against `.signalos/TRACEABILITY_MATRIX.md`.

## Usage

```text
signalos validate-traceability [--repo-root <path>] [--json]
```

## What It Proves

- Product Belief markdown files exist under `.signalos/Beliefs/` or
  `core/governance/Beliefs/`.
- `.signalos/TRACEABILITY_MATRIX.md` exists and covers every Belief.
- Belief front matter carries source, wave, scale, delivery, provenance,
  designation, build-size, author, and date fields.
- Belief IDs match filenames.
- Source artifacts resolve inside the workspace.
- Required sections are present: Problem, Disproof condition, Bet Score,
  Smallest Testable Build, Signal threshold, Confidence bar, User served, and
  Zone history.
- Signal threshold includes Signal lag, User served declares numeric MIN N, and
  Zone history contains table evidence.
- Traceability matrix source rows match the Belief front matter.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Product traceability validation passed |
| 1 | Product traceability validation found blockers |
