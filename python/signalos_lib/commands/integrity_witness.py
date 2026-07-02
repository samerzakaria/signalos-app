"""App-native integrity witness for governance artifacts and hooks."""

from __future__ import annotations

__all__ = [
    "EXIT_BAD_ARGS",
    "EXIT_DRIFT",
    "EXIT_OK",
    "WITNESS_REL_PATH",
    "check_witness",
    "create_witness",
    "current_entries",
    "main",
]

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_BAD_ARGS = 2

SCHEMA_VERSION = "signalos.integrity_witness.v1"
WITNESS_REL_PATH = ".signalos/INTEGRITY_WITNESS.yaml"

BLOCKED_SIGNER_MARKERS = (
    "agent",
    "assistant",
    "automation",
    "bot",
    "claude",
    "codex",
    "copilot",
    "cursor",
    "github-actions",
    "llm",
)
ALLOWED_ROLES = {"po", "pe", "qa", "devops", "product-owner", "principal-engineer"}

INTEGRITY_CANDIDATES = (
    ".signalos/CONSTITUTION.md",
    ".signalos/SOUL-DOCUMENT.md",
    ".signalos/BELIEF_MAP.md",
    ".signalos/BELIEF_LITE.md",
    ".signalos/EXPECTATION_MAP.md",
    ".signalos/DESIGN_NOTE.md",
    ".signalos/TRUST_TIER.md",
    ".signalos/QUALITY_CHECK.md",
    ".signalos/PERMANENTLY_T3.md",
    ".signalos/DECISION-DNA.md",
    ".signalos/SURFACE_INVENTORY.md",
    ".signalos/AMENDMENTS.md",
    ".signalos/PRD_TRACEABILITY.md",
    ".signalos/TRACEABILITY_MATRIX.md",
    # Governed skill supply chain: watch the license-checked skill lockfile so
    # tampering with pinned hashes/licenses is drift-detected. Optional — only
    # included when the file is present (see current_entries' is_file guard).
    ".signalos/skills-lock.json",
    "core/governance/Governance/CONSTITUTION.md",
    "core/governance/Governance/SOUL-DOCUMENT.md",
    "core/governance/Governance/SURFACE_INVENTORY.md",
    "core/governance/Governance/PERMANENTLY_T3.md",
    "core/governance/DECISION-DNA.md",
    "core/governance/Retro/.constitution-hashes.log",
    "scripts/pre-push-security-gate.sh",
)


@dataclass(frozen=True)
class IntegrityEntry:
    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


def current_entries(repo_root: Path | str | None = None) -> list[IntegrityEntry]:
    root = _resolve_root(repo_root)
    entries: list[IntegrityEntry] = []
    for rel in INTEGRITY_CANDIDATES:
        path = root / rel
        if path.is_file():
            entries.append(IntegrityEntry(rel, _sha256_file(path)))

    hooks_dir = root / "core" / "execution" / "hooks"
    if hooks_dir.is_dir():
        for path in sorted(hooks_dir.iterdir()):
            if not path.is_file() or path.name.lower().startswith("install-hooks."):
                continue
            entries.append(IntegrityEntry(_display_path(path, root), _sha256_file(path)))
    return sorted(entries, key=lambda entry: entry.path)


