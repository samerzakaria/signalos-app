"""Technology-independent observability primitives for delivered products.

The event ingest helpers keep the original Day-2 summary contract. The
listening-window helpers model the SignalOS.NET ListeningWindow aggregate as
portable app behavior: Pending -> Active -> Closed lifecycle, metric readings,
SLO/cohort/staleness validation, draft Keep/Kill/Iterate verdicts, and durable
evidence files. Storage is file-backed here on purpose; the behavior is not tied
to ABP, .NET, Redis, SQL, Postgres, or any one runtime.
"""

from __future__ import annotations

__all__ = [
    "EVENT_TYPES",
    "LISTENING_WINDOW_STATUSES",
    "SIGNAL_OUTCOMES",
    "create_listening_window",
    "load_listening_window",
    "open_listening_window",
    "record_window_reading",
    "evaluate_listening_window",
    "close_listening_window",
    "record_deployment_signal",
    "get_deployment_signal",
    "list_deployment_signals",
    "append_observability_journal",
    "load_observability_journal",
    "record_event",
    "ingest_events",
    "summarize_observability",
]

import json
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EVENT_TYPES = ("crash", "error", "feedback", "traffic")
LISTENING_WINDOW_STATUSES = ("pending", "active", "closed")
SIGNAL_OUTCOMES = ("NoSignal", "MetPositive", "MetNegative")

_EVENTS_REL = Path(".signalos") / "observability" / "events.jsonl"
_JOURNAL_REL = Path(".signalos") / "observability" / "journal.jsonl"
_DEPLOYMENT_SIGNALS_REL = Path(".signalos") / "observability" / "deployment-signals.jsonl"
_WINDOWS_REL = Path(".signalos") / "observability" / "listening-windows"
_EVIDENCE_REL = Path(".signalos") / "evidence" / "observability"
_WINDOW_SCHEMA = "signalos.listening_window.v1"
_EVAL_SCHEMA = "signalos.listening_window_evaluation.v1"
_SIGNAL_SCHEMA = "signalos.deployment_signal.v1"
_JOURNAL_SCHEMA = "signalos.observability_journal.v1"


def create_listening_window(
    repo_root: Path | str,
    *,
    wave: str | int,
    belief_id: str,
    opens_at: str | datetime,
    closes_at: str | datetime,
    expected_outcome: str,
    metric_name: str,
    threshold: float | int,
    direction: str,
    minimum_cohort: int = 0,
    force: bool = False,
    threshold_signed: bool = False,
) -> dict[str, Any]:
    """Create a Pending listening window and persist it as JSON.

    3.2 (C-bridge): ``threshold_signed`` records whether this metric/threshold was
    a founder-signed success criterion (e.g. the Expectation Map / Belief gate),
    not just typed into a CLI flag. Defaults to False (fail-closed) -- a window
    resolves KEEP only when its threshold was actually signed; see
    ``evaluate_listening_window``'s ``threshold-unsigned`` blocker.
    """
    root = Path(repo_root)
    normalized_wave = _normalize_wave(wave)
    opened = _parse_time(opens_at, "opens_at")
    closed = _parse_time(closes_at, "closes_at")
    if closed <= opened:
        raise ValueError("closes_at must be after opens_at")
    metric = str(metric_name).strip()
    if not metric:
        raise ValueError("metric_name is required")
    normalized_direction = str(direction).strip().lower()
    if normalized_direction not in {"up", "down"}:
        raise ValueError("direction must be 'up' or 'down'")
    threshold_value = _as_float(threshold, "threshold")
    cohort_minimum = _as_non_negative_int(minimum_cohort, "minimum_cohort")

    path = _window_path(root, normalized_wave)
    if path.exists() and not force:
        raise FileExistsError(f"listening window already exists for wave {normalized_wave}")

    now = _now()
    window = {
        "schema_version": _WINDOW_SCHEMA,
        "wave": normalized_wave,
        "belief_id": str(belief_id).strip(),
        "opens_at": _iso(opened),
        "closes_at": _iso(closed),
        "expected_outcome": str(expected_outcome).strip(),
        "metric": {
            "name": metric,
            "threshold": threshold_value,
            "direction": normalized_direction,
            "minimum_cohort": cohort_minimum,
            "threshold_signed": bool(threshold_signed),
        },
        "status": "pending",
        "readings": [],
        "created_at": _iso(now),
        "opened_at": None,
        "closed_at": None,
        "close_reason": None,
    }
    _write_window(root, normalized_wave, window)
    return window


