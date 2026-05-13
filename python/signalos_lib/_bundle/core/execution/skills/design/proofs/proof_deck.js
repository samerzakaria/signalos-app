// SignalOS Visual — 3-slide proof v2.
// Two builds from one script: LTR and RTL. The RTL build exercises the full
// shape-mirror — every shape flips around the canvas's vertical axis via
// DIR.mirrorX in the helpers.
//
//   Slide 1 — Open (cover) · cover mode: indigo (governance default)
//   Slide 2 — Pillar       · title two-line-capped, widened to full CW
//   Slide 3 — Gate         · mono artifact lines with charSpacing pinned
//
// Usage:
//   node proof_deck_v2.js ltr
//   node proof_deck_v2.js rtl

const path = require("path");
const pptxgen = require("pptxgenjs");
const {
  T, mkCard, mx, DIR,
  addChrome, addHeader, addGateHex, addGateTrack, addCover
} = require("../scripts/helpers.js");

const DIR_MODE = (process.argv[2] || "ltr").toLowerCase();
if (!["ltr", "rtl"].includes(DIR_MODE)) {
  console.error("usage: node proof_deck_v2.js [ltr|rtl]");
  process.exit(2);
}

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
pres.author = "SignalOS v1.0";
pres.company = "SignalOS";
pres.title = `SignalOS Visual — System Proof (${DIR_MODE.toUpperCase()})`;

const DECK_LABEL = `SignalOS · Visual System Proof · ${DIR_MODE.toUpperCase()}`;
const { W, H, MARGIN, CW } = T.deck;

// Content is direction-agnostic; localise strings if needed
const COPY = {
  ltr: {
    openEyebrow:  "SignalOS · Visual System",
    openTitle:    "The system, rendered.",
    openSubtitle: "Three archetypes, one palette, one type scale, one grid — all locked.",
    openMeta:     "Authored by Mohammed Shaban & Samer Zakaria · 18 April 2026 · v1.0.3",
    pillarEyebrow:  "Archetype · Pillar",
    pillarTitle:    "Three columns. One insight each.",
    pillarSubtitle: "How the SignalOS Operating Model deck frames Engage · Enable · Deliver.",
    gateEyebrow:  "Archetype · Gate",
    gateTitle:    "Gate 1 — Belief signed.",
    gateSubtitle: "The canonical SignalOS gate glyph. One per gate reference throughout every deck.",
    pillarNames:  ["Engage", "Enable", "Deliver"],
    pillarLabels: [
      "How IT engages with the enterprise",
      "How IT is resourced",
      "How IT does the work"
    ],
    pillarBodies: [
      [
        "Performance — live dashboards, not quarterly slides.",
        "Decision Rights — six named gates, four named humans.",
        "Financials — Belief is the 2-week funding unit."
      ],
      [
        "Organisational Structures — 4 humans · 10 agent seats.",
        "Sourcing — seven tool adapters, portable across vendors.",
        "Talent — Trust Tiers T1/T2/T3 per surface."
      ],
      [
        "Delivery Model — Wave unit, 1–5 days, six gates.",
        "Tools & Platforms — 10 commands, 7 skills, 5 hooks.",
        "Workplaces — squad + swarm, parallel worktrees."
      ]
    ],
    gateSignedBy: "Signed by · Product Owner",
    gateInsight:  "Problem · Bet · Signal",
    gateBody:     "The Belief states the 2-week falsifiable bet. The Signal states the live observation that will confirm or falsify it. The PO signs before Wave execution begins.",
    gateArtifacts: ["A-5 · BELIEF.md", "A-7 · ROLE_ACTIVATION_CARD.md"]
  },
  rtl: {
    // For the RTL build we keep English text to make the mirror-equivalence
    // test work (check 14). Arabic glyphs would shift heights and inflate
    // the delta. The test only needs SAME content in both directions.
    openEyebrow:  "SignalOS · Visual System",
    openTitle:    "The system, rendered.",
    openSubtitle: "Three archetypes, one palette, one type scale, one grid — all locked.",
    openMeta:     "Authored by Mohammed Shaban & Samer Zakaria · 18 April 2026 · v1.0.3",
    pillarEyebrow:  "Archetype · Pillar",
    pillarTitle:    "Three columns. One insight each.",
    pillarSubtitle: "How the SignalOS Operating Model deck frames Engage · Enable · Deliver.",
    gateEyebrow:  "Archetype · Gate",
    gateTitle:    "Gate 1 — Belief signed.",
    gateSubtitle: "The canonical SignalOS gate glyph. One per gate reference throughout every deck.",
    pillarNames:  ["Engage", "Enable", "Deliver"],
    pillarLabels: [
      "How IT engages with the enterprise",
      "How IT is resourced",
      "How IT does the work"
    ],
    pillarBodies: [
      [
        "Performance — live dashboards, not quarterly slides.",
        "Decision Rights — six named gates, four named humans.",
        "Financials — Belief is the 2-week funding unit."
      ],
      [
        "Organisational Structures — 4 humans · 10 agent seats.",
        "Sourcing — seven tool adapters, portable across vendors.",
        "Talent — Trust Tiers T1/T2/T3 per surface."
      ],
      [
        "Delivery Model — Wave unit, 1–5 days, six gates.",
        "Tools & Platforms — 10 commands, 7 skills, 5 hooks.",
        "Workplaces — squad + swarm, parallel worktrees."
      ]
    ],
    gateSignedBy: "Signed by · Product Owner",
    gateInsight:  "Problem · Bet · Signal",
    gateBody:     "The Belief states the 2-week falsifiable bet. The Signal states the live observation that will confirm or falsify it. The PO signs before Wave execution begins.",
    gateArtifacts: ["A-5 · BELIEF.md", "A-7 · ROLE_ACTIVATION_CARD.md"]
  }
}[DIR_MODE];

