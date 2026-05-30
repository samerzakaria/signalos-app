"""Release readiness gate for SignalOS-governed product repos.

The command composes existing governance surfaces instead of reimplementing
them: Layer 1 validators, product verification evidence, gate artifact
signatures, source intent, audit trail, and publish-state markers.
"""

from __future__ import annotations

__all__ = ["main", "release_readiness"]

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.artifacts import expected_gate_artifacts, list_gates
from signalos_lib.sign import check_gate
from signalos_lib.validate_cmd import overall_exit_code, run_validators

SCHEMA_VERSION = "signalos.release_readiness.v1"
DEFAULT_WAVE = "release-readiness"


@dataclass
class ReadinessCheck:
    id: str
    status: str
    severity: str
    message: str
    evidence: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
            "details": self.details,
        }


def release_readiness(
    repo_root: Path | str | None = None,
    *,
    wave: str | None = None,
    profile_id: str | None = None,
    run_verification: bool = False,
    timeout_sec: int = 300,
) -> dict[str, Any]:
    """Return a stable release-readiness payload for *repo_root*."""

    root = Path(repo_root or Path.cwd()).resolve()
    wave_segment = _safe_segment(wave or DEFAULT_WAVE)
    evidence_dir = root / ".signalos" / "evidence" / wave_segment
    generated_at = _utc_now()

    checks: list[ReadinessCheck] = []
    checks.append(_check_workspace(root))

    if root.exists() and root.is_dir():
        layer1_check = _check_layer1(root)
        checks.append(layer1_check)
        checks.append(_check_source_intent(root))
        checks.extend(_check_gate_artifacts(root))
        checks.append(_check_product_verification(
            root,
            wave_segment,
            profile_id=profile_id,
            run_verification=run_verification,
            timeout_sec=timeout_sec,
        ))
        checks.append(_check_audit_trail(root))
        checks.append(_check_risks_visible(root))
        checks.append(_check_deployment_path(root))
        checks.append(_check_required_templates(root))
        checks.append(_check_release_blockers(root))

    check_dicts = [check.to_dict() for check in checks]
    blockers = [
        {
            "id": check["id"],
            "severity": check["severity"],
            "message": check["message"],
            "evidence": check["evidence"],
        }
        for check in check_dicts
        if check["status"] == "FAIL" and check["severity"] in {"HALT", "BLOCK_MERGE"}
    ]
    ok = not blockers
    publish_relationship = _publish_relationship(root, ok)
    next_action = _next_action(ok, publish_relationship, blockers)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(root),
        "wave": wave_segment,
        "ok": ok,
        "pass": ok,
        "status": publish_relationship if ok else "blocked",
        "checks": check_dicts,
        "blockers": blockers,
        "evidence": _collect_evidence(check_dicts),
        "next_action": next_action,
        "publish_relationship": publish_relationship,
        "generated_at": generated_at,
        "summary": {
            "total": len(check_dicts),
            "passed": sum(1 for check in check_dicts if check["status"] == "PASS"),
            "failed": sum(1 for check in check_dicts if check["status"] == "FAIL"),
            "warnings": sum(1 for check in check_dicts if check["severity"] == "WARN"),
        },
    }

    if root.exists() and root.is_dir():
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / "release-readiness.json"
        payload["evidence_path"] = _display_path(evidence_path, root)
        evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        payload["evidence"] = _collect_evidence(check_dicts) + [payload["evidence_path"]]
        evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        payload["evidence_path"] = None

    return payload


def _check_workspace(root: Path) -> ReadinessCheck:
    if root.exists() and root.is_dir():
        return ReadinessCheck(
            id="active-workspace",
            status="PASS",
            severity="HALT",
            message="workspace root exists and is a directory",
            evidence=[str(root)],
        )
    return ReadinessCheck(
        id="active-workspace",
        status="FAIL",
        severity="HALT",
        message=f"workspace root is missing or not a directory: {root}",
        details={"repo_root": str(root), "exists": root.exists(), "is_dir": root.is_dir()},
    )


