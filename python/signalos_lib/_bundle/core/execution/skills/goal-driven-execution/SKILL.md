---
name: goal-driven-execution
description: Use when starting any implementation task - requires converting the request into explicit, verifiable success criteria with named verification checkpoints before and during the work, so "done" means proven, not assumed
---

# Goal-Driven Execution

## Overview

A task without success criteria has no definition of done — so it is never done, only abandoned. Define what "working" means in checkable terms before you build, then check.

**Core principle:** Turn the request into criteria you can verify, and verify them.

This is loaded guidance, not a hard gate. It shapes how you plan and close out a task; it does not block a write.

## The Method

```
1. TRANSLATE the request into 1-N concrete success criteria.
   Each criterion is observable: a command, an output, a behaviour.
2. NAME a verification checkpoint for each criterion
   (what you will run / inspect to confirm it).
3. BUILD toward the criteria, not toward "looks finished."
4. VERIFY each checkpoint against real output.
5. CLAIM done only when every criterion is checked and passing.
```

## Writing Verifiable Criteria

| Vague goal | Verifiable criterion |
|------------|----------------------|
| "Make login work" | "POST /login with valid creds returns 200 + session cookie; invalid returns 401" |
| "Fix the flaky test" | "`pytest test_x.py -q` passes 20/20 consecutive runs" |
| "Improve performance" | "p95 latency on /report under 200ms at 50 rps (was 800ms)" |
| "Handle the edge case" | "Empty input returns [] with no exception; covered by a test" |

If you cannot state the check, you do not yet understand the goal — return to think-before-coding.

## Tie to SignalOS

These criteria ARE the task's acceptance criteria, and the verification checkpoints ARE its proof. SignalOS closes work against acceptance + proof, not against assertion. Define the criteria up front so the proof step has something concrete to measure, and so the operator agreed to the target before you spent effort hitting it.

## Cross-Reference

This skill defines the criteria and checkpoints. The discipline of actually running the check before claiming success lives in **verification-before-completion** — `core/execution/build/verification-before-completion/SKILL.md`. Goal-driven execution decides *what* counts as done; verification-before-completion enforces *evidence before the claim*. Use them together: set criteria here, prove them there.

## Red Flags - STOP

- You are about to start coding and have not written down what "done" means.
- Your only success check is "it looks right" or "it should work."
- A criterion is stated but you never named how you'd verify it.
- You are claiming completion against criteria you have not run.

## Anti-Pattern: "I'll Know It When I See It"

Without criteria fixed in advance, "done" drifts to wherever you happen to stop, and verification becomes a vibe. Write the checks first; let them, not fatigue, decide completion.

## Tradeoff Note

Bias toward defining and running explicit checks over trusting that it works — proof is cheap insurance against shipping broken work. But use judgment: a trivial, self-evidently correct change (a typo fix, a constant tweak) does not need a formal criteria-and-checkpoint pass. Match the rigor to the stakes.

## Attribution

Adapted from the MIT-licensed multica-ai/andrej-karpathy-skills (derived from Andrej Karpathy's observations on LLM coding pitfalls). See THIRD_PARTY_NOTICES.md.
