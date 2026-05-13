// SignalOS Visual — Arabic RTL proof.
// Same helpers, same archetype geometry, same shape mirror — only the
// strings change. Latin tokens (filenames, version numbers, trust-tier
// labels) are kept as Latin inside Arabic paragraphs, relying on the
// rendering engine's bidi algorithm to place them correctly.
//
// Purpose: stress-test whether Arabic glyph metrics (taller diacritics,
// deeper descenders, denser letter-width) break the Latin-tuned
// container sizes. Validator will tell us.
//
// Usage:  node proof_deck_ar.js

const path = require("path");
const pptxgen = require("pptxgenjs");
const {
  T, mkCard, mx, DIR, safeTrack,
  addChrome, addHeader, addGateHex, addGateTrack, addCover
} = require("../scripts/helpers.js");

const DIR_MODE = "rtl";

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
pres.author = "SignalOS v1.0";
pres.company = "SignalOS";
pres.title = "SignalOS Visual — System Proof (Arabic RTL)";
pres.rtlMode = true;

const DECK_LABEL = "SignalOS · إثبات النظام المرئي · عربي";
const { W, H, MARGIN, CW } = T.deck;

const COPY = {
  openEyebrow:   "SignalOS · النظام المرئي",
  openTitle:     "النظام، مُقدَّماً.",
  openSubtitle:  "ثلاثة أنماط، لوحة ألوان واحدة، سلّم طباعة واحد، شبكة واحدة — كلها مثبّتة.",
  openMeta:      "سامر زكريا · 16 أبريل 2026 · الإصدار v1.0 · مستوحى من Signal لمحمد شعبان",

  pillarEyebrow:  "النمط · العمود",
  pillarTitle:    "ثلاثة أعمدة. استنتاج واحد لكل عمود.",
  pillarSubtitle: "كيف يُؤطّر عرض نموذج التشغيل في SignalOS: التفاعل · التمكين · التسليم.",

  pillarNames:  ["التفاعل", "التمكين", "التسليم"],
  pillarLabels: [
    "كيف تتفاعل تقنية المعلومات مع المؤسسة",
    "كيف تُزوَّد تقنية المعلومات بالموارد",
    "كيف تُنجز تقنية المعلومات العمل"
  ],
  pillarBodies: [
    [
      "الأداء — لوحات بيانات حيّة، لا شرائح ربع سنوية.",
      "حقوق القرار — 6 بوابات مسمّاة، 4 أشخاص مسمَّون.",
      "المالية — القناعة هي وحدة التمويل لأسبوعين."
    ],
    [
      "الهياكل التنظيمية — 4 أشخاص · 10 مقاعد وكلاء.",
      "التوريد — 6 محوّلات أدوات، قابلة للنقل بين المورّدين.",
      "المواهب — طبقات الثقة T1/T2/T3 لكل سطح."
    ],
    [
      "نموذج التسليم — وحدة الموجة، 1–5 أيام، 6 بوابات.",
      "الأدوات والمنصّات — 9 أوامر، 7 مهارات، 5 خطافات.",
      "أماكن العمل — فِرَق وأسراب، أشجار عمل متوازية."
    ]
  ],

  gateEyebrow:  "النمط · البوابة",
  gateTitle:    "البوابة 1 — القناعة موقَّعة.",
  gateSubtitle: "الشعار الرسمي لبوابة SignalOS. واحد لكل مرجع بوابة في كل عرض.",
  gateSignedBy: "موقَّعة من · مالك المنتج",
  gateInsight:  "المشكلة · الرهان · الإشارة",
  gateBody:     "تصوغ القناعة الرهان القابل للتكذيب خلال أسبوعين. تصوغ الإشارة الملاحظة الحيّة التي ستؤكّده أو تكذّبه. يوقّع مالك المنتج قبل بدء تنفيذ الموجة.",
  gateArtifacts: ["A-5 · BELIEF.md", "A-7 · ROLE_ACTIVATION_CARD.md"]
};

const align = DIR.align(DIR_MODE, "start");

// ---------- Slide 1 · Open ----------
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

