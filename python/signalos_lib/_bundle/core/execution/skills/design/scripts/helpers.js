// SignalOS Visual — PPT helpers
// Locked 2026-04-16. Every new deck script imports from this file.
// If a helper is not here, it should not exist in deck-level code.
//
// RTL discipline: every shape that accepts an `x` coordinate in these helpers
// is routed through DIR.mirrorX(dir, x, w) when dirMode === "rtl". This is
// NOT text alignment — it is a full canvas mirror so that when an Arabic
// reader opens the deck, every shape sits exactly where their eye expects
// it, identical rhythm to the LTR render, just mirrored. Check 14 of the
// critic rubric verifies this against the LTR reference render.

const { T, mkCard, mkBase, mkHero, DIR } = require("../assets/tokens.js");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");

// Convenience: mirror just x,w given the deck's canvas width
const mx = (dir, x, w) => DIR.mirrorX(dir, x, w, T.deck.W);

// ------------------------------------------------------------
// ARABIC TYPOGRAPHY GUARD
// Arabic (and other Unicode Arabic-family) letters are contextually shaped:
// each letter joins to its neighbours, and the joined form differs from the
// isolated form. Letterspacing (charSpacing) breaks those joins — "التفاعل"
// renders as "ا ل ت ف ا ع ل" (seven isolated letters) instead of a single
// connected word. The same applies to display-kerning (negative tracking)
// which collapses diacritics into their base letter.
//
// Rule (locked into the skill, not the deck): any text run whose string
// contains a character in the Arabic Unicode ranges receives charSpacing: 0.
// Latin tracking is preserved on every run that is pure Latin — including
// Latin technical tokens (filenames, version numbers) inside an RTL deck.
//
// Ranges covered:
//   U+0600-06FF  Arabic
//   U+0750-077F  Arabic Supplement
//   U+08A0-08FF  Arabic Extended-A
//   U+FB50-FDFF  Arabic Presentation Forms-A
//   U+FE70-FEFF  Arabic Presentation Forms-B
// ------------------------------------------------------------
const ARABIC_RE = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/;
function hasArabic(text) {
  return text != null && ARABIC_RE.test(String(text));
}
function safeTrack(text, trackValue) {
  return hasArabic(text) ? 0 : trackValue;
}

// ------------------------------------------------------------
// WESTERN-NUMERAL NORMALISATION
// SignalOS standard: Western Arabic numerals (0-9) everywhere, including
// inside Arabic paragraphs. Never Arabic-Indic (٠-٩) or Persian (۰-۹).
// Rationale: gate numbers, artifact IDs, dates, SLAs are technical tokens,
// not narrative prose — a Latin reader and an Arabic reader should both
// see "Gate 1", "v1.0", "1-5 days", "45-day pilot". Also avoids bidi
// reordering bugs around middle-dots and hyphens with Arabic-Indic digits.
//
// Source strings should already use 0-9. This helper is defence-in-depth:
// pass every user-supplied string through it so translations / pasted
// content cannot smuggle Arabic-Indic digits into a SignalOS artifact.
// ------------------------------------------------------------
const AR_INDIC_DIGITS_RE = /[\u0660-\u0669]/g;   // ٠-٩
const FA_DIGITS_RE       = /[\u06F0-\u06F9]/g;   // ۰-۹ (Persian)
function toWesternDigits(text) {
  if (text == null) return text;
  return String(text)
    .replace(AR_INDIC_DIGITS_RE, d => String(d.charCodeAt(0) - 0x0660))
    .replace(FA_DIGITS_RE,       d => String(d.charCodeAt(0) - 0x06F0));
}

// ------------------------------------------------------------
// CHROME — the signal bar, footer, and tick on every slide
// Fully mirrored: in RTL, the signal tick sits at the right,
// the wordmark sits at the right, the deck label sits at the left.
// ------------------------------------------------------------

