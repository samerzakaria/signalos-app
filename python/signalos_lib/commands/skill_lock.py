"""`signalos skill-lock` — verify the license-checked skill lockfile.

Governed, license-checked skill supply chain. Mirrors the argparse /
return-code / ``--repo-root`` / ``--json`` style of ``commands/bundle.py``
and composes with the existing SHA-256 + evidence + audit conventions.

Subcommands:
  * ``verify`` (default): run ``verify_skills_lock``, write evidence JSON,
    append an audit row, and return a NON-ZERO exit on ANY hash mismatch or
    license refusal (SignalOS enforces, never advises).
  * ``list``: show each locked skill id, source, license, and verdict.
"""

from __future__ import annotations

__all__ = [
    "EXIT_OK",
    "EXIT_LOCK_VIOLATION",
    "EXIT_BAD_ARGS",
    "EVIDENCE_REL_PATH",
    "PIN_EVIDENCE_REL_PATH",
    "AUDIT_REL_PATH",
    "AUDIT_PINNED",
    "AUDIT_PIN_BLOCKED",
    "main",
]

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.skills_lock import (
    SkillLockError,
    is_permissive,
    lockfile_path,
    load_lockfile,
    normalize_spdx,
    pin_skill,
    verify_skills_lock,
)

EXIT_OK = 0
EXIT_LOCK_VIOLATION = 1
EXIT_BAD_ARGS = 2

EVIDENCE_REL_PATH = Path(".signalos") / "evidence" / "skills" / "skills-lock.json"
PIN_EVIDENCE_REL_PATH = Path(".signalos") / "evidence" / "skills" / "skills-lock-pin.json"
AUDIT_REL_PATH = Path(".signalos") / "AUDIT_TRAIL.jsonl"

