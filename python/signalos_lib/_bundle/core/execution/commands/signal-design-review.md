---
description: "W8 Design Pipeline. Score a design variant against an 8-dimension rubric."
---

<!-- SignalOS v1.0 — W8 Design Pipeline -->

# /signal-design-review — Variant Review

Owner: QA / Design agent. Design phase. Must run before `/signal-design approve`.

## Purpose

Score a variant against 8 quality dimensions. Variants scoring below 7.0 overall are blocked from approval.

## Eight dimensions

| Dimension | Question |
|-----------|----------|
| `clarity` | Is the visual hierarchy immediately clear to a new user? |
| `consistency` | Does it use existing patterns (spacing, type, colour)? |
| `accessibility` | Does it meet WCAG 2.1 AA contrast, focus order, and semantic HTML? |
| `slop` | Is this generic AI output — lorem ipsum, placeholder icons, stock gradients? (**lower = better**) |
| `performance` | Are there unnecessary layout thrash risks, large images, or blocking scripts? |
| `responsiveness` | Does it work at 375 px, 768 px, and 1280 px? |
| `semantics` | Is the HTML structure meaningful (headings, landmarks, roles)? |
| `taste` | Does it match the product's approved taste memory? |

> **Note on `slop`:** The slop score is inverted in the rubric. A score of 0 (no slop) contributes 10 points; a score of 10 (all slop) contributes 0. Flag any lorem ipsum, stock gradients, or placeholder text as slop.

## CLI

```
signalos design-review \
  --variant .signalos/design/variants/wave-08/variant-01-hero-split.html \
  --wave 08 \
  --scores '{"clarity":8,"consistency":7,"accessibility":7,"slop":1,"performance":9,"responsiveness":8,"semantics":8,"taste":7}'
```

Output: pass/fail per dimension, overall score, list of issues.

Review result written to `.signalos/design/reviews/wave-{N}/{variant-stem}-review.json`.

## C14 advisory

Wiring guard C14 warns if `core/strategy/DESIGN_NOTE.md` is signed but no review file exists for the current wave. C14 is advisory (`--warn` safe) — it surfaces the gap without blocking.

## Fix-in-place policy

If overall < 7.0:
1. Edit the variant HTML directly (do not generate a new one)
2. Re-run `/signal-design-review` with updated scores
3. Repeat until score ≥ 7.0 before approving
