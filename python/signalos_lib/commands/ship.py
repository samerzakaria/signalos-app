"""App-native deterministic ship ceremony.

This mirrors the SignalOS.NET ship concept without binding release to .NET,
ABP, or a deployment provider. The command verifies local release gates,
optionally creates a local tag, and appends an audit-trail row. It never pushes,
publishes, or deploys.
"""

from __future__ import annotations

__all__ = ["main", "normalize_wave_segment", "ship_wave"]

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.sign import check_gate, check_gate_signed_strict

SCHEMA_VERSION = "signalos.ship.v1"
DEFAULT_TAG_FORMAT = "wave-{W}"
AGENT_SELF_SIGNATURES = {"agent", "claude", "cursor", "copilot", "windsurf", "llm", "ai"}


@dataclass
class ShipCheck:
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


def ship_wave(
    repo_root: Path | str | None = None,
    *,
    wave: str | int,
    dry_run: bool = False,
    no_tag: bool = False,
    allow_dirty: bool = False,
    tag_format: str = DEFAULT_TAG_FORMAT,
    actor: str | None = None,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Verify and optionally confirm a wave shipment.

    Green path writes a local tag unless ``no_tag`` is set, then appends
    ``ship-confirmed`` to ``.signalos/AUDIT_TRAIL.jsonl``. Red path appends
    ``ship-blocked`` unless ``dry_run`` is set. The command intentionally
    stops at the local boundary.
    """

    root = Path(repo_root or Path.cwd()).resolve()
    wave_name = _normalize_wave(wave)
    tag_name = _format_tag(tag_format, wave_name)
    effective_actor = actor or _git_user(root) or "unknown"
    generated_at = _utc_now()

    checks = [
        _check_wave_directory(root, wave_name),
        _check_gate5_signed(root),
        _check_clean_tree(root, allow_dirty=allow_dirty),
        _check_self_assessment(root),
        _check_release_readiness(root),
        _check_release_proof(root),
        _check_closeout_honesty(root),
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
        if check["status"] == "FAIL" and check["severity"] in {"HALT", "BLOCK_MERGE"}
    ]
    ok = not blockers
    tag_result: dict[str, Any] = {
        "requested": not no_tag,
        "created": False,
        "tag": tag_name,
        "commit": None,
        "message": "dry-run did not create a tag" if dry_run else "not attempted",
    }

    audit_action: str | None = None
    if dry_run:
        status = "ship-ready" if ok else "blocked"
    elif ok:
        if not no_tag:
            tag_result = _create_local_tag(root, tag_name, actor=effective_actor, wave=wave_name)
            if not tag_result["created"]:
                ok = False
                blockers.append(
                    {
                        "id": "local-tag",
                        "severity": "BLOCK_MERGE",
                        "message": str(tag_result.get("message") or "local tag creation failed"),
                        "evidence": [],
                    }
                )
                check_dicts.append(
                    ShipCheck(
                        id="local-tag",
                        status="FAIL",
                        severity="BLOCK_MERGE",
                        message=str(tag_result.get("message") or "local tag creation failed"),
                    ).to_dict()
                )
        else:
            tag_result.update({"message": "--no-tag specified"})
        audit_action = "ship-confirmed" if ok else "ship-blocked"
        status = "shipped" if ok else "blocked"
    else:
        audit_action = "ship-blocked"
        status = "blocked"

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(root),
        "wave": wave_name,
        "ok": ok,
        "status": status,
        "mode": "dry-run" if dry_run else "live",
        "actor": effective_actor,
        "tag": tag_result,
        "checks": check_dicts,
        "blockers": blockers,
        "next_action": _next_action(ok, dry_run=dry_run, no_tag=no_tag, tag_name=tag_name),
        "generated_at": generated_at,
        "summary": {
            "total": len(check_dicts),
            "passed": sum(1 for check in check_dicts if check["status"] == "PASS"),
            "failed": sum(1 for check in check_dicts if check["status"] == "FAIL"),
            "warnings": sum(1 for check in check_dicts if check["severity"] == "WARN"),
        },
    }

    if audit_action is not None:
        _append_ship_audit(root, payload, action=audit_action)

    if write_evidence and root.exists() and root.is_dir():
        evidence_dir = root / ".signalos" / "evidence" / wave_name
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / "ship.json"
        payload["evidence_path"] = _display_path(evidence_path, root)
        evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        payload["evidence_path"] = None

    return payload


def _check_wave_directory(root: Path, wave_name: str) -> ShipCheck:
    path = root / ".signalos" / "waves" / wave_name
    if path.is_dir():
        return ShipCheck(
            id="wave-directory",
            status="PASS",
            severity="HALT",
            message=f"{_display_path(path, root)} exists",
            evidence=[_display_path(path, root)],
        )
    return ShipCheck(
        id="wave-directory",
        status="FAIL",
        severity="HALT",
        message=f".signalos/waves/{wave_name} is missing",
        evidence=[f".signalos/waves/{wave_name}"],
    )


def _check_gate5_signed(root: Path) -> ShipCheck:
    statuses = check_gate(root, "G5")
    failures: list[str] = []
    evidence: list[str] = []
    details: dict[str, Any] = {"artifacts": []}
    for status in statuses:
        evidence.append(status.rel_path)
        artifact = {
            "path": status.rel_path,
            "exists": status.exists,
            "signers": status.signers,
            "is_draft": status.is_draft,
            "hash_valid": status.hash_valid,
        }
        details["artifacts"].append(artifact)
        if not status.exists:
            failures.append(f"{status.rel_path} missing")
            continue
        if not status.has_signatures:
            failures.append(f"{status.rel_path} unsigned")
            continue
        if status.is_draft:
            failures.append(f"{status.rel_path} has DRAFT signature")
        if status.hash_valid is None:
            failures.append(f"{status.rel_path} signature missing artifact hash")
        if status.hash_valid is False:
            failures.append(f"{status.rel_path} signature hash mismatch")
        agent_signers = [name for name in status.signers if _is_agent_signature(name)]
        if agent_signers:
            failures.append(f"{status.rel_path} self-signed by agent: {', '.join(agent_signers)}")
    # Route the gate through the single strict signature validator so "signed"
    # means the SAME thing here as on the primary board / preflight: verdict
    # APPROVED, an authorized role, a valid CURRENT artifact hash, audit-chain
    # linkage, and non-revoked. The per-artifact checks above catch unsigned /
    # draft / hash-missing / agent-self-signer, but NOT a REJECTED verdict, a
    # wrong role, a missing audit row, or a reopened (revoked) gate -- so a block
    # with a valid hash and a real-looking signer could otherwise ship while the
    # board correctly rejects it.
    strict = check_gate_signed_strict(root, "G5")
    if not strict.signed:
        failures.extend(strict.reasons or ["Gate 5 is not validly signed"])
    details["strict_signed"] = strict.signed
    details["strict_reasons"] = strict.reasons
    return ShipCheck(
        id="gate-5-signed",
        status="PASS" if not failures else "FAIL",
        severity="HALT",
        message="Gate 5 quality check is signed" if not failures else "; ".join(failures),
        evidence=evidence,
        details=details,
    )


def _check_clean_tree(root: Path, *, allow_dirty: bool) -> ShipCheck:
    if allow_dirty:
        return ShipCheck(
            id="clean-tree",
            status="PASS",
            severity="WARN",
            message="working tree cleanliness was explicitly waived with --allow-dirty",
            details={"allow_dirty": True},
        )
    result = _git(root, ["status", "--porcelain"])
    if result is None:
        return ShipCheck(
            id="clean-tree",
            status="FAIL",
            severity="HALT",
            message="workspace is not a git repository or git is unavailable",
        )
    dirty = [line for line in result.stdout.splitlines() if line.strip()]
    return ShipCheck(
        id="clean-tree",
        status="PASS" if not dirty else "FAIL",
        severity="HALT",
        message="working tree clean" if not dirty else f"working tree dirty: {len(dirty)} path(s)",
        details={"dirty": dirty[:50]},
    )


def _check_self_assessment(root: Path) -> ShipCheck:
    path = root / "core" / "governance" / "QUALITY_CHECK.md"
    if not path.is_file():
        return ShipCheck(
            id="self-assessment-no-fail",
            status="FAIL",
            severity="BLOCK_MERGE",
            message="core/governance/QUALITY_CHECK.md is missing",
            evidence=["core/governance/QUALITY_CHECK.md"],
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    fail_lines = _detect_fail_verdicts(text)
    return ShipCheck(
        id="self-assessment-no-fail",
        status="PASS" if not fail_lines else "FAIL",
        severity="BLOCK_MERGE",
        message="quality check has no FAIL marks" if not fail_lines else "quality check contains FAIL marks",
        evidence=["core/governance/QUALITY_CHECK.md"],
        details={"fail_lines": fail_lines[:20]},
    )


# Real fail verdict values. Bare prose containing the substring "fail"
# (e.g. "no failures observed", "fail-closed design") must NOT match.
_FAIL_VERDICT_VALUES = {"fail", "failed", "failing", "blocked", "rejected"}

# Explicit verdict/status field, e.g. "Verdict: FAIL" or "Result: FAILED".
_VERDICT_FIELD_RE = re.compile(
    r"(?i)^\s*[-*]?\s*\**\s*(?:verdict|status|result|outcome|self[\s_-]*assessment)\s*\**\s*[:=]\s*(.+?)\s*$"
)
# Checked markdown checkbox, e.g. "- [x] FAIL" or "* [X] failed".
_CHECKBOX_RE = re.compile(r"(?i)^\s*[-*]\s*\[\s*[xX]\s*\]\s*(.+?)\s*$")


def _is_fail_verdict_value(value: str) -> bool:
    # Strip markdown emphasis/punctuation, then look at the leading token so
    # "FAIL — coverage gap" still counts but "fail-closed design" does not.
    cleaned = value.strip().strip("*`_").strip()
    head = re.split(r"[\s:.;,()/\\-]+", cleaned, maxsplit=1)[0].lower()
    return head in _FAIL_VERDICT_VALUES


def _detect_fail_verdicts(text: str) -> list[dict[str, Any]]:
    """Structured FAIL detection over QUALITY_CHECK.md.

    Only an explicit verdict/status field set to a real fail value, or a
    checked markdown checkbox whose label is a real fail value, blocks ship.
    Bare prose that merely contains the word "fail" does not.
    """
    fail_lines: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        match = _VERDICT_FIELD_RE.match(stripped) or _CHECKBOX_RE.match(stripped)
        if match and _is_fail_verdict_value(match.group(1)):
            fail_lines.append({"line": index, "text": stripped[:240]})
    return fail_lines


def _check_release_readiness(root: Path) -> ShipCheck:
    path = _latest_evidence(root, "release-readiness.json")
    if path is None:
        return ShipCheck(
            id="release-readiness",
            status="FAIL",
            severity="BLOCK_MERGE",
            message="release-readiness evidence is missing",
        )
    payload = _load_json(path)
    # Acceptance requires an explicit ok:true AND no blockers. A status of
    # "ready-to-publish"/"published" is not sufficient on its own; an
    # ok:false or ok-missing payload must still block (fail-closed).
    ok = isinstance(payload, dict) and payload.get("ok") is True and not payload.get("blockers")
    return ShipCheck(
        id="release-readiness",
        status="PASS" if ok else "FAIL",
        severity="BLOCK_MERGE",
        message="release-readiness evidence passed" if ok else "release-readiness evidence is blocked",
        evidence=[_display_path(path, root)],
        details={
            "status": payload.get("status") if isinstance(payload, dict) else None,
            "blocker_count": len(payload.get("blockers") or []) if isinstance(payload, dict) else None,
        },
    )


def _check_release_proof(root: Path) -> ShipCheck:
    path = _latest_evidence(root, "release-proof.json")
    if path is None:
        return ShipCheck(
            id="release-proof",
            status="PASS",
            severity="WARN",
            message="release-proof evidence is not present; no artifact handoff is claimed",
        )
    payload = _load_json(path)
    ok = isinstance(payload, dict) and payload.get("ok") is True and not payload.get("blockers")
    return ShipCheck(
        id="release-proof",
        status="PASS" if ok else "FAIL",
        severity="BLOCK_MERGE",
        message="release-proof evidence passed" if ok else "release-proof evidence is blocked",
        evidence=[_display_path(path, root)],
        details={
            "status": payload.get("status") if isinstance(payload, dict) else None,
            "blocker_count": len(payload.get("blockers") or []) if isinstance(payload, dict) else None,
        },
    )


def _check_closeout_honesty(root: Path) -> ShipCheck:
    path = root / ".signalos" / "product" / "CLOSEOUT.json"
    if not path.is_file():
        return ShipCheck(
            id="product-closeout-honesty",
            status="PASS",
            severity="WARN",
            message="product closeout is not present; no product closeout claim is checked",
        )
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return ShipCheck(
            id="product-closeout-honesty",
            status="FAIL",
            severity="BLOCK_MERGE",
            message=".signalos/product/CLOSEOUT.json is not valid JSON",
            evidence=[".signalos/product/CLOSEOUT.json"],
        )
    from signalos_lib.product.closeout import check_closeout_honesty

    result = check_closeout_honesty(payload)
    ok = bool(result.get("honest"))
    return ShipCheck(
        id="product-closeout-honesty",
        status="PASS" if ok else "FAIL",
        severity="BLOCK_MERGE",
        message="product closeout is honest" if ok else "product closeout overstates readiness",
        evidence=[".signalos/product/CLOSEOUT.json"],
        details={"issues": result.get("issues", [])},
    )


def _create_local_tag(root: Path, tag_name: str, *, actor: str, wave: str) -> dict[str, Any]:
    existing = _git(root, ["rev-parse", "-q", "--verify", f"refs/tags/{tag_name}"])
    if existing is not None and existing.returncode == 0:
        return {
            "requested": True,
            "created": False,
            "tag": tag_name,
            "commit": existing.stdout.strip() or None,
            "message": f"tag already exists: {tag_name}",
        }
    result = _git(
        root,
        ["tag", "-a", tag_name, "-m", f"SignalOS ship {wave} by {actor}"],
    )
    if result is None or result.returncode != 0:
        return {
            "requested": True,
            "created": False,
            "tag": tag_name,
            "commit": None,
            "message": (result.stderr.strip() if result else "git is unavailable") or "git tag failed",
        }
    commit = _git(root, ["rev-parse", tag_name])
    return {
        "requested": True,
        "created": True,
        "tag": tag_name,
        "commit": commit.stdout.strip() if commit is not None and commit.returncode == 0 else None,
        "message": "local annotated tag created",
    }


def _append_ship_audit(root: Path, payload: dict[str, Any], *, action: str) -> None:
    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    trail.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": _utc_now(),
        "actor": payload["actor"],
        "action": action,
        "wave": payload["wave"],
        "status": payload["status"],
        "tag": payload["tag"]["tag"],
        "tag_created": payload["tag"]["created"],
        "ok": payload["ok"],
        "checks": [
            {"id": check["id"], "status": check["status"], "severity": check["severity"]}
            for check in payload["checks"]
        ],
        "blockers": payload["blockers"],
    }
    with trail.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _latest_evidence(root: Path, filename: str) -> Path | None:
    base = root / ".signalos" / "evidence"
    if not base.is_dir():
        return None
    candidates = sorted(
        base.glob(f"*/{filename}"),
        key=lambda path: (path.stat().st_mtime_ns, path.as_posix()),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git_user(root: Path) -> str | None:
    result = _git(root, ["config", "user.name"])
    if result is None or result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


def _is_agent_signature(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    tokens = set(normalized.split())
    return bool(tokens.intersection(AGENT_SELF_SIGNATURES)) or normalized in AGENT_SELF_SIGNATURES


def normalize_wave_segment(value: str | int, *, default: str = "W00") -> str:
    """Normalize a wave id into a single canonical evidence-path segment.

    Shared by ``ship`` and ``release_proof`` so that the same numeric wave
    always resolves to the same ``.signalos/evidence/<wave>/`` directory.
    Numeric waves (``1``, ``W1``, ``w01``) collapse to zero-padded ``W0N``;
    non-numeric waves are kept as a filesystem-safe segment.
    """
    raw = str(value).strip()
    if not raw:
        return default
    upper = raw.upper()
    candidate = upper[1:] if upper.startswith("W") else upper
    if candidate.isdigit():
        return f"W{int(candidate):02d}"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    return cleaned or default


def _normalize_wave(value: str | int) -> str:
    if not str(value).strip():
        raise ValueError("wave is required")
    return normalize_wave_segment(value)


def _format_tag(tag_format: str, wave_name: str) -> str:
    fmt = tag_format or DEFAULT_TAG_FORMAT
    tag = fmt.replace("{W}", wave_name).replace("{wave}", wave_name)
    if not tag.strip() or re.search(r"\s", tag):
        raise ValueError("tag format must produce a non-empty tag without whitespace")
    return tag


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _next_action(ok: bool, *, dry_run: bool, no_tag: bool, tag_name: str) -> str:
    if not ok:
        return "Resolve ship blockers and rerun signalos ship."
    if dry_run:
        return "Dry-run passed. Rerun without --dry-run to append audit and create the local tag."
    if no_tag:
        return "Ship confirmed without local tag. Create or publish release artifacts only after explicit operator approval."
    return f"Local tag {tag_name} created. Push or publish only after explicit operator approval."


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos ship",
        description="Verify Gate 5 ship readiness, optionally create a local tag, and append audit.",
    )
    parser.add_argument("wave", help="Wave id or number, e.g. 1 or W01.")
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-tag", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--tag-format", default=DEFAULT_TAG_FORMAT)
    parser.add_argument("--actor", default=None)
    parser.add_argument("--no-evidence", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        payload = ship_wave(
            repo_root=args.repo_root,
            wave=args.wave,
            dry_run=args.dry_run,
            no_tag=args.no_tag,
            allow_dirty=args.allow_dirty,
            tag_format=args.tag_format,
            actor=args.actor,
            write_evidence=not args.no_evidence,
        )
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"signalos ship: {exc}\n")
        return 2

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(payload)
    return 0 if payload.get("ok") else 1


def _print_human(payload: dict[str, Any]) -> None:
    print(f"SignalOS ship {payload['wave']}: {payload['status']}")
    for check in payload.get("checks", []):
        print(f"- {check['id']}: {check['status']} {check['message']}")
    if payload.get("tag", {}).get("created"):
        print(f"tag: {payload['tag']['tag']} {payload['tag'].get('commit') or ''}".rstrip())
    if payload.get("evidence_path"):
        print(f"evidence: {payload['evidence_path']}")
    if payload.get("blockers"):
        print("blockers:")
        for blocker in payload["blockers"]:
            print(f"- {blocker['id']}: {blocker['message']}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
