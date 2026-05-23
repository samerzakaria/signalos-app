"""Tests for signalos_lib.product.lifecycle — P4 Repo Lifecycle."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from signalos_lib.product.lifecycle import (
    capture_git_state,
    create_delivery_state,
    detect_mode,
    init_product_repo,
    load_delivery_state,
    record_checkpoint,
    update_delivery_phase,
)


# ---------------------------------------------------------------------------
# detect_mode
# ---------------------------------------------------------------------------

class TestDetectMode:
    def test_nonexistent_path(self, tmp_path: Path) -> None:
        assert detect_mode(tmp_path / "does_not_exist") == "greenfield"

    def test_empty_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert detect_mode(empty) == "greenfield"

    def test_directory_with_files_no_signalos(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("hello")
        assert detect_mode(tmp_path) == "adopt"

    def test_directory_with_signalos(self, tmp_path: Path) -> None:
        (tmp_path / ".signalos").mkdir()
        assert detect_mode(tmp_path) == "refresh"


# ---------------------------------------------------------------------------
# create / load / update delivery state
# ---------------------------------------------------------------------------

class TestDeliveryState:
    def test_create_writes_json(self, tmp_path: Path) -> None:
        state = create_delivery_state(
            repo_root=tmp_path,
            mode="greenfield",
            prompt="build a todo app",
            profile="react-vite",
            blueprint="bp-1",
        )
        assert state["schema_version"] == "signalos.delivery_state.v1"
        assert state["phase"] == "intent"
        assert state["mode"] == "greenfield"
        assert state["profile"] == "react-vite"
        assert state["blueprint"] == "bp-1"
        assert state["status"] == "running"
        assert state["created_at"]
        assert state["updated_at"]
        assert state["checkpoints"] == []

        # Verify file on disk
        path = tmp_path / ".signalos" / "product" / "DELIVERY_STATE.json"
        assert path.is_file()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == state

    def test_prompt_sha256_correct(self, tmp_path: Path) -> None:
        prompt = "build a todo app"
        state = create_delivery_state(
            repo_root=tmp_path,
            mode="greenfield",
            prompt=prompt,
            profile="generic",
            blueprint="",
        )
        expected = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        assert state["prompt_sha256"] == expected

    def test_load_reads_back(self, tmp_path: Path) -> None:
        original = create_delivery_state(
            repo_root=tmp_path,
            mode="adopt",
            prompt="migrate legacy app",
            profile="generic",
            blueprint="bp-2",
        )
        loaded = load_delivery_state(tmp_path)
        assert loaded == original

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert load_delivery_state(tmp_path) is None

    def test_update_phase_changes_phase_and_status(self, tmp_path: Path) -> None:
        create_delivery_state(
            repo_root=tmp_path,
            mode="greenfield",
            prompt="x",
            profile="generic",
            blueprint="",
        )
        updated = update_delivery_phase(tmp_path, "scaffolded", "complete")
        assert updated["phase"] == "scaffolded"
        assert updated["status"] == "complete"

    def test_update_phase_preserves_other_fields(self, tmp_path: Path) -> None:
        original = create_delivery_state(
            repo_root=tmp_path,
            mode="greenfield",
            prompt="x",
            profile="react-vite",
            blueprint="bp-1",
        )
        updated = update_delivery_phase(tmp_path, "validated")
        assert updated["mode"] == original["mode"]
        assert updated["profile"] == original["profile"]
        assert updated["blueprint"] == original["blueprint"]
        assert updated["prompt_sha256"] == original["prompt_sha256"]
        assert updated["created_at"] == original["created_at"]

    def test_update_phase_advances_timestamp(self, tmp_path: Path) -> None:
        original = create_delivery_state(
            repo_root=tmp_path,
            mode="greenfield",
            prompt="x",
            profile="generic",
            blueprint="",
        )
        original_ts = original["updated_at"]
        # Small delay to ensure timestamp differs
        time.sleep(0.01)
        updated = update_delivery_phase(tmp_path, "generated")
        assert updated["updated_at"] >= original_ts

    def test_update_missing_state_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            update_delivery_phase(tmp_path, "scaffolded")

    def test_roundtrip_consistency(self, tmp_path: Path) -> None:
        """Create -> update -> load maintains full consistency."""
        create_delivery_state(
            repo_root=tmp_path,
            mode="adopt",
            prompt="test prompt",
            profile="generic",
            blueprint="bp-x",
        )
        update_delivery_phase(tmp_path, "scaffolded", "complete")
        update_delivery_phase(tmp_path, "validated", "running")

        loaded = load_delivery_state(tmp_path)
        assert loaded is not None
        assert loaded["phase"] == "validated"
        assert loaded["status"] == "running"
        assert loaded["mode"] == "adopt"
        assert loaded["profile"] == "generic"
        assert loaded["blueprint"] == "bp-x"


# ---------------------------------------------------------------------------
# capture_git_state
# ---------------------------------------------------------------------------

class TestCaptureGitState:
    def test_non_git_directory(self, tmp_path: Path) -> None:
        state = capture_git_state(tmp_path)
        assert state["has_git"] is False
        assert state["head_sha"] is None
        assert state["branch"] is None
        assert state["clean"] is None
        assert state["untracked_count"] is None

    def test_real_git_repo(self, tmp_path: Path) -> None:
        # Set up a real git repo with a commit
        subprocess.run(["git", "init"], cwd=str(tmp_path), check=True,
                        capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                        cwd=str(tmp_path), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                        cwd=str(tmp_path), check=True, capture_output=True)
        (tmp_path / "hello.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True,
                        capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"],
                        cwd=str(tmp_path), check=True, capture_output=True)

        state = capture_git_state(tmp_path)
        assert state["has_git"] is True
        assert state["head_sha"] is not None
        assert len(state["head_sha"]) == 40
        assert state["clean"] is True
        assert state["untracked_count"] == 0

    def test_git_repo_with_untracked(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init"], cwd=str(tmp_path), check=True,
                        capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                        cwd=str(tmp_path), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                        cwd=str(tmp_path), check=True, capture_output=True)
        (tmp_path / "tracked.txt").write_text("tracked")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True,
                        capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"],
                        cwd=str(tmp_path), check=True, capture_output=True)

        # Add untracked files
        (tmp_path / "untracked1.txt").write_text("u1")
        (tmp_path / "untracked2.txt").write_text("u2")

        state = capture_git_state(tmp_path)
        assert state["has_git"] is True
        assert state["clean"] is False
        assert state["untracked_count"] == 2


# ---------------------------------------------------------------------------
# record_checkpoint
# ---------------------------------------------------------------------------

class TestRecordCheckpoint:
    def test_creates_checkpoint_file(self, tmp_path: Path) -> None:
        cp = record_checkpoint(tmp_path, "phase-intent")
        assert cp["label"] == "phase-intent"
        assert cp["timestamp"]
        assert "git_state" in cp

        path = tmp_path / ".signalos" / "product" / "checkpoints" / "phase-intent.json"
        assert path.is_file()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == cp

    def test_multiple_checkpoints_dont_overwrite(self, tmp_path: Path) -> None:
        cp1 = record_checkpoint(tmp_path, "alpha")
        cp2 = record_checkpoint(tmp_path, "beta")

        cp_dir = tmp_path / ".signalos" / "product" / "checkpoints"
        assert (cp_dir / "alpha.json").is_file()
        assert (cp_dir / "beta.json").is_file()

        # Verify contents are independent
        alpha = json.loads((cp_dir / "alpha.json").read_text(encoding="utf-8"))
        beta = json.loads((cp_dir / "beta.json").read_text(encoding="utf-8"))
        assert alpha["label"] == "alpha"
        assert beta["label"] == "beta"

    def test_checkpoint_appends_to_delivery_state(self, tmp_path: Path) -> None:
        create_delivery_state(
            repo_root=tmp_path,
            mode="greenfield",
            prompt="x",
            profile="generic",
            blueprint="",
        )
        record_checkpoint(tmp_path, "cp-1")
        record_checkpoint(tmp_path, "cp-2")

        state = load_delivery_state(tmp_path)
        assert state is not None
        assert len(state["checkpoints"]) == 2
        assert state["checkpoints"][0]["label"] == "cp-1"
        assert state["checkpoints"][1]["label"] == "cp-2"


# ---------------------------------------------------------------------------
# init_product_repo
# ---------------------------------------------------------------------------

class TestInitProductRepo:
    def test_greenfield_creates_signalos_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "new_project"
        result = init_product_repo(
            repo_root=target,
            mode="greenfield",
            profile="generic",
            product_name="new_project",
        )
        assert result["success"] is True
        assert result["mode"] == "greenfield"
        assert (target / ".signalos").is_dir()

    def test_adopt_preserves_existing_files(self, tmp_path: Path) -> None:
        # Create existing content
        (tmp_path / "existing.txt").write_text("preserve me")

        result = init_product_repo(
            repo_root=tmp_path,
            mode="adopt",
            profile="generic",
            product_name="adopted",
        )
        assert result["success"] is True
        assert result["mode"] == "adopt"
        # Existing file must survive
        assert (tmp_path / "existing.txt").read_text() == "preserve me"
        assert (tmp_path / ".signalos").is_dir()

    def test_auto_mode_resolves(self, tmp_path: Path) -> None:
        target = tmp_path / "auto_project"
        result = init_product_repo(
            repo_root=target,
            mode="auto",
            profile="generic",
            product_name="auto_project",
        )
        assert result["success"] is True
        assert result["mode"] == "greenfield"

    def test_unknown_mode_errors(self, tmp_path: Path) -> None:
        result = init_product_repo(
            repo_root=tmp_path,
            mode="invalid",
            profile="generic",
            product_name="test",
        )
        assert result["success"] is False
        assert any("unknown mode" in e for e in result["errors"])

    def test_refresh_mode(self, tmp_path: Path) -> None:
        # First, do a greenfield init
        result1 = init_product_repo(
            repo_root=tmp_path,
            mode="greenfield",
            profile="generic",
            product_name="test_refresh",
        )
        assert result1["success"] is True

        # Now refresh
        result2 = init_product_repo(
            repo_root=tmp_path,
            mode="refresh",
            profile="generic",
            product_name="test_refresh",
        )
        assert result2["success"] is True
        assert result2["mode"] == "refresh"
