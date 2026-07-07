"""Evidence freshness binding (mechanical-verification Layer 2).

SignalOS's promise is constant quality per contract; today the pipeline
proves "we built something that runs", but nothing proves the evidence was
STILL TRUE at delivery. This module binds the validation/proof evidence to
the exact bytes that were verified:

- ``snapshot_workspace`` hashes the generated product files (the generation
  manifest's on-disk files + package.json etc., never ``.signalos/**``
  itself) into a ``workspace_snapshot`` payload that the pipeline attaches
  to the VALIDATION_RESULT and runtime-proof artifacts.
- ``verify_workspace_snapshot`` re-hashes at closeout and reports drift
  (changed / added / removed generated files after proof). The delivery
  pipeline folds a non-fresh verdict through the same ``gate-compliance``
  rule-mode resolution the review gate uses: strict blocks (closure_level
  downgraded to "partial"), warn records the drift in known_limitations.

Snapshot points sit AFTER the last evidence-producing activity (see the
comments at the call sites in ``delivery.run_delivery``) so the repair
loop's legitimate file rewrites between validation cycles never
false-positive.
"""

from __future__ import annotations

__all__ = [
    "snapshot_workspace",
    "verify_workspace_snapshot",
    "workspace_snapshot_files",
    "workspace_scan_files",
    "write_freshness_report",
    "load_freshness_report",
]

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA_VERSION = "signalos.workspace_snapshot.v1"
FRESHNESS_SCHEMA_VERSION = "signalos.evidence_freshness.v1"
SNAPSHOT_ALGO = "sha256"

# The evidence store itself, VCS internals, and installed dependencies are
# never part of the delivered product bytes -- excluding .signalos/** also
# prevents the snapshot from invalidating itself when later evidence is
# written.
_EXCLUDED_PREFIXES = (".signalos/", ".git/", "node_modules/")

# Top-level files the built/proved product depends on beyond the manifest's
# own records ("package.json etc." -- a post-proof dependency edit is just as
# stale as a post-proof source edit).
_DEFAULT_EXTRA_FILES = (
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "requirements.txt",
    "index.html",
)

# The generated source roots swept by the added-file scan (added-detection
# blind-spot fix): an arbitrary file written after proof OUTSIDE the
# manifest-derived candidate set must still be visible. Scoped to the roots
# the generators actually emit into -- never the whole workspace -- to keep
# the false-positive discipline.
_SCAN_ROOTS = ("src", "tests", "test", "public")

# Directory names excluded from the added-file scan. Each entry exists for a
# specific false-positive it would otherwise cause:
#   node_modules  -- installed dependencies, not product bytes; `npm install`
#                    during proof would flag thousands of "added" files.
#   dist / build  -- build outputs; the proof run itself legitimately
#                    (re)generates them after the snapshot.
#   coverage      -- test-run byproduct written by the validation/proof runs.
#   .signalos     -- the evidence store; later evidence writes must never
#                    invalidate the snapshot (self-invalidation guard).
#   .git          -- VCS internals, never part of the delivered product.
#   __pycache__ / .pytest_cache -- Python bytecode/test caches written by the
#                    validation runs themselves.
#   .cache / .turbo / .next / .vite / .parcel-cache -- bundler/toolchain
#                    caches written as a side effect of building/serving.
_SCAN_EXCLUDED_DIRS = frozenset({
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".signalos",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".cache",
    ".turbo",
    ".next",
    ".vite",
    ".parcel-cache",
})

# File suffixes excluded from the added-file scan:
#   .map -- sourcemaps emitted next to build outputs by the proof build; they
#           appear/refresh whenever the bundler runs, not when a human or
#           agent writes product source.
_SCAN_EXCLUDED_SUFFIXES = (".map",)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _normalize(rel_path: str) -> str:
    return str(rel_path).replace("\\", "/").lstrip("/")


def _is_snapshot_candidate(rel_path: str) -> bool:
    return bool(rel_path) and not rel_path.startswith(_EXCLUDED_PREFIXES)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_snapshot_files(
    repo_root: Path,
    manifest: dict[str, Any] | None,
) -> list[str]:
    """The candidate file list for a workspace snapshot.

    The generation manifest's on-disk files plus the default top-level
    config/dependency files, with ``.signalos/**``, ``.git/**`` and
    ``node_modules/**`` always excluded. Only files that exist are returned;
    the same derivation is used at capture and at verification time so
    "added" drift is detectable.
    """
    seen: set[str] = set()
    out: list[str] = []
    for record in (manifest or {}).get("files", []) or []:
        if not isinstance(record, dict):
            continue
        rel = _normalize(str(record.get("path") or ""))
        if not _is_snapshot_candidate(rel) or rel in seen:
            continue
        seen.add(rel)
        if (repo_root / rel).is_file():
            out.append(rel)
    for rel in _DEFAULT_EXTRA_FILES:
        if rel not in seen and (repo_root / rel).is_file():
            seen.add(rel)
            out.append(rel)
    return sorted(out)


