---
description: "W8 Design Pipeline. Pre-design scoping — 6 forcing questions, PO_BRIEF.md output."
---

<!-- SignalOS v1.0 — W8 Design Pipeline -->

# /signal-pre-design — Design Scoping

Owner: PO agent. Design phase. Run before any design work begins. Produces `core/strategy/PO_BRIEF.md`.

## Your first action
Read `core/governance/Governance/SOUL-DOCUMENT.md` and the current Wave's `core/strategy/BELIEF.md`.
Confirm Gate 3 artifacts are in place: `core/strategy/DESIGN_NOTE.md` stub may not yet exist, but `core/strategy/BELIEF.md` and `core/execution/PLAN.md` must be signed.

## Four design modes

| Mode | When to use |
|------|-------------|
| **Expansion** | Adding net-new surfaces or capabilities |
| **Selective Expansion** | Expanding one area, holding all others |
| **Hold Scope** | Improving quality/polish within current surface boundary |
| **Reduction** | Removing surfaces; net scope decreases |

## Six forcing questions

You must answer all six. Vague answers block sign-off.

1. What is the single most important user outcome this design must enable?
2. Which existing patterns or conventions in this product must this design respect?
3. What is the primary constraint (time / technical / resource) shaping the scope?
4. Who is the exact user persona and what context are they in when they encounter this?
5. What is the one thing this design must absolutely not do or break?
6. How will we know in two weeks whether this design decision was correct?

## CLI

```
signalos pre-design --mode "Hold Scope" --wave 08 --author "PO"
# Interactive: prompts for each answer in the terminal.

signalos pre-design --mode Expansion --wave 08 \
  --answers '{"Q1 text": "Answer", ...}'
# Non-interactive: pass all answers as JSON.
```

## Output

- Writes `core/strategy/PO_BRIEF.md`
- Appends one entry to `core/governance/Governance/DECISION-DNA.md`
- PO must sign `core/strategy/PO_BRIEF.md` before `/signal-design explore` is unlocked

## Gate lock

`core/strategy/PO_BRIEF.md` must carry a PO signature before:
- `/signal-design explore` (generates variants)
- Any DESIGN_NOTE.md signing

C13 in `wiring-guard.sh` enforces this. DESIGN_NOTE.md signed without a signed PO_BRIEF → **FAIL**.
