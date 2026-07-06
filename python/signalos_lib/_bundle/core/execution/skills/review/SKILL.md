---
name: review
description: "Structured code and artifact review following SignalOS quality standards."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Skill — review

## One-liner

Stage-1 spec-drift review: compare a Build PR's diff against the Wave's Belief + Expectation Map + PLAN task + Trust Tier Declaration, return PASS / BLOCK / FLAG-FOR-HUMAN.

## Trigger phrase

`/review stage-1 {pr-url}` — or `review PR {pr-url}` in natural language.

## Owning agent

**Review agent** (`core/execution/agents/review.md`).

## Inputs

- PR URL or diff text
- Paths (read-only):
  - `core/strategy/BELIEF.md`
  - `core/strategy/EXPECTATION_MAP.md`
  - `core/execution/PLAN.md`
  - `core/strategy/DESIGN_NOTE.md`
  - `core/execution/TRUST_TIER.md`

## Outputs

- PR comment with a structured report (sections fixed, no deviation)
- `core/execution/review/wave-{N}/pr-{nnn}-stage-1-report.md` — archived copy
- Verdict: `PASS` | `BLOCK` | `FLAG-FOR-HUMAN`

## Report shape (fixed)

```
# Stage-1 Review — PR #{nnn} — Wave {N}

## Spec drift check
## Belief check
## Trust Tier check
## Scope check
## Verdict
## Reasoning (one paragraph)
```

No additional sections. No summary. No postamble.

## Refusal conditions

- PR diff is empty — emit "Empty diff; nothing to review."
- PR touches a surface not listed in `TRUST_TIER.md` — emit HARD BLOCK: "Unmapped surface."
- Any of the prerequisite artifacts is missing or unsigned — emit HARD BLOCK with the missing-artifact path.

## Side-effects

Writes one PR comment. Writes one archive file. Does not merge, does not push, does not modify code.

## Trust Tier

**T2** — proposes the verdict; PE's merge click is the human signature. Skill itself never self-merges.

## Enforcement layer

This skill is invoked by Layer 3 (CI Validators — `gate-signature-guard` auto-invokes on PR open/push) and by Layer 2 (Review agent activation). Its report feeds Layer 1 (PE human decision).

## Amendment history

| Date | Change | Signer |
|---|---|---|
| 2026-04-16 | Initial v1.0 skill | PE |
