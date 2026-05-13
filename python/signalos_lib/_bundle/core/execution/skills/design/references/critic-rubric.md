<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Visual Critic Rubric — 14 Checks

Every SignalOS artifact is reviewed against these 14 checks before handoff. The critic is run as a subagent, not by the author — independent verification catches what the author's eye has adapted to. A single fail blocks the artifact. No exceptions.

---

## The 14 checks

### A · Discipline (checks 1–4)

**1 · Restraint.** No slide/page uses more than three levels of visual hierarchy (title · subheading · body; or eyebrow · title · body). Count the distinct type sizes + weights in play. If four or more appear, fail.

**2 · Whitespace.** At least 40% of the slide canvas is `--paper` or `--wash` (the two light neutrals). Count the filled pixels. If the slide has cards + chart + sidebar + band all in colour, it fails. Whitespace is the primary design element.

**3 · Grid alignment.** Every element's left/right edge falls on the 12-column grid within ±0.01". Every vertical position snaps to the 0.1" baseline. No manual offsets.

**4 · Palette discipline.** Every colour used is in `tokens.json`. Run a chromatic histogram on the rasterised page; any cluster outside the 13-token set (excluding near-black text antialiasing) is a fail. No off-brand purple, blue, or green.

### B · Typography (checks 5–6)

**5 · Type scale discipline.** Every type size renders at one of `{8, 10, 11, 13, 18, 28, 44, 72}` pt. No arbitrary sizes. Extract every text block's font size; a single `14pt` block fails the slide.

**6 · Weight and face discipline.** Decks use Calibri only; static artifacts use the Plex family only. Weights are restricted to `{300, 400, 600, 700}`. Italics only on Quote-archetype body text. Any use of Arial, Helvetica, Times, Comic Sans, or a decorative face anywhere on the artifact fails.

### C · Narrative (checks 7–9)

**7 · One insight per slide.** The slide title states the insight as a declarative sentence or fragment, not a generic label. Bad: "Performance". Good: "Performance is measured live in the Signal Window, not quarterly in slides." The body supports the insight; it does not introduce a second one.

**8 · Data honesty.** No 3D effects on any chart. No bevels, glows, inner shadows. No pie charts with more than 5 slices. No doughnut holes smaller than 50%. Axis labels present, axis titles present when non-obvious. Legend present when there is more than one series. Sources/footnote present when data has a source. Tufte's ink-to-data ratio ≥ 0.5.

**9 · Slide/page rhythm.** Across the full deck, no single archetype appears 3 times in a row. Specifically: the Pillar archetype cannot be used 3 times consecutively (fatiguing), and the Open/Close archetypes are used exactly once each. The deck has a narrative shape: Open → Thesis → [varied] → Close.

### D · Craft (checks 10–11)

**10 · Iconography consistency.** Every icon in the artifact is from the same family (Feather, `react-icons/fi` for PPT; Lucide or Phosphor for static if declared). Stroke weight is uniform at 2. Colour is white on dark backgrounds, `--ink` or `--signal` on light. No PNG icons from Google Image search. No emoji used as UI iconography.

**11 · Craft residuals — NO OVERLAP, NO CLIP (hard fail).** This is the check that catches what mechanical rules miss. A single instance of any of the following fails the artifact outright:

- Title text overlapping the rule divider below it, or wrapping into the subtitle slot. (Caught on proof slide 2, 2026-04-16 — the reason this check is called out explicitly.)
- Body text overflowing its card/container — clipped at the bottom edge, or running into the next element.
- Icon or image stacked on top of a text glyph at any zoom level.
- Chart labels overlapping axis lines, legend entries, or each other.
- Footer text running into the slide-number tick or wordmark.
- Shadow bleeding into an adjacent card.
- Orphan text (a single word dangling at the end of a paragraph) or widow lines (last line stranded on the next page).
- `TODO`, `TK`, `XXX`, `FIXME`, or placeholder text shipping.
- Shadow opacity above `mkHero`'s 0.13 ceiling.
- **Arabic letterspacing — contextual shaping broken.** Any Arabic word rendered as sequentially separated letterforms (e.g. "التفاعل" appearing as "ا ل ت ف ا ع ل") is a hard fail. Root cause is `charSpacing > 0` on an Arabic text run, which forces every letter into its isolated form. Fix at the skill level via `safeTrack(text, trackValue)` from `scripts/helpers.js` — never per-deck. See `rtl-discipline.md` §9.

