# signalos_lib/product/gtm.py
# Go-to-market auto-generation.
#
# During Deliver/closeout, turn the product intent into launch assets: an SEO
# landing page outline, app/store listing copy, and a Product Hunt post. Follows
# the house pattern (questions.py, design.py): if a provider is configured the
# LLM writes richer copy (resolving its key product-first via secrets_resolver);
# otherwise a deterministic template fills from the intent so there is always a
# usable first draft -- never empty, never fabricated metrics.

from __future__ import annotations

__all__ = [
    "generate_gtm",
    "generate_gtm_markdown",
]

import json
from typing import Any


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _first_sentence(workflows: list[str]) -> str:
    return workflows[0] if workflows else "get real work done"


def _deterministic_gtm(intent: dict[str, Any]) -> dict[str, Any]:
    name = str(intent.get("product_name") or "Your Product").strip()
    users = _as_list(intent.get("target_users")) or ["teams"]
    workflows = _as_list(intent.get("primary_workflows"))
    audience = users[0]

    headline = f"{name}: {_first_sentence(workflows)}".strip().rstrip(":")
    subheadline = (
        f"Built for {', '.join(users[:3])}. "
        f"{name} helps you {_first_sentence(workflows)} without the busywork."
    )
    feature_sections = [
        {"title": wf.capitalize(), "body": f"{name} lets you {wf}."}
        for wf in workflows[:5]
    ]

    landing = {
        "headline": headline,
        "subheadline": subheadline,
        "sections": feature_sections,
        "cta": f"Start with {name}",
    }
    keywords = sorted({
        *(w.lower() for wf in workflows for w in wf.split() if len(w) > 3),
        *(u.lower() for u in users),
    })[:12]
    app_store = {
        "title": name[:30],
        "subtitle": (f"For {audience}")[:30],
        "description": (
            f"{name} helps {audience} {_first_sentence(workflows)}.\n\n"
            + "\n".join(f"• {wf.capitalize()}" for wf in workflows[:6])
        ),
        "keywords": keywords,
    }
    product_hunt = {
        "tagline": (f"{name} — {_first_sentence(workflows)}")[:60],
        "first_comment": (
            f"Hey hunters! We built {name} for {audience}. "
            f"It helps you {_first_sentence(workflows)}. "
            "Would love your feedback!"
        ),
        "topics": (["productivity", "saas"] + [u.lower() for u in users[:2]])[:4],
    }
    return {"landing_page": landing, "app_store": app_store, "product_hunt": product_hunt}


def generate_gtm(
    intent: dict[str, Any],
    root=None,
    use_llm: bool = True,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Generate launch assets from product intent.

    Always returns a complete, usable set (landing_page, app_store,
    product_hunt) from the deterministic template; when an LLM provider is
    configured (and use_llm), richer LLM-written copy replaces it. ``root`` is
    the product workspace so the LLM call resolves its key product-first.
    """
    base = _deterministic_gtm(intent)
    base["llm_authored"] = False

    if not use_llm:
        return base

    from .llm_provider import is_llm_available
    if not is_llm_available(root):
        return base

    enriched = _llm_gtm(intent, root, provider_name)
    if enriched is not None:
        enriched["llm_authored"] = True
        return enriched
    return base


def _llm_gtm(intent: dict[str, Any], root, provider_name: str | None) -> dict[str, Any] | None:
    """Best-effort LLM GTM authoring. Returns None on any problem."""
    from .llm_provider import call_llm

    prompt = (
        "You are a product marketer. From this product intent, write launch "
        "copy. Respond ONLY with JSON of shape: {\"landing_page\": {\"headline\", "
        "\"subheadline\", \"sections\": [{\"title\",\"body\"}], \"cta\"}, "
        "\"app_store\": {\"title\",\"subtitle\",\"description\",\"keywords\":[]}, "
        "\"product_hunt\": {\"tagline\",\"first_comment\",\"topics\":[]}}. "
        "Be specific and benefit-led; do not invent statistics.\n\n"
        f"Product intent:\n{json.dumps(intent, ensure_ascii=False)[:4000]}"
    )
    result = call_llm(prompt, provider_name=provider_name, root=root)
    if not result.success:
        return None
    text = (result.text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    # Require the three top-level assets; otherwise fall back.
    if not all(k in parsed for k in ("landing_page", "app_store", "product_hunt")):
        return None
    return parsed


def generate_gtm_markdown(gtm: dict[str, Any]) -> str:
    """Render a GTM asset bundle as a single founder-facing markdown doc."""
    lp = gtm.get("landing_page", {})
    store = gtm.get("app_store", {})
    ph = gtm.get("product_hunt", {})
    lines = ["# Go-to-market assets", ""]
    if not gtm.get("llm_authored", False):
        lines += ["_Draft generated from your product brief. Connect a provider "
                  "for richer copy._", ""]

    lines += ["## Landing page", "",
              f"**{lp.get('headline', '')}**", "", lp.get("subheadline", ""), ""]
    for s in lp.get("sections", []):
        lines.append(f"- **{s.get('title','')}** — {s.get('body','')}")
    if lp.get("cta"):
        lines += ["", f"> {lp['cta']}"]

    lines += ["", "## App store listing", "",
              f"- **Title:** {store.get('title','')}",
              f"- **Subtitle:** {store.get('subtitle','')}",
              f"- **Keywords:** {', '.join(store.get('keywords', []))}",
              "", store.get("description", "")]

    lines += ["", "## Product Hunt", "",
              f"- **Tagline:** {ph.get('tagline','')}",
              f"- **Topics:** {', '.join(ph.get('topics', []))}",
              "", ph.get("first_comment", "")]
    return "\n".join(lines) + "\n"
