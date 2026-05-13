<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# RTL Discipline — SignalOS Visual

SignalOS supports right-to-left languages (Arabic, Hebrew, Persian, Urdu) as a first-class output direction, not a retrofit. This reference is binding: every artifact that may ever be translated to or localised in RTL must conform to these rules, even when the first render is LTR.

---

## 1. What flips, what does not

### RTL is a full layout mirror — not a text-align change

Setting a deck to RTL does NOT just change `text-align` from `left` to `right`.
Every **shape** on every **slide** (or page) mirrors its X coordinate around
the canvas's vertical axis. When an Arabic reader opens the deck, the
composition they see is the mirror image of the LTR render — identical
rhythm, identical whitespace ratios, identical anchor points relative to
their reading flow. Their eye lands on the same content at the same
moment the Latin reader's eye does.

The transform is mechanical: `x_rtl = canvasW - x_ltr - shape_w`. It is
implemented in one place (`DIR.mirrorX` in `assets/tokens.js`) and every
helper in `scripts/helpers.js` routes through it. Deck-level code must
never hard-code a dir-dependent x — always pass the LTR x through the
helper. Layout-level lists (pillar columns, flow phases, matrix rows)
are reversed at iteration time via `DIR.seq`, not at render time.

The single most common RTL bug — caught by check 14 of the critic — is
text direction flipped correctly but shapes stranded on the wrong side
of the canvas. That bug is structurally prevented by the helpers here.

### Flips (every RTL render)

- **Text alignment.** Default changes from `left` to `right`. `text-align: start/end` is always used, never hard-coded `left/right`.
- **Shape X coordinates.** Every shape, image, chart, card, icon, rule, tick, hex, and text-frame anchored to a non-centre column. `x_rtl = canvasW - x_ltr - shape_w`.
- **Reading order across multi-item layouts.** In the Flow archetype, phase 1 lives on the right, phase 6 on the left. In Matrix, column 1 is rightmost, column 9 is leftmost. The grid is mirrored at layout time, not at render time.
- **List anchoring.** Bullets and numbers sit on the right.
- **Chrome position.** Wordmark in the footer moves to the right slot; deck label moves to the left slot; the slide-number tick moves to the right side.
- **Directional icons** (see §3). `FiArrowRight` becomes `FiArrowLeft` in the icon call itself — we swap the component, never apply a `scaleX(-1)` transform (that corrupts Feather strokes).
- **Atmosphere shapes.** The Open-archetype disc and any decorative blur/orb moves to the reader's end corner (top-left in RTL, top-right in LTR).
- **Gate track direction.** Gate 0 sits at the reader's start edge (right in RTL, left in LTR). Gate 5 sits at the end edge. The progression follows the reader's eye.
- **Quotation marks.** `"..."` becomes `„..."` (or proper Arabic quotes `«...»` for Arabic text).
- **Page numbering direction.** Slide 1 appears at the right-most position in any thumbnail strip.

### Does not flip

- **Non-directional icons** (see `tokens.json` blacklist). `FiShield`, `FiUser`, `FiStar`, `FiChart`, `FiCalendar`, `FiClock`, etc. A shield is a shield in any language.
- **Shadows and depth cues.** Light source is top-left in both LTR and RTL. This is physics, not direction. Changing shadow direction makes RTL slides look off-system.
- **Charts' numeric axes.** A bar chart's y-axis reads bottom-up in both; a timeline chart's x-axis reads left-to-right *chronologically* in both (but the labels are right-anchored in RTL).
- **Code, commands, URLs, file paths, artifact IDs (A-1…A-13), gate numbers (0–5).** These are Latin-script technical tokens and always render LTR, even inside Arabic paragraphs. Wrap in bidi-isolation (see §4).
- **Logo and wordmark glyph itself.** "SignalOS" is a Latin wordmark — do not mirror the letterforms.

---

## 2. Setting direction

### PPT (pptxgenjs)

The direction flag is set at deck level:

```js
const DIR_MODE = "rtl";
```

Every helper in `scripts/helpers.js` reads this flag and routes shape X
coordinates through `DIR.mirrorX(dir, x, w, canvasW)`. Deck-level code
must do the same for any custom shape it draws:

