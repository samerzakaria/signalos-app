"""Product lesson catalog and accounting.

SignalOS lessons are reusable product-delivery rules selected for a specific
agent packet. They are not framework-specific and are enforced as evidence
accounting before an agent result can be accepted as complete.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CATALOG_SCHEMA_VERSION = "signalos.product_lesson_catalog.v1"
CONTEXT_SCHEMA_VERSION = "signalos.product_lesson_context.v1"

_LESSONS: list[dict[str, Any]] = [
    {
        "id": "lesson.tests-before-ready",
        "lesson_kind": "quality",
        "enforcement": "required",
        "title": "Tests before ready handoff",
        "summary": "New behavior needs executable tests or an explicit blocker before ready closeout.",
        "triggers": ["test", "build", "feature", "workflow", "api", "ui", "dashboard"],
        "required_evidence": "Test file, validation result, or blocker evidence path.",
    },
    {
        "id": "lesson.no-fabricated-proof",
        "lesson_kind": "governance",
        "enforcement": "required",
        "title": "No fabricated proof",
        "summary": "Proof must point at real files, commands, validation results, or human approval.",
        "triggers": ["proof", "closeout", "ship", "release", "validation", "gate"],
        "required_evidence": "Proof artifact, validation result, audit row, or exact blocker.",
    },
    {
        "id": "lesson.accessible-ui-states",
        "lesson_kind": "ux",
        "enforcement": "required",
        "title": "Accessible UI states",
        "summary": "UI work must include empty, loading, error, and success states that users can understand.",
        "triggers": ["ui", "ux", "screen", "dashboard", "form", "component", "angular", "react"],
        "required_evidence": "Component test, screenshot proof, or documented blocker.",
    },
    {
        "id": "lesson.infrastructure-portability",
        "lesson_kind": "architecture",
        "enforcement": "advisory",
        "title": "Infrastructure portability",
        "summary": "Selected databases, caches, queues, or providers must stay injectable and replaceable.",
        "triggers": ["postgresql", "postgres", "redis", "sql", "database", "cache", "provider"],
        "required_evidence": "Adapter boundary, configuration file, or design note.",
    },
    {
        "id": "lesson.scope-honesty",
        "lesson_kind": "scope",
        "enforcement": "required",
        "title": "Scope honesty",
        "summary": "Partial output must remain partial; do not hide limitations behind completed status.",
        "triggers": ["partial", "blocker", "limitation", "handoff", "acceptance", "criteria"],
        "required_evidence": "Known limitation, acceptance reconciliation row, or blocker note.",
    },
]

_DEFAULT_REQUIRED = ("lesson.tests-before-ready", "lesson.no-fabricated-proof")


def lesson_catalog() -> dict[str, Any]:
    """Return the product lesson catalog with a deterministic pack hash."""
    lessons = [dict(item) for item in _LESSONS]
    canonical = json.dumps(lessons, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "pack_version": "app-native-2026-06-29",
        "pack_sha256_lf": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "lessons": lessons,
    }


def build_lesson_context(
    *,
    intent: dict[str, Any],
    blueprint: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    generation_packet: dict[str, Any] | None = None,
    global_lesson_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select lessons for a product agent packet."""
    catalog = lesson_catalog()
    haystack = _lesson_haystack(intent, blueprint, tasks, generation_packet)
    selected_ids: list[str] = []

    for lesson_id in _DEFAULT_REQUIRED:
        _append_unique(selected_ids, lesson_id)

    for lesson in catalog["lessons"]:
        triggers = [str(item).lower() for item in lesson.get("triggers", [])]
        if any(trigger and trigger in haystack for trigger in triggers):
            _append_unique(selected_ids, lesson["id"])

    selected = [
        _context_lesson(lesson)
        for lesson in catalog["lessons"]
        if lesson["id"] in selected_ids
    ]

    candidates = [
        _candidate_to_lesson(candidate)
        for candidate in global_lesson_candidates or []
        if isinstance(candidate, dict)
    ]
    for candidate in candidates:
        if candidate["id"] not in {lesson["id"] for lesson in selected}:
            selected.append(candidate)

    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "catalog_schema_version": catalog["schema_version"],
        "pack_version": catalog["pack_version"],
        "pack_sha256_lf": catalog["pack_sha256_lf"],
        "awareness": {
            "lesson_count": len(catalog["lessons"]),
            "rule": "Selected lessons must be applied, evidenced, or explicitly marked not applicable with a reason.",
        },
        "scope_rules": [
            "Required lessons selected for this packet must be accounted for before completed results can be accepted.",
            "Advisory lessons may guide implementation but still need honest accounting if cited.",
            "Do not cite lessons that are not present in this context.",
        ],
        "selected_lessons": selected,
    }


