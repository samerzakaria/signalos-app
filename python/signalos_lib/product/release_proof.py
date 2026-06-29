"""Technology-neutral release artifact proof validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import sys
from typing import Any

SCHEMA_VERSION = "signalos.release_proof.v1"
SIGNATURE_PROOF_SCHEMA_VERSION = "signalos.artifact_signature_proof.v1"
CLEAN_MACHINE_PROOF_SCHEMA_VERSION = "signalos.clean_machine_proof.v1"
DEFAULT_WAVE = "release-proof"
PASS_STATUSES = {"pass", "passed", "ok", "success", "ready", "green"}


@dataclass
class ReleaseProofCheck:
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


def validate_release_proof(
    repo_root: Path | str | None = None,
    *,
    artifact: Path | str | None = None,
    artifact_kind: str | None = None,
    signature: Path | str | None = None,
    clean_machine_proof: Path | str | None = None,
    installer_proof: Path | str | None = None,
    readiness_evidence: Path | str | None = None,
    require_signature: bool = False,
    require_clean_machine: bool = False,
    require_installer_proof: bool = False,
    require_readiness: bool = False,
    wave: str | None = None,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Validate a releasable artifact without assuming any product stack.

    The artifact can be a zip, wheel, tarball, installer, container manifest,
    NuGet package, or any other file. Signature and installer/clean-machine
    evidence are supplied as explicit proof files so release policy can be
    strict without forcing a particular signing or packaging tool.
    """

    root = Path(repo_root or Path.cwd()).resolve()
    wave_segment = _safe_segment(wave or DEFAULT_WAVE)
    generated_at = _utc_now()

    artifact_check, artifact_meta = _check_artifact(
        root,
        artifact,
        artifact_kind=artifact_kind,
    )
    checks = [
        artifact_check,
        _check_signature(root, signature, artifact_meta, require_signature),
        _check_json_proof(
            root,
            clean_machine_proof,
            check_id="clean-machine-proof",
            label="clean-machine proof",
            required=require_clean_machine,
            require_environment=True,
            require_steps=True,
        ),
        _check_json_proof(
            root,
            installer_proof,
            check_id="installer-proof",
            label="installer proof",
            required=require_installer_proof,
            require_environment=False,
            require_steps=True,
        ),
        _check_readiness(root, readiness_evidence, require_readiness),
    ]

    check_dicts = [check.to_dict() for check in checks]
    blockers = [
        {
            "id": check["id"],
            "severity": check["severity"],
            "message": check["message"],
            "evidence": check["evidence"],
        }
        for check in check_dicts
        if check["status"] != "PASS"
    ]
    ok = not blockers
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(root),
        "wave": wave_segment,
        "ok": ok,
        "status": "release-proofed" if ok else "blocked",
        "artifact": artifact_meta,
        "checks": check_dicts,
        "blockers": blockers,
        "evidence": _collect_evidence(check_dicts),
        "generated_at": generated_at,
        "summary": {
            "total": len(check_dicts),
            "passed": sum(1 for check in check_dicts if check["status"] == "PASS"),
            "failed": sum(1 for check in check_dicts if check["status"] != "PASS"),
        },
    }

    if write_evidence and root.exists() and root.is_dir():
        evidence_dir = root / ".signalos" / "evidence" / wave_segment
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / "release-proof.json"
        payload["evidence_path"] = _display_path(evidence_path, root)
        payload["evidence"] = payload["evidence"] + [payload["evidence_path"]]
        evidence_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    else:
        payload["evidence_path"] = None

    return payload


