---
name: simplicity-first
description: Use when implementing a feature or fix and tempted to add structure, options, or guards beyond the stated need - requires the minimum code that solves the actual problem, with no speculative features, premature abstractions, or unrequested error handling
---

# Simplicity First

## Overview

The right amount of code is the least that solves the stated problem. Everything past that is a liability you have to carry, test, and explain.

**Core principle:** Build what was asked, not what might one day be asked.

This is loaded guidance, not a hard gate. It steers you toward the smallest correct solution; it does not block a write.

## The Test

```
Before shipping, ask:

Would a senior engineer call this overcomplicated?

If yes -> cut until the answer is no.
```

## What "Minimum" Excludes

| Temptation | Why to drop it |
|------------|----------------|
| Speculative features ("they'll want this later") | YAGNI — add it when "later" arrives with a real requirement |
| Premature abstractions (interfaces, plugins, generics for one caller) | One concrete implementation is clearer than a framework for one |
| Unrequested config / flags / options | Each option doubles the test surface and the ways to be wrong |
| Error handling for cases that cannot occur here | Guards for impossible states hide the real control flow |
| Layers "for flexibility" with no second use | Flexibility you don't need is just indirection |

## Tie to SignalOS

SignalOS works from a Belief and an explicit scope. Code that serves neither is not "extra value" — it is unscoped work that no Gate asked for and no acceptance criterion covers. The smallest change that satisfies the criteria is the one the proof can actually verify.

## The Discipline

```
1. SOLVE the stated problem directly.
2. STOP when the stated problem is solved.
3. RESIST the urge to generalise before a second concrete case exists.
4. DELETE any scaffolding the current task does not exercise.
```

## Red Flags - STOP

- You are adding a parameter "in case someone needs it."
- You are introducing an interface/base class with exactly one implementation.
- You wrote a `try/except` (or `catch`) around code that cannot raise in this path.
- You are building a config system for a value that has never changed.
- The diff is much larger than the request, and the extra is "infrastructure."

## Anti-Pattern: "Doing It Properly"

"Properly" often smuggles in speculative generality. The proper solution to a small problem is a small solution. Make it correct and clear first; generalise only when a real second case forces the issue.

## Tradeoff Note

Bias toward the simplest thing that works over the speedy clever thing — simple code is what survives the next change. But use judgment: a genuinely known, imminent second requirement can justify a seam now, and not every change needs the full minimalist audit. Trivial changes: just make them clean and move on.

## Attribution

Adapted from the MIT-licensed multica-ai/andrej-karpathy-skills (derived from Andrej Karpathy's observations on LLM coding pitfalls). See THIRD_PARTY_NOTICES.md.
