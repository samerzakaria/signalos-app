<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# PPT Design System — SignalOS Visual

This is the production manual for every SignalOS `.pptx` file. Read it in full before writing a single line of a new deck script. The tokens are non-negotiable; the archetypes are closed.

---

## 0. Setup

```js
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const { T, mkCard, mkBase, mkHero, DIR } = require("../assets/tokens.js");

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
// Convenience aliases
const { W, H, MARGIN, CW } = T.deck;
```

**Define once, per presentation:**

```js
pres.author = "SignalOS v1.0";
pres.company = "SignalOS";
pres.title = "…";
const DIR_MODE = "ltr";         // "ltr" or "rtl" — flips layouts and icons
const DECK_LABEL = "SignalOS · Executive Overview · v1.0";
```

---

## 1. The brand chrome on every slide

Every slide gets the same three elements. They are the visual signature of SignalOS. A slide that lacks them is off-system.

### 1.1 Top rule — the "Signal bar"

A 0.04" tall rectangle in `T.indigo` running the full slide width at `y = 0`. The single brightest brand cue. Never removed.

### 1.2 Footer bar (0.28" tall)

Bottom of slide, full width, `T.ink` fill. Contents:

- **Left (from deck margin)** — SignalOS wordmark, 8pt Calibri bold, colour `T.paper`, character spacing 2.
- **Centre** — slide number, 8pt Calibri regular, colour `T.muted`.
- **Right** — `DECK_LABEL  · v1.0`, 7pt Calibri bold, colour `T.paper`, aligned right.

On the Operating Model deck only, right-hand slot reads:  
`Structure reference: Gartner IT Operating Model · SignalOS v1.0 `.

### 1.3 Slide-number tick

A 0.02" tall rectangle in `T.signal`, 0.5" wide, placed at the baseline above the footer bar — visual cue tied to slide index. Adds texture without clutter.

**RTL note.** In `DIR_MODE = "rtl"`: wordmark moves right, date/label row moves left, tick mirrors.

---

## 2. The nine archetypes

### 2.1 Open (dark cover)

- Background: `T.indigo`, solid.
- Subtle atmosphere: **one** `T.indigoDk` radial disc, top-right, 40% transparent, 4" diameter. One. Not three.
- Eyebrow row — 8pt, `T.signal`, tracking 4, uppercased. E.g. `SIGNALOS · OPERATING MODEL`.
- Title — 44pt Calibri bold, `T.paper`, tracking −1, at `(MARGIN, 2.0)`. One line, max 40 characters.
- Subtitle — 13pt Calibri regular, `T.paper` at 70% opacity (`transparency: 30`), at `(MARGIN, 2.95)`.
- Rule — 0.25" wide × 0.04" tall rectangle in `T.signal` at `(MARGIN, 3.35)`.
- Meta row — 10pt Calibri, `T.paper` 60% opacity, at `(MARGIN, H − 0.85)`. Format: `Presenter · 18 April 2026 · v1.0`.

### 2.2 Thesis (one sentence)

- Background: `T.paper`. No cards, no chart, no icons.
- Centered horizontally. Vertically at 40% down.
- One sentence, 28pt Calibri regular, `T.ink`, max 18 words. That is the entire slide.
- Small attribution line 11pt Calibri, `T.muted`, centred beneath the sentence, with a 0.6" rule in `T.signal` between sentence and attribution.

Use once per deck. More than once and it loses weight.

### 2.3 Pillar (3 columns)

- Three equal columns at `colX(0..3)`, `colX(4..7)`, `colX(8..11)`.
- Each column:
  - **Stage chip** — 0.06" tall bar in the column's stage colour (`T.indigo`, `T.trust`, `T.signal` — never more than three unique column colours).
  - **Eyebrow** — 8pt, tracking 4, uppercased, colour = column stage.
  - **Title** — 18pt Calibri bold, `T.ink`, max 4 words.
  - **Body list** — 3 bullet rows, 11pt `T.slate`, each max 12 words. Bullets set via `bullet: true`.
- White card surface, `T.rule` 0.5pt border, `mkCard()` shadow. No tint.

