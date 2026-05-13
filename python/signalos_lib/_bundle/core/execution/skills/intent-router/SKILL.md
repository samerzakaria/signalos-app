---
name: intent-router
description: "Natural language intent routing — maps free-form phrases to signalos commands with confidence scoring. No LLM call on the routing path."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.0.3 — intent-router skill (AMD-CORE-016). -->

# Skill: intent-router

Owner: Any role. Decision-support skill — not a runtime agent.

## When to invoke

Load this skill whenever an agent or human has a free-form goal statement
and needs to know which `signalos` command to run next. Common triggers:

- A product owner types "I want to review what we shipped."
- A principal engineer asks "How do I pause the current step?"
- An onboarding flow asks "What should I run after brainstorming?"
- An automated pipeline needs to dispatch based on a natural language task spec.

## Routing decision guide

The intent-router classifies phrases against 10 intents using weighted
keyword matching. Each intent maps to one `signalos` command.

| If the phrase sounds like… | Intent | Run |
|---|---|---|
| Starting fresh, onboarding, new product | onboard | `signalos session start` |
| Generating ideas, exploring features | brainstorm | `signalos harness call --step brainstorm` |
| Creating a plan, scheduling tasks, backlog | plan | `signalos orchestrate --wave <id> --plan PLAN.md` |
| Running tasks, implementing, executing | execute | `signalos orchestrate --wave <id> --plan PLAN.md` |
| Reviewing output, quality check, QA | review | `signalos harness call --step review` |
| Checking progress, current state, where are we | status | `signalos status` |
| Signing a gate, approving, G0–G5 | sign | `signalos sign <gate>` |
| Pausing a step, stopping | pause | `signalos pause list` |
| Resuming a paused step, continuing | resume | `signalos pause resume <step-id>` |
| Shrinking context, too many tokens, compress | compress | `signalos context compress <input-file>` |

## Confidence thresholds

| Confidence | Action |
|---|---|
| ≥ 0.70 | Route directly — print command, exit 0 |
| < 0.70 | Ask the clarifying question — exit 1 |

The threshold can be overridden with `--threshold <float>`.

## How to use from the CLI

```bash
# Quick route — human card
signalos intent "I want to review the output quality"

# Machine-readable — pipe to scripts
signalos intent "brainstorm new features" --json

# Show all intent scores (debugging / disambiguation UI)
signalos intent "check progress" --all

# Lower threshold for exploratory dispatch
signalos intent "let's do something" --threshold 0.3
```

## How to use from an agent

```python
from signalos_lib.intent import route_or_clarify

result = route_or_clarify("I want to run the tasks")
if result["routed"]:
    print(f"Run: {result['command']}")
else:
    print(f"Ask: {result['clarify']}")
```

## JSON output shape

```json
{
  "routed": true,
  "intent": "execute",
  "command": "signalos orchestrate --wave <wave-id> --plan PLAN.md",
  "confidence": 0.571,
  "clarify": "",
  "top2": ["execute", "plan"]
}
```

When `--all` is also passed, an `"all"` array is appended:
```json
{
  "all": [
    {"intent": "execute", "confidence": 0.571, "command": "..."},
    {"intent": "plan",    "confidence": 0.364, "command": "..."},
    ...
  ]
}
```

## Disambiguation rules

1. If `routed` is false and the top-2 intents are close (< 0.10 apart),
   present both options to the human and ask which they meant.
2. If all scores are 0.0 the phrase has no recognisable keywords — ask the
   human to rephrase using one of the intent keywords in the table above.
3. Never auto-route to `sign` or `execute` below the threshold — these have
   irreversible side-effects. Always ask the clarifying question first.

## Implementation files

- `cli/signalos_lib/intent.py` — `INTENTS`, `classify()`, `top_match()`, `route_or_clarify()`
- `cli/signalos_lib/commands/intent.py` — `main()`, `_render_card()`
- `core/execution/commands/intent.md` — command reference
- `integrations/rules/intent.mdc` — IDE rule
