"""`signalos fleet` — governed agent fleet runtime (foundation).

Detect the agent CLIs SignalOS can dispatch to, list registered/detected
runtimes, and garbage-collect isolated task workspaces under
``.signalos/fleet/tasks/``. Mirrors the argparse / return-code /
``--repo-root`` / ``--json`` style of ``commands/skill_lock.py`` and composes
with the existing evidence + audit conventions.

Subcommands:
  * ``detect`` (default): print detected runtimes, write evidence to
    ``.signalos/evidence/fleet/runtimes.json``.
  * ``list``: show the persisted/detected runtime records.
  * ``gc``: TTL-prune task workspaces, write evidence + an audit row. GC is
    housekeeping, not a gate, so it returns non-zero ONLY on a real error.

The live agent CLI executor is roadmap (see
``docs/GOVERNED_FLEET_RUNTIME_DESIGN.md``); this command is the governed
foundation around it.
"""

from __future__ import annotations

__all__ = [
    "EXIT_OK",
    "EXIT_ERROR",
    "EXIT_BAD_ARGS",
    "RUNTIMES_EVIDENCE_REL_PATH",
    "GC_EVIDENCE_REL_PATH",
    "AUDIT_REL_PATH",
    "AUDIT_GC",
    "main",
]

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.fleet_runtime import detect_runtimes, gc_task_workspaces

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BAD_ARGS = 2

RUNTIMES_EVIDENCE_REL_PATH = Path(".signalos") / "evidence" / "fleet" / "runtimes.json"
GC_EVIDENCE_REL_PATH = Path(".signalos") / "evidence" / "fleet" / "gc.json"
AUDIT_REL_PATH = Path(".signalos") / "AUDIT_TRAIL.jsonl"

DEFAULT_TASKS_ROOT = Path(".signalos") / "fleet" / "tasks"

AUDIT_GC = "fleet-gc"

# Conservative defaults (seconds): done tasks live 1 day, orphans 7 days,
# heavy artifacts 1 hour.
DEFAULT_DONE_TTL_S = 24 * 60 * 60
DEFAULT_ORPHAN_TTL_S = 7 * 24 * 60 * 60
DEFAULT_ARTIFACT_TTL_S = 60 * 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _repo_root(args: argparse.Namespace) -> Path:
    if getattr(args, "repo_root", None):
        return Path(args.repo_root).expanduser().resolve()
    return Path.cwd().resolve()


