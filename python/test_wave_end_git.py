"""test_wave_end_git.py - Tests for SignalOS Milestone 4 (audit completion plan).

Milestone 4 is "agent ships your work": after a wave finishes and the user
signs G5, the orchestrator auto-commits and pushes. Three things must hold:

  1. _auto_commit_wave skips silently when there is no .git dir or the
     tree is clean (no empty commits).
  2. _auto_commit_wave produces a real commit with the expected message
     shape when the tree is dirty.
  3. G5 sign followed by _auto_push_on_g5 records a g5-push-result entry
     with status=deferred when there is no remote AND no
     SIGNALOS_GH_CLIENT_ID — no exception raised; gate signing succeeds.

The tests run actual `git init` / `git add` / `git commit` in temp dirs
so any divergence between our subprocess plumbing and real git surfaces
loudly. Skipped wholesale on systems without git on PATH (matches the
_bash_available skipUnless idiom already used in test_orchestrator_core.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.orchestrator import _auto_commit_wave
from signalos_lib.sign import _auto_push_on_g5


def _git_available() -> bool:
    """Return True if `git --version` succeeds on this machine.

    Mirrors orchestrator._bash_available's intent: the test relies on
    real git, so skip cleanly when it isn't installed (e.g. minimal CI
    containers) rather than fail with a misleading subprocess error.
    """
    if shutil.which("git") is None:
        return False
    try:
        proc = subprocess.run(
            ["git", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _git_init_workspace(root: Path) -> None:
    """Run `git init` + minimal config so commits land without env-var setup."""
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    # User identity is required for `git commit`; set it locally so the
    # test doesn't depend on the host machine's git config.
    subprocess.run(
        ["git", "config", "user.email", "signalos-test@example.com"],
        cwd=str(root), check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "SignalOS Test"],
        cwd=str(root), check=True,
    )
    # Disable commit hooks (e.g. host pre-commit) so the test doesn't
    # accidentally pick up the developer's global hook config. Point at
    # a path inside the temp tree that does not exist; git treats a
    # nonexistent hooksPath as "no hooks installed", which is what we
    # want, and the empty subdir trick works on Windows too (unlike
    # /dev/null which is POSIX-only).
    subprocess.run(
        ["git", "config", "core.hooksPath", str(root / ".no-hooks")],
        cwd=str(root), check=False,
    )


def _read_audit_trail(root: Path) -> list[dict]:
    """Read .signalos/AUDIT_TRAIL.jsonl as a list of dicts. Empty if absent."""
    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not trail.is_file():
        return []
    entries: list[dict] = []
    for line in trail.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


# ---------------------------------------------------------------------------
# _auto_commit_wave
# ---------------------------------------------------------------------------

@unittest.skipUnless(_git_available(), "git unavailable")
class AutoCommitWave(unittest.TestCase):

    def test_auto_commit_no_op_when_no_git_dir(self) -> None:
        """Workspace without a .git/ folder is silently skipped.

        Auto-commit is opt-in; we shouldn't `git init` for users who
        chose not to version their workspace.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # Intentionally NOT calling _git_init_workspace.
            (root / "some-output.txt").write_text("wave wrote me")
            result = _auto_commit_wave(root, "1", {"tasks": [], "completed": 1})
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result.get("reason"), "no-git-dir")
            # No AUDIT_TRAIL.jsonl written (we don't audit happy-path skips).
            self.assertFalse((root / ".signalos" / "AUDIT_TRAIL.jsonl").is_file())

    def test_auto_commit_no_op_when_no_changes(self) -> None:
        """Clean tree -> no empty commit.

        We `git init`, add+commit a baseline, then call _auto_commit_wave
        on the now-clean tree and expect status=skipped (reason=clean-tree).
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _git_init_workspace(root)
            # Seed an initial commit so HEAD exists -- otherwise the
            # second commit attempt would have nothing to compare against.
            (root / "README.md").write_text("baseline")
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
            subprocess.run(
                ["git", "commit", "-m", "seed", "--no-verify"],
                cwd=str(root), check=True,
            )
            # Tree is now clean.
            result = _auto_commit_wave(root, "1", {"tasks": [], "completed": 1})
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result.get("reason"), "clean-tree")

    def test_auto_commit_creates_commit_with_summary(self) -> None:
        """Dirty tree -> a real commit with the expected message shape.

        After the call:
          - status == "committed"
          - `git log -1 --pretty=%B` shows "feat(wave-3): <titles>"
          - the body mentions task count and file count
          - AUDIT_TRAIL.jsonl has an auto-commit-ok row
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _git_init_workspace(root)
            (root / "src").mkdir()
            (root / "src" / "hello.ts").write_text("export const hello = 1;\n")
            (root / "README.md").write_text("# project\n")

            summary = {
                "tasks": [
                    {
                        "task": "T1",
                        "title": "build hello module",
                        "result": {
                            "status": "completed",
                            "files_written": ["src/hello.ts"],
                        },
                    },
                    {
                        "task": "T2",
                        "title": "add README",
                        "result": {
                            "status": "completed",
                            "files_written": ["README.md"],
                        },
                    },
                ],
                "completed": 2,
                "failed": 0,
            }

            result = _auto_commit_wave(root, "3", summary)
            self.assertEqual(result["status"], "committed", result)
            self.assertEqual(result["files_count"], 2)

            # Verify the commit landed and the message looks right.
            log = subprocess.run(
                ["git", "log", "-1", "--pretty=%B"],
                cwd=str(root), capture_output=True, text=True, check=True,
            )
            msg = log.stdout
            self.assertIn("feat(wave-3):", msg)
            self.assertIn("build hello module", msg)
            self.assertIn("add README", msg)
            self.assertIn("Wave summary", msg)
            self.assertIn("2 task(s)", msg)
            self.assertIn("2 file(s)", msg)
            self.assertIn("Auto-committed by SignalOS", msg)

            entries = _read_audit_trail(root)
            ok_entries = [e for e in entries if e.get("action") == "auto-commit-ok"]
            self.assertEqual(len(ok_entries), 1, entries)
            self.assertEqual(ok_entries[0].get("wave_id"), "3")
            self.assertEqual(ok_entries[0].get("files_count"), 2)