def produce_signature_proof(
    repo_root: Path | str | None = None,
    *,
    artifact: Path | str,
    signature_file: Path | str,
    output: Path | str | None = None,
    signed_by: str | None = None,
    signing_tool: str | None = None,
    wave: str | None = None,
) -> dict[str, Any]:
    """Write JSON signature proof tied to a release artifact and signature file."""

    root = Path(repo_root or Path.cwd()).resolve()
    artifact_path = _resolve_path(root, artifact)
    signature_path = _resolve_path(root, signature_file)
    wave_segment = _safe_segment(wave or DEFAULT_WAVE)
    output_path = (
        _resolve_path(root, output)
        if output is not None
        else root / ".signalos" / "evidence" / wave_segment / "artifact-signature.json"
    )

    checks: list[dict[str, Any]] = []
    artifact_meta: dict[str, Any] | None = None
    signature_meta: dict[str, Any] | None = None

    if artifact_path.is_file() and artifact_path.stat().st_size > 0:
        artifact_meta = {
            "path": _display_path(artifact_path, root),
            "size_bytes": artifact_path.stat().st_size,
            "sha256": _sha256_file(artifact_path),
            "kind": _infer_artifact_kind(artifact_path),
        }
        checks.append({"name": "artifact-readable", "status": "pass", "path": artifact_meta["path"]})
    else:
        checks.append({
            "name": "artifact-readable",
            "status": "fail",
            "path": _display_path(artifact_path, root),
            "message": "artifact is missing or empty",
        })

    if signature_path.is_file() and signature_path.stat().st_size > 0:
        signature_meta = {
            "path": _display_path(signature_path, root),
            "size_bytes": signature_path.stat().st_size,
            "sha256": _sha256_file(signature_path),
        }
        checks.append({"name": "signature-readable", "status": "pass", "path": signature_meta["path"]})
    else:
        checks.append({
            "name": "signature-readable",
            "status": "fail",
            "path": _display_path(signature_path, root),
            "message": "signature file is missing or empty",
        })

    ok = artifact_meta is not None and signature_meta is not None
    payload: dict[str, Any] = {
        "schema_version": SIGNATURE_PROOF_SCHEMA_VERSION,
        "status": "pass" if ok else "blocked",
        "ok": ok,
        "artifact_path": artifact_meta["path"] if artifact_meta else _display_path(artifact_path, root),
        "artifact_sha256": artifact_meta["sha256"] if artifact_meta else None,
        "signature": signature_meta,
        "signed_by": signed_by or "",
        "signing_tool": signing_tool or "external",
        "checks": checks,
        "blockers": [
            {"id": check["name"], "message": check.get("message", "check failed")}
            for check in checks
            if str(check.get("status", "")).lower() not in PASS_STATUSES
        ],
        "generated_at": _utc_now(),
    }

    _write_json_payload(output_path, payload)
    payload["path"] = _display_path(output_path, root)
    return payload


def produce_clean_machine_proof(
    repo_root: Path | str | None = None,
    *,
    artifact: Path | str | None = None,
    output: Path | str | None = None,
    fresh_workspace: bool = False,
    environment_label: str | None = None,
    wave: str | None = None,
) -> dict[str, Any]:
    """Write clean-machine proof with explicit environment and pass/fail checks."""

    root = Path(repo_root or Path.cwd()).resolve()
    wave_segment = _safe_segment(wave or DEFAULT_WAVE)
    output_path = (
        _resolve_path(root, output)
        if output is not None
        else root / ".signalos" / "evidence" / wave_segment / "clean-machine.json"
    )
    clean_marker = fresh_workspace or _env_truthy("SIGNALOS_CLEAN_MACHINE") or _env_truthy("CI")
    environment = {
        "label": environment_label or os.environ.get("RUNNER_NAME") or platform.node(),
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "cwd": str(root),
        "fresh_workspace": bool(clean_marker),
        "ci": _env_truthy("CI"),
    }

    commands: list[dict[str, Any]] = []
    commands.append({
        "command": "clean-environment-marker",
        "status": "pass" if clean_marker else "fail",
        "detail": "clean environment marker present" if clean_marker else "missing clean environment marker",
    })

    if artifact is not None:
        artifact_path = _resolve_path(root, artifact)
        artifact_ok = artifact_path.is_file() and artifact_path.stat().st_size > 0
        commands.append({
            "command": "release-artifact-readable",
            "status": "pass" if artifact_ok else "fail",
            "path": _display_path(artifact_path, root),
            "detail": "artifact exists and is non-empty" if artifact_ok else "artifact is missing or empty",
        })

    ok = all(str(command.get("status", "")).lower() in PASS_STATUSES for command in commands)
    payload: dict[str, Any] = {
        "schema_version": CLEAN_MACHINE_PROOF_SCHEMA_VERSION,
        "status": "pass" if ok else "blocked",
        "ok": ok,
        "environment": environment,
        "commands": commands,
        "blockers": [
            {"id": command["command"], "message": command.get("detail", "command failed")}
            for command in commands
            if str(command.get("status", "")).lower() not in PASS_STATUSES
        ],
        "generated_at": _utc_now(),
    }

    _write_json_payload(output_path, payload)
    payload["path"] = _display_path(output_path, root)
    return payload