AUDIT_PASS = "skill-lock-verified"
AUDIT_BLOCK = "skill-lock-blocked"
AUDIT_PINNED = "skill-lock-pinned"
AUDIT_PIN_BLOCKED = "skill-lock-pin-blocked"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_audit(root: Path, action: str, payload: dict[str, Any]) -> None:
    audit = root / AUDIT_REL_PATH
    audit.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": _utc_now(), "action": action, **payload}
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_evidence(root: Path, payload: dict[str, Any]) -> Path:
    evidence = root / EVIDENCE_REL_PATH
    evidence.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["generated_at"] = _utc_now()
    evidence.write_text(
        json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence


def _run_verify(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path.cwd().resolve()
    installed_root = (
        Path(args.installed_root).expanduser().resolve()
        if getattr(args, "installed_root", None)
        else None
    )
    try:
        result = verify_skills_lock(root, args.lock, installed_root=installed_root)
    except SkillLockError as exc:
        sys.stderr.write(f"signalos skill-lock: {exc}\n")
        return EXIT_BAD_ARGS

    payload = result.to_dict()

    if not getattr(args, "no_evidence", False):
        _write_evidence(root, payload)

    action = AUDIT_PASS if result.ok else AUDIT_BLOCK
    _append_audit(root, action, {
        "lock_path": result.lock_path,
        "verdict": "verified" if result.ok else "blocked",
        "skill_count": len(result.skills),
        "refusal_count": len(result.refusals),
        "refusals": [
            {"skill_id": s.skill_id, "status": s.status, "reason": s.reason}
            for s in result.refusals
        ],
    })

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_verify_human(result)

    # Fail-closed: any mismatch or license refusal returns non-zero.
    return EXIT_OK if result.ok else EXIT_LOCK_VIOLATION


def _print_verify_human(result: Any) -> None:
    verdict = "verified" if result.ok else "BLOCKED"
    print(f"signalos skill-lock: {verdict}")
    print(f"lockfile: {result.lock_path}")
    print(f"skills: {len(result.skills)}  refusals: {len(result.refusals)}")
    for s in result.skills:
        mark = "ok " if s.ok else "REFUSED"
        lic = s.license_normalized or s.license or "(none)"
        print(f"  [{mark}] {s.skill_id} <{s.status}> license={lic}")
        if not s.ok and s.reason:
            print(f"          {s.reason}")


def _run_list(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path.cwd().resolve()
    path = lockfile_path(root, args.lock)
    try:
        data = load_lockfile(path)
    except SkillLockError as exc:
        sys.stderr.write(f"signalos skill-lock: {exc}\n")
        return EXIT_BAD_ARGS

    rows: list[dict[str, Any]] = []
    for skill_id in sorted(data["skills"].keys()):
        entry = data["skills"][skill_id]
        entry = entry if isinstance(entry, dict) else {}
        license_decl = entry.get("license")
        rows.append({
            "skill_id": skill_id,
            "source": str(entry.get("source", "")),
            "source_type": str(entry.get("source_type", "")),
            "license": str(license_decl or ""),
            "license_normalized": normalize_spdx(license_decl),
            "permitted": is_permissive(license_decl),
        })

    if args.as_json:
        print(json.dumps({"lock_path": str(path), "skills": rows}, indent=2, ensure_ascii=False))
        return EXIT_OK

    print(f"lockfile: {path}")
    for row in rows:
        verdict = "permitted" if row["permitted"] else "REFUSED"
        lic = row["license_normalized"] or row["license"] or "(none)"
        print(f"  {row['skill_id']}  source={row['source']}  license={lic}  [{verdict}]")
    return EXIT_OK


def _write_pin_evidence(root: Path, payload: dict[str, Any]) -> Path:
    evidence = root / PIN_EVIDENCE_REL_PATH
    evidence.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["generated_at"] = _utc_now()
    evidence.write_text(
        json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence


def _run_pin(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path.cwd().resolve()

    try:
        result = pin_skill(
            root,
            args.skill_id,
            args.from_path,
            source=args.source or "",
            source_type=args.source_type or "local",
            skill_path=args.skill_path or "",
            license_decl=args.license,
            lock_path=args.lock,
        )
    except SkillLockError as exc:
        sys.stderr.write(f"signalos skill-lock: {exc}\n")
        return EXIT_BAD_ARGS

    lic = result.license
    payload: dict[str, Any] = {
        "skill_id": result.skill_id,
        "ok": result.ok,
        "reason": result.reason,
        "sha256": result.sha256,
        "license": lic.spdx,
        "license_source": lic.source,
        "license_permitted": lic.permitted,
        "license_detail": lic.detail,
        "lock_path": str(lockfile_path(root, args.lock)),
        "entry": result.entry,
    }

    if not getattr(args, "no_evidence", False):
        _write_pin_evidence(root, payload)

    action = AUDIT_PINNED if result.ok else AUDIT_PIN_BLOCKED
    _append_audit(root, action, {
        "skill_id": result.skill_id,
        "verdict": "pinned" if result.ok else "blocked",
        "sha256": result.sha256,
        "license": lic.spdx,
        "license_source": lic.source,
        "reason": result.reason,
    })

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if result.ok:
            print(f"signalos skill-lock: pinned {result.skill_id}")
            print(f"  license={lic.spdx} ({lic.source})  sha256={result.sha256}")
        else:
            print(f"signalos skill-lock: REFUSED to pin {result.skill_id}")
            print(f"  {result.reason}")

    # Fail-closed: a refused pin returns non-zero and writes no lock entry.
    return EXIT_OK if result.ok else EXIT_LOCK_VIOLATION


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos skill-lock",
        description="Verify declared external skills against pinned hashes and a license policy.",
    )
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    p_verify = sub.add_parser("verify", help="Verify the skill lockfile (default).")
    _add_common(p_verify)
    p_verify.add_argument("--installed-root", default=None, metavar="PATH",
                          help="Root the installed skills resolve under (default: repo root).")
    p_verify.add_argument("--no-evidence", action="store_true",
                          help="Do not write the evidence JSON.")

    p_list = sub.add_parser("list", help="List locked skills and license verdicts.")
    _add_common(p_list)

    p_pin = sub.add_parser(
        "pin",
        help="Pin/acquire a skill into the lockfile (fail-closed on license policy).",
    )
    _add_common(p_pin)
    p_pin.add_argument("skill_id", metavar="ID", help="Skill id to pin.")
    p_pin.add_argument("--from", dest="from_path", required=True, metavar="PATH",
                       help="Local file or directory holding the skill content.")
    p_pin.add_argument("--source", default=None, metavar="URL-OR-PATH",
                       help="Recorded provenance (defaults to --from).")
    p_pin.add_argument("--source-type", default="local",
                       choices=["github", "local", "url"], dest="source_type",
                       help="Provenance kind (default: local).")
    p_pin.add_argument("--skill-path", default=None, dest="skill_path", metavar="PATH",
                       help="Workspace-relative path the skill resolves under.")
    p_pin.add_argument("--license", default=None, metavar="SPDX",
                       help="Explicit SPDX id (highest-precedence license source).")
    p_pin.add_argument("--no-evidence", action="store_true",
                       help="Do not write the evidence JSON.")

    return parser


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo-root", default=None, metavar="PATH")
    p.add_argument("--lock", default=None, metavar="PATH",
                   help="Lockfile path (default: .signalos/skills-lock.json).")
    p.add_argument("--json", action="store_true", dest="as_json")


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    action = args.action or "verify"
    if action == "verify":
        return _run_verify(args)
    if action == "list":
        return _run_list(args)
    if action == "pin":
        return _run_pin(args)
    parser.print_help(sys.stderr)
    return EXIT_BAD_ARGS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
