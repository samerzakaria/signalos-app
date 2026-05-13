# SignalOS Core — W8 Design Pipeline
# cli/signalos_lib/design.py
# AMD-CORE-029
#
# Provides: PreDesignMode, PoBrief, generate_po_brief, generate_variants,
#           review_variant, generate_production_html, record_taste,
#           load_taste_context, append_decision_dna
#
# Entry points consumed by cli/signalos_lib/commands/design.py

from __future__ import annotations

__all__ = [
    "PreDesignMode",
    "PoBrief",
    "ReviewResult",
    "VariantFile",
    "TasteEntry",
    "FORCING_QUESTIONS",
    "REVIEW_DIMENSIONS",
    "generate_po_brief",
    "generate_variants",
    "review_variant",
    "generate_production_html",
    "record_taste",
    "load_taste_context",
    "decay_weight",
    "append_decision_dna",
    "check_po_brief_signed",
    "check_design_reviewed",
]

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class PreDesignMode(str, Enum):
    EXPANSION           = "Expansion"
    SELECTIVE_EXPANSION = "Selective Expansion"
    HOLD_SCOPE          = "Hold Scope"
    REDUCTION           = "Reduction"


FORCING_QUESTIONS: list[str] = [
    "What is the single most important user outcome this design must enable?",
    "Which existing patterns or conventions in this product must this design respect?",
    "What is the primary constraint (time / technical / resource) shaping the scope?",
    "Who is the exact user persona and what context are they in when they encounter this?",
    "What is the one thing this design must absolutely not do or break?",
    "How will we know in two weeks whether this design decision was correct?",
]

REVIEW_DIMENSIONS: list[tuple[str, str]] = [
    ("clarity",       "Is the visual hierarchy immediately clear to a new user?"),
    ("consistency",   "Does it use existing patterns from the product (spacing, type, colour)?"),
    ("accessibility", "Does it meet WCAG 2.1 AA contrast, focus order, and semantic HTML?"),
    ("slop",          "Is this generic AI output — lorem ipsum, placeholder icons, stock gradients?"),
    ("performance",   "Are there unnecessary layout thrash risks, large images, or blocking scripts?"),
    ("responsiveness","Does it work at 375 px, 768 px, and 1280 px?"),
    ("semantics",     "Is the HTML structure meaningful (headings, landmarks, roles)?"),
    ("taste",         "Does it match the product's approved taste memory?"),
]

TASTE_DECAY_BASE = 0.95  # per week
TASTE_DECAY_WEEKS_FULL_CUTOFF = 52  # weight below 0.07 after 52 weeks


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PoBrief:
    wave: str
    mode: PreDesignMode
    answers: dict[str, str]   # question → answer
    authored_by: str
    authored_at: str = ""     # ISO-8601

    def __post_init__(self) -> None:
        if not self.wave:
            raise ValueError("PoBrief.wave is required")
        if not isinstance(self.mode, PreDesignMode):
            raise ValueError(f"PoBrief.mode must be a PreDesignMode, got {self.mode!r}")
        if not self.authored_by:
            raise ValueError("PoBrief.authored_by is required")
        if not self.authored_at:
            self.authored_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class VariantFile:
    path: Path
    archetype: str     # e.g. "hero-left", "dashboard", "minimal"
    description: str


@dataclass
class ReviewResult:
    variant_path: Path
    scores: dict[str, float]   # dimension → 0-10
    overall: float
    passed: bool               # True if overall >= 7.0
    issues: list[str] = field(default_factory=list)


@dataclass
class TasteEntry:
    wave: str
    variant_archetype: str
    verdict: str               # "approved" | "rejected"
    traits: list[str]
    recorded_at: str           # ISO-8601
    weight: float = 1.0        # decays via decay_weight()


# ---------------------------------------------------------------------------
# PO Brief generation
# ---------------------------------------------------------------------------