// ---------- Slide 2 · Pillar ----------
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
  const order  = [0, 1, 2];
  const seq    = DIR.seq(DIR_MODE, order);

  seq.forEach((idx, i) => {
    const x    = T.deck.colX(i * 4);
    const w    = T.deck.col(4);
    const y0   = 1.85;
    const name  = COPY.pillarNames[idx];
    const label = COPY.pillarLabels[idx];
    const body  = COPY.pillarBodies[idx];
    const color = colors[idx];

    slide.addShape(pres.shapes.RECTANGLE, {
      x, y: y0, w, h: 2.4,
      fill: { color: T.paper }, line: { color: T.rule, width: 0.5 },
      shadow: mkCard()
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x, y: y0, w, h: 0.08,
      fill: { color }, line: { color, width: 0 }
    });
    // Eyebrow — safeTrack zeroes charSpacing for Arabic runs so contextual
    // shaping is preserved (tracked Arabic breaks into isolated letters).
    slide.addText(name, {
      x: x + 0.2, y: y0 + 0.18, w: w - 0.4, h: 0.22,
      fontSize: T.type.xs, bold: true, color,
      charSpacing: safeTrack(name, T.track.eyebrow),
      fontFace: T.font.latin, align, valign: "top", margin: 0,
      rtlMode: true
    });
    slide.addText(label, {
      x: x + 0.2, y: y0 + 0.42, w: w - 0.4, h: 0.56,
      fontSize: T.type.md, bold: true, color: T.ink,
      fontFace: T.font.latin, align, valign: "top", margin: 0,
      rtlMode: true
    });
    slide.addText(
      body.map((t, j) => ({
        text: t,
        options: {
          bullet: true,
          breakLine: j < body.length - 1,
          paraSpaceAfter: 4,
          rtlMode: true
        }
      })),
      {
        x: x + 0.2, y: y0 + 1.08, w: w - 0.4, h: 1.2,
        fontSize: T.type.base, color: T.slate, fontFace: T.font.latin,
        align, valign: "top", margin: 0, rtlMode: true
      }
    );
  });

  addChrome(pres, slide, { dirMode: DIR_MODE, deckLabel: DECK_LABEL, slideN: 2, totalN: 3 });
}

// ---------- Slide 3 · Gate ----------
{
  const slide = pres.addSlide();
  slide.background = { color: T.paper };
  addHeader(pres, slide, {
    eyebrow:  COPY.gateEyebrow,
    title:    COPY.gateTitle,
    subtitle: COPY.gateSubtitle,
    dirMode:  DIR_MODE
  });

  const hexSize = 1.6;
  const hexXLtr = MARGIN;
  addGateHex(pres, slide, {
    x: mx(DIR_MODE, hexXLtr, hexSize),
    y: 1.95, size: hexSize, number: 1, state: "active"
  });

  const blockXLtr = MARGIN + hexSize + 0.4;
  const blockW    = W - MARGIN - blockXLtr;
  const blockX    = mx(DIR_MODE, blockXLtr, blockW);

  slide.addText(COPY.gateSignedBy, {
    x: blockX, y: 2.02, w: blockW, h: 0.22,
    fontSize: T.type.xs, bold: true, color: T.signal,
    charSpacing: safeTrack(COPY.gateSignedBy, T.track.eyebrow),
    fontFace: T.font.latin, align, margin: 0, rtlMode: true
  });
  slide.addText(COPY.gateInsight, {
    x: blockX, y: 2.30, w: blockW, h: 0.50,
    fontSize: T.type.xl, bold: true, color: T.ink,
    charSpacing: safeTrack(COPY.gateInsight, T.track.display),
    fontFace: T.font.latin, align, margin: 0, rtlMode: true
  });
  slide.addText(COPY.gateBody, {
    x: blockX, y: 2.92, w: blockW, h: 0.80,
    fontSize: T.type.base, color: T.slate, fontFace: T.font.latin,
    align, margin: 0, rtlMode: true
  });
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
      align, valign: "top", margin: 0, rtlMode: true
    }
  );

  addGateTrack(pres, slide, { activeGate: 1, dirMode: DIR_MODE });
  addChrome(pres, slide, { dirMode: DIR_MODE, deckLabel: DECK_LABEL, slideN: 3, totalN: 3 });
}

const outPath = path.resolve(__dirname, "signalos_proof_rtl_ar.pptx");
pres.writeFile({ fileName: outPath })
    .then(p => console.log(`Wrote: ${p}`));
