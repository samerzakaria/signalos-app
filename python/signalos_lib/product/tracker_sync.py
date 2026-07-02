"""Headless bidirectional tracker sync (Wave 1.7).

Foundry's plan is the source of truth; a connected Jira-class tracker is mirrored
invisibly so non-Foundry collaborators can follow along. The founder never opens
the tracker -- Foundry operates it. This module owns the sync *logic* behind a
`TrackerAdapter` protocol; the real Jira adapter implements the protocol, and an
in-memory adapter exercises the logic in tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TrackerAdapter(Protocol):
    def upsert(self, key: str, fields: dict[str, Any]) -> str:
        """Create or update the issue for *key*; return its external id."""
        ...

    def fetch(self, external_id: str) -> dict[str, Any]:
        """Return the external issue's current fields (empty if missing)."""
        ...


@dataclass
class InMemoryTracker:
    """A deterministic stand-in for a real tracker, used in tests and dry-runs."""
    issues: dict[str, dict[str, Any]] = field(default_factory=dict)
    _seq: int = 0

    def upsert(self, key: str, fields: dict[str, Any]) -> str:
        existing = self.issues.get(key)
        if existing:
            external_id = existing["id"]
            existing.update(fields)
        else:
            self._seq += 1
            external_id = f"EXT-{self._seq}"
            self.issues[key] = {"id": external_id, **fields}
        return external_id

    def fetch(self, external_id: str) -> dict[str, Any]:
        for issue in self.issues.values():
            if issue.get("id") == external_id:
                return dict(issue)
        return {}

    def set_status(self, external_id: str, status: str) -> None:
        for issue in self.issues.values():
            if issue.get("id") == external_id:
                issue["status"] = status


def push_plan(doc: Any, adapter: TrackerAdapter, mapping: dict[str, str] | None = None) -> dict[str, str]:
    """Mirror the plan's tasks into the tracker (plan -> tracker). Returns an
    updated task_id -> external_id mapping. Idempotent: re-pushing updates."""
    mapping = dict(mapping or {})
    for task in doc.tasks:
        external_id = adapter.upsert(task.id, {
            "title": task.title,
            "status": task.status,
            "epic": getattr(task, "epic", ""),
        })
        mapping[task.id] = external_id
    return mapping


def pull_statuses(adapter: TrackerAdapter, mapping: dict[str, str]) -> dict[str, str]:
    """Pull external statuses back (tracker -> plan), keyed by task id. Foundry
    decides what to do with drift; the plan remains the source of truth."""
    out: dict[str, str] = {}
    for task_id, external_id in mapping.items():
        out[task_id] = str(adapter.fetch(external_id).get("status", ""))
    return out