def generate_po_brief(
    brief: PoBrief,
    repo_root: Path,
) -> Path:
    """
    Write core/strategy/PO_BRIEF.md from a PoBrief.
    Returns the path written.
    """
    template_path = repo_root / "core" / "governance" / "Templates" / "po-brief-template.md"
    out_path = repo_root / "core" / "strategy" / "PO_BRIEF.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    qa_block = "\n".join(
        f"**Q{i+1}: {q}**\n\n{brief.answers.get(q, '_No answer provided._')}\n"
        for i, q in enumerate(FORCING_QUESTIONS)
    )

    content = f"""\
<!-- SignalOS v1.0 — W8 Design Pipeline -->

# PO Brief — Wave {brief.wave}

`Canonical path: core/strategy/PO_BRIEF.md · Authored by: {brief.authored_by} · {brief.authored_at}`

## Design Mode

**{brief.mode.value}**

{_mode_description(brief.mode)}

---

## Forcing Questions

{qa_block}

---

## Signatures

"""
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _mode_description(mode: PreDesignMode) -> str:
    return {
        PreDesignMode.EXPANSION: (
            "Add net-new surfaces or capabilities. Scope grows beyond the current feature set."
        ),
        PreDesignMode.SELECTIVE_EXPANSION: (
            "Expand one specific area while holding all others at current scope."
        ),
        PreDesignMode.HOLD_SCOPE: (
            "Improve quality, polish, or performance within the current surface boundary. No new surfaces."
        ),
        PreDesignMode.REDUCTION: (
            "Remove surfaces or simplify the product. Net scope decreases."
        ),
    }[mode]


# ---------------------------------------------------------------------------
# Variant generation
# ---------------------------------------------------------------------------

_ARCHETYPES: list[tuple[str, str]] = [
    ("hero-split",   "Split hero — content left, visual right"),
    ("dashboard",    "Data-first dashboard — metrics at top, detail below"),
    ("minimal",      "Single-column minimal — typography-led, high whitespace"),
    ("card-grid",    "Card grid — scannable tile layout for collections"),
    ("sidebar-nav",  "Sidebar navigation — persistent nav + content area"),
]

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — {archetype}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --c-bg:#fff;--c-surface:#f4f4f5;--c-border:#e4e4e7;
  --c-text:#18181b;--c-secondary:#71717a;--c-accent:#6366f1;
  --font:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --radius:8px;--shadow:0 1px 3px rgba(0,0,0,.08);
}}
body{{font-family:var(--font);background:var(--c-bg);color:var(--c-text);min-height:100vh}}
{archetype_css}
</style>
</head>
<body>
{archetype_html}
<!-- SignalOS design variant · archetype: {archetype} · mode: {mode} · wave: {wave} · generated: {ts} -->
</body>
</html>
"""

def generate_variants(
    wave: str,
    title: str,
    brief: PoBrief,
    repo_root: Path,
    taste_context: str = "",
    count: int = 3,
) -> list[VariantFile]:
    """
    Generate *count* self-contained HTML variant files (3–5).
    Writes to .signalos/design/variants/wave-{wave}/.
    Returns list of VariantFile.
    """
    count = max(3, min(5, count))
    out_dir = repo_root / ".signalos" / "design" / "variants" / f"wave-{wave}"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    archetypes = _ARCHETYPES[:count]
    result: list[VariantFile] = []

    for idx, (archetype, description) in enumerate(archetypes, 1):
        css, html = _build_archetype(archetype, title, wave, brief.mode)
        rendered = _HTML_TEMPLATE.format(
            title=title,
            archetype=archetype,
            archetype_css=css,
            archetype_html=html,
            wave=wave,
            ts=ts,
            mode=brief.mode.value,
        )
        fname = f"variant-{idx:02d}-{archetype}.html"
        fpath = out_dir / fname
        fpath.write_text(rendered, encoding="utf-8")
        result.append(VariantFile(path=fpath, archetype=archetype, description=description))

    # Write comparison board
    _write_comparison_board(out_dir, result, title, wave, ts)

    return result


def _build_archetype(
    archetype: str, title: str, wave: str, mode: PreDesignMode
) -> tuple[str, str]:
    """Return (css_string, html_string) for the given archetype."""
    accent = "#6366f1"
    if archetype == "hero-split":
        css = """
