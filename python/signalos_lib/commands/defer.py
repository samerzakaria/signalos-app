"""`signalos defer` — count, reconcile, and harvest DEFER markers."""

from __future__ import annotations

__all__ = ["EXIT_UNRECONCILED", "main"]

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable


EXIT_UNRECONCILED = 100


# Match either the full `// TODO: ... — DEFER: ...` form enforced by the
# pre-commit hook, or the simpler `// DEFER:` / `# DEFER:` standalone form.
# We require the comment marker (`//` or `#`) to be at the start of the line
# (optionally preceded by whitespace) — that way string literals like
# "# DEFER: ..." inside source code (e.g. our own tests for this scanner)
# don't get harvested as real defers.
_DEFER_RE = re.compile(
    r"^\s*(?:#|//)\s*(?:TODO\s*:.*?[—\-]\s*)?DEFER\s*:\s*(?P<note>.*)",
    re.IGNORECASE,
)
_TARGET_WAVE_RE = re.compile(
    r"(?<![A-Z0-9_])(?P<wave>W\d+\+?)(?![A-Z0-9_])",
    re.IGNORECASE,
)

# Directories we never scan — they would dominate the count with vendored or
# generated content.
_SKIP_DIRS = frozenset({
    ".git", ".signalos", "node_modules", "__pycache__", ".venv", "venv",
    "target", "dist", "build", ".next", ".turbo", ".cache", "_bundle",
})

# File extensions we consider source — keeps the scan bounded and fast.
_SOURCE_SUFFIXES = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".rb", ".sh",
    ".java", ".kt", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".md",
    ".yaml", ".yml", ".toml",
})


@dataclass(frozen=True)
class DeferHit:
    """One DEFER marker location."""

    path: str  # POSIX-style path relative to repo root
    line: int
    note: str
    target_wave: str | None = None


def _iter_source_files(root: Path) -> Iterable[Path]:
    """Yield candidate source files under *root*, skipping known noise dirs."""

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        # Skip if any ancestor directory is in the skip set.
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in rel_parts[:-1]):
            continue
        yield path


def _extract_target_wave(note: str) -> str | None:
    match = _TARGET_WAVE_RE.search(note)
    if not match:
        return None
    return match.group("wave").upper()


