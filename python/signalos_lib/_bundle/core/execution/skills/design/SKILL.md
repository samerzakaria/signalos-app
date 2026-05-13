---
name: signalos-design
description: "The canonical visual system for every SignalOS artifact — decks, static PDFs, the Blueprint, governance .docx, and the Operating Model deck. Synthesised from McKinsey pyramid discipline, Apple/Linear restraint, Stripe/IBM Plex precision, and Tufte data honesty. RTL-capable from day one. Use whenever creating, rebuilding, or upgrading any SignalOS-visible document. Overrides ayn-docs, elm-docs, and canvas-design for SignalOS-branded outputs."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Skill — signalos-design

## One-liner

The SignalOS Visual System. One palette, one type scale, one grid, nine slide archetypes, bidi-safe. Every SignalOS deliverable passes through this skill before handoff.

## When to invoke

Any task that touches a SignalOS-visible artifact: PPT deck (executive, summary, playbook, operating model), Blueprint, governance .docx, one-pager, poster, or README that ships to an external audience. **Do not** generate a SignalOS document through `ayn-docs`, `elm-docs`, `canvas-design`, or a bare pandoc run. Those produce generic output; this skill produces SignalOS output.

## Two modes

**Mode A — generate from scratch.** Content is in markdown or a brief. Output `.pptx`, `.pdf`, or `.png`.

**Mode B — rebuild an existing file.** A legacy `.pptx`/`.docx` is the input. Extract content with `python -m markitdown` (pptx) or `pandoc` (docx); preserve every word; discard every byte of original styling; rebuild on the SignalOS system.

## Required reading before producing anything

Read the relevant reference files below before writing a single line of script. This is non-negotiable — the skill exists to prevent drift.

- **Every artifact** — `references/critic-rubric.md`, `assets/tokens.js` (or `tokens.json`)
- **PPT deck** — `references/ppt-system.md`
- **Blueprint, poster, static PDF** — `references/static-system.md`
- **Any Arabic or mixed Latin/Arabic output** — `references/rtl-discipline.md`
- **Any governance .docx** — `references/static-system.md` §Docx section

## The nine archetypes — use only these on decks

1. **Open** — dark cover. One-word eyebrow · display title · metadata row.
2. **Thesis** — one sentence centred on a white/wash ground. Nothing else. Used once per deck.
3. **Pillar** — 3 equal columns. Each column: stage chip · title · 3 supporting facts.
4. **Gate** — the canonical SignalOS gate visual. Hex gate glyph · gate name · owner · artifact signed here.
5. **Flow** — left-to-right (or right-to-left in RTL) phase flow with gate hexes inline on the track.
6. **Matrix** — 2×2 or 3×3 grid. Used for the Operating Model deck (3×3), trust-tier matrix (2×2), and like shapes. Never more than 9 cells.
7. **Proof** — one insight line · one chart · two supporting cards. The insight is the slide title; the chart is evidence; the cards are context.
8. **Quote** — a signed Belief, centred. Attribution line below. Used for Gate 1/Gate 5 signature slides.
9. **Close** — summary sentence · next-Wave call to action · footer link to `DECISION-DNA.md`.

**If a slide doesn't fit one of these nine, the slide is wrong.** Redesign the content, not the archetype.

## Named decisions baked in (do not renegotiate)

- **Palette** — 13 tokens only. No colour outside `assets/tokens.js` ever enters a SignalOS artifact.
- **Type scale** — 8 / 10 / 11 / 13 / 18 / 28 / 44 / 72 pt. Every size must be from this list.
- **Weights** — 300 / 400 / 600 / 700. No 500. No 800.
- **Fonts — decks (.pptx)** — Calibri only. Universal, handles Arabic shaping on every Windows install, zero embed risk.
- **Fonts — static (PDF, PNG, web)** — IBM Plex Serif (display) · IBM Plex Sans (body Latin) · IBM Plex Sans Arabic (body RTL) · IBM Plex Mono (commands). Fallback chain in `assets/fonts-fallback.md`.
- **Grid** — 12 columns · 0.1" gutter · 0.5" outer margin on decks · 0.75" on static pages · 0.1" baseline.
- **Shadows** — subtle only. `mkCard`, `mkBase`, `mkHero` factories in `scripts/helpers.js`. Never reuse a shadow object (pptxgenjs mutates).
- **Atmosphere** — no decorative orbs by default. Orbs only on Open and Close archetypes, and only sparingly.
- **Iconography** — one family: `react-icons/fi` (Feather). One stroke weight: 2. Directional icons (arrows, chevrons) flip in RTL; non-directional icons do not.
- **Footer** — every slide. SignalOS wordmark left · slide number centre · `CONFIDENTIAL · v1.0` right. Gartner attribution appears in footer only (never title) on the Operating Model deck.

## The visual-validator gate (mandatory, built in)

**No SignalOS artifact ships without passing `scripts/visual_validate.py`.** The validator is the hard gate between "rendered" and "shippable." It is built into the skill — not a later step a human runs manually. A build script that skips it is incomplete.

The validator does three things in one call:

1. **Rasterises** the .pptx (or .pdf, .docx) into per-slide JPEGs.
2. **Runs the mechanical critic** (`scripts/critic.py`) — palette, whitespace, footer-overflow, mirror-similarity.
3. **Spawns a vision-capable subagent** with the 14-check rubric and hands it every slide image. The subagent did not author the deck. Its brief is in `scripts/visual_validate.py`. Its output is a table with `PASS`/`FAIL` per check per slide and a `VERDICT: N/14 — SHIP` or `REWORK` line.

The validator also runs the **RTL mirror-equivalence test** (check 14) when both an LTR and an RTL render are supplied. Structural similarity below 92% is a fail.

