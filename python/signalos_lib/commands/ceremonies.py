"""Executable SignalOS ceremony commands for catalog protocol entries."""

from __future__ import annotations

__all__ = ["CEREMONY_COMMANDS", "main"]

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.artifacts import gate_artifact_rel_path

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_ARGS = 2


def _gate_artifact_path(root: Path, gate: str, label: str) -> Path:
    """Resolve a scaffold target from the canonical gate manifest so ceremony
    output always lands where the gate validator looks."""
    return root.joinpath(*gate_artifact_rel_path(gate, label).split("/"))

CEREMONY_COMMANDS = {
    "signal-discovery",
    "signal-onboard",
    "signal-pre-wave",
    "signal-review",
    "signal-wave-review",
    "signal-debrief",
}


def main(command: str, argv: list[str]) -> int:
    if command not in CEREMONY_COMMANDS:
        sys.stderr.write(f"unknown ceremony command: {command}\n")
        return EXIT_BAD_ARGS
    parser = _build_parser(command)
    args = parser.parse_args(argv)
    root = Path(args.repo_root or Path.cwd()).resolve()
    if not root.exists() or not root.is_dir():
        sys.stderr.write(f"{command}: repo-root not found: {root}\n")
        return EXIT_BAD_ARGS

    try:
        payload = _run(command, root, args)
    except OSError as exc:
        sys.stderr.write(f"{command}: {exc}\n")
        return EXIT_FAILED

    if args.as_json:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        files = ", ".join(payload.get("files", [])) or "no files"
        sys.stdout.write(f"{command}: {payload['status']} ({files})\n")
    return EXIT_OK if payload.get("ok", True) is not False else EXIT_FAILED


def _build_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"signalos {command}",
        description=f"Run the app-native {command} ceremony.",
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--wave", default=None)
    parser.add_argument("--actor", default="operator")
    parser.add_argument("--name", default=None, help="Product, ceremony, or stakeholder name.")
    parser.add_argument("--summary", default=None, help="Human summary to seed the artifact.")
    parser.add_argument("--verdict", default="pending", help="Review verdict or ceremony outcome.")
    parser.add_argument("--force", action="store_true", help="Overwrite ceremony artifacts when applicable.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--no-evidence", action="store_true")
    parser.add_argument("text", nargs="*", help="Optional free-form ceremony text.")
    return parser


def _run(command: str, root: Path, args: argparse.Namespace) -> dict[str, Any]:
    if command == "signal-discovery":
        return _signal_discovery(root, args)
    if command == "signal-onboard":
        return _signal_onboard(root, args)
    if command == "signal-pre-wave":
        return _signal_pre_wave(root, args)
    if command == "signal-review":
        return _signal_review(root, args)
    if command == "signal-wave-review":
        return _signal_wave_review(root, args)
    if command == "signal-debrief":
        return _signal_debrief(root, args)
    raise AssertionError(command)


