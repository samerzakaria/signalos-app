---
description: "W8 Design Pipeline. Variant generation, approval, and taste iteration."
---

<!-- SignalOS v1.0 — W8 Design Pipeline -->

# /signal-design — Design Variants

Owner: PE / Design agent. Design phase. Requires signed `core/strategy/PO_BRIEF.md`.

## Gate check (before any subcommand)

```
signalos wiring-guard --check C13
```

C13 verifies `core/strategy/PO_BRIEF.md` exists and is signed. Any failure → hard stop.

## Three subcommands

### explore — generate variants
```
signalos design explore --wave 08 --title "Dashboard" --count 3
```
Generates 3–5 self-contained HTML mockups in `.signalos/design/variants/wave-{N}/`.
Each variant uses a distinct layout archetype:
- `hero-split` — content left, visual right
- `dashboard` — metrics at top, detail below
- `minimal` — typography-led, high whitespace
- `card-grid` — scannable tile layout
- `sidebar-nav` — persistent nav + content area

Opens a comparison board at `.signalos/design/variants/wave-{N}/index.html`.
Taste memory context is automatically injected before generation.

### approve — select a variant
```
signalos design approve \
  --variant .signalos/design/variants/wave-08/variant-01-hero-split.html \
  --wave 08
```
Records approval in `.signalos/design/reviews/wave-{N}/approval.json`.
Appends approved variant to DECISION-DNA.
Run `/signal-design-review` first — score must be ≥ 7.0 before approval.

### iterate — record taste and regenerate
```
signalos design iterate \
  --variant .signalos/design/variants/wave-08/variant-02-dashboard.html \
  --wave 08 \
  --verdict rejected \
  --traits '["heavy gradients", "too many columns"]'
```
Records verdict to `.signalos/design-taste.jsonl` with weight 1.0.
Weights decay at `0.95^weeks`. Re-run `explore` for a fresh round with updated taste.

## Taste memory

Entries in `.signalos/design-taste.jsonl` are injected as context before each `explore` run.
Top 3 approved traits are encouraged; top 3 rejected traits are suppressed.

## Exit criteria for explore → approve

- [ ] `PO_BRIEF.md` signed (C13)
- [ ] At least one variant generated (`explore`)
- [ ] Variant reviewed (`/signal-design-review` score ≥ 7.0)
- [ ] Variant approved (`approve`)
- [ ] DESIGN_NOTE.md updated with approved variant reference
- [ ] Production HTML generated (`/signal-design-html`)