def _check_artifact(
    root: Path,
    artifact: Path | str | None,
    *,
    artifact_kind: str | None,
) -> tuple[ReleaseProofCheck, dict[str, Any] | None]:
    if artifact is None:
        return (
            ReleaseProofCheck(
                id="release-artifact",
                status="FAIL",
                severity="HALT",
                message="release artifact path is required",
            ),
            None,
        )

    path = _resolve_path(root, artifact)
    if not path.is_file():
        return (
            ReleaseProofCheck(
                id="release-artifact",
                status="FAIL",
                severity="HALT",
                message="release artifact is missing or is not a file",
                evidence=[_display_path(path, root)],
                details={"path": str(path)},
            ),
            None,
        )

    try:
        stat = path.stat()
        sha256 = _sha256_file(path)
    except OSError as exc:
        return (
            ReleaseProofCheck(
                id="release-artifact",
                status="FAIL",
                severity="HALT",
                message=f"release artifact could not be read: {exc}",
                evidence=[_display_path(path, root)],
            ),
            None,
        )

    if stat.st_size <= 0:
        return (
            ReleaseProofCheck(
                id="release-artifact",
                status="FAIL",
                severity="HALT",
                message="release artifact is empty",
                evidence=[_display_path(path, root)],
                details={"size_bytes": stat.st_size},
            ),
            None,
        )

    meta = {
        "path": _display_path(path, root),
        "kind": artifact_kind or _infer_artifact_kind(path),
        "size_bytes": stat.st_size,
        "sha256": sha256,
    }
    return (
        ReleaseProofCheck(
            id="release-artifact",
            status="PASS",
            severity="HALT",
            message="release artifact exists and has a stable digest",
            evidence=[meta["path"]],
            details=meta,
        ),
        meta,
    )


def _check_signature(
    root: Path,
    signature: Path | str | None,
    artifact_meta: dict[str, Any] | None,
    required: bool,
) -> ReleaseProofCheck:
    if signature is None:
        return ReleaseProofCheck(
            id="artifact-signature",
            status="FAIL" if required else "PASS",
            severity="BLOCK_MERGE",
            message=(
                "artifact signature proof is required"
                if required
                else "artifact signature proof is not required"
            ),
            details={"required": required},
        )

    path = _resolve_path(root, signature)
    if not path.is_file():
        return ReleaseProofCheck(
            id="artifact-signature",
            status="FAIL",
            severity="BLOCK_MERGE",
            message="artifact signature proof is missing or is not a file",
            evidence=[_display_path(path, root)],
            details={"required": required},
        )

    try:
        stat = path.stat()
        signature_sha256 = _sha256_file(path)
    except OSError as exc:
        return ReleaseProofCheck(
            id="artifact-signature",
            status="FAIL",
            severity="BLOCK_MERGE",
            message=f"artifact signature proof could not be read: {exc}",
            evidence=[_display_path(path, root)],
            details={"required": required},
        )

    if stat.st_size <= 0:
        return ReleaseProofCheck(
            id="artifact-signature",
            status="FAIL",
            severity="BLOCK_MERGE",
            message="artifact signature proof is empty",
            evidence=[_display_path(path, root)],
            details={"required": required, "size_bytes": stat.st_size},
        )

    details: dict[str, Any] = {
        "required": required,
        "path": _display_path(path, root),
        "size_bytes": stat.st_size,
        "sha256": signature_sha256,
        "artifact_digest_match": None,
    }
    payload = _load_json_file(path)
    if isinstance(payload, dict):
        details["format"] = "json"
        explicit_status = any(key in payload for key in ("ok", "passed", "pass", "status", "result", "outcome"))
        if explicit_status and not _proof_passed(payload):
            return ReleaseProofCheck(
                id="artifact-signature",
                status="FAIL",
                severity="BLOCK_MERGE",
                message="artifact signature proof did not pass",
                evidence=[_display_path(path, root)],
                details=details,
            )
        claimed_digest = _extract_claimed_artifact_digest(payload)
        if claimed_digest:
            details["claimed_artifact_sha256"] = claimed_digest
            actual_digest = str((artifact_meta or {}).get("sha256") or "")
            details["artifact_digest_match"] = claimed_digest == actual_digest
            if actual_digest and claimed_digest != actual_digest:
                return ReleaseProofCheck(
                    id="artifact-signature",
                    status="FAIL",
                    severity="BLOCK_MERGE",
                    message="artifact signature digest does not match the release artifact",
                    evidence=[_display_path(path, root)],
                    details=details,
                )
    else:
        details["format"] = "opaque"
        if required:
            return ReleaseProofCheck(
                id="artifact-signature",
                status="FAIL",
                severity="BLOCK_MERGE",
                message="artifact signature proof must be structured JSON when required",
                evidence=[_display_path(path, root)],
                details=details,
            )

    return ReleaseProofCheck(
        id="artifact-signature",
        status="PASS",
        severity="BLOCK_MERGE",
        message="artifact signature proof is present",
        evidence=[_display_path(path, root)],
        details=details,
    )


