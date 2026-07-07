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


def snapshot_workspace(
    repo_root: Path,
    files: list[str],
) -> dict[str, Any]:
    """Hash *files* under *repo_root* into a workspace snapshot.

    Returns ``{"algo": "sha256", "captured_at": ..., "files": {rel: hash}}``.
    Missing/unreadable files are skipped (the snapshot records what could be
    verified, honestly); ``.signalos/**`` is never hashed.
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
                   on disk, but absent from the snapshot.

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
    added = sorted({
        rel_n
        for rel_n in (_normalize(str(rel)) for rel in current_files or [])
        if _is_snapshot_candidate(rel_n)
        and rel_n not in snap_files
        and (repo_root / rel_n).is_file()
    })
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
