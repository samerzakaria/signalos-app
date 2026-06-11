# signalos_lib/product/competitor.py
# Competitor ingestion -> Competitive UX Matrix (Brief phase).
#
# Given competitor URLs, extract the signals that matter for a UX/positioning
# comparison -- title, meta description, headings, calls-to-action, nav, and
# pricing cues -- and assemble a Competitive UX Matrix. Extraction is pure and
# stdlib-only (regex, no bs4) so it is testable on static HTML; fetching is a
# separate best-effort helper that never raises and is not exercised in tests.
# An optional LLM pass adds a positioning synthesis, resolving its key
# product-first via secrets_resolver.

from __future__ import annotations

__all__ = [
    "extract_page",
    "fetch_page",
    "build_matrix",
    "matrix_markdown",
]

import re
from html import unescape
from typing import Any

_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_DESC = re.compile(
    r"""<meta[^>]+name=["']description["'][^>]+content=["'](.*?)["']""", re.I | re.S
)
_HEADING = re.compile(r"<h([1-3])[^>]*>(.*?)</h\1>", re.I | re.S)
_ANCHOR = re.compile(r"<a\b[^>]*>(.*?)</a>", re.I | re.S)
_BUTTON = re.compile(r"<button\b[^>]*>(.*?)</button>", re.I | re.S)
_PRICE = re.compile(r"(\$\s?\d[\d,]*(?:\.\d+)?|\b\d+\s?(?:/mo|per month|/month|/yr)\b)", re.I)
_CTA_WORDS = ("start", "try", "get", "sign up", "signup", "buy", "subscribe",
              "book", "demo", "free", "join", "download", "request")


def _clean(text: str) -> str:
    return unescape(_TAG.sub("", text or "")).strip()


def _dedupe(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if it and key not in seen:
            seen.add(key)
            out.append(it)
        if len(out) >= limit:
            break
    return out


def extract_page(url: str, html: str) -> dict[str, Any]:
    """Extract competitive UX signals from a page's HTML. Pure, never raises."""
    html = html or ""
    title_m = _TITLE.search(html)
    desc_m = _META_DESC.search(html)
    headings = [_clean(h) for _, h in _HEADING.findall(html)]
    headings = [h for h in headings if h]

    raw_ctas = [_clean(t) for t in _ANCHOR.findall(html)] + [
        _clean(t) for t in _BUTTON.findall(html)
    ]
    ctas = [c for c in raw_ctas if c and any(w in c.lower() for w in _CTA_WORDS)]

    pricing = _dedupe(_PRICE.findall(html), 6)

    return {
        "url": url,
        "title": _clean(title_m.group(1)) if title_m else "",
        "description": _clean(desc_m.group(1)) if desc_m else "",
        "headline": headings[0] if headings else "",
        "headings": _dedupe(headings, 12),
        "ctas": _dedupe(ctas, 8),
        "pricing_signals": pricing,
        "has_pricing": bool(pricing),
    }


def fetch_page(url: str, timeout: float = 10.0) -> str | None:
    """Best-effort fetch of a URL's HTML. Returns None on any failure.

    Stdlib-only, polite (a UA header), never raises. Not called in tests.
    """
    import urllib.request
    import urllib.error

    if not re.match(r"^https?://", url, re.I):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Foundry-Brief/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read(2_000_000).decode(charset, errors="replace")
    except (urllib.error.URLError, ValueError, OSError, TimeoutError):
        return None


# Dimensions compared across competitors in the matrix.
_DIMENSIONS = [
    {"key": "headline", "label": "Headline / positioning"},
    {"key": "primary_cta", "label": "Primary call-to-action"},
    {"key": "feature_count", "label": "Distinct headings (feature breadth)"},
    {"key": "has_pricing", "label": "Pricing shown"},
]


def build_matrix(
    pages: list[dict[str, str]],
    root=None,
    use_llm: bool = True,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Build a Competitive UX Matrix from a list of {url, html} pages.

    Always returns the deterministic matrix; when a provider is configured an
    LLM positioning synthesis is added under ``insights``.
    """
    competitors = [extract_page(p.get("url", ""), p.get("html", "")) for p in pages]

    rows = []
    for c in competitors:
        rows.append({
            "url": c["url"],
            "headline": c["headline"] or c["title"],
            "primary_cta": c["ctas"][0] if c["ctas"] else "—",
            "feature_count": len(c["headings"]),
            "has_pricing": "yes" if c["has_pricing"] else "no",
        })

    result: dict[str, Any] = {
        "dimensions": _DIMENSIONS,
        "competitors": competitors,
        "matrix": rows,
        "insights": None,
        "llm_authored": False,
    }

    if use_llm and competitors:
        from .llm_provider import is_llm_available
        if is_llm_available(root):
            insight = _llm_insights(competitors, root, provider_name)
            if insight:
                result["insights"] = insight
                result["llm_authored"] = True
    return result


def _llm_insights(competitors, root, provider_name):
    from .llm_provider import call_llm
    import json

    summary = [
        {"url": c["url"], "headline": c["headline"] or c["title"],
         "ctas": c["ctas"][:3], "has_pricing": c["has_pricing"]}
        for c in competitors
    ]
    prompt = (
        "You are a product strategist. Given these competitor pages, identify "
        "in 3-5 short bullet points the positioning gaps and UX opportunities a "
        "new entrant could exploit. Respond as plain text bullets, no preamble.\n\n"
        f"{json.dumps(summary, ensure_ascii=False)[:4000]}"
    )
    result = call_llm(prompt, provider_name=provider_name, root=root)
    return result.text.strip() if result.success and result.text else None


def matrix_markdown(matrix: dict[str, Any]) -> str:
    dims = matrix.get("dimensions", [])
    rows = matrix.get("matrix", [])
    lines = ["# Competitive UX Matrix", ""]
    if not rows:
        return "\n".join(lines + ["No competitors analysed."]) + "\n"

    header = "| Competitor | " + " | ".join(d["label"] for d in dims) + " |"
    sep = "| --- " * (len(dims) + 1) + "|"
    lines += [header, sep]
    for r in rows:
        cells = [str(r.get(d["key"], "")) for d in dims]
        lines.append(f"| {r.get('url','')} | " + " | ".join(cells) + " |")

    if matrix.get("insights"):
        lines += ["", "## Positioning opportunities", "", matrix["insights"]]
    return "\n".join(lines) + "\n"