def create_witness(
    repo_root: Path | str | None = None,
    *,
    actor: str,
    role: str,
    refresh: bool = False,
) -> dict[str, Any]:
    root = _resolve_root(repo_root)
    signer_error = _validate_signer(actor, role)
    if signer_error:
        raise ValueError(signer_error)

    entries = current_entries(root)
    canonical = "\n".join(f"{entry.path}|{entry.sha256}" for entry in entries)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "approved_by": actor.strip(),
        "approved_role": role.strip(),
        "canonical_sha256": _sha256_text(canonical),
        "entries": [entry.to_dict() for entry in entries],
    }
    witness = root / WITNESS_REL_PATH
    witness.parent.mkdir(parents=True, exist_ok=True)
    witness.write_text(_render_witness(payload), encoding="utf-8")
    action = "integrity-witness-refresh" if refresh else "integrity-witness-init"
    _append_audit(root, action, {
        "actor": actor.strip(),
        "role": role.strip(),
        "artifact": WITNESS_REL_PATH,
        "verdict": "approved",
        "entry_count": len(entries),
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "status": "ok",
        "action": action,
        "witness_path": WITNESS_REL_PATH,
        "entry_count": len(entries),
        "canonical_sha256": payload["canonical_sha256"],
    }


def check_witness(repo_root: Path | str | None = None) -> dict[str, Any]:
    root = _resolve_root(repo_root)
    witness = root / WITNESS_REL_PATH
    if not witness.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "status": "drift",
            "witness_path": WITNESS_REL_PATH,
            "issues": [
                "integrity witness is missing; run `signalos integrity-witness --init --actor <human> --role <role>`.",
            ],
            "entry_count": 0,
        }

    expected = _parse_witness(witness)
    current = {entry.path: entry.sha256 for entry in current_entries(root)}
    issues: list[str] = []
    for rel, sha in expected.items():
        actual = current.get(rel)
        if actual is None:
            issues.append(f"witnessed file missing or no longer included: {rel}")
        elif actual != sha:
            issues.append(f"hash mismatch: {rel}")
    for rel in sorted(path for path in current if path not in expected):
        issues.append(f"new integrity file not in witness: {rel}")

    # Wave 0.2: audit-ledger tamper-evidence. A break in the signed hash chain
    # (row edited in place, inserted, deleted, or reordered) is integrity drift,
    # surfaced alongside governance-file drift.
    audit_log = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if audit_log.is_file():
        try:
            from ..sign import verify_audit_chain

            for violation in verify_audit_chain(audit_log):
                issues.append(f"audit ledger tampering: {violation}")
        except Exception:  # pragma: no cover - verification must never crash the check
            pass

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not issues,
        "status": "ok" if not issues else "drift",
        "witness_path": WITNESS_REL_PATH,
        "issues": issues,
        "entry_count": len(expected),
        "current_entry_count": len(current),
    }


def _resolve_root(repo_root: Path | str | None) -> Path:
    root = Path(repo_root).expanduser().resolve() if repo_root else Path.cwd().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"repo-root not found: {root}")
    return root


def _validate_signer(actor: str | None, role: str | None) -> str | None:
    if not actor or not actor.strip():
        return "--actor is required for witness init/refresh."
    normalized_actor = actor.strip().lower()
    for marker in BLOCKED_SIGNER_MARKERS:
        if marker in normalized_actor:
            return f"actor '{actor}' matches blocked agent/bot signer markers."
    if not role or not role.strip():
        return "--role is required for witness init/refresh."
    if role.strip().lower() not in ALLOWED_ROLES:
        return "--role must be one of PO, PE, QA, DevOps, Product-Owner, Principal-Engineer."
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _render_witness(payload: dict[str, Any]) -> str:
    lines = [
        f"schema_version: {payload['schema_version']}",
        f"generated_at: {payload['generated_at']}",
        f"approved_by: {_escape_scalar(payload['approved_by'])}",
        f"approved_role: {_escape_scalar(payload['approved_role'])}",
        f"canonical_sha256: {payload['canonical_sha256']}",
        "entries:",
    ]
    for entry in payload["entries"]:
        lines.append(f"  - path: {_escape_scalar(entry['path'])}")
        lines.append(f"    sha256: {entry['sha256']}")
    return "\n".join(lines) + "\n"


def _parse_witness(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    current_path: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("- path:"):
            current_path = _unescape_scalar(line[len("- path:"):].strip())
        elif current_path is not None and line.startswith("sha256:"):
            sha = line[len("sha256:"):].strip()
            if current_path and len(sha) == 64:
                entries[current_path] = sha
            current_path = None
    return entries


def _escape_scalar(value: str) -> str:
    if ":" in value or "#" in value:
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _unescape_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"')
    return value


def _append_audit(root: Path, action: str, payload: dict[str, Any]) -> None:
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": _utc_now(), "action": action, **payload}
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos integrity-witness",
        description="Check, initialize, or refresh the governance integrity witness.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="Create the integrity witness.")
    mode.add_argument("--refresh", action="store_true", help="Refresh the witness after human approval.")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--actor", default=None, help="Human signer approving init/refresh.")
    parser.add_argument("--role", default=None, help="Human signer role.")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.init or args.refresh:
            payload = create_witness(
                args.repo_root,
                actor=args.actor,
                role=args.role,
                refresh=args.refresh,
            )
        else:
            payload = check_witness(args.repo_root)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"signalos integrity-witness: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGS

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif not args.quiet:
        _print_human(payload)
    return EXIT_OK if payload.get("ok") else EXIT_DRIFT


def _print_human(payload: dict[str, Any]) -> None:
    print(f"signalos integrity-witness: {payload['status']}")
    print(f"witness: {payload['witness_path']}")
    print(f"entries: {payload.get('entry_count', 0)}")
    for issue in payload.get("issues", []):
        print(f"- {issue}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