def _signal_discovery(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    summary = _summary(args, "Discovery brief created from operator input.")
    stakeholder = args.name or "stakeholder"
    brief_dir = root / "core" / "strategy" / "discovery-briefs"
    target = brief_dir / f"wave-0-session-{_next_index(brief_dir, 'wave-0-session-*.md'):03d}.md"
    _write_text(target, "\n".join([
        "# Discovery Brief",
        "",
        f"Stakeholder: {stakeholder}",
        f"Captured: {_utc_now()}",
        f"Actor: {args.actor}",
        "",
        "## Summary",
        "",
        summary,
        "",
        "## Open Questions",
        "",
        "- Confirm problem priority with the PO.",
        "- Confirm measurable signal before onboarding.",
        "",
    ]), force=True)
    return _finish(root, "signal-discovery", args, [target], {"stakeholder": stakeholder})


def _signal_onboard(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    product = args.name or root.name
    summary = _summary(args, f"{product} is onboarded into SignalOS governance.")
    files = [
        _gate_artifact_path(root, "G0", "Soul Document"),
        _gate_artifact_path(root, "G0", "Surface Inventory"),
        _gate_artifact_path(root, "G0", "Permanently T3"),
        root / "core" / "execution" / "onboarding-report.md",
    ]
    _write_text(files[0], "\n".join([
        "# Soul Document",
        "",
        f"Product: {product}",
        f"Owner: {args.actor}",
        "",
        "## Mandate",
        "",
        summary,
        "",
    ]), force=args.force)
    _write_text(files[1], "# Surface Inventory\n\n- app: product surface pending classification\n", force=args.force)
    _write_text(files[2], "# Permanently T3 Surfaces\n\n- production secrets\n- release signing\n", force=args.force)
    _write_text(files[3], "\n".join([
        "# Onboarding Report",
        "",
        f"Product: {product}",
        f"Generated: {_utc_now()}",
        "",
        "## Read",
        "",
        "- Discovery briefs under core/strategy/discovery-briefs when present.",
        "- Existing repository files available to the operator.",
        "",
        "## Assumptions",
        "",
        "- PO must review and edit generated governance drafts before signing.",
        "",
    ]), force=args.force)
    decision = root / "core" / "governance" / "Governance" / "DECISION-DNA.md"
    _append_text(
        decision,
        f"\n- DEC-{_compact_time()} - Onboarded product {product} under SignalOS app ceremony - PO: {args.actor}\n",
    )
    files.append(decision)
    return _finish(root, "signal-onboard", args, files, {"product": product})


def _signal_pre_wave(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    wave = _normalize_wave(args.wave or "W01")
    summary = _summary(args, "Smallest testable build and measurable signal recorded.")
    active_wave = _read_wave_state(root)
    if active_wave and not args.force:
        return _blocked(
            root,
            "signal-pre-wave",
            args,
            reason=(
                ".signalos/wave.json already exists; rerun with --force to "
                "replace the active wave pointer"
            ),
            extra={"existing_wave": active_wave.get("wave"), "requested_wave": wave},
        )
    files = [
        root / ".signalos" / "wave.json",
        _gate_artifact_path(root, "G1", "Belief"),
        _gate_artifact_path(root, "G2", "Expectation Map"),
        _gate_artifact_path(root, "G1", "Role Activation Card"),
    ]
    _write_json(files[0], {"wave": wave, "status": "ACTIVE", "updated_at": _utc_now()})
    _write_text(files[1], f"# Belief\n\n## Problem\n\n{summary}\n\n## Signal\n\nDefine measurable threshold before build.\n", force=args.force)
    _write_text(files[2], f"# Expectation Map\n\nWave: {wave}\n\n| Expectation | Signal | Status |\n|---|---|---|\n| Smallest testable build | Define threshold | pending |\n", force=args.force)
    _write_text(files[3], f"# Role Activation Card\n\nWave: {wave}\n\n- PO: active\n- PE: active\n- QA: active\n- Observability: deferred until ship\n", force=args.force)
    return _finish(root, "signal-pre-wave", args, files, {"wave": wave})


def _signal_review(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    wave = _normalize_wave(args.wave or _read_wave(root) or "W01")
    verdict = args.verdict.upper()
    summary = _summary(args, "Review ceremony executed; quality evidence requires human confirmation.")
    files = [
        _gate_artifact_path(root, "G5", "Quality Check"),
        root / ".signalos" / "evidence" / wave / "signal-review.json",
    ]
    _write_text(files[0], f"# Quality Check\n\nWave: {wave}\nVerdict: {verdict}\n\n{summary}\n", force=args.force)
    _write_json(files[1], {
        "schema_version": "signalos.signal_review.v1",
        "wave": wave,
        "verdict": verdict,
        "summary": summary,
        "generated_at": _utc_now(),
    })
    if verdict in {"FAIL", "FAILED", "REJECTED", "BLOCKED"}:
        return _finish(
            root,
            "signal-review",
            args,
            files,
            {
                "ok": False,
                "status": "review-blocked",
                "wave": wave,
                "verdict": verdict,
                "reason": f"signal-review verdict is {verdict}",
            },
        )
    return _finish(root, "signal-review", args, files, {"wave": wave, "verdict": verdict})


def _signal_wave_review(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    wave = _normalize_wave(args.wave or _read_wave(root) or "W01")
    summary = _summary(args, "Client reactions and signal result captured for PO review.")
    files = [
        root / "core" / "governance" / "Governance" / "CLIENT-SIGNAL-LOG.md",
        root / "core" / "execution" / "WAVE_REVIEW.md",
    ]
    _append_text(files[0], f"\n## {wave} - {_utc_now()}\n\n{summary}\n")
    _write_text(files[1], f"# Wave Review\n\nWave: {wave}\n\n{summary}\n", force=args.force)
    return _finish(root, "signal-wave-review", args, files, {"wave": wave})


def _signal_debrief(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    wave = _normalize_wave(args.wave or _read_wave(root) or "W01")
    summary = _summary(args, "Wave debrief captured; next belief candidate requires PO approval.")
    files = [
        root / "core" / "execution" / "WAVE_DEBRIEF.md",
        root / "core" / "governance" / "Governance" / "RETROSPECTIVE.md",
    ]
    _write_text(files[0], f"# Wave Debrief\n\nWave: {wave}\n\n{summary}\n", force=args.force)
    _append_text(files[1], f"\n## {wave} Retrospective - {_utc_now()}\n\n{summary}\n")
    return _finish(root, "signal-debrief", args, files, {"wave": wave})


def _finish(
    root: Path,
    action: str,
    args: argparse.Namespace,
    files: list[Path],
    extra: dict[str, Any],
) -> dict[str, Any]:
    rel_files = [_display(path, root) for path in files]
    payload: dict[str, Any] = {
        "schema_version": "signalos.ceremony.v1",
        "status": "ceremony-recorded",
        "action": action,
        "actor": args.actor,
        "generated_at": _utc_now(),
        "files": rel_files,
        **extra,
    }
    if not args.no_evidence:
        evidence = root / ".signalos" / "evidence" / "ceremonies" / f"{action}.json"
        _write_json(evidence, payload)
        payload["evidence_path"] = _display(evidence, root)
    _append_audit(root, action, payload)
    return payload


def _blocked(
    root: Path,
    action: str,
    args: argparse.Namespace,
    *,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "signalos.ceremony.v1",
        "ok": False,
        "status": "ceremony-blocked",
        "action": action,
        "actor": args.actor,
        "reason": reason,
        "generated_at": _utc_now(),
        "files": [],
        **(extra or {}),
    }
    if not args.no_evidence:
        evidence = root / ".signalos" / "evidence" / "ceremonies" / f"{action}.json"
        _write_json(evidence, payload)
        payload["evidence_path"] = _display(evidence, root)
    _append_audit(root, action, payload)
    return payload


def _summary(args: argparse.Namespace, default: str) -> str:
    if args.summary and args.summary.strip():
        return args.summary.strip()
    text = " ".join(args.text).strip()
    return text or default


def _write_text(path: Path, text: str, *, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text if text.endswith("\n") else text + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_audit(root: Path, action: str, payload: dict[str, Any]) -> None:
    row = {
        "ts": _utc_now(),
        "action": action,
        "actor": payload.get("actor") or "operator",
        "detail": payload.get("status"),
        "files": payload.get("files", []),
    }
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _next_index(directory: Path, pattern: str) -> int:
    if not directory.exists():
        return 1
    return len(list(directory.glob(pattern))) + 1


def _read_wave(root: Path) -> str | None:
    path = root / ".signalos" / "wave.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("wave") or data.get("wave_id")
    return str(raw).strip() if raw else None


def _read_wave_state(root: Path) -> dict[str, Any] | None:
    path = root / ".signalos" / "wave.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"wave": None, "status": "invalid"}
    return data if isinstance(data, dict) else {"wave": None, "status": "invalid"}


def _normalize_wave(value: str) -> str:
    raw = str(value).strip().upper()
    if raw.startswith("W") and raw[1:].isdigit():
        return f"W{int(raw[1:]):02d}"
    if raw.isdigit():
        return f"W{int(raw):02d}"
    return raw or "W01"


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compact_time() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(Path(sys.argv[0]).stem, sys.argv[1:]))
