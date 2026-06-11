"""Tests for competitor ingestion and the Competitive UX Matrix."""

from __future__ import annotations

from signalos_lib.product.competitor import (
    build_matrix,
    extract_page,
    matrix_markdown,
)

_PAGE_A = """
<html><head>
  <title>Acme — Project management for teams</title>
  <meta name="description" content="Plan, track, and ship work.">
</head><body>
  <h1>Run your team on Acme</h1>
  <h2>Boards</h2><h2>Timelines</h2><h3>Reports</h3>
  <a href="/signup">Start free trial</a>
  <button>Book a demo</button>
  <span>$12/mo</span>
</body></html>
"""

_PAGE_B = """
<html><head><title>Beta App</title></head><body>
  <h1>The simplest to-do list</h1>
  <a href="/learn">Learn more</a>
  <a href="/get">Get started</a>
</body></html>
"""


def test_extract_core_signals():
    page = extract_page("https://acme.test", _PAGE_A)
    assert page["title"] == "Acme — Project management for teams"
    assert page["description"] == "Plan, track, and ship work."
    assert page["headline"] == "Run your team on Acme"
    assert "Boards" in page["headings"]
    assert page["has_pricing"] is True
    assert any("trial" in c.lower() or "demo" in c.lower() for c in page["ctas"])


def test_extract_handles_missing_meta_and_pricing():
    page = extract_page("https://beta.test", _PAGE_B)
    assert page["title"] == "Beta App"
    assert page["description"] == ""
    assert page["has_pricing"] is False
    assert page["ctas"]  # "Get started" is a CTA; "Learn more" is not


def test_extract_never_raises_on_garbage():
    page = extract_page("x", "<title>broken <h1>no close")
    assert isinstance(page, dict)
    assert page["url"] == "x"


def test_build_matrix_rows(monkeypatch):
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    m = build_matrix([
        {"url": "https://acme.test", "html": _PAGE_A},
        {"url": "https://beta.test", "html": _PAGE_B},
    ])
    assert m["llm_authored"] is False
    assert len(m["matrix"]) == 2
    acme = next(r for r in m["matrix"] if "acme" in r["url"])
    assert acme["has_pricing"] == "yes"
    assert acme["feature_count"] >= 3
    beta = next(r for r in m["matrix"] if "beta" in r["url"])
    assert beta["has_pricing"] == "no"


def test_matrix_markdown_is_a_table(monkeypatch):
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    m = build_matrix([{"url": "https://acme.test", "html": _PAGE_A}])
    md = matrix_markdown(m)
    assert "# Competitive UX Matrix" in md
    assert "| Competitor |" in md
    assert "acme.test" in md


def test_empty_matrix_is_graceful():
    md = matrix_markdown(build_matrix([], use_llm=False))
    assert "No competitors analysed" in md