nav{display:flex;align-items:center;justify-content:space-between;padding:16px 32px;border-bottom:1px solid var(--c-border)}
.logo{font-size:15px;font-weight:600;color:var(--c-text)}
.hero{display:grid;grid-template-columns:1fr 1fr;gap:48px;padding:64px 32px;max-width:1100px;margin:0 auto}
.hero-content{display:flex;flex-direction:column;justify-content:center;gap:20px}
h1{font-size:clamp(28px,4vw,48px);font-weight:700;line-height:1.1;letter-spacing:-.02em}
.sub{font-size:16px;color:var(--c-secondary);line-height:1.6;max-width:440px}
.cta{display:inline-flex;align-items:center;gap:8px;background:var(--c-accent);color:#fff;padding:12px 24px;border-radius:var(--radius);font-size:14px;font-weight:500;border:none;cursor:pointer;width:fit-content}
.hero-visual{background:var(--c-surface);border-radius:var(--radius);min-height:320px;display:flex;align-items:center;justify-content:center;color:var(--c-secondary);font-size:13px}
@media(max-width:640px){.hero{grid-template-columns:1fr}}"""
        html = f"""<nav><span class="logo">Product</span><span style="font-size:13px;color:var(--c-secondary)">{mode.value}</span></nav>
<main class="hero">
  <div class="hero-content">
    <h1>{title}</h1>
    <p class="sub">Wave {wave} · {mode.value} mode · Design variant for review.</p>
    <button class="cta">Get started →</button>
  </div>
  <div class="hero-visual">[ visual area ]</div>
</main>"""

    elif archetype == "dashboard":
        css = """
header{display:flex;align-items:center;justify-content:space-between;padding:16px 24px;border-bottom:1px solid var(--c-border)}
h1{font-size:16px;font-weight:600}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;padding:24px}
.metric{background:var(--c-surface);border-radius:var(--radius);padding:16px}
.metric-label{font-size:11px;color:var(--c-secondary);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.metric-val{font-size:28px;font-weight:600}
.content-grid{display:grid;grid-template-columns:2fr 1fr;gap:16px;padding:0 24px 24px}
.panel{background:var(--c-surface);border-radius:var(--radius);padding:16px;min-height:200px}
.panel-title{font-size:13px;font-weight:500;margin-bottom:12px;color:var(--c-secondary)}
@media(max-width:640px){.content-grid{grid-template-columns:1fr}}"""
        html = f"""<header><h1>{title}</h1><span style="font-size:12px;color:var(--c-secondary)">Wave {wave}</span></header>
<div class="metrics">
  <div class="metric"><div class="metric-label">Total</div><div class="metric-val">—</div></div>
  <div class="metric"><div class="metric-label">Active</div><div class="metric-val">—</div></div>
  <div class="metric"><div class="metric-label">Completed</div><div class="metric-val">—</div></div>
  <div class="metric"><div class="metric-label">Pending</div><div class="metric-val">—</div></div>
</div>
<div class="content-grid">
  <div class="panel"><div class="panel-title">Main content area</div></div>
  <div class="panel"><div class="panel-title">Sidebar</div></div>
</div>"""

    elif archetype == "minimal":
        css = """
body{display:flex;flex-direction:column;align-items:center;padding:80px 24px}
.minimal-wrap{width:100%;max-width:640px}
.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--c-secondary);margin-bottom:16px}
h1{font-size:clamp(24px,5vw,40px);font-weight:700;line-height:1.15;letter-spacing:-.02em;margin-bottom:20px}
.body-text{font-size:16px;color:var(--c-secondary);line-height:1.7;margin-bottom:32px}
.action-row{display:flex;gap:12px;flex-wrap:wrap}
.btn-primary{background:var(--c-text);color:#fff;padding:10px 20px;border-radius:var(--radius);font-size:14px;border:none;cursor:pointer}
.btn-secondary{background:none;border:1px solid var(--c-border);padding:10px 20px;border-radius:var(--radius);font-size:14px;cursor:pointer}
.divider{width:100%;height:1px;background:var(--c-border);margin:40px 0}"""
        html = f"""<div class="minimal-wrap">
  <div class="eyebrow">Wave {wave} · {mode.value}</div>
  <h1>{title}</h1>
  <p class="body-text">A minimal, typography-led layout that lets content breathe. Review whether the information hierarchy is clear without decorative elements.</p>
  <div class="action-row"><button class="btn-primary">Primary action</button><button class="btn-secondary">Secondary</button></div>
  <div class="divider"></div>
  <p style="font-size:14px;color:var(--c-secondary)">Content section follows the divider.</p>
</div>"""

    elif archetype == "card-grid":
        css = """
header{padding:20px 24px;border-bottom:1px solid var(--c-border);display:flex;align-items:baseline;gap:12px}
h1{font-size:16px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px;padding:24px}
.card{background:var(--c-surface);border-radius:var(--radius);padding:20px;box-shadow:var(--shadow)}
.card-tag{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--c-secondary);margin-bottom:8px}
.card-title{font-size:14px;font-weight:500;margin-bottom:6px}
.card-body{font-size:12px;color:var(--c-secondary);line-height:1.5}"""
        html = f"""<header><h1>{title}</h1><span style="font-size:12px;color:var(--c-secondary)">Wave {wave}</span></header>