def _append_audit(root: Path, action: str, payload: dict[str, Any]) -> None:
    audit = root / AUDIT_REL_PATH
    audit.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": _utc_now(), "action": action, **payload}
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_evidence(root: Path, rel_path: Path, payload: dict[str, Any]) -> Path:
    evidence = root / rel_path
    evidence.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["generated_at"] = _utc_now()
    evidence.write_text(
        json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence


# ---------------------------------------------------------------------------
# detect / list
# ---------------------------------------------------------------------------

def _run_detect(args: argparse.Namespace, *, write_evidence: bool) -> int:
    root = _repo_root(args)
    runtimes = detect_runtimes()
    detected = [r for r in runtimes if r["detected"]]

    payload = {
        "runtimes": runtimes,
        "detected_count": len(detected),
        "total_count": len(runtimes),
    }

    if write_evidence and not getattr(args, "no_evidence", False):
        _write_evidence(root, RUNTIMES_EVIDENCE_REL_PATH, payload)

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_runtimes_human(runtimes)
    return EXIT_OK


def _print_runtimes_human(runtimes: list[dict[str, Any]]) -> None:
    detected = sum(1 for r in runtimes if r["detected"])
    print(f"signalos fleet: {detected}/{len(runtimes)} runtime(s) detected")
    for r in runtimes:
        mark = "found  " if r["detected"] else "missing"
        exe = r["executable"] or f"({r['cli']} not on PATH)"
        print(f"  [{mark}] {r['id']:<16} <{r['kind']}>  {exe}")


# ---------------------------------------------------------------------------
# gc
# ---------------------------------------------------------------------------

def _run_gc(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    tasks_root = (
        Path(args.tasks_root).expanduser()
        if getattr(args, "tasks_root", None)
        else root / DEFAULT_TASKS_ROOT
    )
    if not tasks_root.is_absolute():
        tasks_root = (root / tasks_root).resolve()

    now_ts = args.now_ts if getattr(args, "now_ts", None) is not None else time.time()

    try:
        summary = gc_task_workspaces(
            tasks_root,
            now_ts=now_ts,
            done_ttl_s=args.done_ttl,
            orphan_ttl_s=args.orphan_ttl,
            artifact_ttl_s=args.artifact_ttl,
        )
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"signalos fleet gc: {exc}\n")
        return EXIT_ERROR

    if not getattr(args, "no_evidence", False):
        _write_evidence(root, GC_EVIDENCE_REL_PATH, summary)

    _append_audit(root, AUDIT_GC, {
        "tasks_root": str(tasks_root),
        "scanned": summary["scanned"],
        "removed_tasks": len(summary["removed_tasks"]),
        "pruned_artifacts": len(summary["pruned_artifacts"]),
        "kept_tasks": len(summary["kept_tasks"]),
        "errors": len(summary["errors"]),
    })

    if args.as_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        _print_gc_human(summary)

    # GC is housekeeping, not a gate: only a real error (failed removal) fails.
    return EXIT_ERROR if summary["errors"] else EXIT_OK


def _print_gc_human(summary: dict[str, Any]) -> None:
    print(f"signalos fleet gc: scanned {summary['scanned']} task workspace(s)")
    print(f"  removed tasks    : {len(summary['removed_tasks'])}")
    print(f"  pruned artifacts : {len(summary['pruned_artifacts'])}")
    print(f"  kept tasks       : {len(summary['kept_tasks'])}")
    for removed in summary["removed_tasks"]:
        print(f"    - removed {removed['task']} ({removed['reason']})")
    for pruned in summary["pruned_artifacts"]:
        print(f"    - pruned {pruned['task']}/{pruned['artifact']}")
    if summary["errors"]:
        print(f"  errors: {len(summary['errors'])}")
        for e in summary["errors"]:
            print(f"    ! {e['path']}: {e['error']}")


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos fleet",
        description=(
            "Governed agent fleet runtime: detect agent CLIs, list runtimes, "
            "and garbage-collect isolated task workspaces."
        ),
    )
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    p_detect = sub.add_parser("detect", help="Detect agent CLI runtimes on PATH (default).")
    _add_common(p_detect)
    p_detect.add_argument("--no-evidence", action="store_true",
                          help="Do not write the runtimes evidence JSON.")

    p_list = sub.add_parser("list", help="List detected runtime records.")
    _add_common(p_list)

    p_gc = sub.add_parser("gc", help="TTL-prune task workspaces (housekeeping, not a gate).")
    _add_common(p_gc)
    p_gc.add_argument("--tasks-root", default=None, metavar="PATH",
                      help="Task workspaces root (default: .signalos/fleet/tasks).")
    p_gc.add_argument("--done-ttl", type=float, default=DEFAULT_DONE_TTL_S, metavar="SECS",
                      help="TTL for done/idle task dirs (seconds).")
    p_gc.add_argument("--orphan-ttl", type=float, default=DEFAULT_ORPHAN_TTL_S, metavar="SECS",
                      help="TTL for orphan dirs without a .gc_meta.json (seconds).")
    p_gc.add_argument("--artifact-ttl", type=float, default=DEFAULT_ARTIFACT_TTL_S, metavar="SECS",
                      help="TTL for heavy artifact dirs (node_modules/.next/.turbo) (seconds).")
    p_gc.add_argument("--now-ts", type=float, default=None, metavar="EPOCH",
                      help="Override 'now' epoch seconds (for deterministic runs).")
    p_gc.add_argument("--no-evidence", action="store_true",
                      help="Do not write the GC evidence JSON.")

    return parser


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo-root", default=None, metavar="PATH")
    p.add_argument("--json", action="store_true", dest="as_json")


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    action = args.action or "detect"
    if action == "detect":
        return _run_detect(args, write_evidence=True)
    if action == "list":
        # `list` reports without (re)writing the primary detect evidence.
        return _run_detect(args, write_evidence=False)
    if action == "gc":
        return _run_gc(args)
    parser.print_help(sys.stderr)
    return EXIT_BAD_ARGS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