def _check_layer1(root: Path) -> ReadinessCheck:
    results = run_validators(repo_root=root, group="layer1")
    code = overall_exit_code(results)
    payload = {
        "schema_version": "signalos.validate.v1",
        "group": "layer1",
        "repo_root": str(root),
        "status": "PASS" if code == 0 else "FAIL",
        "exit_code": code,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed and not r.skipped),
            "failed": sum(1 for r in results if not r.passed),
            "skipped": sum(1 for r in results if r.skipped),
            "halt_failures": sum(1 for r in results if not r.passed and r.severity == "HALT"),
            "block_merge_failures": sum(1 for r in results if not r.passed and r.severity == "BLOCK_MERGE"),
            "warn_failures": sum(1 for r in results if not r.passed and r.severity == "WARN"),
        },
        "results": [
            {
                "name": r.name,
                "group": r.group,
                "severity": r.severity,
                "status": r.status_label,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
                "skipped": r.skipped,
                "skip_reason": r.skip_reason,
                "message": r.message,
                "details": r.details,
                "stderr": r.stderr[:500] if r.stderr else "",
            }
            for r in results
        ],
    }
    evidence_dir = root / ".signalos" / "evidence" / "layer1"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / "validate-layer1.json"
    evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    failed = [r for r in results if not r.passed]
    severity = "HALT" if any(r.severity == "HALT" for r in failed) else "BLOCK_MERGE"
    return ReadinessCheck(
        id="layer1-valid",
        status="PASS" if code == 0 else "FAIL",
        severity=severity,
        message="Layer 1 structural validation passed" if code == 0 else "Layer 1 structural validation failed",
        evidence=[_display_path(evidence_path, root)],
        details=payload["summary"],
    )


def _check_source_intent(root: Path) -> ReadinessCheck:
    candidates = [
        ".signalos/sources/initial-intent.json",
        ".signalos/sources/source-intent.json",
        ".signalos/source-intent.json",
        ".signalos/SOURCE_PROMPT.md",
        ".signalos/product/INTENT.json",
        ".signalos/product.json",
    ]
    sources_dir = root / ".signalos" / "sources"
    existing = [rel for rel in candidates if (root / rel).is_file()]
    if sources_dir.is_dir():
        known_names = {Path(rel).name for rel in existing}
        existing.extend(
            f".signalos/sources/{path.name}"
            for path in sorted(sources_dir.iterdir())
            if path.is_file() and path.name not in known_names
        )
    return ReadinessCheck(
        id="source-intent-traceable",
        status="PASS" if existing else "FAIL",
        severity="BLOCK_MERGE",
        message="source intent is traceable" if existing else "source intent is not recorded",
        evidence=existing,
        details={"candidates": candidates},
    )


def _check_gate_artifacts(root: Path) -> list[ReadinessCheck]:
    expected = expected_gate_artifacts()
    missing = [artifact.rel_path for artifact in expected if not (root / artifact.rel_path).is_file()]
    present = [artifact.rel_path for artifact in expected if (root / artifact.rel_path).is_file()]

    unsigned: list[str] = []
    drafts: list[str] = []
    hash_mismatch: list[str] = []
    for gate in list_gates():
        for status in check_gate(root, gate):
            if not status.exists:
                continue
            if not status.has_signatures:
                unsigned.append(status.rel_path)
            if status.is_draft:
                drafts.append(status.rel_path)
            if status.hash_valid is False:
                hash_mismatch.append(status.rel_path)

    artifact_check = ReadinessCheck(
        id="required-governance-artifacts",
        status="PASS" if not missing else "FAIL",
        severity="BLOCK_MERGE",
        message="required governance artifacts are present" if not missing else "required governance artifacts are missing",
        evidence=present,
        details={"missing": missing, "required_count": len(expected)},
    )
    signature_failures = unsigned + drafts + hash_mismatch
    gate_check = ReadinessCheck(
        id="required-gates-signed",
        status="PASS" if not signature_failures and not missing else "FAIL",
        severity="BLOCK_MERGE",
        message="required gate artifacts are signed" if not signature_failures and not missing else "one or more gate artifacts are unsigned or invalid",
        evidence=present,
        details={"unsigned": unsigned, "draft_signatures": drafts, "hash_mismatch": hash_mismatch, "missing": missing},
    )
    return [artifact_check, gate_check]


def _check_product_verification(
    root: Path,
    wave: str,
    *,
    profile_id: str | None,
    run_verification: bool,
    timeout_sec: int,
) -> ReadinessCheck:
    payload: dict[str, Any] | None
    evidence_path: str | None = None
    if run_verification:
        from signalos_lib.commands.verify_product import verify_product

        payload = verify_product(
            repo_root=root,
            wave=wave,
            profile_id=profile_id,
            timeout_sec=timeout_sec,
        )
        evidence_path = payload.get("evidence_path")
    else:
        loaded = _load_latest_verify_product(root)
        payload = loaded[0]
        evidence_path = loaded[1]

    if payload is None:
        return ReadinessCheck(
            id="build-test-evidence",
            status="FAIL",
            severity="BLOCK_MERGE",
            message="product verification evidence is missing; run signalos verify-product --json",
            details={"expected": ".signalos/evidence/<wave>/verify-product.json"},
        )

    checks = payload.get("checks", [])
    failed_checks = [
        _verification_check_name(check)
        for check in checks
        if _verification_check_status(check) == "FAIL"
    ]
    skipped_checks = [
        _verification_check_name(check)
        for check in checks
        if _verification_check_status(check) in {"SKIP", "SKIPPED"}
        and not _verification_skip_is_allowed(check)
    ]
    summary = payload.get("summary", {})
    any_reported_skip = any(
        _verification_check_status(check) in {"SKIP", "SKIPPED"}
        for check in checks
    )
    if not skipped_checks and not any_reported_skip and int(summary.get("skipped") or 0) > 0:
        skipped_checks.append("<summary>")
    ok = payload.get("status") == "PASS" and not failed_checks and not skipped_checks
    if ok:
        message = "build and test verification evidence passed"
    elif skipped_checks:
        message = "product verification evidence has skipped checks"
    else:
        message = "build or test verification evidence failed"
    return ReadinessCheck(
        id="build-test-evidence",
        status="PASS" if ok else "FAIL",
        severity="BLOCK_MERGE",
        message=message,
        evidence=[evidence_path] if evidence_path else [],
        details={
            "verification_status": payload.get("status"),
            "failed_checks": failed_checks,
            "skipped_checks": skipped_checks,
            "summary": summary,
        },
    )