function addChrome(pres, slide, { dirMode = "ltr", deckLabel = "", slideN, totalN } = {}) {
  const { W, H, MARGIN } = T.deck;

  // 1 · Signal bar (top) — full-width, no mirror needed
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: W, h: 0.04,
    fill: { color: T.indigo }, line: { color: T.indigo, width: 0 }
  });

  // 2 · Slide-number tick — anchored to reader's start edge
  //    LTR: near left margin · RTL: near right margin (mirrored)
  const tickW = 0.5;
  const tickXLtr = MARGIN;
  slide.addShape(pres.shapes.RECTANGLE, {
    x: mx(dirMode, tickXLtr, tickW), y: H - 0.30, w: tickW, h: 0.02,
    fill: { color: T.signal }, line: { color: T.signal, width: 0 }
  });

  // 3 · Footer bar — full-width, no mirror
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: H - 0.28, w: W, h: 0.28,
    fill: { color: T.ink }, line: { color: T.ink, width: 0 }
  });

  // Wordmark: at reader's start edge · deck label: at reader's end edge.
  // In RTL, these swap sides of the canvas.
  const wordmarkW = 2.4;
  const labelW    = 3.6;
  const wordmarkXLtr = MARGIN;
  const labelXLtr    = W - MARGIN - labelW;

  slide.addText("SIGNALOS", {
    x: mx(dirMode, wordmarkXLtr, wordmarkW), y: H - 0.26, w: wordmarkW, h: 0.24,
    fontSize: T.type.xs, bold: true, color: T.paper, fontFace: T.font.latin,
    charSpacing: safeTrack("SIGNALOS", 2), align: DIR.align(dirMode, "start"), valign: "middle", margin: 0
  });

  if (slideN && totalN) {
    slide.addText(`${slideN} / ${totalN}`, {
      x: (W - 1) / 2, y: H - 0.26, w: 1, h: 0.24,
      fontSize: T.type.xs, color: T.muted, fontFace: T.font.latin,
      align: "center", valign: "middle", margin: 0
    });
  }

  slide.addText(toWesternDigits(deckLabel), {
    x: mx(dirMode, labelXLtr, labelW), y: H - 0.26, w: labelW, h: 0.24,
    fontSize: 7, bold: true, color: T.paper, fontFace: T.font.latin,
    align: DIR.align(dirMode, "end"), valign: "middle", margin: 0
  });
}

// ------------------------------------------------------------
// HEADER — eyebrow + title + rule + subtitle.
// Title region is the full content width to prevent wrap-collision
// with the rule below (caught in proof slide 2, 2026-04-16).
// Archetype specs enforce a two-line cap on titles.
// ------------------------------------------------------------

// addHeader geometry (locked 2026-04-16, v1.0.1):
//   eyebrow   y:0.28  h:0.20
//   title     y:0.48  h:0.94   ← was h:0.64 (clipped any 2-line title)
//   rule      y:1.50  h:0.04   ← was y:1.20
//   subtitle  y:1.62  h:0.30   ← was y:1.32
//   BODY_Y0 = 1.95 (was 1.85). Primary archetypes track this via T.deck.BODY_Y0.
//
// The region now cleanly accommodates titles up to 2 lines at xl (28pt) with
// the pyramid-principle cap (one insight per slide). Titles that wrap to 3
// lines remain a copy-discipline problem, not a layout problem; they are
// caught by the mechanical critic (rule 15: titleLineCount <= 2).
function addHeader(pres, slide, { eyebrow, title, subtitle = "", dirMode = "ltr" } = {}) {
  const { MARGIN, CW } = T.deck;
  const align = DIR.align(dirMode, "start");
  const x     = MARGIN; // eyebrow/title/subtitle all live at reader's start edge

  // Normalise every user-supplied string to Western digits before render.
  eyebrow  = toWesternDigits(eyebrow);
  title    = toWesternDigits(title);
  subtitle = toWesternDigits(subtitle);

  // Arabic has no case → .toUpperCase() is a no-op on Arabic runs but still
  // applies to Latin. safeTrack() zeroes the tracking when the run contains
  // any Arabic character (tracking breaks contextual shaping).
  slide.addText(String(eyebrow).toUpperCase(), {
    x, y: 0.28, w: CW, h: 0.20,
    fontSize: T.type.xs, bold: true, color: T.signal,
    charSpacing: safeTrack(eyebrow, T.track.eyebrow),
    fontFace: T.font.latin, align, valign: "top", margin: 0
  });

  // Title: full content width + taller region. Two-line titles (28pt × ~0.44"
  // leading) now sit cleanly above the rule at y:1.50.
  slide.addText(title, {
    x, y: 0.48, w: CW, h: 0.94,
    fontSize: T.type.xl, bold: true, color: T.ink,
    charSpacing: safeTrack(title, T.track.display),
    fontFace: T.font.latin, align, valign: "top", margin: 0
  });

  // Rule — split: short signal segment + long rule segment.
  // Dropped from 1.20 → 1.50 to give the title its second line.
  const ruleY = 1.50;
  const signalW = 0.28;
  const gap     = 0.06;
  const restW   = CW - signalW - gap;
  const signalXLtr = MARGIN;
  const restXLtr   = MARGIN + signalW + gap;

  slide.addShape(pres.shapes.RECTANGLE, {
    x: mx(dirMode, signalXLtr, signalW), y: ruleY, w: signalW, h: 0.04,
    fill: { color: T.signal }, line: { color: T.signal, width: 0 }
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x: mx(dirMode, restXLtr, restW), y: ruleY, w: restW, h: 0.04,
    fill: { color: T.rule }, line: { color: T.rule, width: 0 }
  });

  if (subtitle) {
    slide.addText(subtitle, {
      x, y: 1.62, w: CW, h: 0.30,
      fontSize: T.type.base, color: T.muted, fontFace: T.font.latin,
      align, valign: "top", margin: 0
    });
  }
}

