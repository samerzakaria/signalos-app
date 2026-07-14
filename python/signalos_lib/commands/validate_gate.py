"""App-native gate validator.

This mirrors the SignalOS.NET validator concept without copying the .NET
implementation: a gate is valid only when its required artifact exists, carries
an approved non-draft signature, and has an append-only audit row that links the
artifact, gate, verdict, hash, and optional wave.
"""

from __future__ import annotations

__all__ = ["main", "validate_gate"]

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.artifacts import list_gates
from signalos_lib.product.run_ids import safe_control_path
from signalos_lib.sign import (
    GATE_LABELS,
    _audit_entry_hash,
    _compute_hash,
    check_gate,
    verify_audit_chain,
)

SCHEMA_VERSION = "signalos.validate_gate.v1"
_APPROVED_VERDICTS = {"APPROVED", "APPROVED-WITH-CONDITIONS", "WAIVED"}
_SIGN_ACTIONS = {"sign", "gate-sign", "gate.signed", "gate.approved"}
_REVERSE_ACTIONS = {"gate.reopen", "gate.unsign", "gate.revoke"}


@dataclass
class GateCheck:
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


def validate_gate(
    repo_root: Path | str | None,
    gate: str | int,
    *,
    wave: str | int | None = None,
    write_evidence: bool = True,
    project_id: str = "default",
) -> dict[str, Any]:
    root = Path(repo_root or Path.cwd()).resolve()
    normalized_gate = _normalize_gate(gate)
    normalized_wave = _normalize_wave(wave)
    checks: list[GateCheck] = []

    if normalized_gate not in list_gates():
        checks.append(
            GateCheck(
                id="gate-known",
                status="FAIL",
                severity="HALT",
                message=f"unknown gate {gate!r}; expected one of {', '.join(list_gates())}",
                details={"gate": str(gate), "known_gates": list_gates()},
            )
        )
        return _payload(root, normalized_gate, normalized_wave, checks, write_evidence=False)

    # §3.2: artifact paths resolve through the shared governance resolver;
    # "default" keeps the workspace-root layout byte-identical.
    statuses = check_gate(root, normalized_gate, project_id=project_id)
    artifact_checks = _check_artifacts(statuses)
    checks.extend(artifact_checks)
    checks.extend(
        _check_audit(
            root,
            normalized_gate,
            normalized_wave,
            statuses,
            project_id=project_id,
        )
    )

    return _payload(root, normalized_gate, normalized_wave, checks, write_evidence=write_evidence)


def _check_artifacts(statuses: list[Any]) -> list[GateCheck]:
    missing = [status.rel_path for status in statuses if not status.exists]
    unsigned = [
        status.rel_path
        for status in statuses
        if status.exists and not status.has_signatures
    ]
    drafts = [status.rel_path for status in statuses if status.exists and status.is_draft]
    hash_mismatch = [
        status.rel_path
        for status in statuses
        if status.exists and status.hash_valid is False
    ]
    signed = [
        {
            "artifact": status.rel_path,
            "signers": list(status.signers),
            "hash_declared": status.hash_valid is not None,
            "hash_valid": status.hash_valid,
        }
        for status in statuses
        if status.exists and status.has_signatures and not status.is_draft
    ]
    return [
        GateCheck(
            id="gate-artifacts-present",
            status="PASS" if not missing else "FAIL",
            severity="HALT",
            message="required gate artifacts are present" if not missing else "required gate artifacts are missing",
            evidence=[status.rel_path for status in statuses if status.exists],
            details={"missing": missing, "required_count": len(statuses)},
        ),
        GateCheck(
            id="gate-artifacts-signed",
            status="PASS" if not unsigned and not drafts else "FAIL",
            severity="HALT",
            message="required gate artifacts are signed" if not unsigned and not drafts else "required gate artifacts are unsigned or draft",
            evidence=[item["artifact"] for item in signed],
            details={"unsigned": unsigned, "drafts": drafts, "signed": signed},
        ),
        GateCheck(
            id="gate-signature-hashes-valid",
            status="PASS" if not hash_mismatch else "FAIL",
            severity="HALT",
            message="signature artifact hashes are valid" if not hash_mismatch else "signature artifact hashes do not match current content",
            evidence=[status.rel_path for status in statuses if status.exists and status.hash_valid is True],
            details={"hash_mismatch": hash_mismatch},
        ),
    ]