```bash
# Every deck build ends with this. No exceptions.
python3 /.../core/execution/skills/design/scripts/visual_validate.py deck.pptx

# RTL decks: provide both renders so check 14 runs.
python3 /.../visual_validate.py deck-ar.pptx --rtl-pair deck-en.pptx
```

Exit code 0 means the artifact passes every mechanical check and the subagent returned `14/14 — SHIP`. Any other exit code blocks handoff. If the session has no network access to invoke the subagent directly, the validator writes `validation-brief.md` with the rendered images referenced and the vision agent's brief — the author pastes that into a fresh vision-capable Claude session and commits the returned verdict alongside the artifact before shipping. No verdict → no ship.

The mandatory check explicitly named by the validator is the one the proof caught: **no overlapping shapes, no clipped text, no title colliding with the rule below it.** That pattern is where eye-test fails and mechanical checks don't — which is exactly why the validator uses a vision agent, not a regex.

## RTL discipline

If the artifact is Arabic (or mixed Latin + Arabic), set `dir = "rtl"` at presentation/page level. This flips: text alignment, reading order across Flow and Matrix archetypes, directional icons, list anchors, page numbering. It does **not** flip: non-directional icons, shadows (light source is physical, not directional), charts' numeric axes. Numbers, code, and URLs inside Arabic paragraphs wrap in bidi-isolation runs.

**Arabic typography — contextual shaping.** Arabic letters join to their neighbours; `charSpacing > 0` breaks the joins and the word renders as isolated letterforms. The skill exposes `safeTrack(text, trackValue)` from `scripts/helpers.js` which zeros tracking on any run containing Arabic characters and leaves Latin tracking intact. Every helper that applies charSpacing (addChrome, addHeader, addCover) is already wrapped; deck-level code doing its own tracked text must use `safeTrack()` too. This is the difference between "we support Arabic" and "Arabic looks like it was designed for Arabic." See `references/rtl-discipline.md` §9.

**Numerals — Western 0–9 everywhere, always.** SignalOS never uses Arabic-Indic digits (٠–٩) or Persian digits (۰–۹), including inside Arabic paragraphs. Gate numbers, artifact IDs, version numbers, dates, SLAs, counts — all render with Western digits so Latin and Arabic readers share the same technical tokens. The helper `toWesternDigits(text)` normalises any pasted or translated string as defence-in-depth and runs automatically inside `addChrome`, `addHeader`, and `addCover`. Source strings should still be written with 0–9 directly.

QA for RTL artifacts includes a side-by-side LTR/RTL render of the same content. If the RTL version breaks rhythm, the artifact fails.

## Tool stack

**PPT:** PptxGenJS (Node.js). `npm i -g pptxgenjs react react-dom react-icons sharp`.

**Static (PDF/PNG):** Python with matplotlib + Pillow for simple posters, or headless Chromium + HTML/CSS (@media print) for the Blueprint — the Blueprint gets HTML→PDF because typography precision matters more than scripting convenience.

**Docx:** Node.js `docx` library (never pandoc). Cover page, styled headings, callout boxes, inline diagrams.

Output all drafts to the session working folder; final artifacts to the relevant SignalOS sub-folder (decks to `enablement/Training-Layer/`, Blueprint to `executive/`, OM deck to `executive/Operating-Model/`).

## Mandatory QA loop

```bash
# PPT — the validator orchestrates everything (convert → rasterise → mechanical
# critic → vision-agent subagent). Exit 0 required.
python3 core/execution/skills/design/scripts/visual_validate.py deck.pptx

# RTL + LTR paired build (mandatory for any Arabic-locale artifact)
python3 .../visual_validate.py deck-ar.pptx --rtl-pair deck-en.pptx

# Static / Blueprint
python3 .../visual_validate.py blueprint.pdf --kind static

# Docx — additionally runs the mechanical font/palette/size linter
python3 .../visual_validate.py doc.docx --kind docx
python3 .../scripts/validate_docx.py doc.docx
```

Never declare done until the validator returns exit 0 AND the vision-agent verdict reads `14/14 — SHIP`. No exceptions. A marginal pass is a fail. The author reworks the cited slides, re-renders the full artifact, and re-runs the full validator (never partial — a fix on one slide can regress another).

## Provenance

This skill synthesises:

- **AYN's production mechanics** — pptxgenjs patterns, factory shadows, PDF→image QA loop.
- **McKinsey pyramid principle** — the slide title *states the insight*; the body only supports it.
- **Apple / Linear restraint** — whitespace as the primary design element; hierarchy capped at 3 levels.
- **Stripe / IBM Plex precision** — thin rules, subtle colour, governance-grade typography.
- **Tufte data honesty** — no 3D charts, no chartjunk, ink-to-data ratio defended.

None of the above are copied; each contributes a discipline. The result is SignalOS's own visual language.

## Change log

- **1.0.1 (2026-04-16).** Header geometry relaxed to support 2-line titles cleanly. `addHeader` title region grew from h:0.64 to h:0.94; the rule dropped from y:1.20 to y:1.50; subtitle from y:1.32 to y:1.62. Canonical body anchor `T.deck.BODY_Y0 = 1.95` now replaces per-archetype magic numbers in every primitive (Pillar, Matrix, Proof, Gate, TwoCol). Found during Summary 12 build: any title that wrapped to a second line at xl (28pt) was clipping into the rule. The copy-discipline rule (titles ≤ 2 lines, pyramid-principle voice) is unchanged; the layout now forgives 2-line titles instead of punishing them.
- **1.0 (2026-04-16).** Initial lock. 13 tokens · 9 archetypes · IBM Plex static / Calibri deck · RTL-capable · 14-check critic rubric.
