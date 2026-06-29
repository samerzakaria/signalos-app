"""Per-phase runners for SignalOS test automation.

Each ``_run_*`` function appends checks to the phase payload. SignalOS
enforces, never advises: soft phases require an explicit verdict, and missing
evidence on a required phase blocks (exit 8) rather than silently passing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from signalos_lib.product.security_gate import run_security_gate, write_security_result
from signalos_lib.product.validation import load_validation_result

from .phases import (
    STATUS_BLOCKED,
    STATUS_FAIL,
    STATUS_NOT_APPLICABLE,
    STATUS_PASS,
    STATUS_PENDING,
    PhaseSpec,
    _check,
    _constitution_flag_enabled,
    _discover_source_files,
    _display_path,
    _float_or_none,
    _is_skipped_path,
    _load_intent,
    _load_json_file,
    _match_evidence_globs,
    _profile_is_no_ui,
    _read_audit_rows,
)


def _run_unit(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    validation_check = _validation_category_check(root, spec)
    if validation_check is not None and validation_check["status"] == STATUS_PASS:
        payload["checks"].append(validation_check)
        return

    from signalos_lib.commands.verify_product import verify_product

    result = verify_product(
        repo_root=root,
        wave=f"test-{spec.verb}",
        profile_id=profile,
        timeout_sec=timeout_sec,
        include_build=False,
        include_test=True,
        include_lint=False,
        include_qa=False,
        include_e2e=False,
    )
    evidence = result.get("evidence_path")
    test_checks = [
        check
        for check in result.get("checks", [])
        if check.get("name") in {"test", "tdd-runner"}
    ]
    if not test_checks:
        payload["checks"].append(_check(
            "unit-profile-test",
            STATUS_PENDING,
            "profile did not produce a unit/component test check.",
            evidence=evidence,
        ))
        return
    failed = [check for check in test_checks if check.get("status") == "FAIL"]
    passed = [check for check in test_checks if check.get("status") == "PASS"]
    if failed:
        payload["checks"].append(_check(
            "unit-profile-test",
            STATUS_FAIL,
            "; ".join(str(check.get("reason", "test failed")) for check in failed),
            evidence=evidence,
            details={"verify_product": result.get("summary", {})},
        ))
        return
    if passed:
        payload["checks"].append(_check(
            "unit-profile-test",
            STATUS_PASS,
            "profile test command passed.",
            evidence=evidence,
            details={"verify_product": result.get("summary", {})},
        ))
        return
    payload["checks"].append(_check(
        "unit-profile-test",
        STATUS_PENDING,
        "unit/component runner is not declared or did not execute.",
        evidence=evidence,
        details={"verify_product": result.get("summary", {})},
    ))


def _run_e2e(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    if _profile_is_no_ui(root, profile):
        payload["checks"].append(_check(
            "ui-applicability",
            STATUS_NOT_APPLICABLE,
            "selected profile is API/headless; browser E2E is not required for this product.",
        ))
        return

    validation_check = _validation_category_check(root, spec)
    if validation_check is not None and validation_check["status"] == STATUS_PASS:
        payload["checks"].append(validation_check)
        return

    from signalos_lib.commands.verify_product import verify_product

    result = verify_product(
        repo_root=root,
        wave=f"test-{spec.verb}",
        profile_id=profile,
        timeout_sec=timeout_sec,
        include_build=False,
        include_test=False,
        include_lint=False,
        include_qa=False,
        include_e2e=True,
    )
    e2e = next((check for check in result.get("checks", []) if check.get("name") == "e2e"), None)
    if e2e is None:
        payload["checks"].append(_check(
            "e2e-runner",
            STATUS_PENDING,
            "e2e runner did not produce evidence.",
            evidence=result.get("evidence_path"),
        ))
        return
    status = e2e.get("status")
    if status == "PASS":
        mapped = STATUS_PASS
    elif status == "FAIL":
        mapped = STATUS_FAIL
    else:
        mapped = STATUS_PENDING
    payload["checks"].append(_check(
        "e2e-runner",
        mapped,
        str(e2e.get("reason") or "e2e check completed"),
        evidence=e2e.get("evidence_path") or result.get("evidence_path"),
        details={"verify_product": result.get("summary", {})},
    ))
    if mapped == STATUS_PENDING:
        _append_evidence_presence_check(payload, root, spec)


def _run_security(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    existing = _load_json_file(root / ".signalos" / "product" / "SECURITY_RESULT.json")
    if isinstance(existing, dict) and existing.get("status") in {"passed", "pass"}:
        payload["checks"].append(_check(
            "security-result",
            STATUS_PASS,
            "existing product security result passed.",
            evidence=".signalos/product/SECURITY_RESULT.json",
        ))
        return

    generated_files = _discover_source_files(root, limit=500)
    result = run_security_gate(
        repo_root=root,
        intent=_load_intent(root),
        generated_files=generated_files,
        profile=profile,
    )
    evidence_path = write_security_result(result, root / ".signalos")
    status = str(result.get("status", "warning"))
    if status in {"passed", "pass"}:
        mapped = STATUS_PASS
    elif status == "failed":
        mapped = STATUS_FAIL
    else:
        mapped = STATUS_BLOCKED
    issues = result.get("injection_scan", {}).get("issues_found", [])
    message = "security gate passed"
    if issues:
        message = f"security gate found {len(issues)} injection issue(s)"
    elif mapped != STATUS_PASS:
        message = "security gate did not produce a passing result"
    payload["checks"].append(_check(
        "security-gate",
        mapped,
        message,
        evidence=_display_path(evidence_path, root),
        details={
            "files_scanned": result.get("injection_scan", {}).get("files_scanned", 0),
            "recommendations": result.get("recommendations", []),
        },
    ))
    payload["recommendations"].extend(result.get("recommendations", []))


def _run_contract(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    _append_audit_status_check(payload, root, spec)
    if any(check["status"] in {STATUS_PASS, STATUS_FAIL, STATUS_BLOCKED} for check in payload["checks"]):
        return

    for path in _contract_json_candidates(root):
        data = _load_json_file(path)
        if not isinstance(data, dict):
            payload["checks"].append(_check(
                "contract-producer",
                STATUS_FAIL,
                "contract JSON evidence is not a JSON object.",
                evidence=_display_path(path, root),
            ))
            return

        verdict = _contract_compatibility_verdict(data)
        contract_type = (
            "openapi"
            if ("openapi" in data or "swagger" in data)
            else "consumer-provider"
            if ("consumer" in data or "provider" in data or "interactions" in data)
            else None
        )
        if contract_type is None:
            # Not a recognizable contract document; keep scanning candidates.
            continue

        if verdict is None:
            # SignalOS enforces, never advises: the mere presence of an
            # OpenAPI/contract document is NOT a pass. A compatibility /
            # breaking-change verdict is required.
            payload["checks"].append(_check(
                "contract-producer",
                STATUS_BLOCKED,
                "contract evidence is present but carries no compatibility verdict "
                "(expected 'compatible' or 'breaking_changes').",
                evidence=_display_path(path, root),
                details={"contract_type": contract_type},
            ))
            return

        if verdict["breaking"]:
            payload["checks"].append(_check(
                "contract-producer",
                STATUS_FAIL,
                "contract evidence reports breaking changes / incompatibility.",
                evidence=_display_path(path, root),
                details={
                    "contract_type": contract_type,
                    "breaking_changes": verdict["breaking_changes"],
                },
            ))
            return

        payload["checks"].append(_check(
            "contract-producer",
            STATUS_PASS,
            "contract evidence reports a compatible (non-breaking) verdict.",
            evidence=_display_path(path, root),
            details={
                "contract_type": contract_type,
                "breaking_changes": verdict["breaking_changes"],
            },
        ))
        return

    _append_evidence_presence_check(payload, root, spec)


def _contract_compatibility_verdict(data: dict[str, Any]) -> dict[str, Any] | None:
    """Extract an explicit compatibility / breaking-change verdict.

    Returns ``None`` when no verdict is present (presence-only evidence),
    otherwise a dict with ``breaking`` (bool) and ``breaking_changes`` detail.
    """

    if "breaking_changes" in data:
        changes = data.get("breaking_changes")
        if isinstance(changes, bool):
            return {"breaking": changes, "breaking_changes": changes}
        if isinstance(changes, (list, tuple)):
            return {"breaking": len(changes) > 0, "breaking_changes": list(changes)}
        if isinstance(changes, int):
            return {"breaking": changes > 0, "breaking_changes": changes}

    for key in ("compatible", "is_compatible", "backward_compatible"):
        if key in data:
            value = data.get(key)
            if isinstance(value, bool):
                return {"breaking": not value, "breaking_changes": (not value)}

    raw = str(data.get("compatibility") or data.get("verdict") or "").strip().lower()
    if raw in {"compatible", "backward-compatible", "non-breaking", "pass", "passed"}:
        return {"breaking": False, "breaking_changes": False}
    if raw in {"breaking", "incompatible", "fail", "failed"}:
        return {"breaking": True, "breaking_changes": True}

    return None


def _run_visual(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    if _profile_is_no_ui(root, profile):
        payload["checks"].append(_check(
            "ui-applicability",
            STATUS_NOT_APPLICABLE,
            "selected profile is API/headless; visual regression is not required for this product.",
        ))
        return

    _append_audit_status_check(payload, root, spec)
    if any(check["status"] in {STATUS_PASS, STATUS_FAIL, STATUS_BLOCKED} for check in payload["checks"]):
        return

    report = _first_existing(root, (
        ".signalos/quality/reports/visual/result.json",
        ".signalos/quality/p05-visual/result.json",
        ".signalos/quality/p05-visual/visual-result.json",
    ))
    if report is not None:
        data = _load_json_file(report)
        if isinstance(data, dict):
            raw = str(data.get("status") or data.get("result") or "").lower()
            failed = int(data.get("failed", data.get("diffs", 0)) or 0)
            has_verdict = raw in {"pass", "passed", "fail", "failed"}
            has_baseline = _visual_has_baseline_comparison(data)

            if raw in {"fail", "failed"} or failed > 0:
                payload["checks"].append(_check(
                    "visual-producer",
                    STATUS_FAIL,
                    "visual regression report exceeds the diff threshold.",
                    evidence=_display_path(report, root),
                    details={"status": raw, "failed": failed},
                ))
                return
            if not has_baseline:
                # A passing verdict without a baseline comparison proves nothing.
                payload["checks"].append(_check(
                    "visual-producer",
                    STATUS_BLOCKED,
                    "visual report has no baseline comparison "
                    "(expected 'baseline', 'diffs', or 'compared' evidence).",
                    evidence=_display_path(report, root),
                    details={"status": raw, "failed": failed},
                ))
                return
            if not has_verdict:
                payload["checks"].append(_check(
                    "visual-producer",
                    STATUS_BLOCKED,
                    "visual report has no explicit pass/fail verdict.",
                    evidence=_display_path(report, root),
                    details={"status": raw, "failed": failed},
                ))
                return
            payload["checks"].append(_check(
                "visual-producer",
                STATUS_PASS,
                "visual regression report passed with a baseline comparison.",
                evidence=_display_path(report, root),
                details={"status": raw, "failed": failed},
            ))
            return

    # Screenshot artifacts alone are NOT a pass: a baseline comparison and an
    # explicit verdict are required. Surface the gap as a blocker.
    screenshots = _visual_artifacts(root)
    if screenshots:
        payload["checks"].append(_check(
            "visual-producer",
            STATUS_BLOCKED,
            "visual screenshots exist but no visual report with a baseline "
            "comparison and pass verdict was produced.",
            evidence=screenshots[:20],
            details={"artifact_count": len(screenshots)},
        ))
        return

    _append_evidence_presence_check(payload, root, spec)


def _visual_has_baseline_comparison(data: dict[str, Any]) -> bool:
    for key in ("baseline", "baselines", "diffs", "compared", "comparisons", "compared_against"):
        if key in data and data.get(key) not in (None, ""):
            return True
    if data.get("baseline_compared") is True:
        return True
    return False


def _run_chaos(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    _append_audit_status_check(payload, root, spec)
    if any(check["status"] in {STATUS_PASS, STATUS_FAIL, STATUS_BLOCKED} for check in payload["checks"]):
        return

    result = _first_existing(root, (
        ".signalos/chaos/results.json",
        ".signalos/quality/p08-chaos/results.json",
        "chaos/results.json",
        "resilience/results.json",
    ))
    if result is not None:
        data = _load_json_file(result)
        if not isinstance(data, dict):
            payload["checks"].append(_check(
                "chaos-producer",
                STATUS_FAIL,
                "chaos result evidence is not a JSON object.",
                evidence=_display_path(result, root),
            ))
            return
        failed = int(data.get("experiments_failed", data.get("failed", 0)) or 0)
        total = int(data.get("experiments_total", data.get("total", 0)) or 0)
        raw_status = str(data.get("status") or "").lower()
        status = STATUS_FAIL if raw_status in {"fail", "failed"} or failed > 0 else STATUS_PASS
        payload["checks"].append(_check(
            "chaos-producer",
            status,
            "chaos/resilience result evidence parsed.",
            evidence=_display_path(result, root),
            details={"experiments_failed": failed, "experiments_total": total},
        ))
        return

    manifests = _match_evidence_globs(root, spec.evidence_globs)
    if manifests and _constitution_flag_enabled(root, spec.constitution_flag):
        payload["checks"].append(_check(
            "chaos-producer",
            STATUS_BLOCKED,
            "chaos manifests exist but no result JSON was produced.",
            evidence=manifests[:20],
            details={"required_by_constitution": True},
        ))
        return

    _append_evidence_presence_check(payload, root, spec)


def _run_production_monitor(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    _append_audit_status_check(payload, root, spec)
    if any(check["status"] == STATUS_PASS for check in payload["checks"]):
        return
    journal = root / ".signalos" / "observability" / "journal.jsonl"
    signals = root / ".signalos" / "observability" / "deployment-signals.jsonl"
    windows = root / ".signalos" / "observability" / "listening-windows"
    evidence = [
        _display_path(path, root)
        for path in (journal, signals)
        if path.is_file() and path.stat().st_size > 0
    ]
    if windows.is_dir() and any(windows.glob("*.json")):
        evidence.append(_display_path(windows, root))
    parsed = _production_monitor_result(root)
    if parsed is not None:
        status = STATUS_PASS if parsed["ok"] else STATUS_FAIL
        payload["checks"].append(_check(
            "production-monitor-producer",
            status,
            parsed["message"],
            evidence=parsed["evidence"],
            details=parsed["details"],
        ))
        return
    if evidence:
        payload["checks"].append(_check(
            "production-monitor-evidence",
            STATUS_PASS,
            "observability signal evidence is present.",
            evidence=evidence,
        ))
        return
    _append_evidence_presence_check(payload, root, spec)


def _run_pipeline(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    _append_audit_status_check(payload, root, spec)
    if any(check["status"] == STATUS_PASS for check in payload["checks"]):
        return
    release_evidence = sorted((root / ".signalos" / "evidence").glob("**/release-readiness.json"))
    for path in release_evidence:
        data = _load_json_file(path)
        if isinstance(data, dict) and data.get("ok") is True:
            payload["checks"].append(_check(
                "release-readiness",
                STATUS_PASS,
                "release-readiness evidence passed.",
                evidence=_display_path(path, root),
            ))
            return
    _append_evidence_presence_check(payload, root, spec)


def _run_governance(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    from .phases import _invalid_jsonl_lines

    audit_path = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if audit_path.is_file():
        invalid = _invalid_jsonl_lines(audit_path)
        payload["checks"].append(_check(
            "audit-trail-jsonl",
            STATUS_PASS if not invalid else STATUS_FAIL,
            "audit trail is valid JSONL" if not invalid else "audit trail contains invalid JSONL rows",
            evidence=_display_path(audit_path, root),
            details={"invalid_lines": invalid},
        ))
    else:
        payload["checks"].append(_check(
            "audit-trail-jsonl",
            STATUS_BLOCKED,
            "audit trail is missing.",
            evidence=".signalos/AUDIT_TRAIL.jsonl",
        ))

    governance_artifacts = [
        "core/governance/Governance/CONSTITUTION.md",
        "core/governance/Governance/SOUL-DOCUMENT.md",
        "core/strategy/BELIEF.md",
        ".signalos/PRD_TRACEABILITY.md",
        ".signalos/TRACEABILITY_MATRIX.md",
    ]
    existing = [rel for rel in governance_artifacts if (root / rel).is_file()]
    payload["checks"].append(_check(
        "governance-artifacts",
        STATUS_PASS if existing else STATUS_BLOCKED,
        "governance artifacts are present" if existing else "no governance artifacts were found",
        evidence=existing,
        details={"count": len(existing)},
    ))

    prd_path = root / ".signalos" / "PRD_TRACEABILITY.md"
    if prd_path.is_file():
        from signalos_lib.validators.traceability import validate_prd_traceability

        trace = validate_prd_traceability(root, write_evidence=True)
        payload["checks"].append(_check(
            "prd-traceability",
            STATUS_PASS if trace.get("ok") else STATUS_FAIL,
            "PRD traceability passed" if trace.get("ok") else "PRD traceability has blockers",
            evidence=trace.get("evidence_path"),
            details={"issues": trace.get("issues", [])},
        ))


def _run_evidence(
    payload: dict[str, Any],
    root: Path,
    spec: PhaseSpec,
    profile: str,
    timeout_sec: int,
) -> None:
    _append_audit_status_check(payload, root, spec)
    if any(check["status"] in {STATUS_PASS, STATUS_FAIL, STATUS_BLOCKED} for check in payload["checks"]):
        return
    validation_check = _validation_category_check(root, spec)
    if validation_check is not None:
        payload["checks"].append(validation_check)
        if validation_check["status"] == STATUS_PASS:
            return
    _append_evidence_presence_check(payload, root, spec)


_RUNNERS: dict[str, Callable[[dict[str, Any], Path, PhaseSpec, str, int], None]] = {
    "unit": _run_unit,
    "contract": _run_contract,
    "e2e": _run_e2e,
    "visual": _run_visual,
    "security": _run_security,
    "chaos": _run_chaos,
    "production_monitor": _run_production_monitor,
    "pipeline": _run_pipeline,
    "governance": _run_governance,
    "evidence": _run_evidence,
}


def _contract_json_candidates(root: Path) -> list[Path]:
    patterns = (
        ".signalos/contracts/**/*.json",
        ".signalos/quality/p03-contract/**/*.json",
        "contracts/**/*.json",
        "pacts/**/*.json",
        "pact/**/*.json",
        "**/openapi*.json",
    )
    candidates: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if _is_skipped_path(path) or not path.is_file():
                continue
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                candidates.append(path)
    return candidates


def _first_existing(root: Path, rel_paths: tuple[str, ...]) -> Path | None:
    for rel in rel_paths:
        path = root / rel
        if path.is_file():
            return path
    return None


def _visual_artifacts(root: Path) -> list[str]:
    patterns = (
        ".signalos/quality/reports/visual/**/*",
        ".signalos/quality/p05-visual/**/*",
        "**/__screenshots__/**/*",
        "**/screenshots/**/*",
        "**/visual-regression/**/*",
    )
    suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    out: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if _is_skipped_path(path) or not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            rel = _display_path(path, root)
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out


def _production_monitor_result(root: Path) -> dict[str, Any] | None:
    path = _first_existing(root, (
        ".signalos/observability/production-monitor.json",
        ".signalos/quality/p09-production-monitor/result.json",
        ".signalos/deploy/production-monitor.json",
    ))
    if path is None:
        return None
    data = _load_json_file(path)
    if not isinstance(data, dict):
        return {
            "ok": False,
            "message": "production-monitor evidence is not a JSON object.",
            "evidence": [_display_path(path, root)],
            "details": {},
        }
    raw_status = str(data.get("status") or "").lower()
    burn_5m = _float_or_none(data.get("burn_rate_5m"))
    burn_1h = _float_or_none(data.get("burn_rate_1h"))
    threshold = _float_or_none(data.get("burn_rate_threshold"))
    if threshold is None:
        threshold = 1.0
    over_threshold = any(
        value is not None and value > threshold
        for value in (burn_5m, burn_1h)
    )
    ok = raw_status not in {"fail", "failed"} and not over_threshold
    return {
        "ok": ok,
        "message": (
            "production monitor metrics are within SLO threshold."
            if ok
            else "production monitor metrics violate SLO threshold."
        ),
        "evidence": [_display_path(path, root)],
        "details": {
            "status": raw_status,
            "burn_rate_5m": burn_5m,
            "burn_rate_1h": burn_1h,
            "burn_rate_threshold": threshold,
        },
    }


def _append_audit_status_check(payload: dict[str, Any], root: Path, spec: PhaseSpec) -> None:
    rows = [
        row
        for row in _read_audit_rows(root)
        if row.get("action") == spec.audit_action
    ]
    if not rows:
        return
    latest = rows[-1]
    raw_status = str(latest.get("status", "")).strip().lower()
    if raw_status in {"pass", "passed", "success", "ok", "dry-run"}:
        status = STATUS_PASS
    elif raw_status in {"fail", "failed", "failure", "threshold-violation"}:
        status = STATUS_FAIL
    elif raw_status in {"infra-failure", "blocked", "error"}:
        status = STATUS_BLOCKED
    else:
        status = STATUS_PENDING
    payload["checks"].append(_check(
        "audit-evidence",
        status,
        f"latest {spec.audit_action} audit row has status {raw_status or 'unknown'}",
        evidence=".signalos/AUDIT_TRAIL.jsonl",
        details={"audit_row_count": len(rows), "latest": latest},
    ))


def _validation_category_check(root: Path, spec: PhaseSpec) -> dict[str, Any] | None:
    result = load_validation_result(root / ".signalos")
    if not result:
        return None
    results = result.get("results", {})
    for category in spec.validation_categories:
        category_result = results.get(category)
        if not isinstance(category_result, dict):
            continue
        raw_status = category_result.get("status")
        if raw_status == "passed":
            return _check(
                f"validation-{category}",
                STATUS_PASS,
                f"validation category {category} passed.",
                evidence=".signalos/product/VALIDATION_RESULT.json",
                details=category_result,
            )
        if raw_status == "failed":
            return _check(
                f"validation-{category}",
                STATUS_FAIL,
                f"validation category {category} failed.",
                evidence=".signalos/product/VALIDATION_RESULT.json",
                details=category_result,
            )
        if raw_status == "blocked":
            return _check(
                f"validation-{category}",
                STATUS_BLOCKED,
                f"validation category {category} is blocked.",
                evidence=".signalos/product/VALIDATION_RESULT.json",
                details=category_result,
            )
    return None


def _append_evidence_presence_check(payload: dict[str, Any], root: Path, spec: PhaseSpec) -> None:
    matches = _match_evidence_globs(root, spec.evidence_globs)
    if matches:
        payload["checks"].append(_check(
            "phase-evidence",
            STATUS_PENDING,
            "phase evidence exists, but no passing execution result or audit row was found.",
            evidence=matches[:20],
            details={"match_count": len(matches)},
        ))
        return
    required = _constitution_flag_enabled(root, spec.constitution_flag) if spec.constitution_flag else True
    status = STATUS_BLOCKED if required else STATUS_PENDING
    payload["checks"].append(_check(
        "phase-evidence",
        status,
        "no phase evidence was found.",
        evidence=list(spec.evidence_globs),
        details={"required_by_constitution": required},
    ))