def _check_audit(
    root: Path,
    gate: str,
    wave: str | None,
    statuses: list[Any],
    *,
    project_id: str = "default",
) -> list[GateCheck]:
    try:
        audit_path = safe_control_path(root, ".signalos", "AUDIT_TRAIL.jsonl")
    except ValueError as exc:
        return [
            GateCheck(
                id="gate-audit-trail-present",
                status="FAIL",
                severity="HALT",
                message="AUDIT_TRAIL.jsonl path is unsafe",
                details={"path": ".signalos/AUDIT_TRAIL.jsonl", "reason": str(exc)},
            ),
            GateCheck(
                id="gate-audit-linked",
                status="FAIL",
                severity="HALT",
                message="gate signatures cannot use a redirected audit trail",
            ),
        ]
    if not audit_path.is_file():
        return [
            GateCheck(
                id="gate-audit-trail-present",
                status="FAIL",
                severity="HALT",
                message="AUDIT_TRAIL.jsonl is missing",
                details={"path": ".signalos/AUDIT_TRAIL.jsonl"},
            ),
            GateCheck(
                id="gate-audit-linked",
                status="FAIL",
                severity="HALT",
                message="gate signatures cannot be audit-linked without AUDIT_TRAIL.jsonl",
            ),
        ]

    chain_violations = verify_audit_chain(audit_path)
    if chain_violations:
        return [
            GateCheck(
                id="gate-audit-trail-present",
                status="PASS",
                severity="HALT",
                message="AUDIT_TRAIL.jsonl exists",
                evidence=[".signalos/AUDIT_TRAIL.jsonl"],
            ),
            GateCheck(
                id="gate-audit-linked",
                status="FAIL",
                severity="HALT",
                message="audit trail integrity validation failed",
                evidence=[".signalos/AUDIT_TRAIL.jsonl"],
                details={"violations": chain_violations},
            ),
        ]

    entries = _read_audit_entries(audit_path)
    present_statuses = [status for status in statuses if status.exists]
    linked: list[dict[str, Any]] = []
    missing_links: list[str] = []

    for status in present_statuses:
        computed_hash = _compute_hash(status.path)
        match = _find_audit_match(
            entries,
            gate=gate,
            wave=wave,
            artifact=status.rel_path,
            computed_hash=computed_hash,
            project_id=project_id,
        )
        if match is None:
            missing_links.append(status.rel_path)
        else:
            linked.append({
                "artifact": status.rel_path,
                "action": match.get("action"),
                "gate": match.get("gate"),
                "wave": match.get("wave"),
                "verdict": match.get("verdict"),
                "project_id": match.get("project_id", "default"),
            })

    return [
        GateCheck(
            id="gate-audit-trail-present",
            status="PASS",
            severity="HALT",
            message="AUDIT_TRAIL.jsonl exists",
            evidence=[".signalos/AUDIT_TRAIL.jsonl"],
            details={"entries": len(entries)},
        ),
        GateCheck(
            id="gate-audit-linked",
            status="PASS" if not missing_links else "FAIL",
            severity="HALT",
            message="gate signatures are audit-linked" if not missing_links else "gate signatures are missing matching audit rows",
            evidence=[".signalos/AUDIT_TRAIL.jsonl"],
            details={
                "linked": linked,
                "missing_links": missing_links,
                "wave_required": wave,
                "project_id": project_id,
            },
        ),
    ]


def _find_audit_match(
    entries: list[dict[str, Any]],
    *,
    gate: str,
    wave: str | None,
    artifact: str,
    computed_hash: str,
    project_id: str = "default",
) -> dict[str, Any] | None:
    gate_label = GATE_LABELS.get(gate, gate)
    for entry in reversed(entries):
        # The workspace audit ledger is shared by every virtual project, while
        # artifact paths stay canonical (for example every project has a
        # ``core/strategy/BELIEF.md``).  Path + hash is therefore insufficient:
        # identical bytes signed for project A must never authorize project B.
        # Historical single-project rows had no project_id, so only the
        # ``default`` project may consume that legacy form.
        if not _audit_project_matches(entry, project_id):
            continue
        action = str(entry.get("action", "")).strip().lower()
        # Reopen/revoke rows are audit-authoritative.  The mutable convenience
        # marker may be deleted or lost, but an older sign row must never become
        # valid again.  A genuinely newer sign row is encountered first and can
        # restore validity for its exact artifact/hash.
        if action in _REVERSE_ACTIONS and _audit_reverses_gate(entry, gate):
            return None
        if action not in _SIGN_ACTIONS:
            continue
        if _normalize_artifact(entry.get("artifact")) != _normalize_artifact(artifact):
            continue
        if _normalize_gate_value(entry.get("gate")) not in {gate, gate_label.lower()}:
            continue
        if wave is not None and _normalize_wave(entry.get("wave")) != wave:
            continue
        if str(entry.get("verdict", "")).upper() not in _APPROVED_VERDICTS:
            continue
        audit_hash = str(entry.get("hash", "")).strip().lower()
        if audit_hash != computed_hash.lower():
            continue
        entry_hash = str(entry.get("entry_hash") or "").strip().lower()
        if (
            len(entry_hash) != 64
            or any(char not in "0123456789abcdef" for char in entry_hash)
            or _audit_entry_hash(entry) != entry_hash
        ):
            continue
        return entry
    return None