def _scan_file(path: Path) -> list[tuple[int, str, str | None]]:
    """Return [(line_no, note, target_wave)] for every DEFER marker in *path*."""

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[tuple[int, str, str | None]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = _DEFER_RE.search(line)
        if not match:
            continue
        note = match.group("note").strip().rstrip("`").strip()
        hits.append((line_no, note, _extract_target_wave(note)))
    return hits


def _collect(root: Path) -> list[DeferHit]:
    """Scan *root* and return all DEFER hits grouped naturally by path."""

    out: list[DeferHit] = []
    for f in _iter_source_files(root):
        rel_posix = f.relative_to(root).as_posix()
        for line_no, note, target_wave in _scan_file(f):
            out.append(
                DeferHit(
                    path=rel_posix,
                    line=line_no,
                    note=note,
                    target_wave=target_wave,
                )
            )
    out.sort(key=lambda h: (h.path, h.line))
    return out


def _group_by_file(hits: list[DeferHit]) -> dict[str, list[dict[str, object]]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for hit in hits:
        item: dict[str, object] = {"line": hit.line, "note": hit.note}
        if hit.target_wave:
            item["target_wave"] = hit.target_wave
        groups.setdefault(hit.path, []).append(item)
    return groups


def _read_prd_defer_rows(root: Path) -> list[str]:
    path = root / ".signalos" / "PRD_TRACEABILITY.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if "DEFER" in line.upper()]


def _has_matching_prd_row(hit: DeferHit, prd_defer_rows: list[str]) -> bool:
    if not hit.target_wave:
        return False
    file_name = Path(hit.path).name
    target = hit.target_wave.upper()
    rel_path = hit.path.upper()
    file_name_upper = file_name.upper()
    for row in prd_defer_rows:
        normalized = row.upper()
        if (
            target in normalized
            or rel_path in normalized
            or file_name_upper in normalized
        ):
            return True
    return False


def _hit_payload(hit: DeferHit) -> dict[str, object]:
    payload: dict[str, object] = {
        "file": hit.path,
        "path": hit.path,
        "line": hit.line,
        "note": hit.note,
    }
    if hit.target_wave:
        payload["target_wave"] = hit.target_wave
    return payload


def _resolve_root(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    return Path.cwd().resolve()


def _audit_append(root: Path, action: str, payload: dict[str, object]) -> None:
    """Append one row to .signalos/AUDIT_TRAIL.jsonl. Best-effort."""

    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    try:
        trail.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": action,
            **payload,
        }
        with trail.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _cmd_count(args: argparse.Namespace) -> int:
    root = _resolve_root(args.repo_root)
    hits = _collect(root)
    groups = _group_by_file(hits)
    prd_defer_rows = _read_prd_defer_rows(root)
    targeted = [hit for hit in hits if hit.target_wave]
    unreconciled = [
        hit for hit in targeted if not _has_matching_prd_row(hit, prd_defer_rows)
    ]
    reconciled_count = len(targeted) - len(unreconciled)

    payload = {
        "schema_version": "signalos.defer.count.v1",
        "repo_root": str(root),
        "total": len(hits),
        "files": len(groups),
        "by_file": {path: items for path, items in sorted(groups.items())},
        "target_markers": len(targeted),
        "prd_defer_rows": len(prd_defer_rows),
        "reconciled_count": reconciled_count,
        "unreconciled_count": len(unreconciled),
        "markers": [_hit_payload(hit) for hit in targeted],
        "unreconciled_markers": [_hit_payload(hit) for hit in unreconciled],
    }

    if args.as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        if not hits:
            sys.stdout.write("No DEFER markers found.\n")
        else:
            sys.stdout.write(
                "defer count: "
                f"{len(targeted)} target marker(s) found, "
                f"{reconciled_count} reconciled with PRD, "
                f"{len(unreconciled)} unreconciled.\n"
            )
            sys.stdout.write(f"DEFER markers: {len(hits)} across {len(groups)} file(s)\n")
            for path, items in sorted(groups.items()):
                sys.stdout.write(f"  {path}  ({len(items)})\n")
                for item in items:
                    suffix = f" -> {item['target_wave']}" if item.get("target_wave") else ""
                    sys.stdout.write(f"    L{item['line']}: {item['note']}{suffix}\n")
            if unreconciled:
                sys.stdout.write("\n")
                sys.stdout.write(
                    "Unreconciled markers (no matching DEFER row in "
                    "PRD_TRACEABILITY.md):\n"
                )
                for hit in unreconciled:
                    sys.stdout.write(f"  {hit.path}:{hit.line} -> {hit.target_wave}\n")
                sys.stdout.write("\n")
                sys.stdout.write(
                    "HOW: Append a DEFER row to .signalos/PRD_TRACEABILITY.md "
                    "citing each marker, or remove the marker if no longer needed.\n"
                )
    return EXIT_UNRECONCILED if unreconciled else 0


def _wave_yaml_path(root: Path, wave: str) -> Path:
    return root / ".signalos" / "backlog" / f"wave-{wave}.yaml"


def _yaml_escape(text: str) -> str:
    """Escape a value for inclusion in a YAML double-quoted scalar."""

    return text.replace("\\", "\\\\").replace('"', '\\"')


def _render_backlog_yaml(wave: str, items: list[dict[str, object]]) -> str:
    """Render a backlog YAML compatible with backlog-schema.yaml's raw form.

    Raw items only need id, title, status:raw, wave, created — per the
    template at core/strategy/Templates/backlog-schema.yaml.
    """

    lines: list[str] = [
        f"# .signalos/backlog/wave-{wave}.yaml",
        "# Two-Speed Backlog — harvested DEFER markers (status: raw)",
        "",
        "backlog:",
        "",
    ]
    for item in items:
        lines.append(f'  - id: "{_yaml_escape(str(item["id"]))}"')
        lines.append(f'    title: "{_yaml_escape(str(item["title"]))}"')
        lines.append("    status: raw")
        lines.append(f"    wave: {wave}")
        lines.append(f'    created: "{item["created"]}"')
        lines.append(f'    source_path: "{_yaml_escape(str(item["source_path"]))}"')
        lines.append(f'    source_line: {item["source_line"]}')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _cmd_harvest(args: argparse.Namespace) -> int:
    root = _resolve_root(args.repo_root)
    wave = str(args.wave).strip()
    if not wave:
        sys.stderr.write("signalos defer harvest: --wave is required.\n")
        return 2

    hits = _collect(root)
    today = date.today().isoformat()
    items: list[dict[str, object]] = []
    for idx, hit in enumerate(hits, start=1):
        title = hit.note or f"DEFER from {hit.path}:{hit.line}"
        items.append({
            "id": f"wave-{wave}-defer-{idx:03d}",
            "title": title,
            "created": today,
            "source_path": hit.path,
            "source_line": hit.line,
        })

    out_path = _wave_yaml_path(root, wave)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_backlog_yaml(wave, items), encoding="utf-8")

    _audit_append(root, "defer-harvest", {
        "wave": wave,
        "count": len(items),
        "path": str(out_path.relative_to(root)) if out_path.is_relative_to(root) else str(out_path),
    })

    payload = {
        "schema_version": "signalos.defer.harvest.v1",
        "repo_root": str(root),
        "wave": wave,
        "harvested": len(items),
        "backlog_path": str(out_path),
    }

    if args.as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(
            f"Harvested {len(items)} DEFER marker(s) into {out_path}\n"
        )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos defer",
        description="Count, reconcile, and harvest // DEFER: markers into the wave backlog.",
    )
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    p_count = sub.add_parser(
        "count",
        help="Count DEFER markers and reconcile W<NN>+ targets with PRD traceability.",
    )
    p_count.add_argument("--repo-root", default=None, metavar="PATH")
    p_count.add_argument("--json", action="store_true", dest="as_json")

    p_harvest = sub.add_parser("harvest", help="Harvest DEFER markers into wave backlog.")
    p_harvest.add_argument("--wave", required=True, metavar="N")
    p_harvest.add_argument("--repo-root", default=None, metavar="PATH")
    p_harvest.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)

    if args.action == "count":
        return _cmd_count(args)
    if args.action == "harvest":
        return _cmd_harvest(args)

    parser.print_help(sys.stderr)
    return 2
