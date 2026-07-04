# python/test_parallel_no_worktrees.py
# EXECUTOR PERF fix (STEP 4): git-worktree isolation is now OPT-IN.
#
# File-disjoint work (chunked dispatch, local parallel render) defaults to the
# fast, in-process run_worker_pool with NO git worktrees -- the worktree path
# (git init + ~48 subprocesses + AV-scanned checkouts + serialized merges) timed
# out on Windows for a 7-component react-vite app. These tests prove:
#   1. dispatch_local_build_agent_parallel takes NO git path by default
#      (both run_isolated_build_tasks and executor._git are monkeypatched to
#       raise -- if either is touched, the test fails loudly).
#   2. isolate=True still routes through the real worktree runner.
#   3. Every component + foundation file is still written, status completed.
#   4. The <2-groups short-circuit still routes to the single synchronous call.
#   5. Exactly ONE agent-runs/ RESULT.json for the whole delivery (no per-
#      sub-task leakage into the product tree).
#   6. The new git-free executor runner writes disjoint files with no worktrees.

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product import agent_dispatch, executor
from signalos_lib.product.agent_dispatch import (
    dispatch_local_build_agent,
    dispatch_local_build_agent_parallel,
)
from signalos_lib.product.executor import run_inprocess_build_tasks


def _run_git(cwd: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "noworktree-test@example.com")
    _run_git(repo, "config", "user.name", "NoWorktree Test")
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


def _boom(*_a, **_k):  # sentinel: any git touch fails the test loudly
    raise AssertionError("git path taken -- the default parallel path must be git-free")


