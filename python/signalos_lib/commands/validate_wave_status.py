"""App-native Wave status validator.

The existing ``signalos status`` command is intentionally advisory. This
command keeps the same local, technology-independent status source but adds the
validator contract used by hooks and CI: gate artifacts must be present,
signed, hash-valid, audit-linked, and signed by an allowed role.
"""

from __future__ import annotations

__all__ = ["main", "validate_wave_status"]

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.artifacts import get_gate_label, list_gates, resolve_gate_artifacts
from signalos_lib.commands.validate_gate import validate_gate
from signalos_lib.status import build_status_json, render_status_card
import signalos_lib.status as status_lib

SCHEMA_VERSION = "signalos.validate_wave_status.v1"
JOURNEY_SCHEMA_VERSION = "signalos.journey.snapshot.v1"

_GATE_NAMES = {
    "G0": "Onboarding",
    "G1": "Belief",
    "G2": "Planning",
    "G3": "Design",
    "G4": "Build",
    "G5": "Review",
}

_ARTIFACT_KINDS = {
    "G0": "FoundationArtifacts",
    "G1": "Belief",
    "G2": "ExpectationMap",
    "G3": "DesignAndPlan",
    "G4": "TrustTier",
    "G5": "QualityCheck",
}


@dataclass(frozen=True)
class WaveBlocker:
    gate: str | None
    kind: str
    message: str
    fix_command: str | None = None
    evidence: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        gate_number = _gate_number(self.gate)
        return {
            "gate": gate_number,
            "gate_code": self.gate,
            "kind": self.kind,
            "message": self.message,
            "fix_command": self.fix_command,
            "evidence": list(self.evidence or []),
        }