// ------------------------------------------------------------
// GATE HEX — reusable glyph for Gate archetype & Flow track.
// Hexagon shape itself is rotationally symmetric; no mirror needed for
// the glyph. The *position* of the glyph on the slide mirrors via the
// caller — addGateHex accepts a pre-mirrored x.
// ------------------------------------------------------------

function addGateHex(pres, slide, { x, y, size = 1.4, number, state = "default" } = {}) {
  // state: "default" | "active" | "passed"
  const fill =
    state === "active" ? T.signal :
    state === "passed" ? T.ok     : T.gate;
  const stroke  = T.indigoDk;
  const txtColor = (state === "active" || state === "passed") ? T.paper : T.ink;

  slide.addShape(pres.shapes.HEXAGON, {
    x, y, w: size, h: size * (Math.sqrt(3) / 2),
    fill: { color: fill }, line: { color: stroke, width: 1.5 }, rotate: 0
  });
  slide.addText(String(number), {
    x, y, w: size, h: size * (Math.sqrt(3) / 2),
    fontSize: size > 1 ? T.type.xxl : T.type.lg,
    bold: true, color: txtColor, fontFace: T.font.latin,
    align: "center", valign: "middle", charSpacing: 0, margin: 0
  });
}

// ------------------------------------------------------------
// GATE TRACK — 6 miniature hexes along the bottom for progress.
// Iteration is reversed in RTL so that Gate 0 (the start) appears at
// the reader's start edge — the right edge in RTL, the left edge in LTR.
// ------------------------------------------------------------

function addGateTrack(pres, slide, { activeGate = null, dirMode = "ltr" } = {}) {
  const { W, MARGIN } = T.deck;
  const trackY = 4.55;
  const hexW   = 0.42;
  const gap    = ((W - MARGIN * 2) - 6 * hexW) / 5;

  // Rule connecting all 6 — centred, full-width, no mirror needed
  slide.addShape(pres.shapes.RECTANGLE, {
    x: MARGIN + hexW / 2, y: trackY + hexW * 0.43 / 2,
    w: (W - MARGIN * 2) - hexW, h: 0.02,
    fill: { color: T.rule }, line: { color: T.rule, width: 0 }
  });

  // Use DIR.seq so the first gate (0) sits at the reader's start edge.
  const gates = DIR.seq(dirMode, [0, 1, 2, 3, 4, 5]);
  gates.forEach((n, i) => {
    const x = MARGIN + i * (hexW + gap);
    const state =
      activeGate == null ? "default" :
      n < activeGate     ? "passed"  :
      n === activeGate   ? "active"  : "default";
    addGateHex(pres, slide, { x, y: trackY, size: hexW, number: n, state });
  });
}

