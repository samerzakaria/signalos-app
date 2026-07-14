#!/usr/bin/env python3
"""Synchronize and verify the three universal consult-panel engines."""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL = REPO_ROOT / "python" / "signalos_lib" / "panel.py"


def installed_targets(home: Path) -> list[Path]:
    return [
        home / ".codex" / "skills" / "consult-panel" / "scripts" / "panel.py",
        home / ".claude" / "skills" / "consult-panel" / "panel.py",
    ]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synchronize(
    canonical: Path,
    targets: Iterable[Path],
    *,
    write: bool,
) -> tuple[bool, list[dict[str, str]]]:
    canonical = canonical.resolve()
    if not canonical.is_file():
        raise FileNotFoundError(f"canonical panel engine not found: {canonical}")
    canonical_hash = digest(canonical)
    canonical_bytes = canonical.read_bytes()
    rows: list[dict[str, str]] = []
    all_match = True
    for raw_target in targets:
        target = raw_target.expanduser().resolve()
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            try:
                temporary.write_bytes(canonical_bytes)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        if not target.is_file():
            status = "missing"
            target_hash = ""
            all_match = False
        else:
            target_hash = digest(target)
            status = "match" if target_hash == canonical_hash else "drift"
            all_match = all_match and status == "match"
        rows.append(
            {
                "path": str(target),
                "status": status,
                "sha256": target_hash,
                "canonical_sha256": canonical_hash,
            }
        )
    return all_match, rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy or verify the canonical consult-panel engine."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Fail if an installed copy differs")
    action.add_argument("--write", action="store_true", help="Replace installed copies, then verify")
    parser.add_argument("--canonical", type=Path, default=CANONICAL)
    parser.add_argument(
        "--target",
        action="append",
        type=Path,
        default=[],
        help="Explicit target path; repeat for multiple copies",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help="Home directory used for default Codex and Claude targets",
    )
    args = parser.parse_args(argv)
    targets = args.target or installed_targets(args.home)
    try:
        matched, rows = synchronize(args.canonical, targets, write=args.write)
    except OSError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    for row in rows:
        print(f"{row['status'].upper():7} {row['sha256'] or '-':64} {row['path']}")
    if not matched:
        print("ERROR: consult-panel engine copies are not identical", file=sys.stderr)
        return 1
    print(f"OK: all copies match {rows[0]['canonical_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
