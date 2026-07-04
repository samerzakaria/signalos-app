# python/test_agent_dispatch_parallel.py
# dispatch_local_build_agent_parallel proves the executor's parallel path with
# a real, small, provably-safe caller -- react-vite components are only ever
# imported from App.tsx (never from each other), so grouping file_specs by
# component is a genuine dependency-safe partition, not an inferred one.
# Verifies the parallel path produces the SAME files a single synchronous call
# would. Per the EXECUTOR PERF fix the DEFAULT partition path is git-free (no
# worktrees, no merges -- that machinery timed out on Windows); the opt-in
# worktree/merge-queue path is exercised separately in test_executor.py and
# test_parallel_no_worktrees.py (isolate=True).

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.agent_dispatch import (
    dispatch_local_build_agent,
    dispatch_local_build_agent_parallel,
)


def _run_git(cwd: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "parallel-test@example.com")
    _run_git(repo, "config", "user.name", "Parallel Test")
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "init")
    return repo


def _component_specs(name: str) -> list[dict]:
    return [
        {"path": f"src/components/{name}.tsx", "kind": "source", "description": f"{name} card"},
        {"path": f"src/components/{name}.test.tsx", "kind": "test", "description": ""},
    ]


def _react_vite_packet(run_id: str, component_names: list[str]) -> dict:
    # "config" kind mirrors generation.py's real foundation/UI-infra specs
    # (theme, layouts, types, css) -- they're intentionally exempt from the
    # TDD source/test pairing check that applies to "source" kind files.
    file_specs = [
        {"path": "src/types.ts", "kind": "config", "description": ""},
        {"path": "src/ui/theme.ts", "kind": "config", "description": ""},
        {"path": "src/ui/index.ts", "kind": "config", "description": ""},
        {"path": "src/ui/layouts/AppLayout.tsx", "kind": "config", "description": ""},
        {"path": "src/ui/layouts/PageLayout.tsx", "kind": "config", "description": ""},
        {"path": "src/product.css", "kind": "config", "description": ""},
        {"path": "src/App.tsx", "kind": "source", "description": ""},
        {"path": "src/App.test.tsx", "kind": "test", "description": ""},
    ]
    for name in component_names:
        file_specs.extend(_component_specs(name))
    return {
        "run_id": run_id,
        "generation": {
            "profile": "react-vite",
            "product": "Acme Tracker",
            "file_specs": file_specs,
            "entities": [],
            "workflows": [],
            "acceptance_criteria": [],
            "design_constraints": {},
            "allowed_paths": [spec["path"] for spec in file_specs],
            "forbidden_paths": [],
        },
    }


class TestParallelLocalBuildFallback(unittest.TestCase):
    def test_non_react_vite_profile_falls_back_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _init_repo(Path(d))
            packet = {
                "run_id": "r1",
                "generation": {
                    "profile": "generic",
                    "file_specs": [
                        {"path": "src/thing.py", "kind": "source", "entity": "Thing", "description": "Fields: id."},
                        {"path": "tests/test_thing.py", "kind": "test", "entity": "Thing", "description": ""},
                    ],
                    "allowed_paths": ["src/thing.py", "tests/test_thing.py"],
                    "forbidden_paths": [],
                },
            }
            result = dispatch_local_build_agent_parallel(repo, packet)
            self.assertEqual(result["agent"], "signalos-local-build-agent")
            self.assertEqual(result["status"], "completed")

    def test_single_component_falls_back_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _init_repo(Path(d))
            packet = _react_vite_packet("r2", ["OnlyOne"])
            result = dispatch_local_build_agent_parallel(repo, packet)
            self.assertEqual(result["agent"], "signalos-local-build-agent")
            self.assertEqual(result["status"], "completed")


class TestParallelLocalBuildRealPartition(unittest.TestCase):
    def test_multi_component_build_matches_the_synchronous_path(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sync_repo = _init_repo(Path(d) / "sync_parent")
            sync_result = dispatch_local_build_agent(
                sync_repo, _react_vite_packet("sync-run", ["Alpha", "Beta", "Gamma"]),
            )
            self.assertEqual(sync_result["status"], "completed")

            par_repo = _init_repo(Path(d) / "par_parent")
            par_result = dispatch_local_build_agent_parallel(
                par_repo, _react_vite_packet("par-run", ["Alpha", "Beta", "Gamma"]),
            )
            self.assertEqual(par_result["agent"], "signalos-local-build-agent-parallel")
            self.assertEqual(par_result["status"], "completed")
            self.assertFalse(par_result["errors"], par_result["errors"])

            # Same file set, same content -- the parallel path is a real
            # equivalent of the synchronous one, not an approximation.
            for spec_path in ("src/App.tsx", "src/types.ts",
                               "src/components/Alpha.tsx", "src/components/Beta.tsx",
                               "src/components/Gamma.tsx"):
                sync_content = (sync_repo / spec_path).read_text(encoding="utf-8")
                par_content = (par_repo / spec_path).read_text(encoding="utf-8")
                self.assertEqual(sync_content, par_content, spec_path)

            # EXECUTOR PERF fix: the default parallel path is git-free -- it
            # NEVER creates a worktree (the worktree machinery timed out a
            # 7-component app on Windows). The generated files are written
            # straight to disk (proven byte-identical above); git is not
            # touched at all -- no worktree dir is even created.
            worktrees_dir = par_repo / ".signalos" / "product" / "worktrees"
            leftover = list(worktrees_dir.glob("*")) if worktrees_dir.exists() else []
            self.assertEqual(leftover, [])
            # No executor commit/merge happened -- the git log still shows only
            # the initial commit (the fast path does not commit or merge).
            git_log = subprocess.run(
                ["git", "log", "--oneline"], cwd=str(par_repo), capture_output=True, text=True,
            ).stdout
            self.assertNotIn("executor merge:", git_log)
            self.assertNotIn("executor:", git_log)
            # Exactly one agent-runs/ entry for the whole delivery -- not
            # one per parallel sub-task. The git-free path suppresses each
            # sub-task's RESULT.json (write_result=False) and writes a single
            # aggregate RESULT.json instead.
            agent_runs_dir = par_repo / ".signalos" / "product" / "agent-runs"
            run_dirs = [p for p in agent_runs_dir.iterdir() if p.is_dir()] if agent_runs_dir.exists() else []
            self.assertEqual(len(run_dirs), 1, run_dirs)
            # No half-finished merge left behind.
            merge_status = subprocess.run(
                ["git", "status"], cwd=str(par_repo), capture_output=True, text=True,
            ).stdout
            self.assertNotIn("You have unmerged paths", merge_status)

    def test_result_shape_matches_dispatch_local_build_agent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _init_repo(Path(d))
            result = dispatch_local_build_agent_parallel(
                repo, _react_vite_packet("shape-run", ["Alpha", "Beta"]),
            )
            for key in ("status", "run_id", "files_written", "errors", "agent"):
                self.assertIn(key, result)
            self.assertEqual(result["run_id"], "shape-run")
            self.assertGreaterEqual(len(result["files_written"]), 8)


if __name__ == "__main__":
    unittest.main()
