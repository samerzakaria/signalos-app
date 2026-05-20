"""Translator-mode (M-W6) — inspect-first fast-forward for non-SignalOS artifacts.

Per WAVE-ENGINE-DESIGN §7 and the §13 Q4 resolution:

> When an artifact exists in non-SignalOS format (e.g., user pasted a
> Figma URL, attached a free-text belief doc, points at an external Jira)
> → fire the gate agent in translator-mode: ingest the external artifact,
> produce the SignalOS-format version, user confirms the translation
> captures their intent, sign.

Supported external formats at launch (Q4):
  - Plain markdown (.md, .markdown) — read as-is
  - PDF (.pdf)                       — pypdf (optional dep)
  - Word (.docx)                     — python-docx (optional dep)
  - Figma URLs                       — recorded as reference (no text extracted)
  - Other http(s) URLs               — recorded as reference

The translator extracts plain text where possible. The wave-engine
caller then passes that text + the detected format to the gate agent
in translator-mode so the agent can produce the SignalOS-format
artifact (e.g., a doc-format belief from a free-text PDF).

Optional dependencies (pypdf, python-docx) are imported lazily — if
they aren't installed, the translator returns a `supported=False`
result with an `install_hint` rather than raising at import time.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


__all__ = [
    "detect_format",
    "translate",
    "ExternalFormat",
]


# Format identifiers used by the translator + wave engine.
class ExternalFormat:
    MARKDOWN = "markdown"
    PDF = "pdf"
    DOCX = "docx"
    FIGMA = "figma-url"
    URL = "url"
    UNKNOWN = "unknown"


def detect_format(artifact: str) -> str:
    """Classify *artifact* (a path string or URL) as a known format.

    Returns one of the ExternalFormat constants. The classifier prefers
    URL detection first (so 'http://example.com/foo.pdf' is treated as
    URL, not PDF — translator-mode for URLs records the reference; PDF
    extraction is only for local files).
    """
    if not artifact:
        return ExternalFormat.UNKNOWN

    text = artifact.strip()

    # URL check first — a remote PDF is a URL reference, not a PDF we'd
    # download and extract here.
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        host = (parsed.netloc or "").lower()
        if "figma.com" in host:
            return ExternalFormat.FIGMA
        return ExternalFormat.URL

    # Local-path checks — by suffix.
    lower = text.lower()
    if lower.endswith((".md", ".markdown")):
        return ExternalFormat.MARKDOWN
    if lower.endswith(".pdf"):
        return ExternalFormat.PDF
    if lower.endswith(".docx"):
        return ExternalFormat.DOCX
    return ExternalFormat.UNKNOWN


def _extract_markdown(path: Path, max_chars: int) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "supported": False,
            "format": ExternalFormat.MARKDOWN,
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "supported": True,
        "format": ExternalFormat.MARKDOWN,
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
        "source_path": str(path),
    }


def _extract_pdf(path: Path, max_chars: int) -> dict[str, Any]:
    try:
        import pypdf  # type: ignore[import-untyped]
    except ImportError:
        return {
            "supported": False,
            "format": ExternalFormat.PDF,
            "text": "",
            "install_hint": "pip install pypdf",
        }

    try:
        reader = pypdf.PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 — pypdf raises a variety
        return {
            "supported": False,
            "format": ExternalFormat.PDF,
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
        }

    parts: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            continue
        parts.append(page_text)
        total += len(page_text)
        if total >= max_chars:
            break
    full = "\n\n".join(parts)
    return {
        "supported": True,
        "format": ExternalFormat.PDF,
        "text": full[:max_chars],
        "truncated": len(full) > max_chars,
        "source_path": str(path),
        "page_count": len(reader.pages),
    }


def _extract_docx(path: Path, max_chars: int) -> dict[str, Any]:
    try:
        import docx  # type: ignore[import-untyped]  # python-docx
    except ImportError:
        return {
            "supported": False,
            "format": ExternalFormat.DOCX,
            "text": "",
            "install_hint": "pip install python-docx",
        }

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        return {
            "supported": False,
            "format": ExternalFormat.DOCX,
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
        }

    paragraphs = [p.text for p in document.paragraphs if p.text]
    full = "\n".join(paragraphs)
    return {
        "supported": True,
        "format": ExternalFormat.DOCX,
        "text": full[:max_chars],
        "truncated": len(full) > max_chars,
        "source_path": str(path),
        "paragraph_count": len(paragraphs),
    }


def _extract_figma(url: str) -> dict[str, Any]:
    # Figma references are not extracted as text — the design agent
    # records the URL and the user reviews the design in Figma.
    file_key = ""
    m = re.search(r"/(file|design|proto)/([A-Za-z0-9]+)", url)
    if m:
        file_key = m.group(2)
    return {
        "supported": True,
        "format": ExternalFormat.FIGMA,
        "text": "",  # no body text — caller records the URL
        "source_url": url,
        "figma_file_key": file_key or None,
        "note": (
            "Figma URL recorded as design reference. The design agent "
            "uses this URL as the external-design-ref shape (audit §6.7); "
            "the user reviews the design in Figma."
        ),
    }


def _extract_url(url: str) -> dict[str, Any]:
    # Generic URL — recorded as a reference without HTTP fetch.
    # Engine-driven HTTP fetch is deferred (sandbox / safety concerns).
    return {
        "supported": True,
        "format": ExternalFormat.URL,
        "text": "",
        "source_url": url,
        "note": (
            "URL recorded as external reference. SignalOS does not fetch "
            "the URL contents from the engine layer; the gate agent "
            "treats the URL as an external-ref artifact."
        ),
    }


def translate(artifact: str, *, max_chars: int = 20_000) -> dict[str, Any]:
    """Translate an external artifact into plain text + metadata.

    Returns a dict shaped like:

        {
            "supported": bool,         # True iff extraction succeeded
                                       # (URL/Figma always True — they're
                                       # recorded as references, not extracted)
            "format": "markdown" | "pdf" | "docx" | "figma-url"
                       | "url" | "unknown",
            "text": "<extracted plain text>" | "",
            "source_path": str | absent (for local files)
            "source_url":  str | absent (for URLs)
            "truncated":   bool — text was longer than max_chars (file types only)
            "install_hint": str — present when optional dep is missing
            "error":       str — present on extraction failure
        }

    max_chars caps the extracted body so a 200-page PDF doesn't blow
    the LLM context window. The full doc is still on disk; the engine
    can chunk further if needed.
    """
    fmt = detect_format(artifact)

    if fmt in (ExternalFormat.FIGMA,):
        return _extract_figma(artifact)
    if fmt is ExternalFormat.URL:
        return _extract_url(artifact)
    if fmt is ExternalFormat.UNKNOWN:
        return {
            "supported": False,
            "format": ExternalFormat.UNKNOWN,
            "text": "",
            "error": f"Could not detect format for: {artifact!r}",
        }

    # Local-file formats.
    path = Path(artifact)
    if not path.is_file():
        return {
            "supported": False,
            "format": fmt,
            "text": "",
            "error": f"File not found: {artifact!r}",
        }

    if fmt is ExternalFormat.MARKDOWN:
        return _extract_markdown(path, max_chars)
    if fmt is ExternalFormat.PDF:
        return _extract_pdf(path, max_chars)
    if fmt is ExternalFormat.DOCX:
        return _extract_docx(path, max_chars)

    # Unreachable — detect_format only returns the above values.
    return {
        "supported": False,
        "format": fmt,
        "text": "",
        "error": f"No translator wired for format {fmt!r}",
    }
