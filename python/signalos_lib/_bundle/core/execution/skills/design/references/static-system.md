<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Static Design System — SignalOS Visual

For artifacts where typography precision and print quality matter more than scripting convenience: the **Blueprint**, printed one-pagers, governance `.docx`, posters, and reference PDFs.

---

## 1. Why static ≠ PPT

Decks are produced with PptxGenJS and must survive on any Windows/Mac running Office. Fonts must be universal (Calibri), layout math is imperial inches, and we accept the grey box PptxGenJS calls a shadow.

Static artifacts have no such constraint. The Blueprint is rendered once to PDF/PNG, the fonts are **baked in**, so we can use IBM Plex and demand print-quality typography. This reference covers two production paths:

- **Path A — HTML/CSS + headless Chromium (Blueprint, posters, one-pagers).** Typography precision, web-font embedding, vector output.
- **Path B — Python `python-docx` (governance .docx).** Cover page, styled headings, callouts, inline images.

---

## 2. Fonts — the Plex family

Install once per rendering machine (free, OFL):

```bash
mkdir -p ~/.fonts/IBMPlex && cd ~/.fonts/IBMPlex
for f in \
  IBMPlexSans-Light.ttf IBMPlexSans-Regular.ttf IBMPlexSans-SemiBold.ttf IBMPlexSans-Bold.ttf \
  IBMPlexSerif-Light.ttf IBMPlexSerif-Regular.ttf IBMPlexSerif-SemiBold.ttf IBMPlexSerif-Bold.ttf \
  IBMPlexMono-Regular.ttf IBMPlexMono-SemiBold.ttf \
  IBMPlexSansArabic-Light.ttf IBMPlexSansArabic-Regular.ttf IBMPlexSansArabic-SemiBold.ttf IBMPlexSansArabic-Bold.ttf
do
  curl -L -o $f "https://github.com/IBM/plex/raw/master/IBM-Plex-Sans/fonts/complete/ttf/$f" 2>/dev/null || true
done
fc-cache -f -v
```

