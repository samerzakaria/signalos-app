"""Tests for app-native WorktreeSync snapshots."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from signalos_lib.cli import main as cli_main
from signalos_lib.worktree_sync import (
    WorktreeSyncError,
    get_worktree_snapshot,
    latest_worktree_snapshot,
    list_worktree_snapshots,
    load_worktree_snapshots,
    take_worktree_snapshot,
    validate_worktree_sync,
)


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def test_take_snapshot_persists_read_only_git_state(tmp_path: Path):
    repo = _init_repo(tmp_path)

    snapshot = take_worktree_snapshot(repo)

    assert snapshot["schema_version"] == "signalos.worktree_snapshot.v1"
    assert snapshot["commit_sha"] == _git(repo, "rev-parse", "HEAD")
    assert snapshot["branch"] == _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    assert snapshot["head_message"] == "initial"
    assert snapshot["dirty_file_count"] == 0
    assert (repo / ".signalos" / "worktree-sync" / "snapshots.jsonl").is_file()


def test_snapshot_counts_dirty_files_and_queries_latest_branch_commit(tmp_path: Path):
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "new.txt").write_text("untracked\n", encoding="utf-8")

    snapshot = take_worktree_snapshot(repo)
    latest = latest_worktree_snapshot(repo)
    by_id = get_worktree_snapshot(repo, snapshot["id"])
    by_branch = list_worktree_snapshots(repo, branch=snapshot["branch"])
    by_commit = list_worktree_snapshots(repo, commit_sha=snapshot["commit_sha"])

    assert snapshot["dirty_file_count"] == 2
    assert latest and latest["id"] == snapshot["id"]
    assert by_id and by_id["id"] == snapshot["id"]
    assert [row["id"] for row in by_branch] == [snapshot["id"]]
    assert [row["id"] for row in by_commit] == [snapshot["id"]]


def test_latest_is_deterministic_for_same_second_snapshots(tmp_path: Path):
    repo = _init_repo(tmp_path)

    first = take_worktree_snapshot(repo)
    second = take_worktree_snapshot(repo)

    # Back-to-back snapshots may share the same wall-clock taken_at; the
    # monotonic per-repo seq is the deterministic tiebreaker.
    assert second["seq"] == first["seq"] + 1
    latest = latest_worktree_snapshot(repo)
    assert latest is not None
    assert latest["id"] == second["id"]
    assert latest["seq"] == second["seq"]

    # Force a taken_at tie to prove seq alone breaks the order deterministically.
    snapshots = load_worktree_snapshots(repo)
    for row in snapshots:
        row["taken_at"] = "2026-06-29T00:00:00.000000Z"
    path = repo / ".signalos" / "worktree-sync" / "snapshots.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in snapshots) + "\n",
        encoding="utf-8",
    )
    forced_latest = latest_worktree_snapshot(repo)
    assert forced_latest is not None
    assert forced_latest["id"] == second["id"]


def test_dirty_count_excludes_signalos_bookkeeping(tmp_path: Path):
    repo = _init_repo(tmp_path)

    # Mutate ONLY a file under .signalos/. This is intentional bookkeeping
    # divergence: SignalOS persists snapshots in-tree but does not count its
    # own JSONL as a dirty product change (see _is_signalos_bookkeeping_status).
    bookkeeping = repo / ".signalos" / "worktree-sync"
    bookkeeping.mkdir(parents=True, exist_ok=True)
    (bookkeeping / "marker.jsonl").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "-A")

    # Sanity: git itself sees the .signalos change as dirty.
    porcelain = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    assert ".signalos" in porcelain

    snapshot = take_worktree_snapshot(repo, persist=False)
    assert snapshot["dirty_file_count"] == 0


def test_validate_worktree_sync_can_require_clean_and_write_evidence(tmp_path: Path):
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    result = validate_worktree_sync(repo, require_clean=True)

    assert result["ok"] is False
    assert result["status"] == "FAIL"
    assert result["snapshot"]["dirty_file_count"] == 1
    assert {blocker["kind"] for blocker in result["blockers"]} == {"dirty-worktree"}
    assert result["evidence_path"]
    assert Path(result["evidence_path"]).is_file()


def test_rejects_non_git_repository(tmp_path: Path):
    with pytest.raises(WorktreeSyncError, match="git rev-parse --show-toplevel failed"):
        take_worktree_snapshot(tmp_path)


def test_worktree_snapshot_cli_and_validator(tmp_path: Path, capsys):
    repo = _init_repo(tmp_path)

    rc = cli_main(
        [
            "signalos",
            "worktree-snapshot",
            "take",
            "--repo-root",
            str(repo),
            "--json",
        ]
    )
    assert rc == 0
    snapshot_payload = json.loads(capsys.readouterr().out)
    assert snapshot_payload["snapshot"]["head_message"] == "initial"

    rc = cli_main(
        [
            "signalos",
            "worktree-snapshot",
            "latest",
            "--repo-root",
            str(repo),
            "--json",
        ]
    )
    assert rc == 0
    latest_payload = json.loads(capsys.readouterr().out)
    assert latest_payload["snapshot"]["id"] == snapshot_payload["snapshot"]["id"]

    subdir = repo / "nested"
    subdir.mkdir()
    rc = cli_main(
        [
            "signalos",
            "worktree-snapshot",
            "get",
            snapshot_payload["snapshot"]["id"],
            "--repo-root",
            str(subdir),
            "--json",
        ]
    )
    assert rc == 0
    get_payload = json.loads(capsys.readouterr().out)
    assert get_payload["snapshot"]["id"] == snapshot_payload["snapshot"]["id"]

    rc = cli_main(
        [
            "signalos",
            "validate-worktree-sync",
            "--repo-root",
            str(repo),
            "--require-clean",
            "--json",
        ]
    )
    assert rc == 0
    validation_payload = json.loads(capsys.readouterr().out)
    assert validation_payload["status"] == "PASS"
    assert validation_payload["snapshot"]["dirty_file_count"] == 0


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(
        repo,
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "user.name=SignalOS Test",
        "commit",
        "-m",
        "initial",
    )
    return repo


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=20,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return (proc.stdout or "").strip()