This check is run by the vision-agent validator (`scripts/visual_validate.py`) against every rasterised slide. No slide ships with an overlap or a clip.

### E · Direction (checks 12–14 — always run, even on LTR artifacts)

**12 · Direction declared.** The artifact explicitly declares its direction. HTML: `dir="ltr"` or `"rtl"` on `<html>`. PPT: `DIR_MODE` constant present at top of script. Docx: section direction set. An undeclared-direction artifact fails.

**13 · Bidi-isolated technical tokens.** Every Latin technical token (artifact ID, gate number, command, file path, URL) inside a natural-language sentence is rendered correctly in both directions. Specifically, in an RTL artifact, `A-8 · /signal-plan` reads left-to-right within its container, not reordered by the paragraph's RTL flow. Inspect by rendering a sample Arabic line with a command and checking the character order.

**14 · Shape-mirror integrity (RTL only; LTR passes by default).** If the artifact is RTL, every non-centre shape — cards, icons, hexes, rules, chart frames, atmosphere discs, the slide-number tick, the wordmark slot — sits at its correct mirrored X. The same-content LTR render exists alongside the RTL render, and when horizontally flipped overlays at ≥ 92% structural similarity. Run `visual_validate.py --rtl-pair rtl.pptx ltr.pptx`. Text-height deltas from Arabic letterforms are accepted; any card, icon, or rule landing on the wrong side of the canvas is a fail. This is the most common RTL bug and the reason the helpers mechanise the mirror via `DIR.mirrorX`; a bespoke shape that skipped the helper is the usual failure mode.

---

## How to run the critic

### Step 1 — Produce the rendered set

```bash
# PPT
node build_deck.js
python3 ../scripts/soffice_convert.py --pdf deck.pptx
pdftoppm -jpeg -r 150 deck.pdf slide

# Static / Blueprint
chromium --headless --disable-gpu --no-sandbox \
  --no-pdf-header-footer --print-to-pdf=blueprint.pdf blueprint.html
pdftoppm -jpeg -r 300 blueprint.pdf blueprint

# Docx
python3 build_doc.py
libreoffice --headless --convert-to pdf output.docx
pdftoppm -jpeg -r 150 output.pdf page
```

### Step 2 — Spawn the critic subagent

Spawn a fresh Claude agent with this brief:

> You are the SignalOS Visual Critic. You are the toughest visual-communication expert in the world: the restraint of Apple, the precision of Stripe, the pyramid discipline of McKinsey, the typographic rigour of IBM Plex, and the data honesty of Tufte all live in your gaze. You did not author the artifact. You have one job: run the 14-check rubric against the images below and return PASS or FAIL per check with one-sentence evidence. If any check fails, return the fail with a specific rework instruction (which slide, which check, what to change).
>
> Read `references/critic-rubric.md` in full. Read `assets/tokens.json` for the palette and type scale. Then review every image. Do not soften verdicts; a marginal pass is a fail. Return the result as a table and a final verdict (`14/14 PASS — SHIP` or `N/14 — REWORK`).

### Step 3 — Execute the rework loop

If the critic returns a fail:

1. Author fixes the cited slides/pages only.
2. Re-render the full artifact.
3. Re-run the critic on the full artifact (not just the fixed slides — a fix can break adjacent slides).
4. Repeat until the critic returns `14/14 PASS`.

No artifact ships before `14/14 PASS`. This is the hard gate.

---

## Deliberate severity

Some of these checks will feel harsh on a first-pass artifact. That is intentional. The SignalOS brand is "governance with precision" — every artifact is a small demonstration of that precision. A slide that ships with a 14pt heading or a manually-placed card tells the reader the rest of the system is also casual about precision. The critic exists to stop that signal leaking out.

---

## Critic output format

```
SignalOS Visual Critic — Artifact: <name>
Generated: 2026-04-16 · Mode: <ltr|rtl>

| Check | Status | Evidence |
|-------|--------|----------|
| 1 · Restraint       | PASS / FAIL | Slide 7: 4 hierarchy levels (title 28 + subtitle 18 + card-title 13 + body 11 + caption 10) — drop caption. |
| 2 · Whitespace      | PASS        | Mean whitespace ratio 52% across deck. |
| 3 · Grid            | FAIL        | Slide 12: right-hand card at x=7.18, expected x=7.20 (0.02" drift). |
| …                   | …           | … |

VERDICT: 12/14 — REWORK (slides 7, 12)
```

That format is mandatory. It makes the rework loop mechanical, not interpretive.