const align = DIR.align(DIR_MODE, "start");

// ---------- Slide 1 · Open (cover mode: indigo) ----------
{
  const slide = pres.addSlide();
  addCover(pres, slide, {
    eyebrow:  COPY.openEyebrow,
    title:    COPY.openTitle,
    subtitle: COPY.openSubtitle,
    meta:     COPY.openMeta,
    dirMode:  DIR_MODE,
    mode:     "indigo"
  });
  addChrome(pres, slide, { dirMode: DIR_MODE, deckLabel: DECK_LABEL, slideN: 1, totalN: 3 });
}

// ---------- Slide 2 · Pillar (content kind: architecture → light ground) ----------
{
  const slide = pres.addSlide();
  slide.background = { color: T.paper };
  addHeader(pres, slide, {
    eyebrow:  COPY.pillarEyebrow,
    title:    COPY.pillarTitle,
    subtitle: COPY.pillarSubtitle,
    dirMode:  DIR_MODE
  });

  const colors = [T.indigo, T.trust, T.signal];
  // Reverse the column iteration in RTL so the FIRST pillar sits at the
  // reader's start edge (right in RTL, left in LTR). The column-math itself
  // is untouched — DIR.seq reverses input, not coordinates.
  const order = [0, 1, 2];
  const seq   = DIR.seq(DIR_MODE, order);

  seq.forEach((idx, i) => {
    const x    = T.deck.colX(i * 4);
    const w    = T.deck.col(4);
    const y0   = 1.85;
    const name  = COPY.pillarNames[idx];
    const label = COPY.pillarLabels[idx];
    const body  = COPY.pillarBodies[idx];
    const color = colors[idx];

    // Card surface
    slide.addShape(pres.shapes.RECTANGLE, {
      x, y: y0, w, h: 2.4,
      fill: { color: T.paper }, line: { color: T.rule, width: 0.5 },
      shadow: mkCard()
    });
    // Stage chip (top edge)
    slide.addShape(pres.shapes.RECTANGLE, {
      x, y: y0, w, h: 0.08,
      fill: { color }, line: { color, width: 0 }
    });
    // Eyebrow — pillar name
    slide.addText(name.toUpperCase(), {
      x: x + 0.2, y: y0 + 0.18, w: w - 0.4, h: 0.22,
      fontSize: T.type.xs, bold: true, color, charSpacing: T.track.eyebrow,
      fontFace: T.font.latin, align, valign: "top", margin: 0
    });
    // Title — pillar label
    slide.addText(label, {
      x: x + 0.2, y: y0 + 0.42, w: w - 0.4, h: 0.56,
      fontSize: T.type.md, bold: true, color: T.ink,
      fontFace: T.font.latin, align, valign: "top", margin: 0
    });
    // Body — three bullets. pptxgenjs array-of-text-objects pattern needs
    // breakLine:true on every item except the last; otherwise every bullet
    // collapses into one run-on paragraph with no line break.
    slide.addText(
      body.map((t, j) => ({
        text: t,
        options: {
          bullet: true,
          breakLine: j < body.length - 1,
          paraSpaceAfter: 4
        }
      })),
      {
        x: x + 0.2, y: y0 + 1.08, w: w - 0.4, h: 1.2,
        fontSize: T.type.base, color: T.slate, fontFace: T.font.latin,
        align, valign: "top", margin: 0
      }
    );
  });

  addChrome(pres, slide, { dirMode: DIR_MODE, deckLabel: DECK_LABEL, slideN: 2, totalN: 3 });
}