```js
const { mx, DIR } = require("./helpers.js");

// WRONG — strands the card on the left in RTL:
slide.addShape(pres.shapes.RECTANGLE, { x: MARGIN, y: 2, w: 3, h: 2, ... });

// RIGHT — mirrors when dirMode is rtl, no-op when ltr:
slide.addShape(pres.shapes.RECTANGLE, { x: mx(dirMode, MARGIN, 3), y: 2, w: 3, h: 2, ... });
```

For lists (pillar columns, flow phases, matrix cells), reverse the
**iteration**, not the math:

```js
const columns = DIR.seq(dirMode, ["Engage", "Enable", "Deliver"]);
columns.forEach((col, i) => {
  const x = T.deck.colX(i * 4);   // same math in both directions
  drawColumn(slide, col, x);       // Engage lands on the RIGHT in RTL
});
```

Text frames with Arabic content also get `{ rtlMode: true }` — a pptxgenjs option that tells PowerPoint the paragraph is RTL at render time. That is a separate concern from shape position.

### HTML (Blueprint, posters)

Set on the `<html>` element:

```html
<html dir="rtl" lang="ar">
```

Then in CSS use logical properties everywhere:

```css
.card {
  padding-inline-start: 1rem;   /* not padding-left */
  padding-inline-end:   1rem;   /* not padding-right */
  border-inline-start:  3px solid var(--signal);
  margin-inline-start:  0.5rem;
}
```

Grid direction follows `dir` automatically — a 16-column grid in `dir="rtl"` places `span-4 / span-8 / span-4` visually right-to-left without any extra code. Only hard-coded `grid-column-start: 1` breaks this; always use `span` or named grid lines instead.

### Docx (python-docx)

Per paragraph:

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def set_rtl(paragraph):
    pPr = paragraph._p.get_or_add_pPr()
    bidi = OxmlElement('w:bidi'); bidi.set(qn('w:val'), '1')
    pPr.append(bidi)
    for run in paragraph.runs:
        rPr = run._r.get_or_add_rPr()
        rtl = OxmlElement('w:rtl'); rtl.set(qn('w:val'), '1')
        rPr.append(rtl)
```

Apply it to every paragraph in an RTL doc, then set the document's default text direction to RTL via the section's `textDirection`.

---

## 3. Icon flip algorithm

Before calling `iconPng(Icon, …)` on any slide, run:

```js
function resolveIcon(iconName, dirMode) {
  if (dirMode !== "rtl") return require("react-icons/fi")[iconName];
  if (!DIR.shouldFlip(iconName)) return require("react-icons/fi")[iconName];
  const swap = {
    FiArrowLeft: "FiArrowRight", FiArrowRight: "FiArrowLeft",
    FiChevronLeft: "FiChevronRight", FiChevronRight: "FiChevronLeft",
    FiCornerDownLeft: "FiCornerDownRight", FiCornerDownRight: "FiCornerDownLeft",
    FiCornerUpLeft: "FiCornerUpRight", FiCornerUpRight: "FiCornerUpLeft",
    FiSkipBack: "FiSkipForward", FiSkipForward: "FiSkipBack",
    FiRewind: "FiFastForward", FiFastForward: "FiRewind",
    FiLogIn: "FiLogOut", FiLogOut: "FiLogIn"
  };
  const resolved = swap[iconName] || iconName;
  return require("react-icons/fi")[resolved];
}
```

Rule: swap the component, never transform it. A Feather icon's stroke joints are non-symmetric; mirroring in CSS or SVG matrix produces jagged edges at render.

---

## 4. Bidi isolation

Inside an Arabic paragraph, any run of Latin characters (a command, an artifact ID, a number, a URL) must be bidi-isolated to prevent the Arabic paragraph's right-to-left flow from interfering with its rendering.

**HTML:**

```html
<p>
  أنجزت العلامة
  <bdi class="mono">A-8 · /signal-plan</bdi>
  قبل بداية ملحمة البناء.
