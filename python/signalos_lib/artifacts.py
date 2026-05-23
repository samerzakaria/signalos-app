"""Shared SignalOS gate artifact definitions and path resolvers.

This module is the Python source of truth for gate artifact paths.  Existing
callers can keep using ``signalos_lib.sign.GATE_MAP``; that value is now derived
from the definitions here instead of being maintained separately.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath

__all__ = [
    "GATE_ARTIFACTS",
    "GATE_LABELS",
    "GATE_MAP",
    "GateArtifact",
    "ResolvedGateArtifact",
    "build_scope_card_code_map",
    "expected_gate_artifacts",
    "get_gate_label",
    "gate_artifact_map",
    "list_gates",
    "resolve_gate_artifacts",
    "resolve_workspace_path",
    "scope_card_code_map",
    "write_scope_card_code_map",
]


@dataclass(frozen=True)
class GateArtifact:
    """One required artifact for a governance gate."""

    gate: str
    rel_path: str
    required_roles: tuple[str, ...]
    label: str


@dataclass(frozen=True)
class ResolvedGateArtifact:
    """A gate artifact resolved under a specific workspace root."""

    gate: str
    rel_path: str
    required_roles: tuple[str, ...]
    label: str
    path: Path


def _load_manifest() -> tuple[
    dict[str, str],
    dict[str, tuple[GateArtifact, ...]],
    dict[str, tuple[str, ...]],
]:
    manifest_path = resources.files("signalos_lib").joinpath("gate_artifacts.json")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    labels = {str(k): str(v) for k, v in raw["gate_labels"].items()}
    gates: dict[str, tuple[GateArtifact, ...]] = {}
    for gate, entries in raw["gates"].items():
        gates[str(gate)] = tuple(
            GateArtifact(
                str(gate),
                str(entry["rel_path"]),
                tuple(str(role) for role in entry["required_roles"]),
                str(entry["label"]),
            )
            for entry in entries
        )
    raw_map = raw.get("scope_card_code_map", {}) or {}
    sc_map: dict[str, tuple[str, ...]] = {
        str(sc_id): tuple(str(p) for p in (paths or []))
        for sc_id, paths in raw_map.items()
    }
    return labels, gates, sc_map


GATE_LABELS, GATE_ARTIFACTS, _SCOPE_CARD_CODE_MAP = _load_manifest()


def gate_artifact_map() -> dict[str, list[tuple[str, list[str], str]]]:
    """Return a compatibility copy shaped like the historical ``GATE_MAP``."""

    return {
        gate: [
            (artifact.rel_path, list(artifact.required_roles), artifact.label)
            for artifact in artifacts
        ]
        for gate, artifacts in GATE_ARTIFACTS.items()
    }


GATE_MAP: dict[str, list[tuple[str, list[str], str]]] = gate_artifact_map()


def list_gates() -> list[str]:
    """Return known gate IDs in canonical order."""

    return list(GATE_ARTIFACTS.keys())


def get_gate_label(gate: str) -> str:
    """Return the display label for *gate*, falling back to the normalized ID."""

    normalized = gate.upper()
    return GATE_LABELS.get(normalized, normalized)


def expected_gate_artifacts(gate: str | None = None) -> list[GateArtifact]:
    """Return expected artifact specs for one gate or all gates."""

    if gate is None:
        return [artifact for artifacts in GATE_ARTIFACTS.values() for artifact in artifacts]
    return list(GATE_ARTIFACTS.get(gate.upper(), ()))


def resolve_workspace_path(repo_root: Path, rel_path: str) -> Path:
    """Resolve *rel_path* under *repo_root* and reject path escape attempts."""

    relative = _relative_path(rel_path)
    root = Path(repo_root).expanduser().resolve(strict=False)
    candidate = (root / relative).resolve(strict=False)
    if not _is_relative_to(candidate, root):
        raise ValueError(f"path escapes workspace root: {rel_path!r}")
    return candidate


def resolve_gate_artifacts(repo_root: Path, gate: str | None = None) -> list[ResolvedGateArtifact]:
    """Resolve expected gate artifacts under *repo_root* with escape checks."""

    resolved: list[ResolvedGateArtifact] = []
    for artifact in expected_gate_artifacts(gate):
        resolved.append(
            ResolvedGateArtifact(
                gate=artifact.gate,
                rel_path=artifact.rel_path,
                required_roles=artifact.required_roles,
                label=artifact.label,
                path=resolve_workspace_path(repo_root, artifact.rel_path),
            )
        )
    return resolved


# ─── Scope-card → code map (Phase 13) ────────────────────────────────────────
#
# A scope card is identified by an ``SC-NNN`` ID (case sensitive). Source files
# that implement a scope card mark themselves with a comment of the form
# ``// SC-NNN`` or ``# SC-NNN`` so we can derive a scope-card → file-path index
# from the codebase itself.
#
# The marker MUST live in a real comment, not inside a string literal. To keep
# false positives down we explicitly skip Python triple-quoted blocks; that
# behavior is documented in tests.
#
# Schema in ``gate_artifacts.json``::
#
#     "scope_card_code_map": {
#         "SC-001": ["python/foo.py", "src-tauri/src/bar.rs"],
#         ...
#     }
#
# Empty dict (``{}``) means "no markers yet" and is the default.
#
# TODO: pre-commit hook for SC-NNN format enforcement — DEFER: add to backlog
# as raw item. The current scanner is permissive (any ``SC-\d+`` works); a
# follow-up wave should add a hook entry that warns when a staged source file
# introduces an ``SC-NNN`` reference that does not match the canonical zero-
# padded three-digit form. Keeping it out of this commit so the scanner and
# the Layer 1 release-readiness consumer can land independently.

# Match a comment-prefixed ``SC-NNN`` token only when the comment marker is
# the FIRST non-whitespace thing on its line. That excludes accidental hits
# inside single/double-quoted string literals (``"# SC-001"``,
# ``'// SC-002'``) while still allowing every real annotation style.
#
# Group 1 is the comment kind (``#`` or ``//``); group 2 is the SC ID.
# Trailing ``\b`` after ``\d+`` keeps ``SC-12345`` as one ID rather than
# matching two overlapping prefixes.
_SC_MARKER_RE = re.compile(r"(?m)^[ \t]*(#|//)\s*(SC-\d+)\b")

# Directories never worth scanning. Mirrors the Rust ``list_workspace_dir``
# skip set so the two stay in sync visually.
_SCAN_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "target",
        "dist",
        "build",
        ".venv",
        "venv",
        ".sidecar-venv",
        "__pycache__",
        ".next",
        ".turbo",
        ".cache",
        ".signalos",
        ".idea",
        ".vscode",
    }
)

# File extensions where ``// SC-NNN`` or ``# SC-NNN`` could be a real comment.
_SCAN_EXTS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".swift",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cs",
        ".sh",
        ".bash",
    }
)

# Extensions where ``#`` is a line comment. Outside this set the scanner
# only treats ``// SC-NNN`` as a marker, so JSON/Markdown ``# heading`` text
# never accidentally claims a scope card.
_HASH_COMMENT_EXTS: frozenset[str] = frozenset(
    {".py", ".pyi", ".sh", ".bash"}
)

# Max bytes per file we are willing to read. Larger files are skipped silently
# rather than failing the whole scan; SC markers belong in source code.
_SCAN_MAX_BYTES = 1_500_000


def scope_card_code_map() -> dict[str, list[str]]:
    """Return the scope-card → file-list map currently stored in the manifest.

    Returned dict is a fresh copy; mutating it does not affect module state.
    """

    return {sc_id: list(paths) for sc_id, paths in _SCOPE_CARD_CODE_MAP.items()}


def build_scope_card_code_map(workspace_root: Path) -> dict[str, list[str]]:
    """Scan *workspace_root* for ``SC-NNN`` markers in source files.

    Returns an SC-NNN → sorted unique list of POSIX-style paths relative to
    *workspace_root*. Returns ``{}`` if no markers are found. Skips well-known
    build/cache directories and Python docstring blocks.
    """

    root = Path(workspace_root).expanduser().resolve(strict=False)
    if not root.is_dir():
        return {}

    found: dict[str, set[str]] = {}
    for path in _iter_scannable_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for sc_id in _extract_sc_markers(text, path.suffix.lower()):
            found.setdefault(sc_id, set()).add(rel)

    return {sc_id: sorted(paths) for sc_id, paths in sorted(found.items())}


def write_scope_card_code_map(
    workspace_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, list[str]]:
    """Build the scope-card map for *workspace_root* and persist it to the
    shared ``gate_artifacts.json`` manifest.

    If *manifest_path* is omitted the packaged manifest under ``signalos_lib``
    is used. The manifest's ``gate_labels`` and ``gates`` sections are left
    untouched; only ``scope_card_code_map`` is replaced. Returns the freshly
    built map.
    """

    sc_map = build_scope_card_code_map(workspace_root)
    target = (
        Path(manifest_path)
        if manifest_path is not None
        else Path(str(resources.files("signalos_lib").joinpath("gate_artifacts.json")))
    )
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["scope_card_code_map"] = sc_map
    target.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return sc_map


def _iter_scannable_files(root: Path):
    """Yield candidate source files under *root*, skipping noisy directories."""

    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            name = entry.name
            try:
                if entry.is_dir():
                    if name in _SCAN_SKIP_DIRS or name.startswith(".git"):
                        continue
                    stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            if entry.suffix.lower() not in _SCAN_EXTS:
                continue
            try:
                if entry.stat().st_size > _SCAN_MAX_BYTES:
                    continue
            except OSError:
                continue
            yield entry


def _extract_sc_markers(text: str, suffix: str) -> set[str]:
    """Return the set of ``SC-NNN`` IDs in *text* that live in real comments.

    Skips Python triple-quoted docstring regions so that example code embedded
    in docstrings does not generate false positives. ``#``-prefixed markers are
    only honored when *suffix* is a hash-comment language; otherwise only
    ``//`` markers are treated as valid.
    """

    accept_hash = suffix in _HASH_COMMENT_EXTS
    is_python = suffix in {".py", ".pyi"}

    if is_python:
        scrubbed = _strip_python_triple_quoted(text)
    else:
        scrubbed = text

    out: set[str] = set()
    for match in _SC_MARKER_RE.finditer(scrubbed):
        marker_kind = match.group(1)
        sc_id = match.group(2)
        if marker_kind == "#" and not accept_hash:
            continue
        out.add(sc_id)
    return out


def _strip_python_triple_quoted(text: str) -> str:
    """Replace the body of every ``\"\"\" ... \"\"\"`` / ``''' ... '''`` block
    with spaces so SC markers inside docstrings are not matched.

    This is a small state machine — not a full Python parser — but it is
    sufficient for the docstring-false-positive case the scanner cares about.
    Newlines are preserved so error reporting that counts lines still works
    for callers that consume the scrubbed text.
    """

    result: list[str] = []
    i = 0
    n = len(text)
    in_block: str | None = None  # which triple-quote opened the current block
    while i < n:
        ch = text[i]
        if in_block is None:
            triple = text[i : i + 3]
            if triple == '"""' or triple == "'''":
                in_block = triple
                result.append(triple)
                i += 3
                continue
            result.append(ch)
            i += 1
            continue
        # Inside a triple-quoted block: preserve newlines, blank everything else.
        triple = text[i : i + 3]
        if triple == in_block:
            result.append(triple)
            in_block = None
            i += 3
            continue
        result.append("\n" if ch == "\n" else " ")
        i += 1
    return "".join(result)


def _relative_path(rel_path: str) -> Path:
    text = str(rel_path).strip()
    if not text:
        raise ValueError("artifact path must not be empty")
    if "\x00" in text:
        raise ValueError("artifact path must not contain NUL bytes")
    if "\\" in text:
        raise ValueError(f"artifact path must use POSIX separators: {rel_path!r}")
    if len(text) >= 2 and text[1] == ":":
        raise ValueError(f"artifact path must be relative: {rel_path!r}")

    path = PurePosixPath(text)
    if path.is_absolute():
        raise ValueError(f"artifact path must be relative: {rel_path!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"artifact path contains unsafe segment: {rel_path!r}")
    return Path(*path.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
