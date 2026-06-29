"""`signalos bundle` - inspect and extract the embedded SignalOS bundle."""

from __future__ import annotations

__all__ = [
    "EXIT_BAD_ARGS",
    "EXIT_EMPTY_CATEGORY",
    "EXIT_OK",
    "KNOWN_CATEGORIES",
    "main",
]

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_EMPTY_CATEGORY = 3

_BUNDLE_ROOT = Path(__file__).resolve().parents[1] / "_bundle"

KNOWN_CATEGORIES: dict[str, tuple[tuple[str, str], ...]] = {
    "commands": (("core/execution/commands", ""),),
    "hooks": (
        ("core/execution/hooks", "execution"),
        ("integrations/hooks", "integrations"),
    ),
    "scripts": (
        ("core/execution/build", "build"),
        ("core/execution/worktree", "worktree"),
    ),
    "integrations": (("integrations", ""),),
    "prompts": (
        ("core/execution/agents", "agents"),
        ("core/execution/skills", "skills"),
        ("core/execution/plan", "plan"),
        ("core/execution/review", "review"),
    ),
    "build": (("core/execution/build", ""),),
    "quality": (
        ("core/governance/QA", "qa"),
        ("core/governance/Validators", "validators"),
        ("core/governance/Proof", "proof"),
    ),
    "journey": (
        ("core/governance/Journey", "journey"),
        ("core/observability", "observability"),
    ),
}


@dataclass(frozen=True)
class BundleEntry:
    category: str
    basename: str
    source: Path

    @property
    def display(self) -> str:
        return f"{self.category}/{self.basename}"


def _iter_files(base: Path) -> Iterable[Path]:
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*")):
        if path.is_file():
            yield path


def _category_entries(category: str) -> list[BundleEntry]:
    roots = KNOWN_CATEGORIES[category]
    entries: list[BundleEntry] = []
    for rel_root, prefix in roots:
        base = _BUNDLE_ROOT / rel_root
        for path in _iter_files(base):
            rel = path.relative_to(base).as_posix()
            basename = f"{prefix}/{rel}" if prefix else rel
            entries.append(BundleEntry(category=category, basename=basename, source=path))
    entries.sort(key=lambda entry: entry.basename)
    return entries


def _all_entries(category: str | None = None) -> list[BundleEntry]:
    if category is not None:
        if category not in KNOWN_CATEGORIES:
            raise KeyError(category)
        return _category_entries(category)
    entries: list[BundleEntry] = []
    for cat in KNOWN_CATEGORIES:
        entries.extend(_category_entries(cat))
    return entries


def _run_list(category: str | None, count: bool) -> int:
    try:
        entries = _all_entries(category)
    except KeyError:
        sys.stderr.write(
            f"bundle list: unknown category '{category}'. Known: "
            f"{', '.join(KNOWN_CATEGORIES)}\n"
        )
        return EXIT_BAD_ARGS

    if count:
        categories = [category] if category else list(KNOWN_CATEGORIES)
        empty = False
        for cat in categories:
            cat_count = len(_category_entries(cat))
            sys.stdout.write(f"{cat}: {cat_count}\n")
            if category is not None and cat_count == 0:
                empty = True
        if empty:
            sys.stderr.write(
                f"bundle list: known category '{category}' resolved to zero "
                "on-disk files; the bundle is missing or empty for this "
                "category.\n"
            )
            return EXIT_EMPTY_CATEGORY
        return EXIT_OK

    # A KNOWN but empty category is a real failure, not a silent success:
    # SignalOS enforces an explicit, non-zero refusal here.
    if category is not None and not entries:
        sys.stderr.write(
            f"bundle list: known category '{category}' resolved to zero "
            "on-disk files; the bundle is missing or empty for this "
            "category.\n"
        )
        return EXIT_EMPTY_CATEGORY

    for entry in entries:
        sys.stdout.write(entry.display + "\n")
    return EXIT_OK


def _run_extract(category: str, output: str) -> int:
    if category not in KNOWN_CATEGORIES:
        sys.stderr.write(
            f"bundle extract: unknown category '{category}'. Known: "
            f"{', '.join(KNOWN_CATEGORIES)}\n"
        )
        return EXIT_BAD_ARGS
    target = Path(output).expanduser().resolve()
    entries = _category_entries(category)
    if not entries:
        # A KNOWN but empty category must not silently report "extracted 0".
        sys.stderr.write(
            f"bundle extract: known category '{category}' resolved to zero "
            "on-disk files; nothing was extracted because the bundle is "
            "missing or empty for this category.\n"
        )
        return EXIT_EMPTY_CATEGORY
    count = 0
    for entry in entries:
        destination = target / entry.basename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.source, destination)
        count += 1
    sys.stdout.write(f"extracted {count} file(s) from category '{category}' to {target}\n")
    return EXIT_OK


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos bundle",
        description="Inspect or extract the embedded SignalOS framework bundle.",
    )
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    p_list = sub.add_parser("list", help="List embedded bundle files.")
    p_list.add_argument("--category", default=None)
    p_list.add_argument("--count", action="store_true")

    p_extract = sub.add_parser("extract", help="Extract a bundle category.")
    p_extract.add_argument("--category", required=True)
    p_extract.add_argument("--output", required=True)

    args = parser.parse_args(argv)
    if args.action == "list":
        return _run_list(args.category, args.count)
    if args.action == "extract":
        return _run_extract(args.category, args.output)

    parser.print_help(sys.stderr)
    return EXIT_BAD_ARGS
