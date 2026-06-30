---
name: think-before-coding
description: Use when about to write code from an instruction that could be read more than one way - requires stating assumptions explicitly, surfacing ambiguity, and presenting interpretations before producing any code; intent before implementation always
---

# Think Before Coding

## Overview

Code written against an unexamined reading of the task is fast and wrong. The cheapest place to be wrong is before the first line.

**Core principle:** Make your interpretation explicit, then write code — not the other way around.

This is loaded guidance, not a hard gate. It biases you toward a short, deliberate pause; it does not block a write.

## Tie to SignalOS

A SignalOS Belief is a falsifiable statement about what to build and why. A coding task inherits that intent. When the task in front of you is ambiguous, you are implicitly choosing one Belief over another — so choose on purpose, in writing, where the operator can correct you.

## The Pause

```
BEFORE writing code for a non-trivial task:

1. RESTATE   the task in your own words — the goal, not the keystrokes.
2. SURFACE   every assumption you are about to bake into the code.
3. DETECT    ambiguity: can a reasonable engineer read this two ways?
4. DECIDE    if uncertainty is material:
   - HIGH stakes / forking interpretations -> ASK or present options
   - LOW stakes / one obvious reading       -> state the assumption and proceed
5. THEN      write code.
```

## Surfacing Ambiguity

When a request admits more than one serious interpretation, name them before coding:

```
"Add caching to the report endpoint" could mean:
  (A) in-memory per-process cache, evicted on TTL — fast, simplest
  (B) shared cache (Redis) so all instances see one result — needs infra
  (C) HTTP response caching at the edge — no app code change

I read this as (A) unless you tell me otherwise, because <reason>.
```

State the interpretation you will act on and why. Do not silently pick one and bury the choice in a diff.

## When To Ask vs. Assume

| Situation | Action |
|-----------|--------|
| Two readings produce materially different systems | ASK before coding |
| Choice affects data model, public API, or security | ASK before coding |
| One reading is clearly intended, others are pedantic | State assumption, proceed |
| Reversible, low-cost, obvious default exists | State assumption, proceed |

## Red Flags - STOP

- You are about to write code and cannot say, in one sentence, what success looks like.
- You notice yourself thinking "they probably mean..." about something expensive to undo.
- The request names a feature but not the behaviour, the inputs, or the edge cases.
- You are filling a gap in the spec with a guess instead of a stated assumption.

## Anti-Pattern: "It's Obvious What They Want"

Obviousness is where unexamined assumptions hide. The fix is not paralysis — it is one sentence: "I'm assuming X; correct me if not." That sentence costs seconds and saves rework.

## Tradeoff Note

Bias toward caution over speed: a stated assumption is cheap, a wrong rebuild is not. But use judgment — a trivial, reversible change with one obvious reading does not need a ceremony. Not every change needs full rigor; reserve the explicit interpretation pass for tasks where being wrong actually costs something.

## Attribution

Adapted from the MIT-licensed multica-ai/andrej-karpathy-skills (derived from Andrej Karpathy's observations on LLM coding pitfalls). See THIRD_PARTY_NOTICES.md.
