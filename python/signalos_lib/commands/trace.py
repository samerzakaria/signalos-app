"""`signalos trace` - link governance tickets to source/proof files."""

from __future__ import annotations

__all__ = ["EXIT_BAD_ARGS", "EXIT_NO_MATCHES", "EXIT_OK", "main", "trace_ticket"]

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXIT_OK = 0
EXIT_NO_MATCHES = 100
EXIT_BAD_ARGS = 1

_SCHEMA_VERSION = "signalos.trace.ticket.v1"
_TICKET_ID_RE = re.compile(r"^T-W\d+-\d{3}$")
_SCAN_DIRS = ("src", "test", "tests", "proof", ".signalos", "core")
_SOURCE_SUFFIXES = {
    ".cs",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".sh",
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".py",
    ".rs",
    ".go",
}
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "bin",
    "obj",
    "node_modules",
    "target",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".cache",
}

# Generated SignalOS outputs under `.signalos/`. Counting ticket-id mentions
# inside these inflates traceability: they are produced BY the trace/proof
# pipeline, not authored governance inputs. We skip these specifically while
# still counting genuine inputs like `.signalos/backlog`, `.signalos/waves`,
# PRD, and traceability files.
#
# Each entry is a POSIX-style path prefix relative to the repo root. A
# candidate file is skipped when its relative path starts with one of these.
_GENERATED_SIGNALOS_PREFIXES = (
    ".signalos/evidence",
    ".signalos/proof",
    ".signalos/product/proof",
    ".signalos/audit",
    ".signalos/handoffs",
    ".signalos/agent-runs",
    ".signalos/product/agent-runs",
)

# Specific generated artifact files under `.signalos/` (exact relative paths).
_GENERATED_SIGNALOS_FILES = (
    ".signalos/AUDIT_TRAIL.jsonl",
)


def _is_generated_signalos_output(rel_posix: str) -> bool:
    """True when *rel_posix* is a generated `.signalos` output, not an input."""

    if rel_posix in _GENERATED_SIGNALOS_FILES:
        return True
    for prefix in _GENERATED_SIGNALOS_PREFIXES:
        if rel_posix == prefix or rel_posix.startswith(prefix + "/"):
            return True
    return False


@dataclass(frozen=True)
class TicketHit:
    path: str
    line: int
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return {
            "file": self.path,
            "path": self.path,
            "line": self.line,
            "snippet": self.snippet,
        }


def _resolve_root(repo_root: str | Path | None) -> Path:
    return Path(repo_root).expanduser().resolve() if repo_root else Path.cwd().resolve()


def _iter_candidate_files(root: Path) -> Iterable[Path]:
    for dirname in _SCAN_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            parts = rel.parts
            if any(part in _SKIP_DIRS for part in parts[:-1]):
                continue
            if _is_generated_signalos_output(rel.as_posix()):
                continue
            yield path


def _snippet(line: str) -> str:
    stripped = line.strip()
    return stripped[:120] + "..." if len(stripped) > 120 else stripped


def _scan_references(root: Path, ticket_id: str) -> list[TicketHit]:
    hits: list[TicketHit] = []
    for path in _iter_candidate_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for idx, line in enumerate(lines, start=1):
            if ticket_id in line:
                hits.append(TicketHit(path=rel, line=idx, snippet=_snippet(line)))
    hits.sort(key=lambda hit: (hit.path, hit.line))
    return hits


def trace_ticket(
    ticket_id: str,
    *,
    repo_root: str | Path | None = None,
) -> tuple[int, dict[str, object]]:
    root = _resolve_root(repo_root)
    if not _TICKET_ID_RE.match(ticket_id or ""):
        return EXIT_BAD_ARGS, {
            "schema_version": _SCHEMA_VERSION,
            "ticket": ticket_id,
            "status": "bad_args",
            "error": (
                f"--id '{ticket_id}' is not valid; expected T-W<NN>-NNN "
                "(for example T-W04-001)"
            ),
        }
    if not root.exists():
        return EXIT_BAD_ARGS, {
            "schema_version": _SCHEMA_VERSION,
            "ticket": ticket_id,
            "status": "bad_args",
            "error": f"repo-root not found: {root}",
        }
    hits = _scan_references(root, ticket_id)
    code = EXIT_OK if hits else EXIT_NO_MATCHES
    return code, {
        "schema_version": _SCHEMA_VERSION,
        "ticket": ticket_id,
        "status": "matched" if hits else "no_matches",
        "repo_root": str(root),
        "file_count": len({hit.path for hit in hits}),
        "reference_count": len(hits),
        "files": [hit.to_dict() for hit in hits],
    }


def _cmd_ticket(args: argparse.Namespace) -> int:
    code, payload = trace_ticket(args.ticket_id, repo_root=args.repo_root)
    if args.as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    elif code == EXIT_BAD_ARGS:
        sys.stderr.write(f"trace ticket: {payload.get('error')}\n")
    else:
        sys.stdout.write(
            f"trace ticket {payload['ticket']}: "
            f"{payload['reference_count']} reference(s) found.\n"
        )
        for item in payload["files"]:
            sys.stdout.write(
                f"  {item['file']}:{item['line']}: {item['snippet']}\n"
            )
    return code


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos trace",
        description="Trace governance artifacts to implementing files.",
    )
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    p_ticket = sub.add_parser("ticket", help="Find files mentioning a backlog ticket id.")
    p_ticket.add_argument("--id", required=True, dest="ticket_id")
    p_ticket.add_argument("--repo-root", default=None, metavar="PATH")
    p_ticket.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)
    if args.action == "ticket":
        return _cmd_ticket(args)

    parser.print_help(sys.stderr)
    return EXIT_BAD_ARGS