def validate_wave_status(
    repo_root: Path | str | None = None,
    *,
    wave: str | int | None = None,
    api_url: str | None = None,
    token: str | None = None,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Validate the current Wave status from local app files.

    ``api_url`` and ``token`` are accepted for CLI compatibility with the
    SignalOS.NET validator, but the app does not trust an external runtime API
    as proof yet. Passing ``api_url`` produces a blocker instead of silently
    downgrading remote validation to local files.
    """

    root = _resolve_root(repo_root)
    status_data = build_status_json(root)
    normalized_wave = _resolve_wave(wave, status_data.get("wave_id"))
    audit_status = _audit_status(root)
    scaffold_status = _scaffold_status(root)

    blockers: list[WaveBlocker] = []
    if api_url:
        blockers.append(
            WaveBlocker(
                gate=None,
                kind="remote-status-api-unsupported",
                message=(
                    "remote Wave status API validation is not available in the app runtime; "
                    "run without --api-url for local file validation"
                ),
                fix_command="signalos validate-wave-status --json",
            )
        )
    if not (root / ".signalos").is_dir():
        blockers.append(
            WaveBlocker(
                gate=None,
                kind="scaffold-missing",
                message=".signalos workspace scaffold is missing",
                fix_command="signalos init <path>",
                evidence=[".signalos"],
            )
        )
    if audit_status == "missing":
        blockers.append(
            WaveBlocker(
                gate=None,
                kind="audit-trail-missing",
                message="audit trail is missing",
                fix_command="restore .signalos/AUDIT_TRAIL.jsonl or rerun the scaffold",
                evidence=[".signalos/AUDIT_TRAIL.jsonl"],
            )
        )
    elif audit_status.startswith("invalid ") or audit_status.startswith("broken "):
        blockers.append(
            WaveBlocker(
                gate=None,
                kind="audit-trail-invalid",
                message=f"audit trail is {audit_status}",
                fix_command="repair .signalos/AUDIT_TRAIL.jsonl from an append-only source",
                evidence=[".signalos/AUDIT_TRAIL.jsonl"],
            )
        )

    gate_results: list[dict[str, Any]] = []
    for gate in list_gates():
        result = validate_gate(
            root,
            gate,
            wave=normalized_wave,
            write_evidence=False,
        )
        role_blockers = _role_blockers(root, gate)
        gate_ok = bool(result.get("ok")) and not role_blockers
        gate_blockers = _gate_blockers(gate, result, role_blockers)
        blockers.extend(gate_blockers)
        gate_results.append(
            {
                "number": _gate_number(gate),
                "code": gate,
                "name": _GATE_NAMES.get(gate, get_gate_label(gate)),
                "artifact_kind": _ARTIFACT_KINDS.get(gate, "GateArtifact"),
                "signed": gate_ok,
                "validator_status": "PASS" if gate_ok else "FAIL",
                "checks": result.get("checks", []),
                "role_checks": [item.to_dict() for item in role_blockers],
            }
        )

    blockers = _dedupe_blockers(blockers)
    signed_gate_count = sum(
        1 for gate in gate_results if gate["code"] != "G0" and gate["signed"]
    )
    next_gate = next((gate for gate in gate_results if not gate["signed"]), None)
    state = _state_from_signed_count(signed_gate_count)
    phase = str(next_gate["code"]) if next_gate else "closeout-ready"
    has_blocking_issue = bool(blockers)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    journey = {
        "schema_version": JOURNEY_SCHEMA_VERSION,
        "source": "files",
        "wave": normalized_wave,
        "state": state,
        "phase": phase,
        "gates": gate_results,
        "next_action": _next_action(next_gate, status_data),
        "blockers": [blocker.message for blocker in blockers],
        "structured_blockers": [blocker.to_dict() for blocker in blockers],
        "generated_at": generated_at,
    }

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(root),
        "source": "files",
        "wave": normalized_wave,
        "wave_label": _wave_label(normalized_wave),
        "state": state,
        "signed_gate_count": signed_gate_count,
        "next_gate": None if next_gate is None else next_gate["number"],
        "audit_status": audit_status,
        "scaffold_status": scaffold_status,
        "has_blocking_issue": has_blocking_issue,
        "ok": not has_blocking_issue,
        "pass": not has_blocking_issue,
        "status": "PASS" if not has_blocking_issue else "FAIL",
        "gates": gate_results,
        "blockers": [blocker.to_dict() for blocker in blockers],
        "journey": journey,
        "status_card": render_status_card(status_data),
        "status_source": status_data,
        "generated_at": generated_at,
    }
    if token:
        payload["token_supplied"] = True
    if write_evidence:
        payload["evidence_path"] = _write_evidence(root, normalized_wave, payload)
    else:
        payload["evidence_path"] = None
    return payload


def _resolve_root(repo_root: Path | str | None) -> Path:
    if repo_root is None:
        return status_lib._repo_root().resolve()
    return Path(repo_root).expanduser().resolve()


def _resolve_wave(explicit: str | int | None, detected: Any) -> str:
    return _normalize_wave(explicit) or _normalize_wave(detected) or "00"


def _normalize_wave(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.upper().removeprefix("W").strip()
    if raw in {"-", "UNKNOWN"}:
        return None
    if raw.isdigit():
        return f"{int(raw):02d}"
    return raw


def _wave_label(wave: str) -> str:
    return wave if wave.upper().startswith("W") else f"W{wave}"


def _gate_number(gate: str | None) -> int | None:
    if not gate:
        return None
    raw = str(gate).upper().removeprefix("G")
    return int(raw) if raw.isdigit() else None


def _state_from_signed_count(signed_gate_count: int) -> str:
    if signed_gate_count <= 0:
        return "Drafting"
    if signed_gate_count >= 5:
        return "Gate5Signed"
    return f"Gate{signed_gate_count}Signed"


def _next_action(
    next_gate: dict[str, Any] | None,
    status_data: dict[str, Any],
) -> dict[str, Any] | None:
    if next_gate is None:
        return None
    action = status_data.get("next_action") or {}
    command = action.get("command")
    if not command or command == "No blocking action":
        command = "signalos validate-gate --gate " + str(next_gate["code"])
    return {
        "gate": next_gate["code"],
        "role": action.get("role"),
        "command": command,
    }


def _gate_blockers(
    gate: str,
    result: dict[str, Any],
    role_blockers: list[WaveBlocker],
) -> list[WaveBlocker]:
    blockers: list[WaveBlocker] = []
    for check in result.get("checks", []):
        if check.get("status") != "FAIL":
            continue
        blockers.append(
            WaveBlocker(
                gate=gate,
                kind=str(check.get("id") or "gate-validation-failed"),
                message=f"{gate} {_GATE_NAMES.get(gate, gate)}: {check.get('message')}",
                fix_command=f"signalos validate-gate --gate {gate} --json",
                evidence=[str(item) for item in check.get("evidence", [])],
            )
        )
    blockers.extend(role_blockers)
    return blockers


def _role_blockers(root: Path, gate: str) -> list[WaveBlocker]:
    blockers: list[WaveBlocker] = []
    for artifact in resolve_gate_artifacts(root, gate):
        if not artifact.path.is_file():
            continue
        roles = _signature_roles(artifact.path)
        if not roles:
            continue
        allowed = {role.upper() for role in artifact.required_roles}
        actual = {role.upper() for role in roles}
        if actual.isdisjoint(allowed):
            blockers.append(
                WaveBlocker(
                    gate=gate,
                    kind="wrong-signer-role",
                    message=(
                        f"{gate} {artifact.label} was signed by "
                        f"{', '.join(sorted(actual))}; requires one of "
                        f"{', '.join(sorted(allowed))}"
                    ),
                    fix_command=f"re-sign {artifact.rel_path} as one of {', '.join(sorted(allowed))}",
                    evidence=[artifact.rel_path],
                )
            )
    return blockers


def _signature_roles(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^## Signatures", text, re.MULTILINE)
    if not match:
        return []
    block = text[match.start():]
    if re.search(r"DRAFT", block, re.IGNORECASE):
        return []
    roles = []
    for role_match in re.finditer(r"(?im)^\s*role:\s*([A-Za-z0-9_-]+)\s*$", block):
        role = role_match.group(1).strip()
        if role:
            roles.append(role)
    return roles


def _audit_status(root: Path) -> str:
    # The app's audit trail does not (yet) write a sha256/previousSha256 hash
    # chain, so there is no chain to verify here. This reports honest
    # presence/row-shape status only: missing / empty / invalid-row / present.
    audit_path = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not audit_path.is_file():
        return "missing"
    checked_rows = 0
    for row_number, line in enumerate(
        audit_path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        checked_rows += 1
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return f"invalid json at row {row_number}"
        if not isinstance(item, dict):
            return f"invalid row shape at row {row_number}"
    if checked_rows == 0:
        return "empty"
    return f"present ({checked_rows} rows)"


def _scaffold_status(root: Path) -> str:
    has_signalos = (root / ".signalos").is_dir()
    has_core = (root / "core").is_dir()
    if has_signalos and has_core:
        return "present"
    if has_signalos:
        return "state-only"
    if has_core:
        return "core-only"
    return "missing"


def _dedupe_blockers(blockers: list[WaveBlocker]) -> list[WaveBlocker]:
    seen: set[tuple[str | None, str, str]] = set()
    out: list[WaveBlocker] = []
    for blocker in blockers:
        key = (blocker.gate, blocker.kind, blocker.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(blocker)
    return out


def _write_evidence(root: Path, wave: str, payload: dict[str, Any]) -> str | None:
    if not (root / ".signalos").is_dir():
        return None
    evidence_dir = root / ".signalos" / "evidence" / "waves"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", wave).strip("-") or "00"
    evidence_path = evidence_dir / f"validate-wave-status-w{suffix.lower()}.json"
    try:
        display_path = evidence_path.relative_to(root).as_posix()
    except ValueError:
        display_path = str(evidence_path)
    payload["evidence_path"] = display_path
    evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return display_path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos validate-wave-status",
        description="Validate the app-native Wave status card and Journey blockers.",
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--wave", default=None, help="Optional wave id, e.g. 01 or W01")
    parser.add_argument("--api-url", default=None, help="Reserved for future app runtime API status validation")
    parser.add_argument("--token", default=None, help="Reserved bearer token for --api-url")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--no-evidence", action="store_true")
    args = parser.parse_args(argv)

    payload = validate_wave_status(
        args.repo_root,
        wave=args.wave,
        api_url=args.api_url,
        token=args.token,
        write_evidence=not args.no_evidence,
    )
    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(payload)
    return 0 if payload["ok"] else 1


def _print_human(payload: dict[str, Any]) -> None:
    print("SignalOS Status Card")
    print(f"Source: {payload['source']}")
    print(f"Wave: {payload['wave_label']}")
    print(f"State: {payload['state']}")
    print(f"Signed gates: {payload['signed_gate_count']}/5")
    next_gate = payload.get("next_gate")
    print(f"Next gate: {'none' if next_gate is None else 'G' + str(next_gate)}")
    print(f"Audit chain: {payload['audit_status']}")
    print(f"Scaffold: {payload['scaffold_status']}")
    print(f"Journey phase: {payload['journey']['phase']}")
    if payload.get("blockers"):
        print("Journey blockers:")
        for blocker in payload["blockers"]:
            print(f"- {blocker['message']}")
    if payload.get("evidence_path"):
        print(f"Evidence: {payload['evidence_path']}")
    if payload.get("has_blocking_issue"):
        print("validate-wave-status: blockers found", file=sys.stderr)