<div class="grid">
  {''.join(f'<div class="card"><div class="card-tag">Item {i+1}</div><div class="card-title">Card title {i+1}</div><div class="card-body">Supporting detail for this card entry.</div></div>' for i in range(6))}
</div>"""

    else:  # sidebar-nav
        css = """
.layout{display:grid;grid-template-columns:220px 1fr;min-height:100vh}
.sidebar{background:var(--c-surface);border-right:1px solid var(--c-border);padding:20px 0}
.sidebar-logo{padding:0 16px 20px;font-size:14px;font-weight:600;border-bottom:1px solid var(--c-border);margin-bottom:8px}
.nav-item{display:block;padding:8px 16px;font-size:13px;color:var(--c-secondary);cursor:pointer;border-radius:0}
.nav-item.active{color:var(--c-text);background:var(--c-border)}
.main{padding:32px}
.page-title{font-size:20px;font-weight:600;margin-bottom:24px}
.content-area{background:var(--c-surface);border-radius:var(--radius);padding:24px;min-height:300px}
@media(max-width:640px){.layout{grid-template-columns:1fr}.sidebar{display:none}}"""
        html = f"""<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-logo">Product</div>
    <div class="nav-item active">Overview</div>
    <div class="nav-item">Section</div>
    <div class="nav-item">Settings</div>
  </aside>
  <main class="main">
    <div class="page-title">{title}</div>
    <div class="content-area"><p style="color:var(--c-secondary);font-size:13px">Wave {wave} · {mode.value} · sidebar-nav variant</p></div>
  </main>
</div>"""

    return css, html


def _write_comparison_board(
    out_dir: Path,
    variants: list[VariantFile],
    title: str,
    wave: str,
    ts: str,
) -> Path:
    """Write a comparison board index.html listing all variants with iframes."""
    rows = "\n".join(
        f'<li><a href="{v.path.name}" target="_blank">'
        f'<strong>{v.archetype}</strong> — {v.description}</a></li>'
        for v in variants
    )
    iframes = "\n".join(
        f'<div class="frame-wrap"><div class="frame-label">{v.archetype}</div>'
        f'<iframe src="{v.path.name}" title="{v.archetype}"></iframe></div>'
        for v in variants
    )
    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Design Compare — {title} — Wave {wave}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:24px;background:#f4f4f5}}
h1{{font-size:18px;font-weight:600;margin-bottom:4px}}
.meta{{font-size:12px;color:#71717a;margin-bottom:20px}}
ul{{margin-bottom:24px;padding-left:20px;font-size:13px;line-height:2}}
.frames{{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px}}
.frame-wrap{{background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
.frame-label{{font-size:11px;font-weight:500;padding:8px 12px;border-bottom:1px solid #e4e4e7;background:#fff;text-transform:uppercase;letter-spacing:.05em;color:#71717a}}
iframe{{width:100%;height:320px;border:none;display:block}}
</style>
</head>
<body>
<h1>Design Compare — {title}</h1>
<div class="meta">Wave {wave} · Generated {ts}</div>
<ul>{rows}</ul>
<div class="frames">{iframes}</div>
</body>
</html>
"""
    board_path = out_dir / "index.html"
    board_path.write_text(html, encoding="utf-8")
    return board_path


# ---------------------------------------------------------------------------
# Design review
# ---------------------------------------------------------------------------

