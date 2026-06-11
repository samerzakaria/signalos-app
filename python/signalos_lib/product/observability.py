# signalos_lib/product/observability.py
# Day-2 observability — ingest contract + summary.
#
# A full observability hub needs the deployed product to stream telemetry to an
# endpoint, which a desktop app does not host. The in-app slice is a local
# ingest contract: the deployed app (or a thin SDK) appends events to
# .signalos/observability/events.jsonl, and this module ingests and summarises
# them into a Day-2 view -- crash/error hot spots, user feedback, traffic -- and
# turns the top signals into seed tasks for the next wave, closing the loop back
# into the agent pipeline.
#
# Event contract (one JSON object per line):
#   {"ts": ISO8601, "type": "crash"|"error"|"feedback"|"traffic",
#    "message": str (optional), "count": int (optional, default 1),
#    "severity": str (optional), "path": str (optional)}

from __future__ import annotations

__all__ = [
    "EVENT_TYPES",
    "record_event",
    "ingest_events",
    "summarize_observability",
]

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENT_TYPES = ("crash", "error", "feedback", "traffic")

_EVENTS_REL = Path(".signalos") / "observability" / "events.jsonl"


def record_event(repo_root, event: dict[str, Any]) -> None:
    """Append one event to the observability log. Best-effort; never raises.

    This is the contract the deployed product / SDK uses; also handy in tests.
    """
    root = Path(repo_root)
    path = root / _EVENTS_REL
    etype = str(event.get("type", "")).lower()
    if etype not in EVENT_TYPES:
        return
    row = {
        "ts": event.get("ts") or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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


def ingest_events(repo_root) -> list[dict[str, Any]]:
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
    recent_feedback = feedback[-top:][::-1]  # most recent first

    # Close the loop: top crashes/errors and feedback become next-wave seeds.
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
