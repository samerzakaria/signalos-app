---
name: surgical-changes
description: Use when editing existing code to satisfy a request - requires touching only what the task demands, matching surrounding style, and making no drive-by refactors; every changed line must trace back to the request
---

# Surgical Changes

## Overview

A change set is read by humans who must trust that every edit is there for a reason. Unrelated edits mixed into a focused change destroy that trust and hide the real diff.

**Core principle:** Change only what the task requires. Everything else stays exactly as it was.

This is loaded guidance, not a hard gate. It keeps diffs minimal and reviewable; it does not block a write.

## The Trace Test

```
For every changed line, ask:

Which part of the request made me change THIS line?

No answer -> revert that line.
```

## Rules

| Rule | Meaning |
|------|---------|
| Touch the minimum | Edit only the files and lines the task needs |
| Match local style | Follow the conventions of the code you are in, not your own |
| No drive-by refactors | Do not rename, reformat, or reorganise unrelated code |
| No opportunistic cleanup | Spotted unrelated tech debt? Note it; don't fix it in this change |
| Preserve unrelated behaviour | A surgical change leaves everything off-task byte-identical |

## Tie to SignalOS

SignalOS scopes work to declared files and forbidden-path boundaries. An edit outside the task's scope is a scope violation even when it "improves" the code — it lands changes no Gate reviewed and no plan authorised. A clean, minimal diff is the artifact the review and proof steps are built to inspect; widen it and you weaken every check downstream.

## Matching Existing Style

- Read the surrounding code before editing. Mirror its indentation, naming, import order, and idioms.
- If the file uses a pattern you dislike, use that pattern anyway — consistency beats your preference inside someone else's file.
- A reviewer should not be able to tell which lines you wrote from style alone.

## Red Flags - STOP

- Your diff reformats a whole file because your editor reflowed it on save.
- You renamed a variable "while you were in there."
- You upgraded an unrelated pattern to your preferred one.
- The change touches files the task never mentioned.
- Reverting your "improvements" would not affect whether the task is done.

## Anti-Pattern: "While I'm Here..."

"While I'm here" is how a one-line fix becomes a hundred-line diff nobody can review. Resist it. If the surrounding code genuinely blocks the task, fix the minimum that unblocks it and say so. Everything else goes in a note, not the diff.

## Tradeoff Note

Bias toward the narrow, surgical diff over the satisfying broad sweep — narrow diffs are reviewable and revertible. But use judgment: when messy nearby code actively obstructs the task, a tightly-scoped local cleanup is fair, and a truly trivial change needn't agonise over each line. Keep the cleanup proportional and call it out.

## Attribution

Adapted from the MIT-licensed multica-ai/andrej-karpathy-skills (derived from Andrej Karpathy's observations on LLM coding pitfalls). See THIRD_PARTY_NOTICES.md.
