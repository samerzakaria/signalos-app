"""Tests for the Agentic QA UX Friction Report (deterministic lenses)."""

from __future__ import annotations

from signalos_lib.product.ux_friction import (
    PERSONAS,
    generate_friction_report,
    heuristic_findings,
)


def _personas_by_id(report):
    return {p["persona"]: p for p in report}


def test_every_persona_present():
    report = heuristic_findings("<div></div>")
    assert {p["persona"] for p in report} == {p["id"] for p in PERSONAS}


def test_impatient_flags_async_without_loading_state():
    html = "<form onsubmit='await fetch(\"/x\")'><button>Save</button></form>"
    by_id = _personas_by_id(heuristic_findings(html))
    issues = by_id["impatient"]["findings"]
    assert any("loading" in f["issue"].lower() or "busy" in f["issue"].lower() for f in issues)


def test_impatient_quiet_when_loading_present():
    html = "<form onsubmit='await fetch(\"/x\")'><div class='spinner'></div><button disabled>Save</button></form>"
    by_id = _personas_by_id(heuristic_findings(html))
    assert by_id["impatient"]["findings"] == []


def test_colorblind_flags_color_only_state():
    html = "<style>.err{color: red;}</style><div class='err'>!</div>"
    by_id = _personas_by_id(heuristic_findings(html))
    assert by_id["colorblind"]["findings"], "expected a colour-only finding"


def test_mobile_flags_missing_viewport_and_fixed_width():
    html = "<head><meta charset='utf-8'></head><div style='width: 1200px'>x</div>"
    by_id = _personas_by_id(heuristic_findings(html))
    issues = " ".join(f["issue"].lower() for f in by_id["mobile"]["findings"])
    assert "viewport" in issues
    assert "fixed pixel" in issues or "px" in issues


def test_keyboard_flags_clickable_div_and_removed_outline():
    html = "<div onclick='go()'>Go</div><style>a{outline: none;}</style>"
    by_id = _personas_by_id(heuristic_findings(html))
    assert len(by_id["keyboard"]["findings"]) >= 1


def test_first_time_flags_list_without_empty_state():
    html = "<ul>{items.map(i => <li>{i}</li>)}</ul>"
    by_id = _personas_by_id(heuristic_findings(html))
    assert by_id["first_time"]["findings"]


def test_clean_surface_has_no_findings():
    html = (
        "<head><meta name='viewport' content='width=device-width'></head>"
        "<main><p>Welcome — get started by adding your first item.</p></main>"
    )
    by_id = _personas_by_id(heuristic_findings(html))
    assert all(p["findings"] == [] for p in by_id.values())


def test_generate_report_structure_without_llm(monkeypatch):
    # Force the no-LLM path so the test never touches the network.
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    html = "<form onsubmit='await fetch(1)'><button>Go</button></form>"
    report = generate_friction_report(html, use_llm=True)
    assert report["summary"]["llm_augmented"] is False
    assert report["summary"]["total_findings"] >= 1
    assert report["summary"]["high_severity"] >= 1
    # Findings are sorted high-severity first within each persona.
    for p in report["personas"]:
        ranks = [{"high": 0, "medium": 1, "low": 2}[f["severity"]] for f in p["findings"]]
        assert ranks == sorted(ranks)