def _audit_project_matches(entry: dict[str, Any], project_id: str) -> bool:
    audit_project = entry.get("project_id")
    if project_id == "default":
        return audit_project in (None, "", "default")
    return audit_project == project_id


def _audit_reverses_gate(entry: dict[str, Any], gate: str) -> bool:
    candidates: list[Any] = [entry.get("gate")]
    listed = entry.get("gates")
    if isinstance(listed, list):
        candidates.extend(listed)
    return gate in {_normalize_gate_value(value) for value in candidates}


def _payload(
    root: Path,
    gate: str,
    wave: str | None,
    checks: list[GateCheck],
    *,
    write_evidence: bool,
) -> dict[str, Any]:
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
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(root),
        "gate": gate,
        "wave": wave,
        "ok": ok,
        "pass": ok,
        "status": "PASS" if ok else "FAIL",
        "checks": check_dicts,
        "blockers": blockers,
        "summary": {
            "total": len(check_dicts),
            "passed": sum(1 for check in check_dicts if check["status"] == "PASS"),
            "failed": sum(1 for check in check_dicts if check["status"] == "FAIL"),
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if write_evidence:
        evidence_dir = root / ".signalos" / "evidence" / "gates"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        name = f"validate-gate-{gate.lower()}"
        if wave:
            name += f"-w{wave}"
        evidence_path = evidence_dir / f"{name}.json"
        payload["evidence_path"] = _display_path(evidence_path, root)
        evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        payload["evidence_path"] = None
    return payload


def _read_audit_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _normalize_gate(value: str | int | None) -> str:
    raw = str(value or "").strip().upper()
    if raw.startswith("G") and raw[1:].isdigit():
        return f"G{int(raw[1:])}"
    if raw.isdigit():
        return f"G{int(raw)}"
    return raw


def _normalize_gate_value(value: Any) -> str:
    raw = str(value or "").strip().lower()
    normalized = _normalize_gate(raw).lower()
    if normalized in {gate.lower() for gate in list_gates()}:
        return normalized.upper()
    if raw.startswith("gate "):
        suffix = raw.split("gate ", 1)[1].strip()
        if suffix.isdigit():
            return f"G{int(suffix)}"
    return raw


def _normalize_wave(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.upper().removeprefix("W").strip()
    if raw.isdigit():
        return f"{int(raw):02d}"
    return raw


def _normalize_artifact(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos validate-gate",
        description="Validate a gate artifact is present, signed, hash-valid, and audit-linked.",
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--gate", required=True, help="Gate id: G0..G5 or 0..5")
    parser.add_argument("--wave", default=None, help="Optional wave id, e.g. 01 or W01")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--no-evidence", action="store_true")
    parser.add_argument(
        "--project-id", default="default", dest="project_id", metavar="ID",
        help="Multi-project namespace (§3.2); default keeps workspace-root paths.",
    )
    args = parser.parse_args(argv)

    payload = validate_gate(
        args.repo_root,
        args.gate,
        wave=args.wave,
        write_evidence=not args.no_evidence,
        project_id=args.project_id,
    )
    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(payload)
    return 0 if payload["ok"] else 1


def _print_human(payload: dict[str, Any]) -> None:
    gate = payload.get("gate")
    wave = payload.get("wave")
    label = f"{gate}" + (f" wave {wave}" if wave else "")
    print(f"validate-gate {label}: {payload['status']}")
    for check in payload.get("checks", []):
        print(f"- {check['status']}: {check['id']} - {check['message']}")
    if payload.get("evidence_path"):
        print(f"evidence: {payload['evidence_path']}")
    for blocker in payload.get("blockers", []):
        print(f"BLOCKER: {blocker['id']}: {blocker['message']}", file=sys.stderr)
