"""OA-47: the host-git release/control-tree queries fall back to a git-free
filesystem walk when git blocks/times out (observed on loaded Windows funded
runs: a ~1.4s `git ls-files` blocked past minutes under mandatory file locking /
AV scan of .git). The fallback is byte-identical to the git path, so a blocked
host git must NOT fail a complete, reviewer-approved product on a
consistency-digest read."""
from __future__ import annotations

import subprocess as sp
import unittest.mock as mock
from pathlib import Path

import pytest

from signalos_lib.product import release_tree as rt


def _seed_git_workspace(root: Path) -> Path:
    (root / "src").mkdir()
    (root / "src" / "App.tsx").write_text("export default 1\n", encoding="utf-8")
    (root / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    (root / "vite.config.cjs").write_text("module.exports={}\n", encoding="utf-8")
    (root / "core").mkdir()
    (root / "core" / "gov.md").write_text("governance\n", encoding="utf-8")
    sp.run(["git", "init", "-q"], cwd=root, check=True)
    sp.run(["git", "add", "-A"], cwd=root, check=True)
    sp.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t",
            "commit", "-qm", "seed"], cwd=root, check=True)
    return root


@pytest.mark.parametrize("fn", [rt.workspace_release_tree, rt.workspace_control_tree])
def test_tree_falls_back_to_walk_when_git_blocks(tmp_path, fn):
    root = _seed_git_workspace(tmp_path)
    git_tree = fn(root)  # normal git path

    # Simulate the host-git BLOCK: ls-files raises ReleaseTreeError (as _run_git
    # does after its retries time out). The function must degrade to the git-free
    # filesystem walk and return the SAME tree, not raise.
    real = rt._git_paths

    def blocked(r, args):
        if "ls-files" in args:
            raise rt.ReleaseTreeError("git ls-files timed out (simulated block)")
        return real(r, args)

    with mock.patch.object(rt, "_git_paths", side_effect=blocked):
        fallback_tree = fn(root)

    assert fallback_tree == git_tree
    assert rt.tree_digest(fallback_tree) == rt.tree_digest(git_tree)


def test_release_tree_fallback_is_git_free_and_nonempty(tmp_path):
    root = _seed_git_workspace(tmp_path)

    def always_block(r, args):
        raise rt.ReleaseTreeError("git blocked")

    with mock.patch.object(rt, "_git_paths", side_effect=always_block):
        tree = rt.workspace_release_tree(root)
    # product payload present, governance excluded, no exception
    assert "package.json" in tree
    assert "src/App.tsx" in tree
    assert not any(rel.startswith("core/") for rel in tree)