// ------------------------------------------------------------
// COVER MODES — six on-palette grounds, all governance-appropriate.
// Each mode is a named preset {ground, discDark, eyebrow, title, subtitle, meta, rule}
// where every colour is a token from assets/tokens.js. Use `mode` to pick.
// ------------------------------------------------------------

const COVER_MODES = {
  // 1. indigo — canonical governance. Default for Executive / Summary / Playbook.
  indigo:   { ground: T.indigo,   disc: T.indigoDk, eyebrow: T.signal, title: T.paper, subtitle: T.paper, meta: T.paper, rule: T.signal },
  // 2. ink — near-black authority. Use for Operating Model, 45-day pilot close, Gate 5 sign-off.
  ink:      { ground: T.ink,      disc: T.slate,    eyebrow: T.signal, title: T.paper, subtitle: T.paper, meta: T.paper, rule: T.signal },
  // 3. indigoDk — deep indigo formality. Use for Blueprint cover page, board-level decks.
  indigoDk: { ground: T.indigoDk, disc: T.ink,      eyebrow: T.signal, title: T.paper, subtitle: T.paper, meta: T.paper, rule: T.signal },
  // 4. slate — warm dark. Use for Training-Layer decks, onboarding, role-activation.
  slate:    { ground: T.slate,    disc: T.ink,      eyebrow: T.signal, title: T.paper, subtitle: T.paper, meta: T.paper, rule: T.signal },
  // 5. trust — muted blue-teal. Use for Enablement/learning, agent-trust tier decks.
  trust:    { ground: T.trust,    disc: T.indigoDk, eyebrow: T.paper,  title: T.paper, subtitle: T.paper, meta: T.paper, rule: T.signal },
  // 6. paper — reverse-open (airy). Use when the deck CONTENT is the heavy subject
  //    and the cover should set a light frame around it; e.g. a single-Belief cover
  //    for a Belief-only playbook, or a Thesis-style opener for a data-led deck.
  paper:    { ground: T.paper,    disc: T.wash,     eyebrow: T.signal, title: T.ink,   subtitle: T.slate, meta: T.muted, rule: T.signal }
};

// decideCoverMode — map an archetype + intent to the right cover mode.
// This is the skill's auto-decide logic. It keeps cover choice rule-based,
// not tasteful, so two authors building two decks with the same intent get
// the same cover.
//
//   archetype: "open" | "close" | "thesis" | "quote"
//   deckKind:  "executive" | "summary" | "playbook" | "om" | "blueprint" |
//              "training" | "enablement" | "belief" | "data"
//   tone:      "signature" | "question" | "data-led" | "definitive"   (optional)
function decideCoverMode({ archetype = "open", deckKind = "executive", tone } = {}) {
  // Close archetypes get the same mode as the Open — bookends the deck.
  if (deckKind === "om")          return "ink";
  if (deckKind === "blueprint")   return "indigoDk";
  if (deckKind === "training" || deckKind === "enablement") return "slate";
  if (deckKind === "belief" || tone === "data-led")         return "paper";
  if (deckKind === "executive" || deckKind === "summary" || deckKind === "playbook") return "indigo";
  return "indigo";
}