def _check_audit_trail(root: Path) -> ReadinessCheck:
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not audit.is_file():
        return ReadinessCheck(
            id="audit-trail-valid",
            status="FAIL",
            severity="HALT",
            message="audit trail is missing",
            evidence=[".signalos/AUDIT_TRAIL.jsonl"],
        )
    invalid_lines = _invalid_jsonl_lines(audit)
    return ReadinessCheck(
        id="audit-trail-valid",
        status="PASS" if not invalid_lines else "FAIL",
        severity="HALT",
        message="audit trail is valid JSONL" if not invalid_lines else "audit trail contains invalid JSONL rows",
        evidence=[".signalos/AUDIT_TRAIL.jsonl"],
        details={"invalid_lines": invalid_lines[:10]},
    )


def _check_risks_visible(root: Path) -> ReadinessCheck:
    candidates = [
        "core/execution/TRUST_TIER.md",
        "core/governance/QUALITY_CHECK.md",
        ".signalos/risks.json",
    ]
    existing = [rel for rel in candidates if _nonempty(root / rel)]
    return ReadinessCheck(
        id="risks-visible",
        status="PASS" if existing else "FAIL",
        severity="BLOCK_MERGE",
        message="risk evidence is visible" if existing else "risk evidence is missing",
        evidence=existing,
        details={"candidates": candidates},
    )