// ---------- Slide 3 · Gate (content kind: signature → but demonstrated on light) ----------
{
  const slide = pres.addSlide();
  slide.background = { color: T.paper };
  addHeader(pres, slide, {
    eyebrow:  COPY.gateEyebrow,
    title:    COPY.gateTitle,
    subtitle: COPY.gateSubtitle,
    dirMode:  DIR_MODE
  });

  // Big active gate hex — anchored to reader's START edge.
  // LTR: near left margin · RTL: mirrored near right margin.
  const hexSize = 1.6;
  const hexXLtr = MARGIN;
  addGateHex(pres, slide, {
    x: mx(DIR_MODE, hexXLtr, hexSize),
    y: 1.95, size: hexSize, number: 1, state: "active"
  });

  // Right-hand block (in LTR; mirrors to left-hand block in RTL).
  // Block starts after the hex + a 0.4" gap, and runs to the opposite margin.
  const blockXLtr = MARGIN + hexSize + 0.4;
  const blockW    = W - MARGIN - blockXLtr;
  const blockX    = mx(DIR_MODE, blockXLtr, blockW);

  slide.addText(COPY.gateSignedBy.toUpperCase(), {
    x: blockX, y: 2.02, w: blockW, h: 0.22,
    fontSize: T.type.xs, bold: true, color: T.signal, charSpacing: T.track.eyebrow,
    fontFace: T.font.latin, align, margin: 0
  });
  slide.addText(COPY.gateInsight, {
    x: blockX, y: 2.30, w: blockW, h: 0.50,
    fontSize: T.type.xl, bold: true, color: T.ink, charSpacing: T.track.display,
    fontFace: T.font.latin, align, margin: 0
  });
  slide.addText(COPY.gateBody, {
    x: blockX, y: 2.92, w: blockW, h: 0.80,
    fontSize: T.type.base, color: T.slate, fontFace: T.font.latin,
    align, margin: 0
  });
  // Artifact lines — Calibri per skill spec (decks are Calibri-only). The
  // "artifact feel" comes from the · separator and the A-n prefix, not a
  // mono face that LibreOffice can't render and that violates the spec.
  // breakLine:true is required on each item but the last, or pptxgenjs
  // collapses them into a single run-on line.
  slide.addText(
    COPY.gateArtifacts.map((t, j) => ({
      text: t,
      options: {
        bullet: true,
        breakLine: j < COPY.gateArtifacts.length - 1,
        paraSpaceAfter: 3
      }
    })),
    {
      x: blockX, y: 3.70, w: blockW, h: 0.60,
      fontSize: T.type.sm, color: T.muted, fontFace: T.font.latin,
      align, valign: "top", margin: 0
    }
  );

  addGateTrack(pres, slide, { activeGate: 1, dirMode: DIR_MODE });
  addChrome(pres, slide, { dirMode: DIR_MODE, deckLabel: DECK_LABEL, slideN: 3, totalN: 3 });
}

const outPath = path.resolve(__dirname, `signalos_proof_${DIR_MODE}.pptx`);
pres.writeFile({ fileName: outPath })
    .then(p => console.log(`Wrote: ${p}`));