def load_listening_window(repo_root: Path | str, wave: str | int) -> dict[str, Any]:
    """Load and minimally validate a persisted listening window."""
    root = Path(repo_root)
    normalized_wave = _normalize_wave(wave)
    path = _window_path(root, normalized_wave)
    if not path.is_file():
        raise FileNotFoundError(f"listening window not found for wave {normalized_wave}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("listening window file must contain a JSON object")
    if data.get("schema_version") != _WINDOW_SCHEMA:
        raise ValueError("unsupported listening window schema")
    if str(data.get("status", "")).lower() not in LISTENING_WINDOW_STATUSES:
        raise ValueError("invalid listening window status")
    opens = _parse_time(data.get("opens_at"), "opens_at")
    closes = _parse_time(data.get("closes_at"), "closes_at")
    if closes <= opens:
        raise ValueError("closes_at must be after opens_at")
    metric = data.get("metric")
    if not isinstance(metric, dict) or not str(metric.get("name", "")).strip():
        raise ValueError("listening window metric is missing")
    if str(metric.get("direction", "")).lower() not in {"up", "down"}:
        raise ValueError("listening window metric direction is invalid")
    _as_float(metric.get("threshold"), "metric.threshold")
    return data


def open_listening_window(
    repo_root: Path | str,
    wave: str | int,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Transition a Pending window to Active once its open time has arrived."""
    root = Path(repo_root)
    normalized_wave = _normalize_wave(wave)
    window = load_listening_window(root, normalized_wave)
    status = str(window.get("status", "")).lower()
    if status != "pending":
        raise ValueError(f"cannot open listening window from {status}")
    effective_now = _parse_time(now, "now") if now is not None else _now()
    opens_at = _parse_time(window.get("opens_at"), "opens_at")
    if effective_now < opens_at:
        raise ValueError("too early to open listening window")
    window["status"] = "active"
    window["opened_at"] = _iso(effective_now)
    _write_window(root, normalized_wave, window)
    append_observability_journal(
        root,
        "ListeningWindowOpened",
        {
            "wave": normalized_wave,
            "belief_id": window.get("belief_id"),
            "opened_at": window.get("opened_at"),
        },
        ts=effective_now,
    )
    return window


def record_window_reading(
    repo_root: Path | str,
    wave: str | int,
    *,
    value: float | int,
    metric_name: str | None = None,
    cohort: int = 0,
    slo_breach: bool = False,
    source: str = "manual",
    message: str = "",
    ts: str | datetime | None = None,
) -> dict[str, Any]:
    """Append one metric reading to an Active listening window."""
    root = Path(repo_root)
    normalized_wave = _normalize_wave(wave)
    window = load_listening_window(root, normalized_wave)
    status = str(window.get("status", "")).lower()
    if status != "active":
        raise ValueError(f"cannot record readings while listening window is {status}")
    timestamp = _parse_time(ts, "ts") if ts is not None else _now()
    metric = window.get("metric", {})
    name = str(metric_name or metric.get("name") or "").strip()
    if not name:
        raise ValueError("metric_name is required")
    reading = {
        "ts": _iso(timestamp),
        "metric": name,
        "value": _as_float(value, "value"),
        "cohort": _as_non_negative_int(cohort, "cohort"),
        "slo_breach": bool(slo_breach),
        "source": str(source or "manual").strip() or "manual",
        "message": str(message or "").strip(),
    }
    readings = window.setdefault("readings", [])
    if not isinstance(readings, list):
        raise ValueError("listening window readings must be a list")
    readings.append(reading)
    _write_window(root, normalized_wave, window)
    return window


def evaluate_listening_window(
    repo_root: Path | str,
    wave: str | int,
    *,
    now: str | datetime | None = None,
    stale_after_hours: float = 4.0,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Evaluate a listening window and draft a Keep/Kill/Iterate verdict."""
    root = Path(repo_root)
    normalized_wave = _normalize_wave(wave)
    command_wave = _display_wave(wave)
    window = load_listening_window(root, normalized_wave)
    effective_now = _parse_time(now, "now") if now is not None else _now()
    opens_at = _parse_time(window.get("opens_at"), "opens_at")
    closes_at = _parse_time(window.get("closes_at"), "closes_at")
    status = str(window.get("status", "")).lower()
    metric = window["metric"]
    metric_name = str(metric.get("name"))
    direction = str(metric.get("direction")).lower()
    threshold = _as_float(metric.get("threshold"), "metric.threshold")
    minimum_cohort = _as_non_negative_int(
        metric.get("minimum_cohort", 0), "metric.minimum_cohort"
    )
    all_readings = list(window.get("readings") or [])
    primary_readings = [r for r in all_readings if str(r.get("metric", "")) == metric_name]

    blockers: list[dict[str, Any]] = []
    if status == "pending":
        kind = "window-not-open" if effective_now >= opens_at else "window-not-due"
        blockers.append(
            _blocker(
                kind,
                f"listening window is pending; opens_at={window.get('opens_at')}",
                f"signalos observe window open --wave {command_wave}",
            )
        )
    if status == "active" and effective_now > closes_at:
        blockers.append(
            _blocker(
                "window-expired",
                "listening window has passed closes_at but is not closed",
                f"signalos observe window close --wave {command_wave} --reason window-expired",
            )
        )
    if not primary_readings:
        blockers.append(
            _blocker(
                "no-primary-readings",
                f"no readings recorded for primary metric {metric_name}",
                f"signalos observe window reading --wave {command_wave} --value <number>",
            )
        )

    latest = _latest_reading(primary_readings)
    if latest is not None:
        latest_ts = _parse_time(latest.get("ts"), "reading.ts")
        latest_cohort = _as_non_negative_int(latest.get("cohort", 0), "reading.cohort")
        if latest_cohort < minimum_cohort:
            blockers.append(
                _blocker(
                    "sub-threshold-cohort",
                    f"latest cohort {latest_cohort} is below minimum_cohort {minimum_cohort}",
                    "continue observation until the configured cohort minimum is reached",
                )
            )
        stale_after = timedelta(hours=max(float(stale_after_hours), 0.0))
        if (
            status == "active"
            and stale_after.total_seconds() > 0
            and effective_now - latest_ts > stale_after
        ):
            blockers.append(
                _blocker(
                    "stale-primary-reading",
                    f"latest primary reading is older than {stale_after_hours:g} hour(s)",
                    "refresh the primary metric reading before evaluating the window",
                )
            )
    if any(bool(r.get("slo_breach")) for r in primary_readings):
        blockers.append(
            _blocker(
                "slo-breach",
                "one or more primary metric readings reported an SLO breach",
                "record SLO evidence and review operational health before final verdict",
            )
        )
    # 3.2 (C-bridge, dev-review): telemetry may only resolve a hypothesis whose
    # success metrics were SIGNED earlier. An unsigned threshold blocks KEEP/KILL
    # even if the raw numbers look good -- never auto-resolve against a target
    # nobody actually committed to.
    if not bool(metric.get("threshold_signed", False)):
        blockers.append(
            _blocker(
                "threshold-unsigned",
                f"metric {metric_name}'s threshold was never signed as a founder "
                "success criterion; a verdict cannot be drawn from an unsigned target",
                "sign the success threshold at the Expectation Map / Belief gate, "
                "then recreate the window with threshold_signed=true",
            )
        )

    values = [_as_float(r.get("value"), "reading.value") for r in primary_readings]
    if direction == "up":
        best_value = max(values) if values else None
        threshold_met = best_value is not None and best_value >= threshold
    else:
        best_value = min(values) if values else None
        threshold_met = best_value is not None and best_value <= threshold

    window_finished = status == "closed" or effective_now >= closes_at
    if blockers:
        proposed_verdict = "ITERATE"
    elif threshold_met:
        proposed_verdict = "KEEP"
    elif window_finished:
        proposed_verdict = "KILL"
    else:
        proposed_verdict = "ITERATE"

    latest_value = None if latest is None else _as_float(latest.get("value"), "reading.value")
    threshold_delta = None
    if latest_value is not None:
        threshold_delta = latest_value - threshold if direction == "up" else threshold - latest_value

    payload: dict[str, Any] = {
        "schema_version": _EVAL_SCHEMA,
        "ok": not blockers,
        "status": "PASS" if not blockers else "FAIL",
        "wave": normalized_wave,
        "belief_id": window.get("belief_id"),
        "window_status": status,
        "opens_at": window.get("opens_at"),
        "closes_at": window.get("closes_at"),
        "expected_outcome": window.get("expected_outcome"),
        "metric": {
            "name": metric_name,
            "threshold": threshold,
            "direction": direction,
            "minimum_cohort": minimum_cohort,
            "latest_value": latest_value,
            "latest_cohort": (
                None
                if latest is None
                else _as_non_negative_int(latest.get("cohort", 0), "reading.cohort")
            ),
            "best_value": best_value,
            "threshold_met": threshold_met,
            "threshold_delta": threshold_delta,
            "reading_count": len(primary_readings),
        },
        "proposed_verdict": proposed_verdict,
        "draft_only": True,
        "decision_owner": "PO",
        "trust_tier": "T1",
        "observability_role": "draft-only",
        "blockers": blockers,
        "slo_breaches": sum(1 for r in primary_readings if bool(r.get("slo_breach"))),
        "readings": primary_readings,
        "generated_at": _iso(effective_now),
    }
    if write_evidence:
        payload["evidence_path"] = _write_evaluation(root, normalized_wave, payload)
    else:
        payload["evidence_path"] = None
    return payload


def close_listening_window(
    repo_root: Path | str,
    wave: str | int,
    *,
    reason: str = "window-closed",
    now: str | datetime | None = None,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Close a listening window, idempotently, and return its evaluation."""
    root = Path(repo_root)
    normalized_wave = _normalize_wave(wave)
    window = load_listening_window(root, normalized_wave)
    effective_now = _parse_time(now, "now") if now is not None else _now()
    if str(window.get("status", "")).lower() != "closed":
        window["status"] = "closed"
        window["closed_at"] = _iso(effective_now)
        window["close_reason"] = str(reason or "window-closed").strip() or "window-closed"
        _write_window(root, normalized_wave, window)
        append_observability_journal(
            root,
            "ListeningWindowClosed",
            {
                "wave": normalized_wave,
                "belief_id": window.get("belief_id"),
                "closed_at": window.get("closed_at"),
                "reason": window.get("close_reason"),
            },
            ts=effective_now,
        )
    return evaluate_listening_window(
        root,
        normalized_wave,
        now=effective_now,
        write_evidence=write_evidence,
    )


def record_deployment_signal(
    repo_root: Path | str,
    *,
    belief_id: str,
    reading: str,
    outcome: str,
    collected_at: str | datetime | None = None,
    listening_window_id: str | None = None,
) -> dict[str, Any]:
    """Record one DeploymentSignal-style observation for a Belief."""
    root = Path(repo_root)
    normalized_belief = str(belief_id or "").strip()
    if not normalized_belief:
        raise ValueError("belief_id is required")
    normalized_reading = str(reading or "").strip()
    if not normalized_reading:
        raise ValueError("reading is required")
    normalized_outcome = _normalize_signal_outcome(outcome)
    timestamp = _parse_time(collected_at, "collected_at") if collected_at is not None else _now()
    signal = {
        "schema_version": _SIGNAL_SCHEMA,
        "id": str(uuid.uuid4()),
        "belief_id": normalized_belief,
        "listening_window_id": (
            str(listening_window_id).strip() if listening_window_id else None
        ),
        "collected_at": _iso(timestamp),
        "reading": normalized_reading,
        "outcome": normalized_outcome,
        "created_at": _iso(_now()),
    }
    _append_jsonl(root / _DEPLOYMENT_SIGNALS_REL, signal)
    append_observability_journal(
        root,
        "DeploymentSignalRecorded",
        {
            "signal_id": signal["id"],
            "belief_id": signal["belief_id"],
            "listening_window_id": signal["listening_window_id"],
            "outcome": signal["outcome"],
            "collected_at": signal["collected_at"],
        },
        ts=timestamp,
    )
    return signal


def get_deployment_signal(repo_root: Path | str, signal_id: str) -> dict[str, Any] | None:
    """Return one DeploymentSignal record by id."""
    wanted = str(signal_id or "").strip()
    if not wanted:
        return None
    for signal in list_deployment_signals(repo_root):
        if str(signal.get("id", "")).strip() == wanted:
            return signal
    return None


def list_deployment_signals(
    repo_root: Path | str,
    *,
    belief_id: str | None = None,
    listening_window_id: str | None = None,
) -> list[dict[str, Any]]:
    """List DeploymentSignal records, optionally filtered by Belief/window."""
    rows = _load_jsonl(Path(repo_root) / _DEPLOYMENT_SIGNALS_REL)
    signals: list[dict[str, Any]] = []
    for row in rows:
        if row.get("schema_version") != _SIGNAL_SCHEMA:
            continue
        if belief_id and str(row.get("belief_id", "")) != str(belief_id):
            continue
        if listening_window_id and str(row.get("listening_window_id", "")) != str(listening_window_id):
            continue
        signals.append(row)
    return signals


def append_observability_journal(
    repo_root: Path | str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    ts: str | datetime | None = None,
) -> dict[str, Any]:
    """Append one structured event to the observability journal."""
    root = Path(repo_root)
    normalized_type = str(event_type or "").strip()
    if not normalized_type:
        raise ValueError("event_type is required")
    timestamp = _parse_time(ts, "ts") if ts is not None else _now()
    row = {
        "schema_version": _JOURNAL_SCHEMA,
        "id": str(uuid.uuid4()),
        "ts": _iso(timestamp),
        "event_type": normalized_type,
        "payload": payload if isinstance(payload, dict) else {},
    }
    _append_jsonl(root / _JOURNAL_REL, row)
    return row


def load_observability_journal(
    repo_root: Path | str,
    *,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Load structured observability journal rows, optionally filtered."""
    rows = _load_jsonl(Path(repo_root) / _JOURNAL_REL)
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("schema_version") != _JOURNAL_SCHEMA:
            continue
        if event_type and str(row.get("event_type", "")) != str(event_type):
            continue
        out.append(row)
    return out


def record_event(repo_root: Path | str, event: dict[str, Any]) -> None:
    """Append one event to the observability log. Best-effort; never raises."""
    root = Path(repo_root)
    path = root / _EVENTS_REL
    etype = str(event.get("type", "")).lower()
    if etype not in EVENT_TYPES:
        return
    row = {
        "ts": event.get("ts") or _iso(_now()),
        "type": etype,
        "message": str(event.get("message", "")).strip(),
        "count": int(event["count"]) if str(event.get("count", "")).isdigit() else 1,
        "severity": str(event.get("severity", "")).strip(),
        "path": str(event.get("path", "")).strip(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def ingest_events(repo_root: Path | str) -> list[dict[str, Any]]:
    """Read and normalise all observability events. Skips malformed lines."""
    path = Path(repo_root) / _EVENTS_REL
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            etype = str(obj.get("type", "")).lower()
            if etype not in EVENT_TYPES:
                continue
            count = obj.get("count", 1)
            obj["type"] = etype
            obj["count"] = count if isinstance(count, int) and count > 0 else 1
            obj["message"] = str(obj.get("message", "")).strip()
            events.append(obj)
    except OSError:
        return []
    return events


def summarize_observability(events: list[dict[str, Any]], top: int = 5) -> dict[str, Any]:
    """Summarise events into a Day-2 view with next-wave seed tasks."""
    totals = {t: 0 for t in EVENT_TYPES}
    error_counter: Counter[str] = Counter()
    feedback: list[dict[str, str]] = []
    traffic_total = 0

    for e in events:
        etype = e["type"]
        n = e.get("count", 1)
        totals[etype] += n
        if etype in ("crash", "error") and e.get("message"):
            error_counter[e["message"]] += n
        elif etype == "feedback" and e.get("message"):
            feedback.append({"ts": e.get("ts", ""), "message": e["message"]})
        elif etype == "traffic":
            traffic_total += n

    top_errors = [{"message": m, "count": c} for m, c in error_counter.most_common(top)]
    recent_feedback = feedback[-top:][::-1]

    seeds: list[str] = []
    for err in top_errors:
        seeds.append(f"Investigate and fix: \"{err['message']}\" ({err['count']} occurrence(s))")
    for fb in recent_feedback[:3]:
        seeds.append(f"Review user feedback: \"{fb['message']}\"")

    return {
        "totals": totals,
        "top_errors": top_errors,
        "recent_feedback": recent_feedback,
        "traffic_total": traffic_total,
        "next_wave_seeds": seeds,
        "healthy": totals["crash"] == 0 and totals["error"] == 0,
        "event_count": len(events),
    }


def _normalize_wave(wave: str | int) -> str:
    text = str(wave).strip()
    if not text:
        raise ValueError("wave is required")
    if text.upper().startswith("W"):
        text = text[1:]
    return text.zfill(2) if text.isdigit() else text


def _display_wave(wave: str | int) -> str:
    normalized = _normalize_wave(wave)
    return normalized if normalized.upper().startswith("W") else f"W{normalized}"


def _window_path(root: Path, wave: str | int) -> Path:
    return root / _WINDOWS_REL / f"{_normalize_wave(wave)}.json"


def _write_window(root: Path, wave: str | int, window: dict[str, Any]) -> None:
    path = _window_path(root, wave)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(window, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_evaluation(root: Path, wave: str | int, payload: dict[str, Any]) -> str:
    path = root / _EVIDENCE_REL / f"wave-{_normalize_wave(wave)}-listening-window.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def _parse_time(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO8601 timestamp") from exc
    else:
        raise ValueError(f"{field} must be an ISO8601 timestamp")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc


def _as_non_negative_int(value: Any, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


def _latest_reading(readings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not readings:
        return None
    return sorted(readings, key=lambda item: _parse_time(item.get("ts"), "reading.ts"))[-1]


def _blocker(kind: str, message: str, fix_command: str) -> dict[str, str]:
    return {"kind": kind, "message": message, "fix_command": fix_command}


def _normalize_signal_outcome(outcome: str) -> str:
    raw = str(outcome or "").strip()
    for allowed in SIGNAL_OUTCOMES:
        if raw.lower() == allowed.lower():
            return allowed
    raise ValueError("outcome must be one of NoSignal, MetPositive, or MetNegative")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    except OSError:
        return []
    return rows
