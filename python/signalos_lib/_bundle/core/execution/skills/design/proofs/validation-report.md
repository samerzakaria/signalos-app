# SignalOS Visual Validator — 3-slide proof (LTR + RTL-Latin + RTL-Arabic)

Three builds from one skill, all mechanically clean.

## Builds covered

| Build | File | Script | Purpose |
|---|---|---|---|
| LTR | `signalos_proof_ltr.pptx` | `node proof_deck.js ltr` | Baseline reference render |
| RTL-Latin | `signalos_proof_rtl.pptx` | `node proof_deck.js rtl` | Shape-mirror test (check 14) — same content, mirrored geometry |
| RTL-Arabic | `signalos_proof_rtl_ar.pptx` | `node proof_deck_ar.js` | Arabic contextual-shaping test — discovers typography defects the Latin RTL cannot |

## Mechanical critic (scripts/critic.py)

All three builds pass all mechanical checks. Three slides per build × three builds = 9 slide-checks green.

```
LTR  slide-1.jpg   whitespace 97.7% (cover)  palette ✓  craft ✓
LTR  slide-2.jpg   whitespace 85.1%          palette ✓  craft ✓
LTR  slide-3.jpg   whitespace 84.4%          palette ✓  craft ✓

RTL  slide-rtl-1   whitespace 97.7% (cover)  palette ✓  craft ✓
RTL  slide-rtl-2   whitespace 85.1%          palette ✓  craft ✓
RTL  slide-rtl-3   whitespace 84.4%          palette ✓  craft ✓

AR   ar-slide-1    whitespace 97.8% (cover)  palette ✓  craft ✓
AR   ar-slide-2    whitespace 84.9%          palette ✓  craft ✓
AR   ar-slide-3    whitespace 84.3%          palette ✓  craft ✓
```

## Mirror-equivalence test (check 14)

Every RTL slide overlays its LTR counterpart after horizontal flip at ≥ 95% structural similarity.

```
slide 1: mirror-similarity 0.983  OK
slide 2: mirror-similarity 0.965  OK
slide 3: mirror-similarity 0.975  OK
```

## Arabic typography (check 11, RTL-specific residual)

The Arabic proof (`proof_deck_ar.js`) stresses contextual shaping. Early
versions broke on `charSpacing: T.track.eyebrow` applied to Arabic runs —
"التفاعل" rendered as seven isolated letters. Fixed at the skill level via
`safeTrack(text, trackValue)` in `scripts/helpers.js`. The helper zeroes
tracking when `hasArabic(text)` is true, leaves Latin tracking intact.

Every deck built through the skill helpers now handles Arabic correctly
without per-deck intervention. See `references/rtl-discipline.md` §9.

## Vision-agent verdict

> SDK unavailable. Manual brief written to: `validation-brief.md`
> Paste the brief into a vision-capable Claude session with the rendered
> images attached, then paste the returned verdict below this line before
> marking the artifact shippable.