// decideSlideGround — content-aware, NOT structure-aware.
// The slide's ground (dark or light) is a function of WHAT THE SLIDE IS SAYING,
// not where it sits in the deck or which archetype it uses. Two Pillar slides
// can have different grounds if one is a data comparison and the other is a
// signed commitment. The archetype is about LAYOUT; the ground is about VOICE.
//
// Content kinds (pass as `kind` or let decideContentKind sniff it from text):
//
//   Dark voice (weight, authority, theatre, intimacy) — use COVER_MODES grounds:
//     signature   — a human is signing / committing / deciding. Gate sign-off,
//                   Belief signature, Decision-DNA card, Role activation.
//                   Ground: ink.
//     revelation  — a single insight meant to land hard. Thesis slide, the
//                   turning point in a Proof, a named reframe. Ground: indigo.
//     summary     — deck close, next-Wave call to action, "what to do Monday".
//                   Ground: indigo.
//     quote       — a person's voice (customer, team, leadership). Ground: slate.
//     warning     — risk, incident, lesson-from-failure, pre-mortem. Ground: ink.
//
//   Light voice (legibility, density, honesty) — paper or wash ground:
//     data        — charts, numbers, tables, evidence. Ground: paper.
//     process     — a step-by-step flow, a timeline, a wave plan. Ground: paper.
//     architecture— a diagram, a map, a layout, the Operating Model grid.
//                   Ground: paper.
//     comparison  — a trade-off, a before/after, a two-column choice. Ground: paper.
//     reference   — an index, a glossary, a roster, a credits list. Ground: wash.
//
// Callers pass `kind` explicitly when they know it. When they don't, run
// `decideContentKind(title, body)` to get a keyword-based guess (see below).
function decideSlideGround(kind = "data") {
  const darkKinds  = new Set(["signature", "revelation", "summary", "quote", "warning"]);
  const darkMode   = {
    signature:  "ink",
    revelation: "indigo",
    summary:    "indigo",
    quote:      "slate",
    warning:    "ink"
  };
  if (darkKinds.has(kind)) return { ground: "dark",  mode: darkMode[kind] };
  if (kind === "reference") return { ground: "light", mode: "wash" };
  return { ground: "light", mode: "paper" };
}

// decideContentKind — keyword-based sniff. Called when the author didn't
// tell us what the slide is saying; returns the best-guess kind.
//
// Deliberately simple. The rubric is: if any strong signal keyword matches
// the slide's title or body, that kind wins. Ties go to "data" (the safe
// default for dense content on white).
//
// Override at the call site when the sniff is wrong — the function exists
// to give the skill a defensible default, not to be the last word.
function decideContentKind(title = "", body = "") {
  const txt = (title + " " + body).toLowerCase();

  // signature — strongest weight, dark ground is theatrical
  if (/\b(signed|sign[- ]?off|commit(ment|ted)?|pledge|belief|decision dna|approved by|product owner)\b/.test(txt)) return "signature";

  // warning — gravity trumps density
  if (/\b(risk|incident|failure|lesson|post[- ]?mortem|pre[- ]?mortem|breach|outage|regret|warning|caution)\b/.test(txt)) return "warning";

  // quote — a human voice
  if (/["“”«»].{8,}["“”«»]/.test(title) || /\b(said|says|according to|in their words|quote)\b/.test(txt)) return "quote";

  // summary / close
  if (/\b(what to do monday|next wave|call to action|close|wrap[- ]?up|in summary|to recap)\b/.test(txt)) return "summary";

  // revelation — a single insight meant to land
  if (/\b(the insight is|the point is|the answer is|therefore|this means|the reframe|north star|thesis)\b/.test(txt)) return "revelation";

  // data — charts, numbers, percentages
  if (/\b(\d+%|\$\d|chart|figure|table|metric|baseline|median|mean|p50|p90|uplift|delta)\b/.test(txt)) return "data";

  // process — flow, steps, wave
  if (/\b(step \d|phase \d|wave \d|gate \d|timeline|roadmap|sequence|workflow|process)\b/.test(txt)) return "process";

  // comparison — before/after, vs, trade-off
  if (/\b(before|after|vs\b|versus|trade[- ]?off|option [ab]|choice|compare|comparison)\b/.test(txt)) return "comparison";

  // architecture — diagrams, maps, models
  if (/\b(architecture|operating model|stack|layer|component|diagram|map|topology|hierarchy)\b/.test(txt)) return "architecture";

  // reference — index, glossary, roster, credits
  if (/\b(index|glossary|roster|credits|appendix|reference|catalog)\b/.test(txt)) return "reference";

  return "data"; // safe default: light ground
}

// ------------------------------------------------------------
// COVER (Open archetype). Fully mirrored: atmosphere disc moves to the
// reader's end corner, top rule stays full-width, text anchors to start.
// `mode` selects a COVER_MODES preset; defaults to "indigo" (canonical).
// ------------------------------------------------------------