### 2.4 Gate

The canonical SignalOS gate visual — used wherever a gate is named.

- **Hex glyph** — a regular hexagon, flat-top, 1.4" wide, fill `T.gate`, stroke `T.indigoDk` 1.5pt. Inside: gate number in 44pt Calibri bold, `T.ink`.
- Positioned at `(MARGIN, 1.3)`.
- To the right, stacked:
  - Eyebrow `GATE ${n}`, 8pt `T.signal` tracking 4.
  - Gate name, 28pt Calibri bold `T.ink`.
  - Owner line, 13pt `T.slate`, "Signed by: Product Owner" etc.
  - Artifacts signed here — 11pt bullet list `T.slate`.
- Bottom of slide: a horizontal track with all 6 gate hexes in miniature (0.35" wide), the current gate highlighted (filled `T.signal`), others in `T.rule`. A visual progress indicator across the deck.

### 2.5 Flow

- Horizontal track at `y = 2.4`, 0.03" tall, `T.rule`, spanning `(MARGIN, CW)`.
- Gate hexes (0.5") placed ON the track at each phase boundary — same glyph as archetype 2.4, miniaturised.
- Between gates: phase card — 11pt title `T.ink`, 10pt sub `T.muted`, and an arrow pointing forward (`FiArrowRight` in LTR, `FiArrowLeft` in RTL).
- Below the track: artifact callouts. Each phase shows the artifact produced, in a 0.4" pill with `T.wash` fill and `T.rule` border.

### 2.6 Matrix

The Operating Model deck's primary archetype. Up to 3×3.

- Column headers across the top — 13pt Calibri bold `T.paper` on an `T.indigo` band. In LTR: left-to-right. In RTL: right-to-left.
- Row headers on the left (or right, in RTL) — same spec on a slightly narrower band.
- Cell — `T.paper` surface, `T.rule` 0.5pt border, `mkCard()` shadow. Inside:
  - 10pt eyebrow (optional stage label) tracking 1.
  - 13pt Calibri bold title `T.ink`.
  - 10pt body `T.slate`, max 3 lines.
- Cells align on the grid — no manual offsets.

### 2.7 Proof

- Insight — slide title area, 18pt Calibri bold `T.ink`, max 12 words. States the insight, does not label the chart.
- Chart — left 64% of content width. Always override defaults (see §4).
- Right 36%: two stacked supporting cards. Each card: 0.06" left accent in `T.signal`, 10pt eyebrow, 13pt value, 10pt muted caption.

### 2.8 Quote

- Background `T.wash`.
- Large open-quote glyph (Georgia serif, 160pt, `T.indigoDk`, 70% opacity) at top-left of the quote zone.
- The Belief text — 28pt Calibri regular `T.ink`, italic, centred, max 40 words.
- 0.6" rule in `T.signal` beneath.
- Attribution — 11pt Calibri bold `T.slate`, "Signed by Product Owner · 2026-04-16".
- Context pills — bottom of slide, 9pt Calibri bold `T.paper` on `T.indigo` 82% transparent rounded rectangles. Max 3 pills.

### 2.9 Close

- Background `T.indigo`. One radial disc of `T.indigoDk`, bottom-right, 40% transparent.
- Centred title: 44pt Calibri bold `T.paper`.
- Below: 28pt Calibri regular `T.paper` summary sentence, max 20 words.
- Rule in `T.signal`.
- Three next-step pill cards across the bottom at `y = 4.3`: each 2.8" wide, 0.7" tall, `T.indigoDk` fill, 11pt Calibri bold `T.paper` title, 9pt subtitle.
- Closing footer link: 8pt `T.paper` 60% opacity, `DECISION-DNA.md · Wave Debrief Ceremony`.

---

## 3. Spacing and alignment rules

- **Top of content** — `y = 0.70` after the signal bar and eyebrow band, never higher.
- **Bottom of content** — no shape inside `(H − 0.36)` except footer bar and tick.
- **Card padding** — internal text at least `0.18"` from card edge.
- **Between cards** — at least `0.15"`, equal to one gutter.
- **Between section groups** — `0.30"` minimum.
- **Line length** — no text box wider than 7.2" at any point (roughly 60 chars at 11pt Calibri).
- **Vertical rhythm** — snap to the 0.1" baseline. `y` values always end in 0.0 or 0.5 tenths.

---

## 4. Charts

```js
slide.addChart(pres.charts.BAR, data, {
  chartColors: [T.indigo, T.trust, T.signal, T.gate],
  chartArea:   { fill: { color: T.paper }, roundedCorners: false },
  plotArea:    { fill: { color: T.paper } },
  catAxisLabelColor: T.muted,
  valAxisLabelColor: T.muted,
  catAxisLabelFontSize: 9,
  valAxisLabelFontSize: 9,
  catAxisLabelFontFace: T.font.latin,
  valAxisLabelFontFace: T.font.latin,
  valGridLine: { color: T.rule, size: 0.5 },
  catGridLine: { style: "none" },
  legendFontSize: 9,
  legendFontColor: T.slate,
  legendFontFace: T.font.latin,
  showLegend: data.length > 1,
  showValue: false,
  barGapWidthPct: 40
});
```

**Forbidden on SignalOS charts:** 3D effects, bevels, gradients, drop shadows on bars, pie charts with >5 slices, doughnut holes under 50%, auto-generated default palettes, "Chart Title" text (the slide title IS the chart title — see archetype 2.7).

---

## 5. Typography — absolute rules

- Every `fontFace` is `T.font.latin` (Calibri). On an RTL slide, it remains Calibri — Calibri's Arabic glyphs handle the shaping.
- Every `fontSize` is from `T.type.*`. Never arbitrary.
- Bullets use `{ bullet: true }`. Never Unicode `•`.
- Line spacing uses `paraSpaceAfter` (pt). Never `lineSpacing` when bullets are on.
- `charSpacing` only on eyebrows (4), table headers (1), and display titles (−1 via negative number — pptxgenjs accepts this).
- Never mix bold and italic in the same run. Italic is for Quote archetype body only.

---

## 6. Icon rules

```js
async function iconPng(Icon, color = "#FFFFFF", size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(Icon, { color, size: String(size), strokeWidth: 2 })
  );
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}
```

- Always `strokeWidth: 2`. Never 1.5. Never 2.5.
- One family: `react-icons/fi` (Feather).
- White icons on indigo/dark surfaces; `T.indigo` or `T.signal` on light surfaces.
- Directional icons (see `tokens.js` `DIR.shouldFlip`) are swapped at script level in RTL mode — never mirrored with CSS `transform: scaleX(-1)` which corrupts the PNG stroke.

---

## 7. Pitfalls — known failure modes

1. **Colour leakage** — a stray `"#6B2FA0"` (old AYN purple) in a copy-pasted block. Audit with `grep -oE '[0-9A-F]{6}' script.js | sort -u` before running.
2. **Shadow reuse** — declaring `const shadow = mkCard(); ... shapeA.addShape({shadow}); shapeB.addShape({shadow});` causes the second shape to inherit corrupted shadow. Always call `mkCard()` fresh.
3. **Unicode bullets** — `"• item"` renders as boxes in some Office installs. Always `{ bullet: true }`.
4. **Off-scale type** — `fontSize: 12` or `fontSize: 14`. Fails the critic. Use 11 or 13.
5. **Footer over content** — placing anything below `y = H − 0.36` collides with the footer bar.
6. **Chart defaults** — forgetting `chartArea.fill` leaves a grey box. Always override.
7. **RTL icon forgotten** — a forward arrow in an Arabic slide breaks the eye. Check every `Icon` against `DIR.shouldFlip()` when `DIR_MODE === "rtl"`.

---

## 8. QA — required before every handoff

```bash
node build_deck.js
python3 ../../../../core/execution/skills/design/scripts/soffice_convert.py --pdf deck.pptx
pdftoppm -jpeg -r 150 deck.pdf slide
python3 ../../../../core/execution/skills/design/scripts/critic.py slide-*.jpg
```

Critic must return `PASS on 14/14 checks`. Any fail → fix → re-render → re-run. Ship only on green.
