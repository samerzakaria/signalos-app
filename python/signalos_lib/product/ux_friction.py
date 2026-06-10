# signalos_lib/product/ux_friction.py
# Agentic QA — UX Friction Report.
#
# Before the Validate gate, evaluate a generated UI surface from the point of
# view of several user personas and emit a structured friction report. Each
# persona has a deterministic static-analysis lens that runs with NO browser
# and NO LLM (so it is fast, free, and testable), plus an optional LLM pass
# that adds richer, context-aware findings when a provider is configured.
#
# The LLM pass resolves its key product-first (see secrets_resolver), so a
# product that overrides the app-level key is honoured here too.
#
# Findings use plain-language severities (high / medium / low) -- never
# internal governance codes -- because this report is shown to founders.

from __future__ import annotations

__all__ = [
    "PERSONAS",
    "generate_friction_report",
    "heuristic_findings",
]

import re
from typing import Any

# Each persona pairs a founder-legible label with the friction it is sensitive
# to. The `id` is stable for downstream tooling; the `label` is user-facing.
PERSONAS: list[dict[str, str]] = [
    {"id": "impatient", "label": "Impatient User",
     "lens": "abandons on slow or unacknowledged actions"},
    {"id": "colorblind", "label": "Colorblind User",
     "lens": "cannot rely on colour alone to read meaning"},
    {"id": "first_time", "label": "First-time User",
     "lens": "needs guidance on an empty or unfamiliar screen"},
    {"id": "mobile", "label": "Mobile User",
     "lens": "small viewport, touch targets, no hover"},
    {"id": "keyboard", "label": "Keyboard-only User",
     "lens": "navigates without a mouse; needs focus and semantics"},
]

_SEVERITIES = ("high", "medium", "low")


def _finding(severity: str, issue: str, suggestion: str) -> dict[str, str]:
    if severity not in _SEVERITIES:
        severity = "medium"
    return {"severity": severity, "issue": issue, "suggestion": suggestion}