(Production machines fetch these once in the build container; the rendered PDFs ship with fonts embedded via `@font-face` + Chromium's `--no-pdf-header-footer` → `@page { ... }`.)

**Usage per role:**

| Role | Face | Weights used |
|------|------|--------------|
| Display (Blueprint title, OM pillar names) | IBM Plex Serif | 300, 700 |
| Body (Latin) | IBM Plex Sans | 400, 600 |
| Body (Arabic) | IBM Plex Sans Arabic | 400, 600 |
| UI / captions | IBM Plex Sans | 400 |
| Commands, gate IDs, paths | IBM Plex Mono | 400, 600 |

No other faces are ever introduced into a SignalOS static artifact.

---

## 3. Path A — HTML/CSS for the Blueprint and one-pagers

### 3.1 Page geometry

The SignalOS Blueprint is landscape 16" × 10" at 300 DPI — a poster-grade single page. One-pagers are A4 portrait.

```css
@page {
  size: 16in 10in;           /* Blueprint */
  /* size: 8.27in 11.69in;   ← A4 portrait, uncomment for one-pagers */
  margin: 0.5in;
}
html, body { margin: 0; padding: 0; }
body {
  background: #FFFFFF;
  color: #0B1221;
  font-family: "IBM Plex Sans", Inter, -apple-system, sans-serif;
  font-weight: 400;
  font-size: 11pt;
  line-height: 1.5;
  font-feature-settings: "ss01", "ss02", "kern", "liga";
  text-rendering: geometricPrecision;
}
```

### 3.2 Token CSS variables (paste into every static artifact)

```css
:root {
  --ink:      #0B1221;
  --slate:    #1F2A44;
  --muted:    #5B6A85;
  --rule:     #E4E9F2;
  --paper:    #FFFFFF;
  --wash:     #F5F7FB;
  --indigo:   #1B2E60;
  --indigoDk: #0B1D4A;
  --trust:    #3B6B8F;
  --signal:   #D95B2B;
  --gate:     #C4A553;
  --ok:       #14734A;
  --risk:     #A3302F;

  --f-display: "IBM Plex Serif", Georgia, serif;
  --f-body:    "IBM Plex Sans", Inter, -apple-system, sans-serif;
  --f-ar:      "IBM Plex Sans Arabic", "Noto Sans Arabic", "Geeza Pro", sans-serif;
  --f-mono:    "IBM Plex Mono", "JetBrains Mono", Menlo, monospace;
}
```

### 3.3 Type scale in CSS

```css
.t-xs      { font-size: 8pt;  line-height: 11pt; }
.t-sm      { font-size: 10pt; line-height: 13pt; }
.t-base    { font-size: 11pt; line-height: 16pt; }
.t-md      { font-size: 13pt; line-height: 18pt; }
.t-lg      { font-size: 18pt; line-height: 24pt; }
.t-xl      { font-size: 28pt; line-height: 32pt; letter-spacing: -0.01em; }
.t-xxl     { font-size: 44pt; line-height: 48pt; letter-spacing: -0.02em; }
.t-display { font-size: 72pt; line-height: 72pt; letter-spacing: -0.03em;
             font-family: var(--f-display); font-weight: 300; }

.eyebrow { font-size: 8pt; font-weight: 700; letter-spacing: 0.25em;
           text-transform: uppercase; color: var(--signal); }
.mono    { font-family: var(--f-mono); font-variant-ligatures: none; }
```

### 3.4 Grid

The Blueprint uses a 16-column grid (1 column per inch of content width):

```css
.grid16 {
  display: grid;
  grid-template-columns: repeat(16, 1fr);
  column-gap: 0.15in;
  row-gap: 0.2in;
}
.span-4 { grid-column: span 4; }
.span-8 { grid-column: span 8; }
.span-12 { grid-column: span 12; }
.span-16 { grid-column: span 16; }
```

### 3.5 Components

**Gate hex (SVG, reusable):**

```html
<svg viewBox="-1 -1 2 2" width="60" height="60">
  <polygon points="1,0 0.5,0.866 -0.5,0.866 -1,0 -0.5,-0.866 0.5,-0.866"
           fill="var(--gate)" stroke="var(--indigoDk)" stroke-width="0.04"/>
  <text x="0" y="0.05" text-anchor="middle" alignment-baseline="middle"
        font-family="var(--f-display)" font-weight="700" font-size="0.9">0</text>
</svg>
```

**Signal tick** — a 0.5" wide × 0.02" tall rule in `--signal`, used to mark live/confirmed signals.

**Pillar header** — `var(--indigo)` band, 72pt Plex Serif Light in `var(--paper)`, subtitle in Plex Sans at 13pt `var(--wash)` 70% opacity.

**Artifact pill** — rounded rectangle, `var(--wash)` fill, `var(--rule)` border 0.5pt, 10pt Plex Sans `var(--slate)`, mono font for A-NN identifier.

### 3.6 HTML → PDF build

**Primary (portable, matches @page precisely):** WeasyPrint.

```bash
pip install --break-system-packages weasyprint
python3 -m weasyprint blueprint.html blueprint.pdf
```

WeasyPrint renders CSS Paged Media specs faithfully — `@page`, `@font-face` (local file paths or URLs), print-specific layout, and web font embedding. Preferred for SignalOS static work because the output is pixel-identical across machines.

**Fallback (when WeasyPrint isn't available or a specific CSS-layout bug surfaces):** headless Chromium.

```bash
chromium --headless --disable-gpu --no-sandbox \
  --no-pdf-header-footer --print-to-pdf=blueprint.pdf blueprint.html
```

PDF → PNG when a PNG version is also needed:

```bash
pdftoppm -png -r 300 blueprint.pdf blueprint
```

---

## 4. Path B — Governance .docx

**Do not use pandoc on markdown to produce governance docx.** The output is unstyled and fails the critic. Use `python-docx` directly so each doc has a cover page, consistent headings, proper callouts, and inline images.

### 4.1 Template skeleton

```python
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT

doc = Document()

# Page setup
section = doc.sections[0]
section.page_width  = Inches(8.5)
section.page_height = Inches(11.0)
section.left_margin = Inches(0.75)
section.right_margin = Inches(0.75)
section.top_margin  = Inches(0.75)
section.bottom_margin = Inches(0.75)

# ---- default style ----
style = doc.styles['Normal']
style.font.name = 'IBM Plex Sans'
style.font.size = Pt(11)
style.font.color.rgb = RGBColor(0x0B, 0x12, 0x21)
```

### 4.2 Heading styles

```python
for lvl, size, weight, color in [
    ('Heading 1', 28, True,  RGBColor(0x1B, 0x2E, 0x60)),
    ('Heading 2', 18, True,  RGBColor(0x0B, 0x12, 0x21)),
    ('Heading 3', 13, True,  RGBColor(0x1F, 0x2A, 0x44)),
]:
    s = doc.styles[lvl]
    s.font.name  = 'IBM Plex Serif' if lvl == 'Heading 1' else 'IBM Plex Sans'
    s.font.size  = Pt(size)
    s.font.bold  = weight
    s.font.color.rgb = color
```

### 4.3 Cover page

Every governance .docx opens with:

1. Top rule: 0.04" tall band in `--indigo`.
2. 2.5" vertical spacer.
3. Eyebrow: `SIGNALOS · v1.0 · GOVERNANCE`, 10pt Plex Sans bold, letter-spaced 4, colour `--signal`.
4. Document title: 44pt Plex Serif Light, colour `--ink`.
5. Subtitle: 13pt Plex Sans, colour `--slate`.
6. Metadata table: author · date · version · artifact ID (A-NN), 10pt Plex Sans.
7. Hard page break.

### 4.4 Callouts

Every `.docx` needs four callout styles — Rule, Why, How to apply, Pitfall:

```python
def callout(doc, kind, body):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.2)
    tag = p.add_run(f'{kind}  ')
    tag.font.size = Pt(9); tag.font.bold = True
    tag.font.color.rgb = {
        'Rule':          RGBColor(0x1B, 0x2E, 0x60),
        'Why':           RGBColor(0x3B, 0x6B, 0x8F),
        'How to apply':  RGBColor(0x14, 0x73, 0x4A),
        'Pitfall':       RGBColor(0xA3, 0x30, 0x2F),
    }[kind]
    run = p.add_run(body)
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x1F, 0x2A, 0x44)
```

### 4.5 Footer

Every page footer (running):
`SignalOS v1.0 · A-NN · [Doc Title] · Page X of Y`

10pt Plex Sans, `--muted`, rule line above in `--rule`.

---

## 5. Validation — before handoff

```bash
# HTML Blueprint
chromium --headless --disable-gpu --no-sandbox \
  --no-pdf-header-footer --print-to-pdf=blueprint.pdf blueprint.html
python3 ../scripts/critic.py blueprint.pdf

# Docx
python3 path/to/build_doc.py
python3 ../scripts/validate_docx.py output.docx
# visual sanity:
libreoffice --headless --convert-to pdf output.docx
pdftoppm -jpeg -r 150 output.pdf page
# walk page-*.jpg one by one
```

Any page that doesn't match the system rules → rework. Never ship a static artifact that was built without this QA pass.
