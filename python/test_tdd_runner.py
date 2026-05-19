"""test_tdd_runner.py - TDD red->green loop with real test execution.

Tests the runner-detection, the phase-1/phase-2 dispatch, and the
violation paths (LLM cheats by passing tests in red phase, LLM fails
to write impl, etc.). We don't need a real Anthropic call -- we stub
the LLM with predetermined responses keyed on the prompt phase.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.tdd_runner import (
    detect_test_runner,
    is_tdd_task,
    run_tests_for_files,
    run_tdd_task,
)


# ---------------------------------------------------------------------------
# Helpers: a stub LLM that responds based on which TDD phase is being run.
# Each phase prompt is identified by the unique header string the runner
# prepends ("## TDD PHASE 1 OF 2" vs "## TDD PHASE 2 OF 2").
# ---------------------------------------------------------------------------

def _make_stub_llm(red_response: str, green_response: str):
    def call(prompt: str) -> tuple[str, dict]:
        if "TDD PHASE 1" in prompt:
            return (red_response, {"status": "completed", "step_id": "s1"})
        if "TDD PHASE 2" in prompt:
            return (green_response, {"status": "completed", "step_id": "s1"})
        return ("", {"status": "failed"})
    return call


def _ff_block(path: str, content: str, lang: str = "py") -> str:
    """Format a valid filepath-fenced block for _extract_files_from_response."""
    return f"### filepath: {path}\n```{lang}\n{content}\n```"


def _is_tdd_task_stub() -> dict:
    return {
        "task": "T-tdd-1",
        "step_id": "T-tdd-1",
        "title": "Add a sum function",
        "description": "Implement sum(a, b) that returns a+b",
        "files": ["src/sum.py", "src/sum.test.py"],
        "wave": "1",
        "skills": ["test-driven-development"],
    }


# Bring in the orchestrator's primitives for the runner closure args.
from signalos_lib.orchestrator import (
    _extract_files_from_response,
    _write_extracted_files,
)


# ---------------------------------------------------------------------------
# is_tdd_task
# ---------------------------------------------------------------------------

class IsTddTask(unittest.TestCase):
    def test_true_when_skill_tagged(self) -> None:
        self.assertTrue(is_tdd_task({"skills": ["test-driven-development"]}))

    def test_false_when_other_skills(self) -> None:
        self.assertFalse(is_tdd_task({"skills": ["test-generation", "security-audit"]}))

    def test_false_when_no_skills(self) -> None:
        self.assertFalse(is_tdd_task({}))


# ---------------------------------------------------------------------------
# detect_test_runner
# ---------------------------------------------------------------------------

class DetectTestRunner(unittest.TestCase):
    def test_detects_vitest_from_devDependencies(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "devDependencies": {"vitest": "^2.0.0"},
                "scripts": {"test": "vitest run"},
            }))
            runner = detect_test_runner(root)
            self.assertIsNotNone(runner)
            name, cmd = runner  # type: ignore[misc]
            self.assertEqual(name, "vitest")
            self.assertIn("vitest", cmd)

    def test_detects_jest(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "devDependencies": {"jest": "^29.0.0"},
                "scripts": {"test": "jest"},
            }))
            runner = detect_test_runner(root)
            self.assertIsNotNone(runner)
            self.assertEqual(runner[0], "jest")  # type: ignore[index]

    def test_falls_back_to_npm_test_when_unknown_runner(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "dependencies": {"some-test-thing": "1.0"},
                "scripts": {"test": "some-test-thing"},
            }))
            runner = detect_test_runner(root)
            self.assertIsNotNone(runner)
            self.assertEqual(runner[0], "npm test")  # type: ignore[index]

    def test_detects_pytest_from_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
            runner = detect_test_runner(root)
            self.assertIsNotNone(runner)
            self.assertEqual(runner[0], "pytest")  # type: ignore[index]

    def test_returns_none_for_unrecognised_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            # No package.json, no pyproject -> no runner detectable.
            self.assertIsNone(detect_test_runner(Path(d)))


# ---------------------------------------------------------------------------
# run_tests_for_files (real subprocess)
# ---------------------------------------------------------------------------

class RunTestsForFiles(unittest.TestCase):
    """We exercise the runner with a tiny pytest invocation since pytest
    is already available in CI (and locally we just installed it for the
    plan tests). This keeps the loop honest -- if subprocess plumbing
    breaks, this catches it."""

    def test_pytest_passing_returns_true(self) -> None:
        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest not installed; skipping real-subprocess test")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
            test_file = root / "test_x.py"
            test_file.write_text("def test_ok(): assert 1 + 1 == 2\n")
            runner = detect_test_runner(root)
            self.assertIsNotNone(runner)
            passed, output = run_tests_for_files(root, runner, ["test_x.py"])
            self.assertTrue(passed, f"expected pass, got output: {output[:500]}")

    def test_pytest_failing_returns_false_with_failure_output(self) -> None:
        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest not installed")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
            (root / "test_x.py").write_text("def test_no(): assert 1 == 2\n")
            runner = detect_test_runner(root)
            passed, output = run_tests_for_files(root, runner, ["test_x.py"])
            self.assertFalse(passed)
            self.assertIn("assert", output.lower())

    def test_missing_binary_returns_false_with_clear_message(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            passed, output = run_tests_for_files(
                Path(d),
                ("nope", ["definitely-not-a-real-binary-zzz"]),
                ["some-file.test.ts"],
            )
            self.assertFalse(passed)
            self.assertTrue(
                "not found" in output.lower() or "no such" in output.lower()
                or "cannot find" in output.lower() or "not recognized" in output.lower(),
                f"expected a 'not found' message, got: {output[:200]}",
            )


# ---------------------------------------------------------------------------
# run_tdd_task -- the full red->green loop
# ---------------------------------------------------------------------------

class RunTddTaskHappyPath(unittest.TestCase):
    def test_red_then_green_succeeds(self) -> None:
        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest not installed")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")

            # RED: emit a test that fails because sum doesn't exist
            red_response = _ff_block(
                "test_sum.py",
                "from sum import sum_two\n"
                "def test_sum(): assert sum_two(2, 3) == 5",
                lang="py",
            )
            # GREEN: emit the impl that makes the test pass
            green_response = _ff_block(
                "sum.py",
                "def sum_two(a, b): return a + b",
                lang="py",
            )
            call = _make_stub_llm(red_response, green_response)

            result = run_tdd_task(
                task=_is_tdd_task_stub(),
                root=root,
                base_prompt="-- task prompt --",
                call_llm=call,
                write_files=_write_extracted_files,
                extract_files=_extract_files_from_response,
            )

            self.assertEqual(result["status"], "completed", result)
            written = result.get("files_written") or []
            self.assertIn("test_sum.py", written)
            self.assertIn("sum.py", written)
            self.assertEqual(result.get("tdd_phases"), "red->green")


class RunTddTaskRedViolation(unittest.TestCase):
    def test_test_passes_in_red_phase_is_a_violation(self) -> None:
        """If the LLM writes a test that already passes against current
        code, that's a TDD violation -- the test isn't exercising any
        new behaviour."""
        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest not installed")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")

            # RED: emit a tautological test that passes without any impl.
            red_response = _ff_block(
                "test_tauto.py",
                "def test_truth(): assert True",
                lang="py",
            )
            green_response = _ff_block("impl.py", "x = 1", lang="py")
            call = _make_stub_llm(red_response, green_response)

            result = run_tdd_task(
                task=_is_tdd_task_stub(),
                root=root,
                base_prompt="-- task prompt --",
                call_llm=call,
                write_files=_write_extracted_files,
                extract_files=_extract_files_from_response,
            )
            self.assertEqual(result["status"], "failed")
            self.assertIn("PASSED before the implementation", result["failure"])


class RunTddTaskGreenFailure(unittest.TestCase):
    def test_impl_doesnt_make_tests_pass(self) -> None:
        """LLM produces the test, then produces an impl that doesn't
        actually fix the failure. We must report this with the new
        failure output so smart-retry can feed it back."""
        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest not installed")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")

            red_response = _ff_block(
                "test_sum.py",
                "from sum import sum_two\n"
                "def test_sum(): assert sum_two(2, 3) == 5",
                lang="py",
            )
            # Bad impl: returns the wrong answer.
            green_response = _ff_block(
                "sum.py",
                "def sum_two(a, b): return a - b",
                lang="py",
            )
            call = _make_stub_llm(red_response, green_response)

            result = run_tdd_task(
                task=_is_tdd_task_stub(),
                root=root,
                base_prompt="-- task prompt --",
                call_llm=call,
                write_files=_write_extracted_files,
                extract_files=_extract_files_from_response,
            )
            self.assertEqual(result["status"], "failed")
            self.assertIn("still failing", result["failure"])


class RunTddTaskNoTestFile(unittest.TestCase):
    def test_red_phase_emits_no_test_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")

            # RED response forgets to make it a test file (no .test or test_ prefix).
            red_response = _ff_block(
                "regular_module.py",
                "def foo(): pass",
                lang="py",
            )
            call = _make_stub_llm(red_response, "")

            result = run_tdd_task(
                task=_is_tdd_task_stub(),
                root=root,
                base_prompt="-- task prompt --",
                call_llm=call,
                write_files=_write_extracted_files,
                extract_files=_extract_files_from_response,
            )
            self.assertEqual(result["status"], "failed")
            self.assertIn("no test files", result["failure"].lower())


class RunTddTaskNoRunnerFallback(unittest.TestCase):
    def test_workspace_without_runner_runs_as_single_pass(self) -> None:
        """If we can't detect a test runner, TDD enforcement collapses
        to advisory -- we run the task as a normal single-shot call."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # No package.json, no pyproject -> no runner.
            single_response = _ff_block(
                "out.txt",
                "hello",
                lang="",
            )

            def call(_p: str) -> tuple[str, dict]:
                return (single_response, {"status": "completed", "step_id": "s1"})

            result = run_tdd_task(
                task=_is_tdd_task_stub(),
                root=root,
                base_prompt="-- task prompt --",
                call_llm=call,
                write_files=_write_extracted_files,
                extract_files=_extract_files_from_response,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result.get("tdd_note"), "no test runner; ran as single-pass task")


if __name__ == "__main__":
    unittest.main()