def _check_json_proof(
    root: Path,
    proof: Path | str | None,
    *,
    check_id: str,
    label: str,
    required: bool,
    require_environment: bool,
    require_steps: bool,
) -> ReleaseProofCheck:
    if proof is None:
        return ReleaseProofCheck(
            id=check_id,
            status="FAIL" if required else "PASS",
            severity="BLOCK_MERGE",
            message=f"{label} is required" if required else f"{label} is not required",
            details={"required": required},
        )

    path = _resolve_path(root, proof)
    evidence = [_display_path(path, root)]
    if not path.is_file():
        return ReleaseProofCheck(
            id=check_id,
            status="FAIL",
            severity="BLOCK_MERGE",
            message=f"{label} is missing or is not a file",
            evidence=evidence,
            details={"required": required},
        )

    payload = _load_json_file(path)
    if not isinstance(payload, dict):
        return ReleaseProofCheck(
            id=check_id,
            status="FAIL",
            severity="BLOCK_MERGE",
            message=f"{label} must be a JSON object",
            evidence=evidence,
            details={"required": required},
        )

    details: dict[str, Any] = {
        "required": required,
        "path": evidence[0],
        "status": payload.get("status", payload.get("result", payload.get("outcome"))),
    }
    if not _proof_passed(payload):
        return ReleaseProofCheck(
            id=check_id,
            status="FAIL",
            severity="BLOCK_MERGE",
            message=f"{label} did not pass",
            evidence=evidence,
            details=details,
        )

    steps = _proof_steps(payload)
    details["step_count"] = len(steps)
    failed_steps = _failed_steps(steps)
    if require_steps and not steps:
        return ReleaseProofCheck(
            id=check_id,
            status="FAIL",
            severity="BLOCK_MERGE",
            message=f"{label} must list at least one command or check",
            evidence=evidence,
            details=details,
        )
    if failed_steps:
        details["failed_steps"] = failed_steps[:20]
        return ReleaseProofCheck(
            id=check_id,
            status="FAIL",
            severity="BLOCK_MERGE",
            message=f"{label} contains failed commands or checks",
            evidence=evidence,
            details=details,
        )

    environment = payload.get("environment") or payload.get("machine") or payload.get("runner")
    has_environment = isinstance(environment, dict) and bool(environment)
    details["has_environment"] = has_environment
    if require_environment and not has_environment:
        return ReleaseProofCheck(
            id=check_id,
            status="FAIL",
            severity="BLOCK_MERGE",
            message=f"{label} must record the clean environment",
            evidence=evidence,
            details=details,
        )

    return ReleaseProofCheck(
        id=check_id,
        status="PASS",
        severity="BLOCK_MERGE",
        message=f"{label} passed",
        evidence=evidence,
        details=details,
    )


