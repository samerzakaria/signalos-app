---
description: "Validate product-agent guidance obligations from SignalOS packets."
---

# validate-guidance-obligations - Guidance Obligation Validator

Checks that product-agent work loaded the required SignalOS guidance for the
affected paths, stack, and action.

## Usage

```text
signalos validate-guidance-obligations [--repo-root <path>] [--staged | --diff <range>] [--loaded <path>] [--stack <id>] [--action <name>] [--json]
```

`--loaded` points at the guidance file or packet that the agent actually used.
When omitted, the validator uses the app-native default lookup.

## What It Proves

- Path-to-guidance obligations are evaluated for the touched work.
- The active guidance catalog and obligations file agree.
- Product agents cannot silently skip required stack or action guidance.
- Evidence is written to `.signalos/product/VALIDATE_GUIDANCE_OBLIGATIONS.json`
  unless `--no-evidence` is provided.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Required guidance obligations were satisfied |
| 1 | One or more guidance obligations were missing |
| 2 | Invalid command arguments |
