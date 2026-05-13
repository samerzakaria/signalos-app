# SignalOS Core v1.1 — Session journal reader/writer.
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
#
# Read-only module. All writes go through
# core/execution/hooks/_lib/journal-append.sh so the redaction filter and
# flock semantics remain uniform across bash and Python callers. The
# helpers below support:
#   - iter_events / iter_index (existing, preserved)
#   - list_sessions
#   - show_session
#   - resume_session
#   - archive_session
#
# None of these functions mutate a journal.jsonl file. archive_session
# moves the enclosing session directory, but does not touch the per-event
# byte stream inside it.


from __future__ import annotations

__all__ = ["Session", "create_session", "resume_session", "archive_session", "get_session"]  # W-2: explicit public API

import json
import shutil
from pathlib import Path
from typing import Iterator


REPO_ROOT_MARKER = ".signalos"

# Canonical event types (lowercase, dotted — mirrors
# core/execution/skills/session-journal/SKILL.md and TRUST_TIER.md).
CANONICAL_EVENT_TYPES = frozenset({
    "session.start",
    "session.end",
    "step.started",
    "step.completed",
    "step.failed",
    "step.paused",
    "step.resumed",
    "step.aborted",
    "hook.fired",
    "gate.checked",
    "amendment.requested",
    "subagent.spawned",
    "subagent.replied",
})


class SessionMissingError(FileNotFoundError):
    """Raised by show/resume/archive when the requested session has no journal."""


class ArchiveRefusedError(RuntimeError):
    """Raised by archive_session when the session lacks a session.end event."""


def repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (or cwd) until .signalos/ appears, or raise."""
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / REPO_ROOT_MARKER).is_dir():
            return cand
    raise RuntimeError(f"signalos: no {REPO_ROOT_MARKER}/ ancestor of {p}")


def sessions_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / REPO_ROOT_MARKER / "sessions"


def _session_journal_path(session_id: str, root: Path | None = None) -> Path:
    return sessions_dir(root) / session_id / "journal.jsonl"


def iter_events(session_id: str, root: Path | None = None) -> Iterator[dict]:
    """Yield each event dict from a session's journal.jsonl, in order."""
    path = _session_journal_path(session_id, root)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.rstrip("\n")
            if not raw:
                continue
            yield json.loads(raw)


def iter_index(root: Path | None = None) -> Iterator[dict]:
    path = sessions_dir(root) / "INDEX.jsonl"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.rstrip("\n")
            if not raw:
                continue
            yield json.loads(raw)


# -- Public read-only API ----------------------------------------------------


def list_sessions(root: Path | None = None) -> list[dict]:
    """Return every INDEX.jsonl row, sorted by updated_at descending.

    Rows missing an `updated_at` field sort last.
    """
    rows = list(iter_index(root))
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return rows


def list_sessions_in(sdir: Path) -> list[dict]:
    """Return INDEX.jsonl rows from an arbitrary sessions directory.

    Used by multi-tenant session listing to scope to a product namespace:
      .signalos/products/<id>/sessions/INDEX.jsonl

    Returns an empty list if the directory or index does not exist.
    """
    index_path = sdir / "INDEX.jsonl"
    if not index_path.exists():
        return []
    rows: list[dict] = []
    with index_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.rstrip("\n")
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except Exception:
                pass
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return rows


def show_session(session_id: str, root: Path | None = None) -> dict:
    """Summarise a single session by replaying its journal.jsonl.

    Returns a dict with:
        session_id         — the requested id
        started_at         — ts of the first session.start (or None)
        ended_at           — ts of the last  session.end   (or None)
        step_count         — count of step.started events
        last_event         — type of the last event, or None
        last_event_ts      — ts   of the last event, or None
        event_counts_by_type — { type: count } for every type seen

    Raises SessionMissingError if journal.jsonl is missing.
    """
    path = _session_journal_path(session_id, root)
    if not path.exists():
        raise SessionMissingError(f"signalos: session not found: {session_id}")

    started_at: str | None = None
    ended_at: str | None = None
    step_count = 0
    last_event: str | None = None
    last_event_ts: str | None = None
    counts: dict[str, int] = {}

    for event in iter_events(session_id, root):
        etype = event.get("type")
        if etype is None:
            continue
        counts[etype] = counts.get(etype, 0) + 1
        last_event = etype
        last_event_ts = event.get("ts")
        if etype == "session.start" and started_at is None:
            started_at = event.get("ts")
        if etype == "session.end":
            ended_at = event.get("ts")
        if etype == "step.started":
            step_count += 1

    return {
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "step_count": step_count,
        "last_event": last_event,
        "last_event_ts": last_event_ts,
        "event_counts_by_type": counts,
    }