def review_variant(
    variant_path: Path,
    scores: dict[str, float],
) -> ReviewResult:
    """
    Evaluate a variant against REVIEW_DIMENSIONS using provided *scores*.
    Scores must be in [0, 10] for each dimension key.

    The 'slop' dimension is inverted for overall calculation
    (high slop score = bad; it contributes as 10 - slop_score).

    Returns ReviewResult with overall (average) and passed (>= 7.0) flag.
    """
    dimensions = [d for d, _ in REVIEW_DIMENSIONS]
    issues: list[str] = []

    normalised: dict[str, float] = {}
    for dim in dimensions:
        raw = float(scores.get(dim, 0.0))
        if not (0.0 <= raw <= 10.0):
            raise ValueError(f"Score for '{dim}' must be in [0, 10], got {raw}")
        # slop is inverted: 0 slop = 10 contribution, 10 slop = 0 contribution
        normalised[dim] = (10.0 - raw) if dim == "slop" else raw
        if normalised[dim] < 7.0:
            dim_label = next(label for d, label in REVIEW_DIMENSIONS if d == dim)
            issues.append(f"{dim}: {dim_label} (score {normalised[dim]:.1f}/10)")

    overall = sum(normalised.values()) / len(dimensions)

    return ReviewResult(
        variant_path=variant_path,
        scores=normalised,
        overall=round(overall, 2),
        passed=overall >= 7.0,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Production HTML generation
# ---------------------------------------------------------------------------

def generate_production_html(
    approved_variant_path: Path,
    repo_root: Path,
    wave: str,
    framework: str = "html",
) -> Path:
    """
    Promote an approved variant to production HTML.
    Writes to .signalos/design/production/wave-{wave}/output.{ext}.
    framework: "html" | "jsx" | "svelte"
    """
    if framework not in ("html", "jsx", "svelte"):
        raise ValueError(f"framework must be 'html', 'jsx', or 'svelte', got {framework!r}")

    out_dir = repo_root / ".signalos" / "design" / "production" / f"wave-{wave}"
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = {"html": "html", "jsx": "jsx", "svelte": "svelte"}[framework]
    out_path = out_dir / f"output.{ext}"

    source = approved_variant_path.read_text(encoding="utf-8")

    if framework == "html":
        # Add production marker comment and write as-is
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        output = source.replace(
            "</body>",
            f"<!-- SignalOS production output · wave: {wave} · promoted: {ts} -->\n</body>",
        )
        out_path.write_text(output, encoding="utf-8")

    elif framework == "jsx":
        # Wrap HTML body content in a React component
        body_match = re.search(r"<body[^>]*>(.*?)</body>", source, re.DOTALL | re.IGNORECASE)
        body_inner = body_match.group(1).strip() if body_match else source
        # Escape JSX special chars
        jsx_body = body_inner.replace(" class=", " className=").replace(
            " for=", " htmlFor="
        )
        jsx = (
            f"// SignalOS production output — wave {wave}\n"
            f"// Auto-generated by /signal-design-html\n"
            f"export default function ProductionComponent() {{\n"
            f"  return (\n"
            f"    <>\n"
            f"{jsx_body}\n"
            f"    </>\n"
            f"  );\n"
            f"}}\n"
        )
        out_path.write_text(jsx, encoding="utf-8")

    else:  # svelte
        body_match = re.search(r"<body[^>]*>(.*?)</body>", source, re.DOTALL | re.IGNORECASE)
        body_inner = body_match.group(1).strip() if body_match else source
        style_match = re.search(r"<style>(.*?)</style>", source, re.DOTALL | re.IGNORECASE)
        style_inner = style_match.group(1).strip() if style_match else ""
        svelte = (
            f"<!-- SignalOS production output — wave {wave} -->\n"
            f"<script>\n  // Auto-generated by /signal-design-html\n</script>\n\n"
            f"{body_inner}\n\n"
            f"<style>\n{style_inner}\n</style>\n"
        )
        out_path.write_text(svelte, encoding="utf-8")

    return out_path


def detect_framework(repo_root: Path) -> str:
    """
    Detect the project's frontend framework.
    Returns "jsx", "svelte", or "html".
    """
    root = repo_root
    if any(root.glob("**/*.jsx")) or any(root.glob("**/*.tsx")):
        return "jsx"
    if any(root.glob("**/*.svelte")):
        return "svelte"
    return "html"


# ---------------------------------------------------------------------------
# Taste memory
# ---------------------------------------------------------------------------

def _taste_log_path(repo_root: Path) -> Path:
    return repo_root / ".signalos" / "design-taste.jsonl"


def record_taste(
    repo_root: Path,
    wave: str,
    variant_archetype: str,
    verdict: str,
    traits: list[str],
) -> TasteEntry:
    """
    Append one taste entry to .signalos/design-taste.jsonl.
    verdict must be 'approved' or 'rejected'.
    """
    if verdict not in ("approved", "rejected"):
        raise ValueError(f"verdict must be 'approved' or 'rejected', got {verdict!r}")
    if not traits:
        raise ValueError("traits must be a non-empty list")

    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = TasteEntry(
        wave=wave,
        variant_archetype=variant_archetype,
        verdict=verdict,
        traits=traits,
        recorded_at=now_iso,
        weight=1.0,
    )
    log = _taste_log_path(repo_root)
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "wave": entry.wave,
        "variant_archetype": entry.variant_archetype,
        "verdict": entry.verdict,
        "traits": entry.traits,
        "recorded_at": entry.recorded_at,
        "weight": entry.weight,
    }
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return entry