def validate_lesson_accounting(
    result: dict[str, Any],
    lesson_context: dict[str, Any] | None,
    *,
    run_dir: Path,
    repo_root: Path,
) -> list[str]:
    """Return lesson-accounting violations for an agent result."""
    if not lesson_context:
        return []

    selected = {
        str(lesson.get("id")): lesson
        for lesson in lesson_context.get("selected_lessons", [])
        if lesson.get("id")
    }
    if not selected:
        return []

    applied = _string_set(result.get("applied_lessons", []))
    not_applicable = _not_applicable_by_id(result.get("not_applicable_lessons", []))
    evidence = _evidence_by_id(result.get("lesson_evidence", []))
    selected_ids = set(selected)
    violations: list[str] = []

    for lesson_id in sorted((applied | set(not_applicable) | set(evidence)) - selected_ids):
        violations.append(f"lesson accounting cites unselected lesson: {lesson_id}")

    if result.get("status") != "completed":
        return violations

    for lesson_id, lesson in selected.items():
        if lesson.get("enforcement") != "required":
            continue
        accounted = lesson_id in applied or lesson_id in not_applicable
        if not accounted:
            violations.append(f"required lesson not accounted for: {lesson_id}")
            continue
        if lesson_id in applied and lesson_id not in evidence:
            violations.append(f"applied lesson missing evidence: {lesson_id}")
            continue
        if lesson_id in evidence and not _lesson_evidence_exists(evidence[lesson_id], run_dir, repo_root):
            violations.append(f"lesson evidence path missing: {lesson_id}")
        if lesson_id in not_applicable:
            reason = str(not_applicable[lesson_id].get("reason", "")).strip()
            if len(reason) < 12:
                violations.append(f"lesson not-applicable reason too short: {lesson_id}")

    return violations


def _context_lesson(lesson: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": lesson["id"],
        "lesson_kind": lesson["lesson_kind"],
        "enforcement": lesson["enforcement"],
        "summary": lesson["summary"],
        "required_evidence": lesson["required_evidence"],
    }


def _candidate_to_lesson(candidate: dict[str, Any]) -> dict[str, Any]:
    lesson_id = str(candidate.get("id") or candidate.get("lesson_id") or "").strip()
    if not lesson_id:
        lesson_id = "lesson.candidate." + hashlib.sha256(
            json.dumps(candidate, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
    return {
        "id": lesson_id,
        "lesson_kind": str(candidate.get("lesson_kind") or "operations"),
        "enforcement": str(candidate.get("enforcement") or "advisory"),
        "summary": str(candidate.get("summary") or candidate.get("title") or "Reusable lesson candidate."),
        "required_evidence": str(candidate.get("required_evidence") or "Evidence path or blocker note."),
        "source": str(candidate.get("source") or "right-sizing-debt"),
    }


def _lesson_haystack(
    intent: dict[str, Any],
    blueprint: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    generation_packet: dict[str, Any] | None,
) -> str:
    parts = [
        json.dumps(intent, ensure_ascii=False, sort_keys=True),
        json.dumps(blueprint or {}, ensure_ascii=False, sort_keys=True),
        json.dumps(tasks, ensure_ascii=False, sort_keys=True),
    ]
    if generation_packet:
        parts.append(json.dumps(generation_packet, ensure_ascii=False, sort_keys=True))
    return " ".join(parts).lower()


def _append_unique(target: list[str], value: str) -> None:
    if value and value not in target:
        target.append(value)


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _not_applicable_by_id(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        lesson_id = str(item.get("id") or item.get("lesson_id") or "").strip()
        if lesson_id:
            out[lesson_id] = item
    return out


def _evidence_by_id(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, list):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        lesson_id = str(item.get("id") or item.get("lesson_id") or "").strip()
        if lesson_id:
            out.setdefault(lesson_id, []).append(item)
    return out


def _lesson_evidence_exists(
    entries: list[dict[str, Any]],
    run_dir: Path,
    repo_root: Path,
) -> bool:
    for entry in entries:
        detail = str(entry.get("detail") or entry.get("summary") or "").strip()
        path = str(entry.get("path") or entry.get("evidence_path") or "").strip()
        if detail and not path:
            return True
        if not path:
            continue
        for base in (repo_root, run_dir):
            candidate = (base / path).resolve()
            try:
                candidate.relative_to(base.resolve())
            except ValueError:
                continue
            if candidate.is_file():
                return True
    return False