# ---------------------------------------------------------------------------
# _auto_push_on_g5
# ---------------------------------------------------------------------------

@unittest.skipUnless(_git_available(), "git unavailable")
class AutoPushOnG5(unittest.TestCase):

    def test_auto_push_skipped_when_no_remote_and_no_oauth_env(self) -> None:
        """G5 sign with no remote AND no SIGNALOS_GH_CLIENT_ID ->

          - no exception
          - AUDIT_TRAIL.jsonl has a g5-push-result entry with
            status=deferred + reason mentioning the env var
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _git_init_workspace(root)
            # Seed an initial commit so HEAD exists -- otherwise even the
            # attempted `git push` would fail with a different error code.
            (root / "README.md").write_text("hello")
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
            subprocess.run(
                ["git", "commit", "-m", "seed", "--no-verify"],
                cwd=str(root), check=True,
            )

            # Make sure the env var is NOT set for this test, even if
            # the developer running pytest exported one locally.
            prev = os.environ.pop("SIGNALOS_GH_CLIENT_ID", None)
            try:
                # No exception should escape this call.
                _auto_push_on_g5(root)
            finally:
                if prev is not None:
                    os.environ["SIGNALOS_GH_CLIENT_ID"] = prev

            entries = _read_audit_trail(root)
            push_entries = [e for e in entries if e.get("action") == "g5-push-result"]
            self.assertEqual(len(push_entries), 1, entries)
            entry = push_entries[0]
            self.assertEqual(entry.get("status"), "deferred")
            reason = entry.get("reason", "")
            self.assertIn("SIGNALOS_GH_CLIENT_ID", reason)

    def test_auto_push_commits_generated_product_before_pushing(self):
        """Fix 4b: the G5 push must `git add` + `git commit` the generated
        product BEFORE `git push` -- previously it ran a bare `git push origin
        HEAD` with no add/commit, shipping zero product bytes whenever the walk
        had not already committed them. Prove a NEW commit lands, carrying the
        previously-uncommitted product file, ahead of the (deferred) push.

        RED against the old code: no commit is made, so the commit count and the
        HEAD tree are unchanged.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _git_init_workspace(root)
            # A seed commit so HEAD exists.
            (root / "README.md").write_text("hello")
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
            subprocess.run(["git", "commit", "-m", "seed", "--no-verify"],
                           cwd=str(root), check=True)
            # The generated product -- UNCOMMITTED "built bytes".
            (root / "src").mkdir()
            (root / "src" / "App.tsx").write_text(
                "export default function App() { return null; }\n", encoding="utf-8")

            def _count() -> int:
                r = subprocess.run(["git", "rev-list", "--count", "HEAD"],
                                   cwd=str(root), capture_output=True, text=True, check=True)
                return int(r.stdout.strip())

            before = _count()
            prev = os.environ.pop("SIGNALOS_GH_CLIENT_ID", None)
            try:
                _auto_push_on_g5(root)   # must add + commit BEFORE the push
            finally:
                if prev is not None:
                    os.environ["SIGNALOS_GH_CLIENT_ID"] = prev

            self.assertEqual(_count(), before + 1,
                             "G5 push did not commit the generated product")
            # The product file is now tracked in HEAD (it was shipped).
            tracked = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", "HEAD"],
                cwd=str(root), capture_output=True, text=True, check=True).stdout
            self.assertIn("src/App.tsx", tracked)
            # The commit is audited, and the push was still attempted afterwards.
            entries = _read_audit_trail(root)
            commit_rows = [e for e in entries if e.get("action") == "g5-commit-result"]
            self.assertTrue(commit_rows and commit_rows[0]["status"] == "committed",
                            commit_rows)
            self.assertTrue([e for e in entries if e.get("action") == "g5-push-result"])


if __name__ == "__main__":
    unittest.main()
