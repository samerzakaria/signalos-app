"""Tests for Day-2 observability ingest, summary, and signal windows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signalos_lib.cli import main as cli_main
from signalos_lib.product.observability import (
    EVENT_TYPES,
    LISTENING_WINDOW_STATUSES,
    SIGNAL_OUTCOMES,
    append_observability_journal,
    close_listening_window,
    create_listening_window,
    evaluate_listening_window,
    get_deployment_signal,
    ingest_events,
    list_deployment_signals,
    load_observability_journal,
    load_listening_window,
    open_listening_window,
    record_deployment_signal,
    record_event,
    record_window_reading,
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


def test_listening_window_status_constant():
    assert LISTENING_WINDOW_STATUSES == ("pending", "active", "closed")


def test_signal_outcomes_constant():
    assert SIGNAL_OUTCOMES == ("NoSignal", "MetPositive", "MetNegative")


def test_listening_window_rejects_invalid_interval(tmp_path: Path):
    with pytest.raises(ValueError, match="closes_at must be after opens_at"):
        create_listening_window(
            tmp_path,
            wave="01",
            belief_id="belief-1",
            opens_at="2026-01-02T00:00:00Z",
            closes_at="2026-01-01T00:00:00Z",
            expected_outcome="activation rises",
            metric_name="activation_rate",
            threshold=10,
            direction="up",
        )


def test_listening_window_open_reading_keep_and_evidence(tmp_path: Path):
    _create_window(tmp_path, wave="W07", threshold=10, minimum_cohort=20)

    opened = open_listening_window(tmp_path, "07", now="2026-01-01T00:00:00Z")
    assert opened["status"] == "active"
    assert opened["wave"] == "07"

    record_window_reading(
        tmp_path,
        "07",
        value=16,
        cohort=45,
        source="file-backend",
        ts="2026-01-01T00:30:00Z",
    )
    result = evaluate_listening_window(tmp_path, "07", now="2026-01-01T01:00:00Z")

    assert result["ok"] is True
    assert result["proposed_verdict"] == "KEEP"
    assert result["draft_only"] is True
    assert result["decision_owner"] == "PO"
    assert result["metric"]["threshold_met"] is True
    assert result["metric"]["latest_cohort"] == 45
    assert result["evidence_path"]
    assert Path(result["evidence_path"]).is_file()
    journal_types = [row["event_type"] for row in load_observability_journal(tmp_path)]
    assert "ListeningWindowOpened" in journal_types


def test_listening_window_pending_and_missing_readings_block(tmp_path: Path):
    _create_window(tmp_path)

    result = evaluate_listening_window(tmp_path, "03", now="2026-01-01T00:05:00Z")
    blocker_kinds = {blocker["kind"] for blocker in result["blockers"]}

    assert result["ok"] is False
    assert result["status"] == "FAIL"
    assert {"window-not-open", "no-primary-readings"}.issubset(blocker_kinds)


def test_listening_window_fix_commands_render_wave_id(tmp_path: Path):
    _create_window(tmp_path, wave="W07")

    result = evaluate_listening_window(
        tmp_path,
        "W07",
        now="2026-01-01T00:05:00Z",
        write_evidence=False,
    )

    commands = [blocker["fix_command"] for blocker in result["blockers"]]
    assert all("{wave}" not in command for command in commands)
    assert any("--wave W07" in command for command in commands)


def test_listening_window_cohort_slo_and_stale_reading_block(tmp_path: Path):
    _create_window(tmp_path, minimum_cohort=100)
    open_listening_window(tmp_path, "03", now="2026-01-01T00:00:00Z")
    record_window_reading(
        tmp_path,
        "03",
        value=20,
        cohort=5,
        slo_breach=True,
        ts="2026-01-01T00:01:00Z",
    )

    result = evaluate_listening_window(
        tmp_path,
        "03",
        now="2026-01-01T08:00:00Z",
        stale_after_hours=2,
        write_evidence=False,
    )
    blocker_kinds = {blocker["kind"] for blocker in result["blockers"]}

    assert result["ok"] is False
    assert {"sub-threshold-cohort", "slo-breach", "stale-primary-reading"}.issubset(
        blocker_kinds
    )
    assert result["evidence_path"] is None


def test_close_listening_window_is_idempotent_and_can_draft_kill(tmp_path: Path):
    _create_window(tmp_path, metric_name="error_rate", threshold=2, direction="down")
    open_listening_window(tmp_path, "03", now="2026-01-01T00:00:00Z")
    record_window_reading(
        tmp_path,
        "03",
        value=5,
        cohort=50,
        ts="2026-01-01T01:00:00Z",
    )

    first = close_listening_window(
        tmp_path,
        "03",
        reason="window-expired",
        now="2026-01-02T00:00:00Z",
        write_evidence=False,
    )
    second = close_listening_window(
        tmp_path,
        "03",
        reason="ignored-second-close",
        now="2026-01-02T01:00:00Z",
        write_evidence=False,
    )

    persisted = load_listening_window(tmp_path, "03")
    assert persisted["status"] == "closed"
    assert persisted["close_reason"] == "window-expired"
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["proposed_verdict"] == "KILL"
    assert second["proposed_verdict"] == "KILL"
    closed_events = load_observability_journal(tmp_path, event_type="ListeningWindowClosed")
    assert len(closed_events) == 1
    assert closed_events[0]["payload"]["reason"] == "window-expired"


def test_deployment_signal_records_lists_gets_and_journals(tmp_path: Path):
    signal = record_deployment_signal(
        tmp_path,
        belief_id="B-N1",
        listening_window_id="window-1",
        collected_at="2026-01-02T00:00:00Z",
        reading="activation_rate=16",
        outcome="metpositive",
    )

    assert signal["schema_version"] == "signalos.deployment_signal.v1"
    assert signal["outcome"] == "MetPositive"
    assert get_deployment_signal(tmp_path, signal["id"]) == signal
    assert list_deployment_signals(tmp_path, belief_id="B-N1") == [signal]
    assert list_deployment_signals(tmp_path, listening_window_id="window-1") == [signal]
    journal = load_observability_journal(tmp_path, event_type="DeploymentSignalRecorded")
    assert len(journal) == 1
    assert journal[0]["payload"]["signal_id"] == signal["id"]


def test_deployment_signal_rejects_invalid_outcome(tmp_path: Path):
    with pytest.raises(ValueError, match="outcome must be one of"):
        record_deployment_signal(
            tmp_path,
            belief_id="B-N1",
            reading="no clear signal",
            outcome="maybe",
        )


def test_observability_journal_append_and_filter(tmp_path: Path):
    row = append_observability_journal(
        tmp_path,
        "BeliefStateChanged",
        {"belief_id": "B-N1", "from": "Draft", "to": "Active"},
        ts="2026-01-01T00:00:00Z",
    )

    assert row["schema_version"] == "signalos.observability_journal.v1"
    assert load_observability_journal(tmp_path, event_type="BeliefStateChanged") == [row]
    assert load_observability_journal(tmp_path, event_type="Other") == []


def test_observe_cli_window_lifecycle(tmp_path: Path, capsys):
    rc = cli_main(
        [
            "signalos",
            "observe",
            "window",
            "create",
            "--repo-root",
            str(tmp_path),
            "--wave",
            "09",
            "--belief-id",
            "belief-9",
            "--opens-at",
            "2026-01-01T00:00:00Z",
            "--closes-at",
            "2026-01-02T00:00:00Z",
            "--expected-outcome",
            "trial starts rise",
            "--metric",
            "trial_starts",
            "--threshold",
            "12",
            "--direction",
            "up",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pending"

    assert cli_main(
        [
            "signalos",
            "observe",
            "window",
            "open",
            "--repo-root",
            str(tmp_path),
            "--wave",
            "09",
            "--now",
            "2026-01-01T00:00:00Z",
        ]
    ) == 0
    capsys.readouterr()

    assert cli_main(
        [
            "signalos",
            "observe",
            "window",
            "reading",
            "--repo-root",
            str(tmp_path),
            "--wave",
            "09",
            "--value",
            "13",
            "--cohort",
            "25",
            "--ts",
            "2026-01-01T01:00:00Z",
        ]
    ) == 0
    capsys.readouterr()

    assert cli_main(
        [
            "signalos",
            "observe",
            "window",
            "evaluate",
            "--repo-root",
            str(tmp_path),
            "--wave",
            "09",
            "--now",
            "2026-01-01T02:00:00Z",
            "--json",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["proposed_verdict"] == "KEEP"
    assert result["ok"] is True


def test_observe_cli_signal_and_journal_surfaces(tmp_path: Path, capsys):
    rc = cli_main(
        [
            "signalos",
            "observe",
            "signal",
            "record",
            "--repo-root",
            str(tmp_path),
            "--belief-id",
            "B-N1",
            "--reading",
            "trial starts met threshold",
            "--outcome",
            "MetPositive",
            "--collected-at",
            "2026-01-02T00:00:00Z",
            "--json",
        ]
    )
    assert rc == 0
    signal_payload = json.loads(capsys.readouterr().out)
    signal_id = signal_payload["signal"]["id"]

    rc = cli_main(
        [
            "signalos",
            "observe",
            "signal",
            "get",
            signal_id,
            "--repo-root",
            str(tmp_path),
            "--json",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["signal"]["id"] == signal_id

    rc = cli_main(
        [
            "signalos",
            "observe",
            "journal",
            "append",
            "BeliefStateChanged",
            "--repo-root",
            str(tmp_path),
            "--payload",
            '{"belief_id":"B-N1","to":"Active"}',
            "--json",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["event"]["event_type"] == "BeliefStateChanged"

    rc = cli_main(
        [
            "signalos",
            "observe",
            "journal",
            "list",
            "--repo-root",
            str(tmp_path),
            "--event-type",
            "BeliefStateChanged",
            "--json",
        ]
    )
    assert rc == 0
    journal_payload = json.loads(capsys.readouterr().out)
    assert journal_payload["count"] == 1


def _create_window(
    root: Path,
    *,
    wave: str = "03",
    metric_name: str = "activation_rate",
    threshold: float = 10,
    direction: str = "up",
    minimum_cohort: int = 0,
) -> dict[str, object]:
    return create_listening_window(
        root,
        wave=wave,
        belief_id="belief-1",
        opens_at="2026-01-01T00:00:00Z",
        closes_at="2026-01-02T00:00:00Z",
        expected_outcome="activation rises",
        metric_name=metric_name,
        threshold=threshold,
        direction=direction,
        minimum_cohort=minimum_cohort,
    )