def decay_weight(recorded_at_iso: str, now_iso: str | None = None) -> float:
    """
    Return the decayed weight for an entry recorded at *recorded_at_iso*.
    weight = 1.0 × 0.95^weeks_elapsed
    """
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    recorded = datetime.strptime(recorded_at_iso, fmt).replace(tzinfo=timezone.utc)
    if now_iso is None:
        now = datetime.now(tz=timezone.utc)
    else:
        now = datetime.strptime(now_iso, fmt).replace(tzinfo=timezone.utc)
    elapsed_seconds = max(0.0, (now - recorded).total_seconds())
    weeks = elapsed_seconds / (7 * 24 * 3600)
    return round(TASTE_DECAY_BASE ** weeks, 6)


def load_taste_context(repo_root: Path, top_n: int = 3) -> str:
    """
    Load design-taste.jsonl, apply decay weights, return a human-readable
    context string listing top_n approved and top_n rejected traits.
    """
    log = _taste_log_path(repo_root)
    if not log.exists():
        return ""

    entries: list[dict[str, Any]] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            row["_weight"] = decay_weight(row["recorded_at"])
            entries.append(row)
        except (json.JSONDecodeError, KeyError):
            continue

    # Sort by decayed weight desc
    entries.sort(key=lambda r: r["_weight"], reverse=True)

    approved = [e for e in entries if e["verdict"] == "approved"][:top_n]
    rejected = [e for e in entries if e["verdict"] == "rejected"][:top_n]

    lines: list[str] = ["## Taste Memory Context\n"]
    if approved:
        lines.append("**Approved traits (use these):**")
        for e in approved:
            lines.append(f"- {', '.join(e['traits'])} (wave {e['wave']}, weight {e['_weight']:.2f})")
    if rejected:
        lines.append("\n**Rejected traits (avoid these):**")
        for e in rejected:
            lines.append(f"- {', '.join(e['traits'])} (wave {e['wave']}, weight {e['_weight']:.2f})")

    return "\n".join(lines) if (approved or rejected) else ""


# ---------------------------------------------------------------------------
# DECISION-DNA auto-log
# ---------------------------------------------------------------------------

def append_decision_dna(
    repo_root: Path,
    decision: str,
    rationale: str,
    author: str,
    wave: str,
    artifact: str,
) -> None:
    """
    Append one entry to core/governance/Governance/DECISION-DNA.md.
    Creates the file if absent using the template.
    """
    dna_path = repo_root / "core" / "governance" / "Governance" / "DECISION-DNA.md"
    today = date.today().isoformat()

    entry = (
        f"\n---\n\n"
        f"### {today} — {decision}\n\n"
        f"**Wave:** {wave}  \n"
        f"**Artifact:** `{artifact}`  \n"
        f"**Author:** {author}  \n\n"
        f"**Decision:** {decision}\n\n"
        f"**Rationale:** {rationale}\n"
    )

    if dna_path.exists():
        existing = dna_path.read_text(encoding="utf-8")
        dna_path.write_text(existing.rstrip() + entry, encoding="utf-8")
    else:
        dna_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"<!-- SignalOS v1.0 — DECISION-DNA -->\n\n"
            f"# Decision DNA\n\n"
            f"`Canonical path: core/governance/Governance/DECISION-DNA.md`\n"
        )
        dna_path.write_text(header + entry, encoding="utf-8")


# ---------------------------------------------------------------------------
# Gate guards (for C13/C14 checks)
# ---------------------------------------------------------------------------

def check_po_brief_signed(repo_root: Path) -> bool:
    """
    Return True if core/strategy/PO_BRIEF.md exists and has a non-DRAFT signature.
    """
    brief_path = repo_root / "core" / "strategy" / "PO_BRIEF.md"
    if not brief_path.exists():
        return False
    text = brief_path.read_text(encoding="utf-8", errors="replace")
    sig_m = re.search(r"^## Signatures", text, re.MULTILINE)
    if not sig_m:
        return False
    sig_block = text[sig_m.start():]
    # Must have at least one signer: line that is not DRAFT
    for sm in re.finditer(r"signer:\s*(.+)", sig_block):
        name = sm.group(1).strip()
        if name and "DRAFT" not in name.upper():
            return True
    return False


def check_design_reviewed(repo_root: Path, wave: str) -> bool:
    """
    Return True if a design review file exists for this wave in
    .signalos/design/reviews/wave-{wave}/.
    """
    review_dir = repo_root / ".signalos" / "design" / "reviews" / f"wave-{wave}"
    if not review_dir.exists():
        return False
    # Any .json file in the review dir counts as evidence
    return any(review_dir.glob("*.json"))
