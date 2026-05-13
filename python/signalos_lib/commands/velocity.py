# cli/signalos_lib/commands/velocity.py — W11 CLI wrappers (AMD-CORE-032)
# Argparse wrappers for signal-autoplan and signal-context-restore.
# Zero business logic — delegates to signalos_lib.velocity.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.velocity import (
    AutoPlanTask,
    CheckpointEntry,
    DocDriftEntry,
    autoplan,
    autoplan_load,
    checkpoint_list,
    checkpoint_restore,
    checkpoint_save,
    detect_doc_drift,
)

__all__ = ["cmd_signal_autoplan", "cmd_signal_context_restore"]


# ---------------------------------------------------------------------------
# signal-autoplan
# ---------------------------------------------------------------------------

def _build_autoplan_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos signal-autoplan",
        description="Auto-generate PLAN task list from a feature description (W11, AMD-CORE-032).",
    )
    sub = parser.add_subparsers(dest="sub", metavar="SUBCOMMAND")

    p_gen = sub.add_parser("generate", help="Parse description -> structured AutoPlanTask list")
    p_gen.add_argument("description", help="Feature description (newline-delimited lines become tasks)")
    p_gen.add_argument("--wave", required=True, metavar="W", help="Wave label (e.g. 11)")
    p_gen.add_argument("--repo-root", default=None, metavar="PATH", help="Repo root path")
    p_gen.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON array")

    p_list = sub.add_parser("list", help="List saved tasks for a wave")
    p_list.add_argument("--wave", required=True, metavar="W", help="Wave label")
    p_list.add_argument("--repo-root", default=None, metavar="PATH", help="Repo root path")
    p_list.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON array")

    return parser


def cmd_signal_autoplan(args: list[str]) -> int:
    parser = _build_autoplan_parser()
    if not args:
        parser.print_help(sys.stderr)
        return 1

    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    if ns.sub is None:
        parser.print_help(sys.stderr)
        return 1

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()

    if ns.sub == "generate":
        tasks = autoplan(ns.description, ns.wave, root)
        if ns.as_json:
            sys.stdout.write(json.dumps([t.as_dict() for t in tasks], ensure_ascii=False) + "\n")
        else:
            for task in tasks:
                sys.stdout.write(f"[{task.id}] {task.title}\n")
        return 0

    if ns.sub == "list":
        tasks = autoplan_load(ns.wave, root)
        if ns.as_json:
            sys.stdout.write(json.dumps([t.as_dict() for t in tasks], ensure_ascii=False) + "\n")
        else:
            for task in tasks:
                sys.stdout.write(f"[{task.id}] {task.title}\n")
        return 0

    parser.print_help(sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# signal-context-restore
# ---------------------------------------------------------------------------

def _build_context_restore_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos signal-context-restore",
        description="Checkpoint save/restore and doc drift detection (W11, AMD-CORE-032).",
    )
    sub = parser.add_subparsers(dest="sub", metavar="SUBCOMMAND")

    p_save = sub.add_parser("save", help="Save a context checkpoint")
    p_save.add_argument("--wave", required=True, metavar="W", help="Wave label")
    p_save.add_argument("--label", required=True, metavar="L", help="Checkpoint label")
    p_save.add_argument("--context", required=True, metavar="PATH", help="Path to context file")
    p_save.add_argument("--note", default="", metavar="N", help="Optional note")
    p_save.add_argument("--repo-root", default=None, metavar="PATH", help="Repo root path")
    p_save.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON")

    p_list = sub.add_parser("list", help="List checkpoints")
    p_list.add_argument("--wave", default=None, metavar="W", help="Filter by wave")
    p_list.add_argument("--repo-root", default=None, metavar="PATH", help="Repo root path")
    p_list.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON array")

    p_restore = sub.add_parser("restore", help="Restore a checkpoint to output path")
    p_restore.add_argument("checkpoint_id", help="Checkpoint ID (e.g. ckpt-001)")
    p_restore.add_argument("--out", required=True, metavar="PATH", help="Output path")
    p_restore.add_argument("--repo-root", default=None, metavar="PATH", help="Repo root path")
    p_restore.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON")

    p_drift = sub.add_parser("drift", help="Detect stale documentation")
    p_drift.add_argument("--docs-dir", default="docs", metavar="D", help="Docs directory")
    p_drift.add_argument("--max-age-days", default=30.0, type=float, metavar="N",
                         help="Max age in days before stale (default: 30)")
    p_drift.add_argument("--repo-root", default=None, metavar="PATH", help="Repo root path")
    p_drift.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON array")

    return parser


def cmd_signal_context_restore(args: list[str]) -> int:
    parser = _build_context_restore_parser()
    if not args:
        parser.print_help(sys.stderr)
        return 1

    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    if ns.sub is None:
        parser.print_help(sys.stderr)
        return 1

    root = Path(ns.repo_root) if ns.repo_root else Path.cwd()

    if ns.sub == "save":
        entry = checkpoint_save(root, ns.wave, ns.label, ns.context, ns.note)
        if ns.as_json:
            sys.stdout.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(f"{entry.id}\n")
        return 0

    if ns.sub == "list":
        entries = checkpoint_list(root, wave=ns.wave)
        if ns.as_json:
            sys.stdout.write(
                json.dumps([e.as_dict() for e in entries], ensure_ascii=False) + "\n"
            )
        else:
            for e in entries:
                sys.stdout.write(f"[{e.id}] wave={e.wave} label={e.label}\n")
        return 0

    if ns.sub == "restore":
        output_path = Path(ns.out)
        found = checkpoint_restore(root, ns.checkpoint_id, output_path)
        if ns.as_json:
            sys.stdout.write(json.dumps({"found": found, "id": ns.checkpoint_id}) + "\n")
        else:
            if found:
                sys.stdout.write(f"restored {ns.checkpoint_id} -> {output_path}\n")
            else:
                sys.stderr.write(f"checkpoint not found: {ns.checkpoint_id}\n")
        return 0 if found else 1

    if ns.sub == "drift":
        drift_entries = detect_doc_drift(root, docs_dir=ns.docs_dir, max_age_days=ns.max_age_days)
        if ns.as_json:
            sys.stdout.write(
                json.dumps([e.as_dict() for e in drift_entries], ensure_ascii=False) + "\n"
            )
        else:
            for e in drift_entries:
                sys.stdout.write(f"[{e.status}] {e.file} ({e.note})\n")
        return 0

    parser.print_help(sys.stderr)
    return 1