</p>
```

`<bdi>` is the semantic element for this; `dir="auto"` also works on inline spans. Always wrap file paths, command names, gate IDs, artifact IDs, and numeric tokens with more than 2 digits.

**PPT:** inside a mixed-language text frame, split the runs and set each run's language. Technical tokens go into a Latin run with no bidi override.

**Docx:** use `run.font.complex_script = False` on Latin tokens inside an RTL paragraph. Combined with the `w:rtl` flag on the surrounding run, Word handles the isolation.

---

## 5. Layout-time mirroring (Flow, Matrix)

These archetypes iterate content from left-to-right by default. In RTL mode, reverse the iteration before drawing, **do not rely on rendering to mirror the output**.

```js
// Flow archetype
const phases = DIR_MODE === "rtl" ? [...PHASES].reverse() : PHASES;
phases.forEach((phase, i) => {
  const x = T.deck.colX(i * 2);      // same column math, reversed input
  drawPhase(slide, phase, x);
});
```

This keeps the visual reading rhythm correct in RTL: the first phase appears at the reader's entry point (right edge), the last phase at the exit (left edge).

---

## 6. QA for RTL

Every RTL render must pass an extra sanity test alongside the 14-check critic:

**Mirror equivalence test.** Render the same slide twice — once in LTR with content `[A, B, C]`, once in RTL with the same content. Overlay them with the RTL image horizontally flipped. The two layouts should be nearly identical in structure (cards aligned, spacing consistent, no drift). Differences in typography (Arabic letterforms are taller/shorter than Latin) are expected; differences in card alignment or spacing are not.

```bash
# Pseudo-command — critic.py has the real implementation
python3 ../scripts/critic.py --mirror-test ltr.png rtl.png
```

Critic reports the structural delta as a percentage. Pass threshold: ≤ 8%. Anything more means the RTL render has drifted and needs rework.

---

## 7. Common RTL failures

1. **Forgotten icon swap.** A forward arrow on the flow track in an Arabic slide reads as a "back" arrow. Always run `resolveIcon` through the flip algorithm.
2. **Hard-coded `text-align: left`.** Always use `start`. Also applies to `margin-left`, `padding-left`, `border-left` — use `margin-inline-start` etc.
3. **Numbers shattered across paragraph boundary.** "Gate 5" becomes "5 Gate" visually because the `5` reorders. Wrap in `<bdi>` or split into isolated runs.
4. **Grid column 1 still on the left.** A grid template with `grid-column: 1 / 5` is absolute, not logical. Use `grid-column: span 4` so the flow respects `dir`.
5. **Chart category axis labels.** pptxgenjs does not auto-RTL category labels. Set the category array in reverse for RTL charts when the category order has semantic direction (e.g. time).
6. **Shadow mirrored with content.** Don't. Shadows stay LTR-oriented in both modes because light direction is physical.
7. **Letterspacing on Arabic runs.** `charSpacing: 24` on an Arabic eyebrow breaks contextual shaping — "التفاعل" renders as "ا ل ت ف ا ع ل", seven isolated letters. Arabic letters must stay joined; tracking is a Latin-only affordance. Use `safeTrack(text, trackValue)` from `scripts/helpers.js` on every tracked run (§9).

---

## 9. Typography on Arabic runs

Arabic (and other Unicode Arabic-family scripts — Persian, Urdu, Hebrew partly) is **contextually shaped**: every letter has up to four forms (isolated, initial, medial, final) and the shape at render time depends on which letters it joins to on either side. A letter with a space between it and its neighbour is treated as isolated.

Consequence: **any positive `charSpacing` value on an Arabic text run breaks joining.** The letters separate, each snaps to its isolated form, and the word reads as a sequence of disconnected glyphs. It's the single most common Arabic typography defect in decks that were designed for Latin first.

### The rule (binding)

Arabic text runs never receive charSpacing tracking. Latin text runs — including Latin technical tokens inside an RTL deck (file paths, artifact IDs, version numbers) — keep their tracking.

### Implementation

The skill exposes two helpers from `scripts/helpers.js`:

```js
const { hasArabic, safeTrack } = require("./helpers.js");

hasArabic("التفاعل")            // → true
hasArabic("Engage · Enable")    // → false
hasArabic("A-5 · BELIEF.md")    // → false