def _impatient_findings(html: str, low: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    has_async = any(t in low for t in ("fetch(", "await ", "axios", "xmlhttprequest", "onsubmit"))
    has_loading = any(t in low for t in ("spinner", "loading", "aria-busy", "skeleton", "progress"))
    if has_async and not has_loading:
        out.append(_finding(
            "high",
            "Actions trigger async work but no loading or busy state is shown.",
            "Show a spinner, skeleton, or aria-busy while requests are in flight.",
        ))
    if "<button" in low and "disabled" not in low and has_async:
        out.append(_finding(
            "medium",
            "Submit buttons are not disabled during submission.",
            "Disable the button on submit to prevent double-clicks and signal progress.",
        ))
    return out


def _colorblind_findings(html: str, low: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    # Colour words used as the only signifier of state, with no text/icon nearby.
    color_state = re.search(r"color\s*:\s*(red|green)\b", low)
    mentions_textual_state = any(t in low for t in ("error", "success", "invalid", "valid", "warning"))
    has_icon = any(t in low for t in ("<svg", "icon", "aria-label", "<i "))
    if color_state and not (mentions_textual_state and has_icon):
        out.append(_finding(
            "high",
            "State appears to be conveyed by colour (red/green) alone.",
            "Pair colour with a text label or icon so meaning survives colour blindness.",
        ))
    if "required" in low and "*" in html and "aria-required" not in low:
        out.append(_finding(
            "low",
            "Required fields may be marked with a colour/asterisk only.",
            "Add aria-required and a textual 'required' cue.",
        ))
    return out


def _first_time_findings(html: str, low: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    has_list = any(t in low for t in ("<ul", "<table", "map(", "v-for", ".map"))
    has_empty_state = any(t in low for t in ("empty", "no results", "get started", "nothing here", "onboard"))
    if has_list and not has_empty_state:
        out.append(_finding(
            "medium",
            "Lists/tables have no empty state for a brand-new user.",
            "Add an empty state with a one-line explanation and a primary call to action.",
        ))
    if "<form" in low and not any(t in low for t in ("placeholder", "label", "hint", "help")):
        out.append(_finding(
            "low",
            "Form inputs lack labels, placeholders, or hints.",
            "Label every field and add a hint for anything non-obvious.",
        ))
    return out


def _mobile_findings(html: str, low: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if "<meta" in low and "viewport" not in low:
        out.append(_finding(
            "high",
            "No responsive viewport meta tag was found.",
            'Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
        ))
    if re.search(r"width\s*:\s*\d{3,}px", low):
        out.append(_finding(
            "medium",
            "Fixed pixel widths will overflow small screens.",
            "Use max-width, %, or responsive units instead of fixed px widths.",
        ))
    return out


def _keyboard_findings(html: str, low: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    # Clickable non-button elements without keyboard semantics.
    if re.search(r"<(div|span)[^>]*onclick", low) and "tabindex" not in low:
        out.append(_finding(
            "high",
            "Clickable div/span elements are not keyboard-focusable.",
            "Use a <button>, or add role, tabindex, and a key handler.",
        ))
    if "outline: none" in low or "outline:none" in low:
        out.append(_finding(
            "medium",
            "Focus outlines appear to be removed.",
            "Keep a visible focus style (e.g. :focus-visible) for keyboard users.",
        ))
    return out


_LENSES = {
    "impatient": _impatient_findings,
    "colorblind": _colorblind_findings,
    "first_time": _first_time_findings,
    "mobile": _mobile_findings,
    "keyboard": _keyboard_findings,
}


def heuristic_findings(surface_html: str) -> list[dict[str, Any]]:
    """Run every persona's deterministic lens over a UI surface (HTML/JSX/text).

    Returns one entry per persona with its findings. No LLM, no network.
    """
    html = surface_html or ""
    low = html.lower()
    report: list[dict[str, Any]] = []
    for persona in PERSONAS:
        lens = _LENSES[persona["id"]]
        findings = lens(html, low)
        report.append({
            "persona": persona["id"],
            "label": persona["label"],
            "findings": findings,
        })
    return report


def _severity_rank(sev: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(sev, 1)


def generate_friction_report(
    surface_html: str,
    root=None,
    use_llm: bool = True,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Produce a UX Friction Report for a generated UI surface.

    Always returns the deterministic persona findings. When an LLM provider is
    configured (and ``use_llm``), an LLM pass appends richer findings per
    persona. ``root`` is the product workspace, so the LLM call resolves its
    key product-first then app-level (secrets_resolver).
    """
    personas = heuristic_findings(surface_html)
    llm_used = False

    if use_llm and surface_html.strip():
        from .llm_provider import is_llm_available
        if is_llm_available(root):
            llm_personas = _llm_pass(surface_html, root, provider_name)
            if llm_personas:
                llm_used = True
                by_id = {p["persona"]: p for p in personas}
                for lp in llm_personas:
                    target = by_id.get(lp.get("persona"))
                    if target is not None:
                        target["findings"].extend(lp.get("findings", []))

    total = sum(len(p["findings"]) for p in personas)
    high = sum(1 for p in personas for f in p["findings"] if f.get("severity") == "high")
    for p in personas:
        p["findings"].sort(key=lambda f: _severity_rank(f.get("severity", "medium")))

    return {
        "personas": personas,
        "summary": {
            "total_findings": total,
            "high_severity": high,
            "personas_with_findings": sum(1 for p in personas if p["findings"]),
            "llm_augmented": llm_used,
        },
    }


def _llm_pass(surface_html: str, root, provider_name: str | None) -> list[dict[str, Any]]:
    """Best-effort LLM augmentation. Never raises; returns [] on any problem."""
    from .llm_provider import call_llm

    persona_lines = "\n".join(f"- {p['id']}: {p['label']} ({p['lens']})" for p in PERSONAS)
    prompt = (
        "You are a UX QA reviewer. For each persona below, list concrete UX "
        "friction points in the UI surface. Respond ONLY with JSON: a list of "
        '{"persona": <id>, "findings": [{"severity": "high|medium|low", '
        '"issue": <str>, "suggestion": <str>}]}.\n\n'
        f"Personas:\n{persona_lines}\n\nUI surface:\n{surface_html[:6000]}"
    )
    result = call_llm(prompt, provider_name=provider_name, root=root)
    if not result.success:
        return []
    import json

    text = (result.text or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    cleaned: list[dict[str, Any]] = []
    valid_ids = {p["id"] for p in PERSONAS}
    for item in parsed:
        if not isinstance(item, dict) or item.get("persona") not in valid_ids:
            continue
        raw_findings = item.get("findings", [])
        findings = [
            _finding(str(f.get("severity", "medium")), str(f.get("issue", "")), str(f.get("suggestion", "")))
            for f in raw_findings
            if isinstance(f, dict) and f.get("issue")
        ]
        if findings:
            cleaned.append({"persona": item["persona"], "findings": findings})
    return cleaned