class TestParallelDefaultsGitFree(unittest.TestCase):
    def test_parallel_local_uses_worker_pool_not_git(self) -> None:
        """Default (isolate omitted) must NOT touch git: monkeypatch the
        worktree runner AND executor._git to raise; the build must still
        complete and write every file through the in-process worker pool."""
        with tempfile.TemporaryDirectory() as d:
            repo = _init_repo(Path(d))
            orig_isolated = executor.run_isolated_build_tasks
            orig_git = executor._git
            executor.run_isolated_build_tasks = _boom
            agent_dispatch.run_isolated_build_tasks = _boom  # imported symbol in module
            executor._git = _boom
            try:
                result = dispatch_local_build_agent_parallel(
                    repo, _react_vite_packet("no-git", ["Alpha", "Beta", "Gamma"]),
                )
            finally:
                executor.run_isolated_build_tasks = orig_isolated
                agent_dispatch.run_isolated_build_tasks = orig_isolated
                executor._git = orig_git

            self.assertEqual(result["status"], "completed", result.get("errors"))
            self.assertFalse(result["errors"], result["errors"])
            # No worktrees dir was ever created.
            worktrees_dir = repo / ".signalos" / "product" / "worktrees"
            self.assertFalse(
                worktrees_dir.exists() and any(worktrees_dir.iterdir()),
                "worktrees created despite git-free default",
            )

    def test_all_component_files_written(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _init_repo(Path(d))
            names = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta"]
            result = dispatch_local_build_agent_parallel(
                repo, _react_vite_packet("seven", names),
            )
            self.assertEqual(result["status"], "completed", result.get("errors"))
            for name in names:
                self.assertTrue(
                    (repo / "src" / "components" / f"{name}.tsx").exists(), name,
                )
                self.assertTrue(
                    (repo / "src" / "components" / f"{name}.test.tsx").exists(), name,
                )
            for foundation in ("src/App.tsx", "src/types.ts", "src/product.css"):
                self.assertTrue((repo / foundation).exists(), foundation)

    def test_single_agent_run_result_json(self) -> None:
        """Exactly one agent-runs/ entry for the whole delivery -- the git-free
        path must NOT leak one RESULT.json per sub-task into the product tree."""
        with tempfile.TemporaryDirectory() as d:
            repo = _init_repo(Path(d))
            dispatch_local_build_agent_parallel(
                repo, _react_vite_packet("one-run", ["Alpha", "Beta", "Gamma"]),
            )
            runs = repo / ".signalos" / "product" / "agent-runs"
            run_dirs = [p for p in runs.iterdir() if p.is_dir()] if runs.exists() else []
            self.assertEqual([p.name for p in run_dirs], ["one-run"], run_dirs)

    def test_matches_synchronous_content(self) -> None:
        """The git-free parallel path produces byte-identical files to the
        single synchronous renderer -- it is a real equivalent, faster path."""
        with tempfile.TemporaryDirectory() as d:
            sync_repo = _init_repo(Path(d) / "sync")
            dispatch_local_build_agent(
                sync_repo, _react_vite_packet("s", ["Alpha", "Beta"]),
            )
            par_repo = _init_repo(Path(d) / "par")
            dispatch_local_build_agent_parallel(
                par_repo, _react_vite_packet("p", ["Alpha", "Beta"]),
            )
            for path in ("src/App.tsx", "src/types.ts",
                         "src/components/Alpha.tsx", "src/components/Beta.tsx"):
                self.assertEqual(
                    (sync_repo / path).read_text(encoding="utf-8"),
                    (par_repo / path).read_text(encoding="utf-8"),
                    path,
                )


class TestIsolateOptIn(unittest.TestCase):
    def test_isolate_true_uses_worktrees(self) -> None:
        called = {"n": 0}
        orig = executor.run_isolated_build_tasks

        def spy(*a, **k):
            called["n"] += 1
            return orig(*a, **k)

        with tempfile.TemporaryDirectory() as d:
            repo = _init_repo(Path(d))
            executor.run_isolated_build_tasks = spy
            agent_dispatch.run_isolated_build_tasks = spy
            try:
                result = dispatch_local_build_agent_parallel(
                    repo, _react_vite_packet("iso", ["Alpha", "Beta"]),
                    isolate=True,
                )
            finally:
                executor.run_isolated_build_tasks = orig
                agent_dispatch.run_isolated_build_tasks = orig
            self.assertEqual(called["n"], 1, "isolate=True did not use the worktree runner")
            self.assertEqual(result["status"], "completed", result.get("errors"))

    def test_single_component_short_circuits(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _init_repo(Path(d))
            result = dispatch_local_build_agent_parallel(
                repo, _react_vite_packet("solo", ["OnlyOne"]),
            )
            self.assertEqual(result["agent"], "signalos-local-build-agent")
            self.assertEqual(result["status"], "completed")

    def test_non_react_vite_short_circuits(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _init_repo(Path(d))
            packet = {
                "run_id": "gen",
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


class TestInProcessBuildRunner(unittest.TestCase):
    """The git-free executor runner directly: same claim/heartbeat/retry/
    dead-letter contract as run_isolated_build_tasks, but no worktrees, no
    merges, no git at all -- each packet writes its disjoint files in place."""

    def _packet(self, task_id: str, filename: str, entity: str) -> dict:
        return {
            "task_id": task_id,
            "run_id": task_id,
            "generation": {
                "profile": "generic",
                "file_specs": [
                    {"path": f"src/{filename}.py", "kind": "source", "entity": entity, "description": "Fields: id, name."},
                    {"path": f"tests/test_{filename}.py", "kind": "test", "entity": entity, "description": ""},
                ],
                "allowed_paths": [f"src/{filename}.py", f"tests/test_{filename}.py"],
                "forbidden_paths": [],
            },
        }

    def test_disjoint_packets_write_in_place_no_git(self) -> None:
        orig_git = executor._git
        executor._git = _boom  # any git call fails the test
        try:
            with tempfile.TemporaryDirectory() as d:
                repo = Path(d) / "plain"  # NOT a git repo at all
                repo.mkdir()
                report = run_inprocess_build_tasks(
                    repo,
                    [self._packet("task-a", "alpha", "Alpha"),
                     self._packet("task-b", "beta", "Beta")],
                    max_workers=2,
                )
                self.assertEqual(len(report.succeeded), 2, report.outcomes)
                self.assertFalse(report.dead_letters)
                self.assertTrue((repo / "src" / "alpha.py").exists())
                self.assertTrue((repo / "src" / "beta.py").exists())
                self.assertTrue((repo / "tests" / "test_alpha.py").exists())
                self.assertTrue((repo / "tests" / "test_beta.py").exists())
                self.assertFalse((repo / ".signalos" / "product" / "worktrees").exists())
        finally:
            executor._git = orig_git

    def test_failed_dispatch_dead_letters(self) -> None:
        def bad_dispatch(_repo, _packet):
            return {"status": "failed", "errors": ["synthetic failure"], "files_written": []}

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "plain"
            repo.mkdir()
            report = run_inprocess_build_tasks(
                repo,
                [self._packet("doomed", "x", "X")],
                dispatch=bad_dispatch,
            )
            self.assertEqual(report.dead_letters, ["doomed"], report.outcomes)


if __name__ == "__main__":
    unittest.main()
