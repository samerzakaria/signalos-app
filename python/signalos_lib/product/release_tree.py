"""Canonical product-release trees shared by G4, G5, and Git finalization.

The release digest is deliberately a digest of *payload bytes*, not of the
working tree's convenient source subset.  In a Git workspace every tracked
non-governance path is payload (including tracked ``dist/`` output); addable
untracked paths are included unless they live in a dependency/cache directory.
This mirrors what the release commit is allowed to stage while excluding the
SignalOS control plane, whose signatures and checkpoints legitimately change
after G4 verification.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable


UNTRACKED_EXCLUDED_DIRS = frozenset({
    ".git", ".signalos", "node_modules", "vendor", ".venv", "venv",
    "dist", "build", "coverage", "target", ".next", ".nuxt", "out",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".cache", ".turbo",
})


class ReleaseTreeError(RuntimeError):
    """The exact release payload could not be read or represented."""


def _has_git_metadata(root: Path) -> bool:
    marker = root / ".git"
    return marker.is_file() or (marker.is_dir() and (marker / "HEAD").is_file())


def tree_digest(tree: dict[str, str]) -> str:
    canonical = json.dumps(
        tree, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalise_rel(raw: str) -> str:
    value = raw.replace("\\", "/").lstrip("/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ReleaseTreeError(f"unsafe release path: {raw!r}")
    return str(path)


def workspace_path(
    root: Path, rel: str, *, allow_leaf_symlink: bool = False,
) -> Path:
    """Return a lexical workspace path after rejecting redirected ancestors.

    Release inventory comes from Git and therefore crosses a filesystem trust
    boundary: a tracked path can have an intermediate directory replaced by a
    symlink or Windows junction after verification.  ``Path.relative_to`` only
    proves lexical containment and ``Path.read_bytes`` would then follow that
    redirect.  Walk every existing component and require its resolved location
    to be the same lexical location.  A final symlink may be allowed for product
    payloads because Git commits the link text itself; it is never followed.
    """
    workspace = Path(root).resolve()
    normalised = _normalise_rel(rel)
    cursor = workspace
    parts = PurePosixPath(normalised).parts
    for index, part in enumerate(parts):
        cursor = cursor / part
        is_leaf = index == len(parts) - 1
        # lexists semantics are needed for a broken symlink, which is still a
        # real Git payload entry whose link text can be committed safely.
        present = cursor.exists() or cursor.is_symlink()
        if not present:
            continue
        if is_leaf and allow_leaf_symlink and cursor.is_symlink():
            continue
        try:
            resolved = cursor.resolve()
        except OSError as exc:
            raise ReleaseTreeError(
                f"release path cannot be resolved safely: {normalised}"
            ) from exc
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise ReleaseTreeError(
                f"release path resolves outside the workspace: {normalised}"
            ) from exc
        resolved_name = os.path.normcase(os.path.abspath(str(resolved)))
        lexical_name = os.path.normcase(os.path.abspath(str(cursor)))
        if cursor.is_symlink() or resolved_name != lexical_name:
            raise ReleaseTreeError(
                f"release path traverses a symlink or junction: {normalised}"
            )
    return cursor


def is_governance_path(rel: str) -> bool:
    parts = PurePosixPath(_normalise_rel(rel)).parts
    if not parts:
        return True
    # .signalos is mutable run/audit state.  This repository's root core/
    # directory is the gate-document control plane and signatures change at G5.
    return parts[0].lower() in {".git", ".signalos", "core"}


def is_release_control_path(rel: str) -> bool:
    """Versioned governance evidence that belongs beside the product release."""
    parts = PurePosixPath(_normalise_rel(rel)).parts
    return bool(parts) and parts[0].lower() == "core"


def _is_untracked_payload(rel: str) -> bool:
    if is_governance_path(rel):
        return False
    return not any(
        part.lower() in UNTRACKED_EXCLUDED_DIRS
        for part in PurePosixPath(rel).parts
    )


def _run_git(root: Path, args: list[str], *, data: bytes | None = None,
             timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(root), input=data, capture_output=True,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseTreeError(f"git {' '.join(args)} failed: {exc}") from exc


def _git_paths(root: Path, args: list[str]) -> set[str]:
    proc = _run_git(root, args)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace")[:300]
        raise ReleaseTreeError(detail or f"git {' '.join(args)} failed")
    paths: set[str] = set()
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        paths.add(_normalise_rel(raw.decode("utf-8", "surrogateescape")))
    return paths


def git_tracked_release_paths(root: Path) -> set[str]:
    """Tracked payload paths, including paths currently deleted on disk."""
    if not _has_git_metadata(root):
        return set()
    return {
        rel for rel in _git_paths(root, ["ls-files", "--cached", "-z"])
        if not is_governance_path(rel)
    }


def git_tracked_control_paths(root: Path) -> set[str]:
    if not _has_git_metadata(root):
        return set()
    return {
        rel for rel in _git_paths(root, ["ls-files", "--cached", "-z"])
        if is_release_control_path(rel)
    }


def _hash_workspace_path(
    root: Path, rel: str, *, allow_leaf_symlink: bool = True,
) -> str:
    path = workspace_path(root, rel, allow_leaf_symlink=allow_leaf_symlink)
    try:
        if path.is_symlink():
            target = os.readlink(path)
            payload = ("symlink\0" + str(target)).encode(
                "utf-8", errors="surrogatepass",
            )
        elif path.is_file():
            payload = path.read_bytes()
        else:
            raise ReleaseTreeError(f"release path is not a regular file: {rel}")
    except OSError as exc:
        raise ReleaseTreeError(f"cannot read release path {rel}: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()


def workspace_release_tree(root: Path) -> dict[str, str]:
    """Return the exact current product payload tree.

    Git workspaces use Git's tracked/addable inventory.  That both includes a
    tracked build output under an otherwise excluded directory and excludes an
    ignored secret that ``git add`` would not ship.  Non-Git workspaces fall
    back to a conservative filesystem walk.
    """
    root = Path(root).resolve()
    if _has_git_metadata(root):
        tracked = _git_paths(root, ["ls-files", "--cached", "-z"])
        candidates = _git_paths(
            root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        )
        selected = {
            rel for rel in candidates
            if not is_governance_path(rel)
            and (rel in tracked or _is_untracked_payload(rel))
        }
    else:
        selected: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                    followlinks=False):
            current = Path(dirpath)
            rel_dir = current.relative_to(root)
            dirnames[:] = [
                name for name in sorted(dirnames)
                if _is_untracked_payload(
                    str(rel_dir / name).replace("\\", "/")
                )
            ]
            for name in sorted(filenames):
                rel = str(rel_dir / name).replace("\\", "/")
                if _is_untracked_payload(rel):
                    selected.add(_normalise_rel(rel))
            # os.walk does not yield symlinked directories as files.  Bind the
            # link itself without following it outside the workspace.
            for name in sorted(dirnames):
                path = current / name
                if path.is_symlink():
                    rel = str(rel_dir / name).replace("\\", "/")
                    selected.add(_normalise_rel(rel))
        # Root core is governance even when the fallback walk encountered it.
        selected = {rel for rel in selected if not is_governance_path(rel)}

    tree: dict[str, str] = {}
    for rel in sorted(selected):
        path = workspace_path(root, rel, allow_leaf_symlink=True)
        # A tracked deletion is intentionally absent from the current tree.
        if not path.exists() and not path.is_symlink():
            continue
        tree[rel] = _hash_workspace_path(root, rel)
    return tree


def workspace_control_tree(
    root: Path, allowed_paths: Iterable[str] | None = None,
) -> dict[str, str]:
    """Exact current versioned/addable ``core/**`` governance evidence."""
    root = Path(root).resolve()
    allowed = (
        {_normalise_rel(path) for path in allowed_paths}
        if allowed_paths is not None else None
    )
    if allowed is not None:
        selected = set(allowed)
    elif _has_git_metadata(root):
        selected = {
            rel for rel in _git_paths(
                root,
                ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            )
            if is_release_control_path(rel)
        }
    else:
        selected: set[str] = set()
        base = root / "core"
        if base.is_dir():
            for path in base.rglob("*"):
                if path.is_file() or path.is_symlink():
                    selected.add(_normalise_rel(
                        str(path.relative_to(root)).replace("\\", "/")
                    ))
    tree: dict[str, str] = {}
    for rel in sorted(selected):
        # Governance evidence is authority-bearing; unlike a product symlink,
        # even its final component may not redirect to another file.
        path = workspace_path(root, rel, allow_leaf_symlink=False)
        if not path.exists() and not path.is_symlink():
            continue
        tree[rel] = _hash_workspace_path(root, rel, allow_leaf_symlink=False)
    return tree


def _blob_hashes(root: Path, entries: Iterable[tuple[str, str, str]]) -> dict[str, str]:
    """Hash ``(path, mode, object-id)`` Git blob entries with a small cache."""
    cache: dict[tuple[str, str], str] = {}
    result: dict[str, str] = {}
    for rel, mode, oid in entries:
        key = (mode, oid)
        digest = cache.get(key)
        if digest is None:
            proc = _run_git(root, ["cat-file", "blob", oid])
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout).decode("utf-8", "replace")[:300]
                raise ReleaseTreeError(
                    f"cannot read Git blob for {rel}: {detail or oid}"
                )
            payload = proc.stdout
            if mode == "120000":
                payload = b"symlink\0" + payload
            elif mode == "160000":
                raise ReleaseTreeError(
                    f"Git submodule release paths are unsupported: {rel}"
                )
            digest = hashlib.sha256(payload).hexdigest()
            cache[key] = digest
        result[rel] = digest
    return result


def commit_release_tree(root: Path, commit: str = "HEAD") -> dict[str, str]:
    """Return the canonical payload tree stored in a Git commit."""
    proc = _run_git(root, ["ls-tree", "-rz", "--full-tree", commit])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace")[:300]
        raise ReleaseTreeError(detail or f"cannot inspect commit {commit}")
    entries: list[tuple[str, str, str]] = []
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        try:
            meta, raw_path = record.split(b"\t", 1)
            mode_b, type_b, oid_b = meta.split(b" ", 2)
            rel = _normalise_rel(raw_path.decode("utf-8", "surrogateescape"))
        except (ValueError, UnicodeError) as exc:
            raise ReleaseTreeError("malformed git ls-tree output") from exc
        if is_governance_path(rel):
            continue
        if type_b not in {b"blob", b"commit"}:
            raise ReleaseTreeError(f"unsupported Git object for release path: {rel}")
        entries.append((rel, mode_b.decode("ascii"), oid_b.decode("ascii")))
    return _blob_hashes(root, entries)


def commit_control_tree(
    root: Path, commit: str = "HEAD", allowed_paths: Iterable[str] | None = None,
) -> dict[str, str]:
    """Return the versioned ``core/**`` governance tree in a Git commit."""
    proc = _run_git(root, ["ls-tree", "-rz", "--full-tree", commit])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace")[:300]
        raise ReleaseTreeError(detail or f"cannot inspect commit {commit}")
    allowed = (
        {_normalise_rel(path) for path in allowed_paths}
        if allowed_paths is not None else None
    )
    entries: list[tuple[str, str, str]] = []
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        try:
            meta, raw_path = record.split(b"\t", 1)
            mode_b, type_b, oid_b = meta.split(b" ", 2)
            rel = _normalise_rel(raw_path.decode("utf-8", "surrogateescape"))
        except (ValueError, UnicodeError) as exc:
            raise ReleaseTreeError("malformed git ls-tree output") from exc
        if not (rel in allowed if allowed is not None else is_release_control_path(rel)):
            continue
        if type_b not in {b"blob", b"commit"}:
            raise ReleaseTreeError(f"unsupported Git object for control path: {rel}")
        entries.append((rel, mode_b.decode("ascii"), oid_b.decode("ascii")))
    return _blob_hashes(root, entries)


def index_release_tree(root: Path) -> dict[str, str]:
    """Return the canonical payload tree currently staged in Git's index."""
    proc = _run_git(root, ["ls-files", "-s", "-z"])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace")[:300]
        raise ReleaseTreeError(detail or "cannot inspect Git index")
    entries: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        try:
            meta, raw_path = record.split(b"\t", 1)
            mode_b, oid_b, stage_b = meta.split(b" ", 2)
            rel = _normalise_rel(raw_path.decode("utf-8", "surrogateescape"))
        except (ValueError, UnicodeError) as exc:
            raise ReleaseTreeError("malformed git ls-files output") from exc
        if is_governance_path(rel):
            continue
        if stage_b != b"0" or rel in seen:
            raise ReleaseTreeError(f"unmerged Git index entry in release path: {rel}")
        seen.add(rel)
        entries.append((rel, mode_b.decode("ascii"), oid_b.decode("ascii")))
    return _blob_hashes(root, entries)


def index_control_tree(
    root: Path, allowed_paths: Iterable[str] | None = None,
) -> dict[str, str]:
    """Return the versioned ``core/**`` governance tree in Git's index."""
    proc = _run_git(root, ["ls-files", "-s", "-z"])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace")[:300]
        raise ReleaseTreeError(detail or "cannot inspect Git index")
    allowed = (
        {_normalise_rel(path) for path in allowed_paths}
        if allowed_paths is not None else None
    )
    entries: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        try:
            meta, raw_path = record.split(b"\t", 1)
            mode_b, oid_b, stage_b = meta.split(b" ", 2)
            rel = _normalise_rel(raw_path.decode("utf-8", "surrogateescape"))
        except (ValueError, UnicodeError) as exc:
            raise ReleaseTreeError("malformed git ls-files output") from exc
        if not (rel in allowed if allowed is not None else is_release_control_path(rel)):
            continue
        if stage_b != b"0" or rel in seen:
            raise ReleaseTreeError(f"unmerged Git index control entry: {rel}")
        seen.add(rel)
        entries.append((rel, mode_b.decode("ascii"), oid_b.decode("ascii")))
    return _blob_hashes(root, entries)


def git_release_pathspec(root: Path, current_tree: dict[str, str]) -> list[str]:
    """All current plus tracked payload paths, including tracked deletions."""
    return sorted(set(current_tree) | git_tracked_release_paths(Path(root).resolve()))


def git_control_pathspec(
    root: Path,
    current_tree: dict[str, str],
    allowed_paths: Iterable[str] | None = None,
) -> list[str]:
    if allowed_paths is None:
        tracked = git_tracked_control_paths(Path(root).resolve())
    else:
        allowed = {_normalise_rel(path) for path in allowed_paths}
        tracked = git_tracked_release_paths(Path(root).resolve()) & allowed
        # git_tracked_release_paths excludes .signalos/core governance by
        # definition, so inspect the raw tracked inventory for explicit paths.
        if _has_git_metadata(Path(root).resolve()):
            tracked |= _git_paths(
                Path(root).resolve(), ["ls-files", "--cached", "-z"],
            ) & allowed
    return sorted(set(current_tree) | tracked)