def resume_session(session_id: str, root: Path | None = None) -> dict:
    """Validate a session exists and describe what resuming would mean.

    This function performs zero writes. The caller (typically the
    dispatcher in W1.2 headless mode) is responsible for appending the
    next event.

    Returns:
        session_id                — the requested id
        last_step_id              — the step_id of the most recent step.* event, or None
        last_event                — type of the last event, or None
        last_event_ts             — ts of the last event, or None
        next_expected_event_types — small whitelist of valid next events
                                    given the last event (advisory; not enforced)
        ended                     — True if a session.end event was found

    Raises SessionMissingError if the journal is absent.
    """
    summary = show_session(session_id, root)  # raises SessionMissingError if missing

    last_step_id: str | None = None
    # Walk backwards to find the last step_id we saw.
    for event in reversed(list(iter_events(session_id, root))):
        if "step_id" in event and event["step_id"]:
            last_step_id = event["step_id"]
            break

    next_expected = _next_expected_after(summary["last_event"])

    return {
        "session_id": session_id,
        "last_step_id": last_step_id,
        "last_event": summary["last_event"],
        "last_event_ts": summary["last_event_ts"],
        "next_expected_event_types": next_expected,
        "ended": summary["ended_at"] is not None,
    }


def archive_session(
    session_id: str,
    root: Path | None = None,
    force: bool = False,
) -> Path:
    """Move .signalos/sessions/<id>/ under .signalos/sessions/_archive/<id>/.

    Refuses (raises ArchiveRefusedError) unless a session.end event is
    present in the journal, or `force=True`. Raises SessionMissingError
    if the session directory / journal is absent.

    Returns the new archived path.
    """
    src = sessions_dir(root) / session_id
    journal_path = src / "journal.jsonl"
    if not journal_path.exists():
        raise SessionMissingError(f"signalos: session not found: {session_id}")

    if not force:
        has_end = any(e.get("type") == "session.end" for e in iter_events(session_id, root))
        if not has_end:
            raise ArchiveRefusedError(
                f"signalos: session {session_id} has no session.end event; "
                "refusing to archive (use force=True / --force to override)"
            )

    archive_root = sessions_dir(root) / "_archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    dst = archive_root / session_id

    if dst.exists():
        raise RuntimeError(f"signalos: archive destination already exists: {dst}")

    shutil.move(str(src), str(dst))
    return dst


# -- Internals ---------------------------------------------------------------


# Small advisory table describing which next-events are reasonable after a
# given last-event. This is *not* a state machine — it's a hint to callers
# so a resumed session doesn't immediately emit something nonsensical.
_NEXT_EXPECTED: dict[str, tuple[str, ...]] = {
    "session.start":      ("step.started",),
    "step.started":       ("step.completed", "step.failed", "step.paused", "step.aborted",
                           "hook.fired", "gate.checked", "subagent.spawned"),
    "step.completed":     ("step.started", "session.end", "gate.checked"),
    "step.failed":        ("step.started", "session.end", "amendment.requested"),
    "step.paused":        ("step.resumed", "step.aborted"),
    "step.resumed":       ("step.completed", "step.failed", "step.aborted", "step.paused"),
    "step.aborted":       ("session.end", "step.started"),
    "hook.fired":         ("step.completed", "step.failed", "step.started"),
    "gate.checked":       ("step.started", "session.end"),
    "amendment.requested": ("step.started", "session.end"),
    "subagent.spawned":   ("subagent.replied", "step.completed", "step.failed"),
    "subagent.replied":   ("step.completed", "step.failed", "step.started"),
    "session.end":        (),  # terminal
}


def _next_expected_after(last_event: str | None) -> list[str]:
    if last_event is None:
        return ["session.start"]
    return list(_NEXT_EXPECTED.get(last_event, ()))
