---
description: "W8 Design Pipeline. Promote approved variant to production HTML, JSX, or Svelte."
---

<!-- SignalOS v1.0 — W8 Design Pipeline -->

# /signal-design-html — Production HTML

Owner: PE agent. Design phase. Requires approved variant (`.signalos/design/reviews/wave-{N}/approval.json`).

## Purpose

Promote the approved design variant to a production-ready file. Framework is auto-detected from the repo; override with `--framework`.

## Framework detection

| Signal | Framework |
|--------|-----------|
| `*.jsx` or `*.tsx` files in repo | JSX (React) |
| `*.svelte` files in repo | Svelte |
| Neither | Plain HTML |

## CLI

```
# Auto-detect framework
signalos design-html \
  --variant .signalos/design/variants/wave-08/variant-01-hero-split.html \
  --wave 08

# Force JSX output
signalos design-html \
  --variant .signalos/design/variants/wave-08/variant-01-hero-split.html \
  --wave 08 \
  --framework jsx
```

## Output

Production file written to `.signalos/design/production/wave-{N}/output.{ext}`.

- HTML: variant with production marker comment appended
- JSX: body content wrapped in `export default function ProductionComponent()`; `class=` → `className=`
- Svelte: `<script>` block + body content + `<style>` block extracted from variant

## Post-generation checklist

- [ ] Review production file for any leftover placeholder content
- [ ] Wire into the product's actual routing / component tree
- [ ] Commit to the feature branch with a reference to the approved variant path
