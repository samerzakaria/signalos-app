"""Technology-neutral test-automation umbrella for SignalOS product repos.

The behavior is technology-neutral: product tools come from profiles and
evidence, not from a hardcoded .NET, Go, ABP, Node, or Python assumption.

This package preserves the public import path
``signalos_lib.commands.test_automation`` and its ``main`` (the CLI dispatches
to it). It is split into:

* ``phases`` -- the ``PhaseSpec`` catalog, constants, and shared helpers.
* ``runners`` -- the per-phase ``_run_*`` functions and the ``_RUNNERS`` map.
* this ``__init__`` -- orchestration, audit/evidence writing, and the CLI.
"""

from __future__ import annotations

__all__ = [
    "EXIT_BAD_ARGS",
    "EXIT_INTERNAL_ERROR",
    "EXIT_OK",
    "EXIT_THRESHOLD_VIOLATION",
    "PHASES",
    "main",
    "run_test_phase",
]

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Re-export constants, the PhaseSpec catalog, and shared helpers so existing
# importers of ``signalos_lib.commands.test_automation.<name>`` keep working.
from .phases import (  # noqa: F401
    DEFAULT_PROFILE,
    EXIT_BAD_ARGS,
    EXIT_INTERNAL_ERROR,
    EXIT_OK,
    EXIT_THRESHOLD_VIOLATION,
    NO_UI_PROFILES,
    PHASE_BY_VERB,
    PHASES,
    SCAN_DIRS,
    SCHEMA_VERSION,
    SKIP_DIRS,
    SOURCE_SUFFIXES,
    STATUS_BLOCKED,
    STATUS_DRY_RUN,
    STATUS_FAIL,
    STATUS_NOT_APPLICABLE,
    STATUS_PASS,
    STATUS_PASS_WITH_NOT_APPLICABLE,
    STATUS_PENDING,
    PhaseSpec,
    _check,
    _constitution_flag_enabled,
    _discover_source_files,
    _display_path,
    _float_or_none,
    _invalid_jsonl_lines,
    _is_skipped_path,
    _load_intent,
    _load_json_file,
    _match_evidence_globs,
    _profile_from_repo,
    _profile_is_no_ui,
    _read_audit_rows,
)

# Re-export the runners and their helpers (some tests/importers may reach for
# private runner names directly).
from .runners import (  # noqa: F401
    _RUNNERS,
    _append_audit_status_check,
    _append_evidence_presence_check,
    _contract_json_candidates,
    _first_existing,
    _production_monitor_result,
    _run_chaos,
    _run_contract,
    _run_e2e,
    _run_evidence,
    _run_governance,
    _run_pipeline,
    _run_production_monitor,
    _run_security,
    _run_unit,
    _run_visual,
    _validation_category_check,
    _visual_artifacts,
)


