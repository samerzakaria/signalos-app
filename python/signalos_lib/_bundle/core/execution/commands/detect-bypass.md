---
description: "Detect governance-bypass signatures in git diffs and SignalOS agent output."
---

# detect-bypass - Governance Bypass Detector

Scans staged or ranged changes plus optional agent output for attempts to skip
SignalOS gates, evidence, hooks, tests, or governance review.

## Usage

```text
signalos detect-bypass [--repo-root <path>] [--staged | --diff <range>] [--message-file <path>] [--json]
```

When neither `--staged` nor `--diff` is provided, the command inspects staged
changes by default.

## What It Proves

- Delivery text and diffs do not ask agents to bypass SignalOS governance.
- Hook, gate, test, and evidence shortcuts are detected before closeout.
- Findings are written to `.signalos/product/VALIDATE_BYPASS_DETECTION.json`
  unless `--no-evidence` is provided.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | No bypass signature was detected |
| 1 | One or more bypass signatures were detected |
| 2 | Invalid command arguments |
