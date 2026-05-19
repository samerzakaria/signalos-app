"""test_wave_rollback.py - Wave checkpoint + Undo Wave rollback.

The checkpoint/rollback handlers shell out to git, so this suite spins
up real ephemeral git repos in tempdirs. We cover:
  - checkpoint captures the current HEAD SHA
  - rollback resets tracked files + deletes wave-written untracked files
  - rollback refuses cleanly when no checkpoint exists
  - rollback refuses cleanly when the captured SHA is gone (GC'd)
  - audit trail gets a wave_rolled_back entry; original history is kept
  - path-traversal in the --files arg is rejected
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_ipc_server import (
    handle_checkpoint,
    handle_rollback,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.local",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.local",
        },
    )
    return (p.returncode, p.stdout)


def _seed_repo(root: Path) -> str:
    """Init a git repo at *root* with one starter commit. Returns HEAD."""
    _git(["init", "-q", "-b", "main"], root)
    (root / "README.md").write_text("starter\n")
    _git(["add", "README.md"], root)
    _git(["commit", "-q", "-m", "starter"], root)
    _, sha = _git(["rev-parse", "HEAD"], root)
    return sha.strip()


def _have_git() -> bool:
    try:
        p = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

class CheckpointCapture(unittest.TestCase):
    def setUp(self) -> None:
        if not _have_git():
            self.skipTest("git not on PATH")

    def test_writes_sha_and_started_at_to_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            head = _seed_repo(root)
            out = handle_checkpoint(["--wave", "1"], str(root))
            parsed = json.loads(out)
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["sha"], head)
            cp = root / ".signalos" / "wave-checkpoints" / "wave-1.json"
            self.assertTrue(cp.is_file())
            data = json.loads(cp.read_text())
            self.assertEqual(data["sha"], head)
            self.assertEqual(data["wave"], "1")
            self.assertIn("started_at", data)

    def test_appends_audit_entry(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_repo(root)
            handle_checkpoint(["--wave", "2"], str(root))
            audit = (root / ".signalos" / "AUDIT_TRAIL.jsonl").read_text(encoding="utf-8")
            self.assertIn("wave_checkpoint", audit)
            self.assertIn('"wave": "2"', audit)

    def test_refuses_when_not_a_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = handle_checkpoint(["--wave", "1"], d)
            parsed = json.loads(out)
            self.assertFalse(parsed["ok"])
            self.assertIn("rev-parse", parsed["error"].lower())


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

class RollbackHardReset(unittest.TestCase):
    def setUp(self) -> None:
        if not _have_git():
            self.skipTest("git not on PATH")

    def test_restores_tracked_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_repo(root)
            handle_checkpoint(["--wave", "1"], str(root))

            # Simulate "the wave modified a tracked file."
            (root / "README.md").write_text("clobbered by wave\n")

            out = handle_rollback(["--wave", "1"], str(root))
            parsed = json.loads(out)
            self.assertTrue(parsed["ok"], parsed)
            self.assertEqual((root / "README.md").read_text(), "starter\n")

    def test_deletes_files_passed_via_files_arg(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_repo(root)
            handle_checkpoint(["--wave", "1"], str(root))

            (root / "src").mkdir()
            (root / "src" / "App.tsx").write_text("export const App = () => null;")
            (root / "src" / "extra.ts").write_text("export const x = 1;")

            out = handle_rollback(
                ["--wave", "1", "--files", "src/App.tsx,src/extra.ts"],
                str(root),
            )
            parsed = json.loads(out)
            self.assertTrue(parsed["ok"])
            self.assertEqual(set(parsed["files_deleted"]), {"src/App.tsx", "src/extra.ts"})
            self.assertFalse((root / "src" / "App.tsx").exists())
            self.assertFalse((root / "src" / "extra.ts").exists())

    def test_path_traversal_in_files_arg_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_repo(root)
            handle_checkpoint(["--wave", "1"], str(root))

            # Create a file outside the workspace to verify we don't touch it.
            outside = root.parent / "outside-{}.txt".format(os.getpid())
            outside.write_text("safe")
            try:
                out = handle_rollback(
                    ["--wave", "1", "--files", f"../{outside.name},/etc/passwd"],
                    str(root),
                )
                parsed = json.loads(out)
                self.assertTrue(parsed["ok"])
                self.assertEqual(parsed["files_deleted"], [])
                self.assertTrue(outside.is_file(), "outside file was deleted; path-traversal guard failed")
            finally:
                if outside.is_file():
                    outside.unlink()

    def test_uses_checkpoint_files_list_when_no_files_arg(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_repo(root)
            handle_checkpoint(["--wave", "1"], str(root))

            # Pre-write the files list into the checkpoint, as the
            # orchestrator's _append_files_to_wave_checkpoint would.
            cp = root / ".signalos" / "wave-checkpoints" / "wave-1.json"
            data = json.loads(cp.read_text())
            data["files_written"] = ["new1.ts", "new2.md"]
            cp.write_text(json.dumps(data))
            (root / "new1.ts").write_text("x")
            (root / "new2.md").write_text("y")

            out = handle_rollback(["--wave", "1"], str(root))
            parsed = json.loads(out)
            self.assertTrue(parsed["ok"])
            self.assertEqual(set(parsed["files_deleted"]), {"new1.ts", "new2.md"})

    def test_appends_wave_rolled_back_audit_without_deleting_history(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_repo(root)
            handle_checkpoint(["--wave", "1"], str(root))
            handle_rollback(["--wave", "1"], str(root))

            audit_lines = (root / ".signalos" / "AUDIT_TRAIL.jsonl").read_text(encoding="utf-8").strip().splitlines()
            # Both entries present, in order.
            self.assertGreaterEqual(len(audit_lines), 2)
            kinds = [json.loads(line).get("kind") for line in audit_lines]
            self.assertIn("wave_checkpoint", kinds)
            self.assertIn("wave_rolled_back", kinds)


class RollbackRefusals(unittest.TestCase):
    def setUp(self) -> None:
        if not _have_git():
            self.skipTest("git not on PATH")

    def test_no_checkpoint_refuses_with_clear_message(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_repo(root)
            out = handle_rollback(["--wave", "999"], str(root))
            parsed = json.loads(out)
            self.assertFalse(parsed["ok"])
            self.assertIn("no checkpoint", parsed["error"].lower())

    def test_sha_no_longer_reachable_refuses_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_repo(root)
            # Hand-craft a checkpoint with a SHA that doesn't exist in
            # this repo. Real-world equivalent: force-push, gc.
            (root / ".signalos" / "wave-checkpoints").mkdir(parents=True)
            (root / ".signalos" / "wave-checkpoints" / "wave-1.json").write_text(json.dumps({
                "wave": "1",
                "sha": "0" * 40,
                "started_at": "2026-01-01T00:00:00Z",
            }))
            out = handle_rollback(["--wave", "1"], str(root))
            parsed = json.loads(out)
            self.assertFalse(parsed["ok"])
            self.assertIn("no longer reachable", parsed["error"].lower())


if __name__ == "__main__":
    unittest.main()