def run_test_phase(
    repo_root: Path | str | None = None,
    *,
    phase: str,
    profile: str | None = None,
    timeout_sec: int = 300,
    dry_run: bool = False,
    emit_audit: bool = False,
    actor: str | None = None,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Run or evaluate one SignalOS test-automation phase."""

    root = Path(repo_root or Path.cwd()).resolve()
    spec = PHASE_BY_VERB.get(phase)
    if spec is None:
        raise ValueError(f"unknown test phase: {phase}")
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"repo-root not found: {root}")

    started = time.perf_counter()
    payload = _base_payload(root, spec, profile or _profile_from_repo(root), dry_run)
    if dry_run:
        payload["status"] = STATUS_DRY_RUN
        payload["ok"] = True
        payload["checks"].append(_check(
            "phase-wiring",
            STATUS_PASS,
            f"{spec.phase_name} handler is wired; invocation skipped by --dry-run.",
        ))
    else:
        runner = _RUNNERS[spec.runner]
        runner(payload, root, spec, profile or _profile_from_repo(root), timeout_sec)
        _finalize_status(payload)

    payload["duration_ms"] = int((time.perf_counter() - started) * 1000)
    if write_evidence:
        payload["evidence_path"] = _write_phase_evidence(root, spec, payload)
        _write_latest_result(root, payload)
    if emit_audit:
        _append_test_audit(root, spec, payload, actor=actor)
    return payload


def run_all_phases(
    repo_root: Path | str | None = None,
    *,
    profile: str | None = None,
    timeout_sec: int = 300,
    dry_run: bool = False,
    emit_audit: bool = False,
    actor: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root or Path.cwd()).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"repo-root not found: {root}")
    started = time.perf_counter()
    phases = [
        run_test_phase(
            root,
            phase=spec.verb,
            profile=profile,
            timeout_sec=timeout_sec,
            dry_run=dry_run,
            emit_audit=emit_audit,
            actor=actor,
        )
        for spec in PHASES
    ]
    blockers = [
        blocker
        for phase_payload in phases
        for blocker in phase_payload.get("blockers", [])
    ]
    status = _aggregate_status([phase_payload["status"] for phase_payload in phases])
    not_applicable_count = sum(1 for item in phases if item["status"] == STATUS_NOT_APPLICABLE)
    not_applicable_phases = [
        item.get("phase_name") or item.get("phase")
        for item in phases
        if item["status"] == STATUS_NOT_APPLICABLE
    ]
    applicable_count = len(phases) - not_applicable_count
    passed_applicable_count = sum(1 for item in phases if item["status"] == STATUS_PASS)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(root),
        "phase": "all",
        "profile": profile or _profile_from_repo(root),
        "status": status,
        "ok": status in {STATUS_PASS, STATUS_PASS_WITH_NOT_APPLICABLE, STATUS_DRY_RUN},
        "dry_run": dry_run,
        "phases": phases,
        "blockers": blockers,
        "summary": {
            "total": len(phases),
            "applicable": applicable_count,
            "passed": passed_applicable_count,
            "passed_applicable": passed_applicable_count,
            "dry_run": sum(1 for item in phases if item["status"] == STATUS_DRY_RUN),
            "pending": sum(1 for item in phases if item["status"] == STATUS_PENDING),
            "blocked": sum(1 for item in phases if item["status"] == STATUS_BLOCKED),
            "failed": sum(1 for item in phases if item["status"] == STATUS_FAIL),
            "not_applicable": not_applicable_count,
            "not_applicable_phases": not_applicable_phases,
            "coverage_label": (
                f"{passed_applicable_count}/{applicable_count} applicable phases passed"
                + (
                    f"; {not_applicable_count} phase(s) not applicable"
                    if not_applicable_count
                    else ""
                )
            ),
        },
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    out = root / ".signalos" / "quality" / "test-automation" / "all.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload["evidence_path"] = _display_path(out, root)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_latest_result(root, payload)
    return payload


def _base_payload(root: Path, spec: PhaseSpec, profile: str, dry_run: bool) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(root),
        "phase": spec.verb,
        "phase_name": spec.phase_name,
        "carryover_id": spec.carryover_id,
        "scenario_id": spec.scenario_id,
        "audit_action": spec.audit_action,
        "profile": profile,
        "dry_run": dry_run,
        "status": STATUS_PENDING,
        "ok": False,
        "checks": [],
        "blockers": [],
        "recommendations": [],
        "evidence_path": None,
    }


def _finalize_status(payload: dict[str, Any]) -> None:
    statuses = [check["status"] for check in payload["checks"]]
    if STATUS_FAIL in statuses:
        payload["status"] = STATUS_FAIL
    elif STATUS_BLOCKED in statuses:
        payload["status"] = STATUS_BLOCKED
    elif STATUS_PENDING in statuses:
        payload["status"] = STATUS_PENDING
    elif statuses and all(status == STATUS_NOT_APPLICABLE for status in statuses):
        payload["status"] = STATUS_NOT_APPLICABLE
    elif STATUS_PASS in statuses:
        payload["status"] = STATUS_PASS
    else:
        payload["status"] = STATUS_PENDING
    payload["ok"] = payload["status"] in {STATUS_PASS, STATUS_NOT_APPLICABLE}
    payload["blockers"] = [
        check["message"]
        for check in payload["checks"]
        if check["status"] in {STATUS_FAIL, STATUS_BLOCKED, STATUS_PENDING}
    ]


def _aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return STATUS_PENDING
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_BLOCKED in statuses:
        return STATUS_BLOCKED
    if STATUS_PENDING in statuses:
        return STATUS_PENDING
    if all(status == STATUS_DRY_RUN for status in statuses):
        return STATUS_DRY_RUN
    if all(status in {STATUS_PASS, STATUS_NOT_APPLICABLE} for status in statuses):
        if STATUS_NOT_APPLICABLE in statuses:
            return STATUS_PASS_WITH_NOT_APPLICABLE
        return STATUS_PASS
    return STATUS_PENDING


def _write_phase_evidence(root: Path, spec: PhaseSpec, payload: dict[str, Any]) -> str:
    out = root / ".signalos" / "quality" / "test-automation" / f"{spec.verb}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    display = _display_path(out, root)
    payload["evidence_path"] = display
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return display


def _write_latest_result(root: Path, payload: dict[str, Any]) -> None:
    out = root / ".signalos" / "product" / "TEST_AUTOMATION_RESULT.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_test_audit(root: Path, spec: PhaseSpec, payload: dict[str, Any], *, actor: str | None) -> None:
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": _utc_now(),
        "action": spec.audit_action,
        "actor": actor or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "phase": spec.verb,
        "phase_name": spec.phase_name,
        "status": payload["status"],
        "passed": sum(1 for check in payload["checks"] if check["status"] == STATUS_PASS),
        "failed": sum(1 for check in payload["checks"] if check["status"] == STATUS_FAIL),
        "blocked": sum(1 for check in payload["checks"] if check["status"] == STATUS_BLOCKED),
        "pending": sum(1 for check in payload["checks"] if check["status"] == STATUS_PENDING),
        "duration_ms": payload.get("duration_ms", 0),
        "evidence_path": payload.get("evidence_path"),
    }
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _exit_for_payload(payload: dict[str, Any]) -> int:
    status = payload.get("status")
    if status in {
        STATUS_PASS,
        STATUS_PASS_WITH_NOT_APPLICABLE,
        STATUS_DRY_RUN,
        STATUS_NOT_APPLICABLE,
    }:
        return EXIT_OK
    return EXIT_THRESHOLD_VIOLATION


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos test",
        description="Technology-neutral 12-phase test automation umbrella.",
    )
    parser.add_argument(
        "phase",
        choices=["all", *[phase.verb for phase in PHASES]],
        help="Test phase to run or evaluate.",
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--timeout-sec", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--emit-audit", action="store_true")
    parser.add_argument("--actor", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.phase == "all":
            payload = run_all_phases(
                args.repo_root,
                profile=args.profile,
                timeout_sec=args.timeout_sec,
                dry_run=args.dry_run,
                emit_audit=args.emit_audit,
                actor=args.actor,
            )
        else:
            payload = run_test_phase(
                args.repo_root,
                phase=args.phase,
                profile=args.profile,
                timeout_sec=args.timeout_sec,
                dry_run=args.dry_run,
                emit_audit=args.emit_audit,
                actor=args.actor,
            )
    except FileNotFoundError as exc:
        print(f"signalos test: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGS
    except (OSError, ValueError) as exc:
        print(f"signalos test: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGS
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"signalos test: internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(payload)
    return _exit_for_payload(payload)


def _print_human(payload: dict[str, Any]) -> None:
    phase = payload.get("phase")
    status = payload.get("status")
    print(f"signalos test {phase}: {status}")
    if payload.get("evidence_path"):
        print(f"evidence: {payload['evidence_path']}")
    if phase == "all":
        for item in payload.get("phases", []):
            print(f"- {item['phase']}: {item['status']}")
        # Surface waived (not-applicable) phases loudly in human output, not
        # just in the JSON summary. SignalOS enforces, never advises: what was
        # waived must be visible on the CLI.
        summary = payload.get("summary", {})
        coverage_label = summary.get("coverage_label")
        if coverage_label:
            print(coverage_label)
        na_phases = summary.get("not_applicable_phases") or []
        if na_phases:
            print(f"{len(na_phases)} phase(s) not applicable: {', '.join(str(p) for p in na_phases)}")
        return
    for check in payload.get("checks", []):
        print(f"- {check['id']}: {check['status']} {check['message']}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
