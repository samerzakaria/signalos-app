"""Tests for Day-2 observability ingest + summary."""

from __future__ import annotations

from signalos_lib.product.observability import (
    EVENT_TYPES,
    ingest_events,
    record_event,
    summarize_observability,
)


def test_record_and_ingest_roundtrip(tmp_path):
    record_event(tmp_path, {"type": "crash", "message": "NPE in checkout"})
    record_event(tmp_path, {"type": "feedback", "message": "love it"})
    events = ingest_events(tmp_path)
    assert len(events) == 2
    assert {e["type"] for e in events} == {"crash", "feedback"}


def test_record_ignores_unknown_type(tmp_path):
    record_event(tmp_path, {"type": "bogus", "message": "x"})
    assert ingest_events(tmp_path) == []


def test_ingest_missing_file_is_empty(tmp_path):
    assert ingest_events(tmp_path) == []


def test_ingest_skips_malformed(tmp_path):
    path = tmp_path / ".signalos" / "observability" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"type":"error","message":"x"}\nNOT JSON\n{"type":"traffic","count":10}\n',
                    encoding="utf-8")
    events = ingest_events(tmp_path)
    assert [e["type"] for e in events] == ["error", "traffic"]


def test_summary_totals_and_traffic(tmp_path):
    for _ in range(3):
        record_event(tmp_path, {"type": "crash", "message": "NPE in checkout"})
    record_event(tmp_path, {"type": "error", "message": "timeout"})
    record_event(tmp_path, {"type": "traffic", "count": 100})
    record_event(tmp_path, {"type": "feedback", "message": "add dark mode"})

    summary = summarize_observability(ingest_events(tmp_path))
    assert summary["totals"]["crash"] == 3
    assert summary["traffic_total"] == 100
    assert summary["healthy"] is False


def test_summary_top_errors_ranked(tmp_path):
    for _ in range(5):
        record_event(tmp_path, {"type": "crash", "message": "NPE in checkout"})
    record_event(tmp_path, {"type": "error", "message": "rare glitch"})
    summary = summarize_observability(ingest_events(tmp_path))
    assert summary["top_errors"][0] == {"message": "NPE in checkout", "count": 5}


def test_next_wave_seeds_close_the_loop(tmp_path):
    record_event(tmp_path, {"type": "crash", "message": "NPE in checkout", "count": 4})
    record_event(tmp_path, {"type": "feedback", "message": "add dark mode"})
    summary = summarize_observability(ingest_events(tmp_path))
    seeds = " ".join(summary["next_wave_seeds"])
    assert "NPE in checkout" in seeds
    assert "dark mode" in seeds


def test_healthy_when_no_crashes_or_errors(tmp_path):
    record_event(tmp_path, {"type": "traffic", "count": 50})
    record_event(tmp_path, {"type": "feedback", "message": "great"})
    summary = summarize_observability(ingest_events(tmp_path))
    assert summary["healthy"] is True


def test_event_types_constant():
    assert set(EVENT_TYPES) == {"crash", "error", "feedback", "traffic"}