function addCover(pres, slide, { eyebrow, title, subtitle, meta, dirMode = "ltr", mode = "indigo" } = {}) {
  const { W, H, MARGIN, CW } = T.deck;
  const M = COVER_MODES[mode] || COVER_MODES.indigo;

  // Normalise every user-supplied string to Western digits before render.
  eyebrow  = toWesternDigits(eyebrow);
  title    = toWesternDigits(title);
  subtitle = toWesternDigits(subtitle);
  meta     = toWesternDigits(meta);

  slide.background = { color: M.ground };

  // One atmosphere disc — at the reader's END corner (away from their eye
  // landing point). LTR: top-right · RTL: top-left.
  const discW = 4.0;
  const discXLtr = W - 2.5;
  slide.addShape(pres.shapes.OVAL, {
    x: mx(dirMode, discXLtr, discW), y: -1.5, w: discW, h: 4.0,
    fill: { color: M.disc, transparency: 60 },
    line: { color: M.disc, transparency: 100, width: 0 }
  });

  const align = DIR.align(dirMode, "start");

  // Top rule — full-width in the mode's rule colour (signal on every dark
  // mode; signal on paper too, for continuity)
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: W, h: 0.04,
    fill: { color: M.rule }, line: { color: M.rule, width: 0 }
  });

  slide.addText(String(eyebrow).toUpperCase(), {
    x: MARGIN, y: 1.70, w: CW, h: 0.20,
    fontSize: T.type.xs, bold: true, color: M.eyebrow,
    charSpacing: safeTrack(eyebrow, T.track.eyebrow),
    fontFace: T.font.latin, align, margin: 0
  });

  slide.addText(title, {
    x: MARGIN, y: 2.00, w: CW, h: 1.00,
    fontSize: T.type.xxl, bold: true, color: M.title,
    charSpacing: safeTrack(title, T.track.display),
    fontFace: T.font.latin, align, valign: "top", margin: 0
  });

  if (subtitle) {
    slide.addText(subtitle, {
      x: MARGIN, y: 3.05, w: CW, h: 0.35,
      fontSize: T.type.md, color: M.subtitle, transparency: mode === "paper" ? 0 : 30,
      fontFace: T.font.latin, align, margin: 0
    });
  }

  // Short signal rule — anchored to reader's start edge
  const signalRuleW = 0.30;
  slide.addShape(pres.shapes.RECTANGLE, {
    x: mx(dirMode, MARGIN, signalRuleW),
    y: 3.50, w: signalRuleW, h: 0.04,
    fill: { color: M.rule }, line: { color: M.rule, width: 0 }
  });

  if (meta) {
    slide.addText(meta, {
      x: MARGIN, y: H - 0.90, w: CW, h: 0.25,
      fontSize: T.type.sm, color: M.meta, transparency: mode === "paper" ? 0 : 40,
      fontFace: T.font.latin, align, margin: 0
    });
  }
}

// ------------------------------------------------------------
// ICON (async PNG from react-icons/fi) with RTL awareness
// ------------------------------------------------------------

async function iconPng(Icon, color = "#FFFFFF", size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(Icon, { color, size: String(size), strokeWidth: 2 })
  );
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

function resolveIcon(iconName, dirMode = "ltr") {
  const fi = require("react-icons/fi");
  if (dirMode !== "rtl" || !DIR.shouldFlip(iconName)) return fi[iconName];
  const swap = {
    FiArrowLeft: "FiArrowRight",   FiArrowRight: "FiArrowLeft",
    FiChevronLeft: "FiChevronRight", FiChevronRight: "FiChevronLeft",
    FiCornerDownLeft: "FiCornerDownRight", FiCornerDownRight: "FiCornerDownLeft",
    FiCornerUpLeft: "FiCornerUpRight",     FiCornerUpRight: "FiCornerUpLeft",
    FiSkipBack: "FiSkipForward",   FiSkipForward: "FiSkipBack",
    FiRewind: "FiFastForward",     FiFastForward: "FiRewind",
    FiLogIn: "FiLogOut",           FiLogOut: "FiLogIn"
  };
  return fi[swap[iconName] || iconName];
}

// ------------------------------------------------------------
// Exports
// ------------------------------------------------------------

module.exports = {
  T, mkCard, mkBase, mkHero, DIR, mx,
  hasArabic, safeTrack, toWesternDigits,
  addChrome, addHeader, addGateHex, addGateTrack, addCover,
  COVER_MODES, decideCoverMode, decideSlideGround, decideContentKind,
  iconPng, resolveIcon
};
