---
description: "Summarize AI usage and enforce a USD budget."
---

# cost - AI Usage And Budget Gate

Summarizes AI usage/cost rows from local SignalOS evidence and exits non-zero
when a known USD cost exceeds the supplied budget.

## Usage

```text
signalos cost [--repo-root <path>] [--wave W01] [--budget-usd 10] [--json]
signalos cost --ledger .signalos/product/AI_USAGE.jsonl
```

## Inputs

By default the command reads:

- `.signalos/sessions/*/metrics.jsonl`
- `.signalos/product/AI_USAGE.jsonl`
- `.signalos/product/ai-usage.jsonl`
- `.signalos/AI_USAGE.jsonl`
- `.signalos/ai-usage.jsonl`

Rows may use `tokens_in`/`tokens_out`, `input_tokens`/`output_tokens`,
`prompt_tokens`/`completion_tokens`, `total_tokens`, `cost_usd`, or USD
`cost_amount`.

## Behavior

- No provider prices are guessed from source code or model names.
- Unpriced rows contribute calls and tokens, but not known cost.
- `--budget-usd` overrides `SIGNALOS_AI_WAVE_BUDGET_USD`.
- If a budget is set and known USD cost exceeds it, exit code is `1`.
- Bad arguments exit `2`.
- Evidence is written to `.signalos/product/COST_REPORT.json` unless
  `--no-evidence` is supplied.
