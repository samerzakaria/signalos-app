// SignalOS Visual — Design Tokens (JS module for pptxgenjs scripts)
// Locked 2026-04-16. Source of truth is tokens.json; this file mirrors it
// for Node consumption. Update both together if tokens ever change.

const T = {
  // ---- palette (no "#" prefix — pptxgenjs wants bare hex) ----
  ink:       "0B1221",
  slate:     "1F2A44",
  muted:     "5B6A85",
  rule:      "E4E9F2",
  paper:     "FFFFFF",
  wash:      "F5F7FB",
  indigo:    "1B2E60",
  indigoDk:  "0B1D4A",
  trust:     "3B6B8F",
  signal:    "D95B2B",
  gate:      "C4A553",
  ok:        "14734A",
  risk:      "A3302F",

  // ---- type scale ----
  type: {
    xs: 8, sm: 10, base: 11, md: 13,
    lg: 18, xl: 28, xxl: 44, display: 72
  },

  // ---- weights (expressed in pptxgenjs bold/italic; use as flags) ----
  // pptxgenjs does not support 300/400/600/700 natively — it uses bold:true/false.
  // Keep numeric constants for static/HTML renderers.
  wt: { light: 300, regular: 400, semibold: 600, bold: 700 },

  // ---- tracking / char spacing ----
  track: { eyebrow: 4, tablehead: 1, display: -1, body: 0 },

  // ---- fonts ----
  font: {
    latin: "Calibri",
    arabic: "Calibri",
    mono: "Consolas"   // Calibri has no mono; Consolas is the universal PPT mono
  },

  // ---- grid (deck) ----
  deck: {
    W: 10.0, H: 5.625, MARGIN: 0.5, COLS: 12, GUTTER: 0.1, BASELINE: 0.1,
    // Canonical body anchor. Every archetype that paints under addHeader
    // starts its body region at T.deck.BODY_Y0. Locked 2026-04-16 v1.0.1
    // after the header geometry was relaxed to support 2-line titles.
    BODY_Y0: 1.95
  }
};

// Content width and column helper
T.deck.CW = T.deck.W - T.deck.MARGIN * 2;
T.deck.col = (n) => {
  const colW = (T.deck.CW - T.deck.GUTTER * (T.deck.COLS - 1)) / T.deck.COLS;
  return colW * n + T.deck.GUTTER * Math.max(0, n - 1);
};
T.deck.colX = (n) => {
  const colW = (T.deck.CW - T.deck.GUTTER * (T.deck.COLS - 1)) / T.deck.COLS;
  return T.deck.MARGIN + (colW + T.deck.GUTTER) * n;
};

// ---- shadow factories (NEVER reuse a shadow object — pptxgenjs mutates) ----
const mkCard = () => ({ type: "outer", color: "0F172A", blur: 8,  offset: 2, angle: 135, opacity: 0.07 });
const mkBase = () => ({ type: "outer", color: "0F172A", blur: 14, offset: 4, angle: 135, opacity: 0.10 });
const mkHero = () => ({ type: "outer", color: "0F172A", blur: 22, offset: 6, angle: 135, opacity: 0.13 });

// ---- directionality ----
// RTL in SignalOS is a FULL layout mirror, not just text alignment.
// Every shape's X coordinate flips around the deck's vertical axis:
//   x_rtl = DECK_WIDTH - x_ltr - shape_width
// Every helper that positions a shape MUST route x through DIR.mirrorX(...)
// when the deck is in RTL mode. This is mandatory — a missed mirror is
// the single most common RTL failure and will be caught by check 14 in
// the critic rubric.
const DIR = {
  ltr: "ltr",
  rtl: "rtl",

  // Given a direction, return the align token
  align: (dir, side) => {
    if (dir === "rtl") {
      if (side === "start") return "right";
      if (side === "end") return "left";
    } else {
      if (side === "start") return "left";
      if (side === "end") return "right";
    }
    return side;
  },

  // Mirror a shape's X coordinate for the deck canvas when dir = "rtl".
  // Pass the raw LTR x and the shape's width; returns the correct x for the
  // current direction. Width is unchanged (shapes don't resize in a mirror).
  // `canvasW` defaults to T.deck.W but can be overridden for static pages.
  mirrorX: (dir, x, w, canvasW) => {
    if (dir !== "rtl") return x;
    const W = canvasW == null ? T.deck.W : canvasW;
    return W - x - w;
  },

  // Mirror an anchor pair (x, w) AND the text-align in one call.
  // Useful when a helper wants both in a single line.
  mirror: (dir, { x, w, side = "start", canvasW } = {}) => ({
    x: DIR.mirrorX(dir, x, w, canvasW),
    w,
    align: DIR.align(dir, side)
  }),

  // Reverse an array of items when the deck is RTL. Use at LAYOUT time so
  // that Flow / Matrix / Pillar archetypes iterate through their content
  // in the visual reading direction of the reader.
  seq: (dir, arr) => (dir === "rtl" ? [...arr].reverse() : arr),

  // Should the icon mirror?
  shouldFlip: (iconName) => {
    const whitelist = new Set([
      "FiArrowLeft","FiArrowRight","FiArrowUpLeft","FiArrowUpRight",
      "FiArrowDownLeft","FiArrowDownRight",
      "FiChevronLeft","FiChevronRight",
      "FiCornerDownLeft","FiCornerDownRight","FiCornerUpLeft","FiCornerUpRight",
      "FiSkipBack","FiSkipForward","FiRewind","FiFastForward",
      "FiLogIn","FiLogOut","FiRotateCcw","FiRotateCw","FiRepeat"
    ]);
    return whitelist.has(iconName);
  }
};

module.exports = { T, mkCard, mkBase, mkHero, DIR };