def _check_deployment_path(root: Path) -> ReadinessCheck:
    candidates = [
        ".signalos/deployment-path.json",
        ".signalos/product/DEPLOY_DECISION.json",
        ".signalos/product/DEPLOY_EVIDENCE.json",
        "core/execution/DEPLOYMENT.md",
        "docs/deployment.md",
        "DEPLOYMENT.md",
    ]
    existing = [rel for rel in candidates if _nonempty(root / rel)]
    json_errors: list[str] = []
    for rel in existing:
        if not rel.endswith(".json"):
            continue
        try:
            json.loads((root / rel).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            json_errors.append(f"{rel}: {exc}")
    return ReadinessCheck(
        id="deployment-path-known",
        status="PASS" if existing and not json_errors else "FAIL",
        severity="BLOCK_MERGE",
        message="deployment path is known" if existing and not json_errors else "deployment path is missing or invalid",
        evidence=existing,
        details={"candidates": candidates, "json_errors": json_errors},
    )


def _verification_check_status(check: Any) -> str:
    if not isinstance(check, dict):
        return ""
    return str(check.get("status") or "").upper()


def _verification_check_name(check: Any) -> str:
    if not isinstance(check, dict):
        return str(check)
    name = check.get("name") or check.get("kind") or "<unnamed>"
    return str(name)


def _verification_skip_is_allowed(check: Any) -> bool:
    if not isinstance(check, dict):
        return False
    details = check.get("details")
    if isinstance(details, dict) and (
        details.get("not_applicable") is True
        or details.get("release_not_applicable") is True
    ):
        return True
    reason = str(check.get("reason") or "").strip().lower()
    return reason.startswith("not applicable")


def _check_required_templates(root: Path) -> ReadinessCheck:
    required = [
        "core/governance/Templates/plan-template.md",
        "core/governance/Templates/quality-check-template.md",
        "core/governance/Templates/soul-document-template.md",
        "core/governance/Templates/trust-tier-scoring.md",
    ]
    missing = [rel for rel in required if not _nonempty(root / rel)]
    return ReadinessCheck(
        id="required-templates-present",
        status="PASS" if not missing else "FAIL",
        severity="BLOCK_MERGE",
        message="required governance templates are present" if not missing else "required governance templates are missing",
        evidence=[rel for rel in required if rel not in missing],
        details={"missing": missing},
    )


def _check_release_blockers(root: Path) -> ReadinessCheck:
    path = root / ".signalos" / "release-blockers.json"
    if not path.is_file():
        return ReadinessCheck(
            id="no-unresolved-release-blockers",
            status="PASS",
            severity="BLOCK_MERGE",
            message="no release blocker file is present",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ReadinessCheck(
            id="no-unresolved-release-blockers",
            status="FAIL",
            severity="BLOCK_MERGE",
            message=f"release blocker file is invalid JSON: {exc}",
            evidence=[".signalos/release-blockers.json"],
        )
    blockers = _open_blockers(payload)
    return ReadinessCheck(
        id="no-unresolved-release-blockers",
        status="PASS" if not blockers else "FAIL",
        severity="BLOCK_MERGE",
        message="no unresolved release blockers" if not blockers else "unresolved release blockers remain",
        evidence=[".signalos/release-blockers.json"],
        details={"open_blockers": blockers[:20]},
    )


def _load_latest_verify_product(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    base = root / ".signalos" / "evidence"
    if not base.is_dir():
        return None, None
    candidates = sorted(
        base.glob("*/verify-product.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.as_posix()),
        reverse=True,
    )
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        return payload, _display_path(path, root)
    return None, None


def _publish_relationship(root: Path, ok: bool) -> str:
    if not ok:
        return "blocked"
    if _published_detected(root):
        return "published"
    return "ready-to-publish"


def _published_detected(root: Path) -> bool:
    for rel in (".signalos/published.json", ".signalos/publish.json"):
        if (root / rel).is_file():
            return True
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not audit.is_file():
        return False
    try:
        for line in audit.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            action = str(row.get("action") or "")
            if "publish" in action.lower():
                return True
    except (OSError, json.JSONDecodeError):
        return False
    return False


def _next_action(ok: bool, publish_relationship: str, blockers: list[dict[str, Any]]) -> str:
    if publish_relationship == "published":
        return "Release is published; verify catalog/deployment state if needed."
    if ok:
        return "Ready to publish. Run signalos publish when the user asks for deployment or package publication."
    if blockers:
        first = blockers[0]
        return f"Resolve {first['id']}: {first['message']}"
    return "Resolve release readiness blockers."


def _open_blockers(payload: Any) -> list[dict[str, Any]]:
    items: list[Any]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        raw = payload.get("blockers", payload.get("items", []))
        items = raw if isinstance(raw, list) else []
    else:
        items = []

    blockers: list[dict[str, Any]] = []
    closed = {"closed", "resolved", "done", "waived", "accepted"}
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            blockers.append({"index": idx, "message": str(item)})
            continue
        status = str(item.get("status") or item.get("state") or "open").lower()
        if status not in closed and not item.get("resolved", False):
            blockers.append(item)
    return blockers


def _invalid_jsonl_lines(path: Path) -> list[int]:
    invalid: list[int] = []
    with path.open("r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                invalid.append(idx)
    return invalid


def _nonempty(path: Path) -> bool:
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _collect_evidence(checks: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    evidence: list[str] = []
    for check in checks:
        for item in check.get("evidence", []):
            if item and item not in seen:
                seen.add(item)
                evidence.append(item)
    return evidence


def _safe_segment(value: str) -> str:
    import re

    segment = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return segment or DEFAULT_WAVE


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos release-readiness",
        description="Evaluate release readiness and publish relationship for a governed product repo.",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Product repo root. Defaults to cwd.")
    parser.add_argument("--wave", default=None, help=f"Evidence wave folder. Defaults to {DEFAULT_WAVE}.")
    parser.add_argument("--profile", default=None, help="Profile id to forward when --run-verification is used.")
    parser.add_argument("--timeout-sec", type=int, default=300, help="Timeout for profile commands when verification runs.")
    parser.add_argument(
        "--run-verification",
        action="store_true",
        help="Run signalos verify-product before evaluating readiness instead of reading existing evidence.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON.")
    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    payload = release_readiness(
        repo_root=args.repo_root,
        wave=args.wave,
        profile_id=args.profile,
        run_verification=args.run_verification,
        timeout_sec=args.timeout_sec,
    )
    if args.as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        _render_summary(payload)
    return 0 if payload["ok"] else 1


def _render_summary(payload: dict[str, Any]) -> None:
    sys.stdout.write(f"SignalOS release readiness: {payload['status']}\n")
    sys.stdout.write(f"Evidence: {payload.get('evidence_path')}\n")
    for check in payload["checks"]:
        sys.stdout.write(f"- {check['id']}: {check['status']} {check['message']}\n")
    if payload.get("blockers"):
        sys.stdout.write(f"Next: {payload['next_action']}\n")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
