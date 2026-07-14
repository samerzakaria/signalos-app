#!/usr/bin/env python3
"""Synchronize and verify the three universal consult-panel engines."""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import tempfile
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


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _assert_regular_target(path: Path) -> None:
    if path.is_symlink() or _is_reparse_point(path):
        raise OSError(f"refusing symlink/reparse-point consult-panel target: {path}")
    if path.exists() and not path.is_file():
        raise OSError(f"consult-panel target is not a regular file: {path}")


def _stage_bytes(target: Path, payload: bytes, mode: int) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if hashlib.sha256(temporary.read_bytes()).digest() != hashlib.sha256(payload).digest():
            raise OSError(f"staged consult-panel verification failed: {target}")
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def synchronize(
    canonical: Path,
    targets: Iterable[Path],
    *,
    write: bool,
) -> tuple[bool, list[dict[str, str]]]:
    canonical = canonical.resolve()
    if not canonical.is_file():
        raise FileNotFoundError(f"canonical panel engine not found: {canonical}")
    canonical_bytes = canonical.read_bytes()
    canonical_hash = hashlib.sha256(canonical_bytes).hexdigest()
    canonical_mode = canonical.stat().st_mode & 0o777
    normalized_targets: list[Path] = []
    seen: set[str] = set()
    for raw_target in targets:
        target = _absolute_without_resolving(raw_target)
        marker = os.path.normcase(str(target))
        if marker in seen:
            raise OSError(f"duplicate consult-panel target: {target}")
        if os.path.normcase(str(canonical)) == marker:
            raise OSError("canonical consult-panel engine cannot also be a sync target")
        seen.add(marker)
        _assert_regular_target(target)
        normalized_targets.append(target)

    if write:
        staged: dict[Path, Path] = {}
        originals: dict[Path, bytes | None] = {}
        applied: list[Path] = []
        try:
            # Stage and verify every destination before replacing any of them.
            for target in normalized_targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                _assert_regular_target(target)
                originals[target] = target.read_bytes() if target.exists() else None
                staged[target] = _stage_bytes(target, canonical_bytes, canonical_mode)
            for target in normalized_targets:
                _assert_regular_target(target)
                os.replace(staged[target], target)
                applied.append(target)
            for target in normalized_targets:
                if digest(target) != canonical_hash:
                    raise OSError(f"post-install consult-panel verification failed: {target}")
        except Exception as install_error:
            rollback_errors: list[str] = []
            for target in reversed(applied):
                try:
                    original = originals[target]
                    if original is None:
                        target.unlink(missing_ok=True)
                    else:
                        rollback = _stage_bytes(target, original, canonical_mode)
                        try:
                            os.replace(rollback, target)
                        finally:
                            rollback.unlink(missing_ok=True)
                except Exception as rollback_error:
                    rollback_errors.append(f"{target}: {rollback_error}")
            if rollback_errors:
                raise OSError(
                    f"{install_error}; rollback also failed: {'; '.join(rollback_errors)}"
                ) from install_error
            raise
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)

    rows: list[dict[str, str]] = []
    all_match = True
    for target in normalized_targets:
        _assert_regular_target(target)
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