def workspace_scan_files(repo_root: Path) -> list[str]:
    """Scoped sweep of the generated source roots for added-file detection.

    Returns every file under ``src/``, ``tests/``, ``test/``, ``public/``
    plus the root config files in ``_DEFAULT_EXTRA_FILES``, as sorted
    POSIX-style rel paths -- minus the documented exclusions
    (``_SCAN_EXCLUDED_DIRS`` / ``_SCAN_EXCLUDED_SUFFIXES``).

    This closes the added-file blind spot: an arbitrary file written after
    proof used to be invisible unless it happened to appear in the
    manifest-derived candidate set. The scan runs at BOTH capture and
    verification time (capture records it as the presence baseline), so
    pre-existing non-manifest files never false-positive as "added".
    """
    root = Path(repo_root)
    out: set[str] = set()
    for scan_root in _SCAN_ROOTS:
        base = root / scan_root
        if not base.is_dir():
            continue
        stack = [base]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                try:
                    if entry.is_dir():
                        if name in _SCAN_EXCLUDED_DIRS:
                            continue
                        stack.append(entry)
                        continue
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                if name.lower().endswith(_SCAN_EXCLUDED_SUFFIXES):
                    continue
                rel = _normalize(entry.relative_to(root).as_posix())
                if _is_snapshot_candidate(rel):
                    out.add(rel)
    for rel in _DEFAULT_EXTRA_FILES:
        if (root / rel).is_file():
            out.add(rel)
    return sorted(out)


def snapshot_workspace(
    repo_root: Path,
    files: list[str],
) -> dict[str, Any]:
    """Hash *files* under *repo_root* into a workspace snapshot.

    Returns ``{"algo": "sha256", "captured_at": ..., "files": {rel: hash},
    "scanned": [rel, ...]}``. Missing/unreadable files are skipped (the
    snapshot records what could be verified, honestly); ``.signalos/**`` is
    never hashed.

    ``scanned`` is the presence baseline for the added-file scan (additive
    key, same schema version): the files that existed under the generated
    source roots at capture time. Verification treats anything the scan
    finds beyond this baseline (and beyond the hashed set) as post-proof
    "added" drift.
    """
    repo_root = Path(repo_root)
    hashes: dict[str, str] = {}
    for rel in files or []:
        rel_n = _normalize(str(rel))
        if not _is_snapshot_candidate(rel_n):
            continue
        target = repo_root / rel_n
        if not target.is_file():
            continue
        try:
            hashes[rel_n] = _hash_file(target)
        except OSError:
            continue
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "algo": SNAPSHOT_ALGO,
        "captured_at": _utc_now(),
        "files": hashes,
        "scanned": workspace_scan_files(repo_root),
    }


def verify_workspace_snapshot(
    repo_root: Path,
    snapshot: dict[str, Any] | None,
    current_files: list[str],
) -> dict[str, Any]:
    """Re-hash the workspace and compare against *snapshot*.

    - ``changed``: in the snapshot, still on disk, hash differs.
    - ``removed``: in the snapshot, no longer on disk (or unreadable).
    - ``added``:   in *current_files* (same derivation as capture time) and
                   on disk, but absent from the snapshot; PLUS anything the
                   scoped source-root scan (``workspace_scan_files``) finds
                   beyond both the snapshot's hashed set and its ``scanned``
                   presence baseline. The scan-widened check only runs when
                   the snapshot carries a baseline -- an old snapshot without
                   one cannot distinguish pre-existing files from post-proof
                   writes, so it keeps the original (narrower) semantics
                   rather than false-positive.

    ``fresh`` is True only when all three lists are empty.
    """
    repo_root = Path(repo_root)
    snap_files = (snapshot or {}).get("files", {}) or {}
    changed: list[str] = []
    removed: list[str] = []
    for rel, expected in snap_files.items():
        target = repo_root / rel
        if not target.is_file():
            removed.append(rel)
            continue
        try:
            actual = _hash_file(target)
        except OSError:
            removed.append(rel)
            continue
        if actual != expected:
            changed.append(rel)
    added_set = {
        rel_n
        for rel_n in (_normalize(str(rel)) for rel in current_files or [])
        if _is_snapshot_candidate(rel_n)
        and rel_n not in snap_files
        and (repo_root / rel_n).is_file()
    }
    scan_baseline = (snapshot or {}).get("scanned")
    if isinstance(scan_baseline, list):
        baseline = {_normalize(str(rel)) for rel in scan_baseline}
        added_set.update(
            rel
            for rel in workspace_scan_files(repo_root)
            if rel not in snap_files and rel not in baseline
        )
    added = sorted(added_set)
    fresh = not (changed or added or removed)
    return {
        "schema_version": FRESHNESS_SCHEMA_VERSION,
        "fresh": fresh,
        "algo": (snapshot or {}).get("algo", SNAPSHOT_ALGO),
        "snapshot_captured_at": (snapshot or {}).get("captured_at"),
        "checked_at": _utc_now(),
        "files_verified": len(snap_files),
        "changed": sorted(changed),
        "added": added,
        "removed": sorted(removed),
    }


def write_freshness_report(
    report: dict[str, Any],
    signalos_dir: Path,
) -> Path:
    """Write to ``.signalos/product/EVIDENCE_FRESHNESS.json``."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "EVIDENCE_FRESHNESS.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_freshness_report(signalos_dir: Path) -> dict[str, Any] | None:
    path = signalos_dir / "product" / "EVIDENCE_FRESHNESS.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
