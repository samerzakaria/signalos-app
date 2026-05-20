"""test_wave_engine_m_w5.py — M-W5: G5 agent + M4 git-push handoff.

Per WAVE-ENGINE-DESIGN §2 and the M4 auto-commit shipped in
commit 5372546.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.agent_loader import load_agent
from signalos_lib.wave_engine import WaveEngine


def _mk_git_workspace() -> Path:
    """Initialize a real git workspace so _auto_commit_wave has a .git/."""
    root = Path(tempfile.mkdtemp(prefix="signalos-m-w5-")).resolve()
    (root / ".signalos").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True, timeout=30)
    subprocess.run(
        ["git", "config", "user.email", "test@signalos.local"],
        cwd=str(root), check=True, timeout=30,
    )
    subprocess.run(
        ["git", "config", "user.name", "M-W5 Test"],
        cwd=str(root), check=True, timeout=30,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=str(root), check=True, timeout=30,
    )
    # An initial commit so subsequent commits aren't on an empty branch.
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(root), check=True, timeout=30)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=str(root), check=True, timeout=30,
    )
    return root


# ---------------------------------------------------------------------------
# G5 agent file
# ---------------------------------------------------------------------------

class G5AgentTests(unittest.TestCase):
    def test_g5_observability_agent_loads(self):
        result = load_agent("G5")
        self.assertTrue(result["exists"])
        self.assertEqual(result["filename"], "observability.md")
        self.assertIn("Observability", result["content"])

    def test_g5_agent_lists_belief_as_prerequisite(self):
        content = load_agent("G5")["content"]
        self.assertIn("BELIEF.md", content)


# ---------------------------------------------------------------------------
# run_g5_handoff — M4 auto-commit integration
# ---------------------------------------------------------------------------

class G5HandoffTests(unittest.TestCase):
    def test_handoff_skipped_when_workspace_clean(self):
        root = _mk_git_workspace()
        eng = WaveEngine(root)
        result = eng.run_g5_handoff(wave_id="W7.1", summary={"tasks": []})
        self.assertEqual(result["commit_outcome"]["status"], "skipped")
        self.assertEqual(result["commit_outcome"]["reason"], "clean-tree")
        self.assertEqual(result["system_bubble"]["kind"], "complete")
        self.assertEqual(result["system_bubble"]["gate"], "G5")

    def test_handoff_commits_uncommitted_changes(self):
        root = _mk_git_workspace()
        # Simulate wave output by writing a file.
        (root / "wave-output.txt").write_text("done\n", encoding="utf-8")
        eng = WaveEngine(root)
        summary = {
            "tasks": [{"task": "ship-it", "title": "Ship the feature"}],
            "completed": 1,
            "failed": 0,
        }
        result = eng.run_g5_handoff(wave_id="W7.1", summary=summary)
        self.assertEqual(result["commit_outcome"]["status"], "committed")
        # The wave-output file is now tracked in HEAD.
        ls_proc = subprocess.run(
            ["git", "ls-tree", "--name-only", "HEAD"],
            cwd=str(root), capture_output=True, text=True, check=True, timeout=15,
        )
        self.assertIn("wave-output.txt", ls_proc.stdout)
        # Latest commit message reflects the wave id.
        log_proc = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=str(root), capture_output=True, text=True, check=True, timeout=15,
        )
        self.assertIn("wave-W7.1", log_proc.stdout)

    def test_handoff_skipped_without_git_dir(self):
        # No git init — _auto_commit_wave returns skipped/no-git-dir.
        root = Path(tempfile.mkdtemp(prefix="signalos-m-w5-nogit-")).resolve()
        (root / ".signalos").mkdir()
        eng = WaveEngine(root)
        result = eng.run_g5_handoff(wave_id="W7.1", summary={"tasks": []})
        self.assertEqual(result["commit_outcome"]["status"], "skipped")
        self.assertEqual(result["commit_outcome"]["reason"], "no-git-dir")

    def test_handoff_bubble_carries_g5_gate(self):
        root = _mk_git_workspace()
        eng = WaveEngine(root)
        result = eng.run_g5_handoff(wave_id="W7.1", summary={"tasks": []})
        self.assertEqual(result["system_bubble"]["gate"], "G5")


if __name__ == "__main__":
    unittest.main()