def _check_readiness(
    root: Path,
    readiness_evidence: Path | str | None,
    required: bool,
) -> ReleaseProofCheck:
    path = _resolve_path(root, readiness_evidence) if readiness_evidence else None
    if path is None and required:
        path = _latest_readiness_evidence(root)
    if path is None:
        return ReleaseProofCheck(
            id="release-readiness-evidence",
            status="FAIL" if required else "PASS",
            severity="BLOCK_MERGE",
            message=(
                "release-readiness evidence is required"
                if required
                else "release-readiness evidence is not required"
            ),
            details={"required": required},
        )

    evidence = [_display_path(path, root)]
    if not path.is_file():
        return ReleaseProofCheck(
            id="release-readiness-evidence",
            status="FAIL",
            severity="BLOCK_MERGE",
            message="release-readiness evidence is missing or is not a file",
            evidence=evidence,
            details={"required": required},
        )
    payload = _load_json_file(path)
    if not isinstance(payload, dict):
        return ReleaseProofCheck(
            id="release-readiness-evidence",
            status="FAIL",
            severity="BLOCK_MERGE",
            message="release-readiness evidence must be a JSON object",
            evidence=evidence,
            details={"required": required},
        )

    # Acceptance requires an explicit ok:true AND no blockers. A status of
    # "ready-to-publish"/"published" is not sufficient on its own; an
    # ok:false or ok-missing payload must still block (fail-closed).
    ok = payload.get("ok") is True and not payload.get("blockers")
    return ReleaseProofCheck(
        id="release-readiness-evidence",
        status="PASS" if ok else "FAIL",
        severity="BLOCK_MERGE",
        message=(
            "release-readiness evidence passed"
            if ok
            else "release-readiness evidence is blocked"
        ),
        evidence=evidence,
        details={
            "required": required,
            "status": payload.get("status"),
            "blocker_count": len(payload.get("blockers") or []),
        },
    )


def _resolve_path(root: Path, value: Path | str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _safe_segment(value: str) -> str:
    # Use the shared ship normalization so the same numeric wave resolves to
    # the same .signalos/evidence/<wave>/ directory across ship and
    # release-proof (e.g. "1" and "W1" both become "W01").
    from signalos_lib.commands.ship import normalize_wave_segment

    return normalize_wave_segment(value, default=DEFAULT_WAVE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {
        "1", "true", "yes", "y", "on", "pass", "passed",
    }


def _write_json_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _infer_artifact_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".tar.gz"):
        return "tar.gz"
    if name.endswith(".tar.bz2"):
        return "tar.bz2"
    if name.endswith(".tar.xz"):
        return "tar.xz"
    suffix = path.suffix.lower().lstrip(".")
    return suffix or "file"


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _extract_claimed_artifact_digest(payload: dict[str, Any]) -> str:
    for key in ("artifact_sha256", "artifactDigest", "artifact_digest", "sha256", "digest"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_digest(value)
    subject = payload.get("subject")
    if isinstance(subject, dict):
        for key in ("sha256", "digest", "artifact_sha256"):
            value = subject.get(key)
            if isinstance(value, str) and value.strip():
                return _normalize_digest(value)
    return ""


def _normalize_digest(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned.startswith("sha256:"):
        cleaned = cleaned.split(":", 1)[1]
    return cleaned


def _proof_passed(payload: dict[str, Any]) -> bool:
    for key in ("ok", "passed", "pass"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
    for key in ("status", "result", "outcome"):
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip().lower() in PASS_STATUSES
    return False


def _proof_steps(payload: dict[str, Any]) -> list[Any]:
    for key in ("commands", "checks", "steps", "validations"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _failed_steps(steps: list[Any]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        status = step.get("status", step.get("result", step.get("outcome")))
        if isinstance(status, str) and status.strip().lower() not in PASS_STATUSES:
            failed.append({
                "index": idx,
                "command": step.get("command", step.get("name", f"step-{idx}")),
                "status": status,
            })
        elif isinstance(status, bool) and not status:
            failed.append({
                "index": idx,
                "command": step.get("command", step.get("name", f"step-{idx}")),
                "status": status,
            })
    return failed


def _latest_readiness_evidence(root: Path) -> Path | None:
    base = root / ".signalos" / "evidence"
    if not base.is_dir():
        return None
    candidates = sorted(
        base.glob("*/release-readiness.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.as_posix()),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _collect_evidence(checks: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    evidence: list[str] = []
    for check in checks:
        for item in check.get("evidence", []):
            if item and item not in seen:
                seen.add(item)
                evidence.append(item)
    return evidence