safeTrack("التفاعل", T.track.eyebrow)  // → 0
safeTrack("ARCHETYPE", T.track.eyebrow) // → T.track.eyebrow (unchanged)
```

Every helper that applies `charSpacing` (addChrome, addHeader, addCover) is already wrapped. Deck-level code must do the same for any custom tracked text:

```js
// WRONG — breaks Arabic shaping:
slide.addText(pillarName, {
  fontSize: T.type.xs, bold: true, charSpacing: T.track.eyebrow,
  ...
});

// RIGHT — tracks Latin, leaves Arabic joined:
slide.addText(pillarName, {
  fontSize: T.type.xs, bold: true,
  charSpacing: safeTrack(pillarName, T.track.eyebrow),
  ...
});
```

The Unicode ranges `hasArabic` checks:

```
U+0600-06FF  Arabic
U+0750-077F  Arabic Supplement
U+08A0-08FF  Arabic Extended-A
U+FB50-FDFF  Arabic Presentation Forms-A
U+FE70-FEFF  Arabic Presentation Forms-B
```

### Font stack

Arabic runs should resolve to IBM Plex Sans Arabic (primary) with Calibri as the PPT-native fallback. The deck helpers use `fontFace: T.font.latin` which in PPT cascades to the system's Arabic fallback for characters in the Arabic ranges. On Windows this resolves correctly. On LibreOffice/Linux the fallback depends on the installed Arabic face; if Plex is unavailable, Noto Sans Arabic is the secondary.

### Numeral style (binding)

SignalOS uses **Western Arabic numerals (0–9) everywhere, including inside Arabic paragraphs.** Never Arabic-Indic digits (٠–٩) and never Persian digits (۰–۹). The rule is absolute — no per-artifact opt-out, no "editorial choice."

Why SignalOS is strict on this:

- Numbers in SignalOS content are technical tokens — gate numbers (Gate 1, Gate 5), artifact IDs (A-5, A-7), version numbers (v1.0), dates (18 April 2026), SLAs (1–5 days, 45-day pilot), counts (4 humans, 10 agents, 9 skills). A Latin reader and an Arabic reader should both see the same tokens so cross-referencing across decks works.
- Mixing Arabic-Indic digits with middle-dots, hyphens, and surrounding Latin tokens triggers bidi-reorder bugs in LibreOffice's engine. Sticking to Western digits eliminates an entire class of render defects.
- Arabic-Indic digits are common in narrative Arabic prose, but SignalOS content is not prose — it is technical documentation and governance artifacts.

**Enforcement.** The helper `toWesternDigits(text)` normalises any Arabic-Indic or Persian digits to Western. It runs automatically inside every skill helper (addChrome, addHeader, addCover) so user-supplied strings cannot smuggle non-Western digits into a rendered slide. Source strings should also be written with 0–9 directly — the normaliser is defence-in-depth, not a licence to write sloppy source.

```js
const { toWesternDigits } = require("./helpers.js");

toWesternDigits("١٦ أبريل ٢٠٢٦")  // → "16 أبريل 2026"
toWesternDigits("نسخة ۱٫۰")       // → "نسخة 1٫0"  (٫ decimal separator stays)
toWesternDigits("18 April 2026")  // → "18 April 2026"  (unchanged)
```

### LibreOffice bidi rendering quirks (known issues)

The middle-dot (`·`) sometimes fuses with adjacent Arabic-Indic digits in LibreOffice's bidi engine. With the Western-numeral rule enforced above, this quirk no longer applies to SignalOS output. PowerPoint on Windows renders correctly in either case.

---

## 10. Languages supported

- **Arabic** — primary RTL target. Uses IBM Plex Sans Arabic.
- **Hebrew** — falls back to IBM Plex Sans (limited glyph coverage) → Noto Sans Hebrew.
- **Persian (Farsi)** — same as Arabic with extended glyphs (`ک`, `گ`, `پ`, `چ`, `ژ`, `ی`). Plex Sans Arabic covers these.
- **Urdu** — uses Noto Nastaliq Urdu; falls back to Plex Sans Arabic in Naskh style if Nastaliq isn't available.

Declare the language in HTML (`lang="ar"`, `lang="he"`, `lang="fa"`, `lang="ur"`) so the font stack resolves correctly and so screen readers pick the right voice.
