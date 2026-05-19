"""tdd_runner.py - Real TDD loop with red->green test execution.

When a task is tagged `test-driven-development`, the orchestrator
dispatches it through this module instead of the normal one-shot LLM
call. The flow:

  Phase 1 (RED):
    Prompt the LLM to write ONLY the test file(s). Write them. Run the
    test runner against them. The test MUST fail -- if it passes, that's
    a TDD violation: the test is wrong, or there's no bug to test for.

  Phase 2 (GREEN):
    Prompt the LLM with the failing-test output and ask for the
    implementation that makes them pass. Write the impl. Run the
    tests again. The test MUST pass -- if it still fails, we feed
    the new failure output into a smart retry (capped at 2).

This is the only enforcement layer in the bundle that actually runs
code. Every other validator checks file shapes; TDD runs the tests
and observes the outcome. That's what makes red->green meaningful
rather than advisory.

Test runners auto-detected from the workspace:
  - vitest:  package.json has `vitest` in deps + scripts.test
  - jest:    package.json has `jest` in deps + scripts.test
  - pytest:  pyproject.toml / setup.py / *.py test files present
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "is_tdd_task",
    "run_tdd_task",
    "detect_test_runner",
    "run_tests_for_files",
]


# ---------------------------------------------------------------------------
# Runner detection
# ---------------------------------------------------------------------------

TestRunner = tuple[str, list[str]]  # (display_name, base_cmd_to_extend)


def detect_test_runner(root: Path) -> TestRunner | None:
    """Inspect *root* and return how to run a single test file.

    Returns (name, base_cmd) where base_cmd is a shell-safe list ready
    to extend with the test-file path. None means we couldn't detect a
    supported runner and TDD enforcement falls back to advisory.
    """
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        deps = {
            **(data.get("dependencies") or {}),
            **(data.get("devDependencies") or {}),
        }
        scripts = data.get("scripts") or {}
        if "vitest" in deps:
            # Vitest: `npx vitest run <file>` is the canonical "run this
            # single file once and exit" invocation.
            return ("vitest", ["npx", "vitest", "run"])
        if "jest" in deps:
            return ("jest", ["npx", "jest"])
        # Fallback to whatever `scripts.test` is, if it exists.
        if "test" in scripts:
            return ("npm test", ["npm", "test", "--"])

    # Python project hints: pyproject.toml / setup.py / setup.cfg, OR
    # any *test_*.py / *_test.py files in the workspace.
    if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file() \
            or (root / "setup.cfg").is_file():
        return ("pytest", [sys.executable, "-m", "pytest", "-x", "-q"])

    return None


# ---------------------------------------------------------------------------
# Test file filtering / running
# ---------------------------------------------------------------------------

_TEST_FILE_RE = re.compile(
    r"(?:\.test\.|\.spec\.|^test_|_test\.)",
    re.IGNORECASE,
)


def _is_test_file(rel: str) -> bool:
    return bool(_TEST_FILE_RE.search(Path(rel).name))


def _split_test_vs_impl(files: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    tests: list[tuple[str, str]] = []
    impls: list[tuple[str, str]] = []
    for f in files:
        (tests if _is_test_file(f[0]) else impls).append(f)
    return tests, impls


def run_tests_for_files(
    root: Path,
    runner: TestRunner,
    test_file_paths: list[str],
    timeout_sec: int = 120,
) -> tuple[bool, str]:
    """Run *runner* against *test_file_paths* and return (passed, output).

    `passed` is True iff exit code == 0. Output is the combined stdout
    + stderr, capped at a sane size so we don't blow the next prompt's
    context.

    Sandbox routing: if .signalos/sandbox.json has enabled=true AND
    Docker is running, the command is wrapped in `docker run` and
    executed inside the container with the workspace mounted at
    /workspace. The TDD loop is the first beachhead for sandboxed
    execution; the preview path and orchestrator subprocess calls
    still run on host pending future work.
    """
    _name, base_cmd = runner
    cmd = base_cmd + list(test_file_paths)
    # Wrap in `docker run` when sandboxed mode is enabled.
    from signalos_lib.sandbox import maybe_wrap_for_sandbox
    cmd, sandboxed = maybe_wrap_for_sandbox(root, cmd)
    if sandboxed:
        import sys as _sys
        _sys.stdout.write(f"[tdd] running tests inside Docker sandbox (image from .signalos/sandbox.json)\n")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return (False, f"Test runner timed out after {timeout_sec}s.")
    except FileNotFoundError as exc:
        return (False, f"Test runner binary not found: {exc}")
    except OSError as exc:
        return (False, f"Test runner OS error: {exc}")

    output = (proc.stdout or "") + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else "")
    output = output.strip()
    if len(output) > 6000:
        output = output[:3000] + "\n\n[...output truncated for prompt budget...]\n\n" + output[-3000:]
    return (proc.returncode == 0, output)


# ---------------------------------------------------------------------------
# TDD task entrypoint
# ---------------------------------------------------------------------------

def is_tdd_task(task: dict) -> bool:
    """True iff this task should go through the TDD red->green loop."""
    skills = task.get("skills") or []
    return "test-driven-development" in skills


def _build_red_phase_prompt(task: dict, base_prompt: str) -> str:
    """Phase 1 instruction: write ONLY the failing test."""
    return (
        f"## TDD PHASE 1 OF 2 — WRITE A FAILING TEST\n\n"
        f"This task is tagged `test-driven-development`. You will be invoked TWICE: "
        f"once for the test, once for the implementation.\n\n"
        f"For THIS call you must emit ONLY test file(s). Do NOT emit the implementation. "
        f"A test file's name must contain `.test.`, `.spec.`, or `test_` (Python). "
        f"The test must EXERCISE the behaviour described below and FAIL against current code "
        f"(because the impl doesn't exist yet, or the bug being fixed is still present).\n\n"
        f"If you emit any non-test files in this phase, the orchestrator will discard them.\n\n"
        f"---\n\n"
        f"{base_prompt}"
    )


def _build_green_phase_prompt(
    task: dict,
    base_prompt: str,
    test_files_written: list[str],
    failure_output: str,
) -> str:
    """Phase 2 instruction: write the impl that makes the failing test pass."""
    tests_list = "\n".join(f"- {p}" for p in test_files_written)
    return (
        f"## TDD PHASE 2 OF 2 — WRITE THE IMPLEMENTATION\n\n"
        f"The failing test files are in place:\n{tests_list}\n\n"
        f"They currently FAIL with the following output:\n\n"
        f"```\n{failure_output}\n```\n\n"
        f"For THIS call you must emit the implementation files needed to make those "
        f"tests pass. Do NOT modify the test files -- if the test is wrong, leave it "
        f"and add a follow-up note instead. Do NOT emit additional test files.\n\n"
        f"---\n\n"
        f"{base_prompt}"
    )


def run_tdd_task(
    task: dict,
    root: Path,
    *,
    base_prompt: str,
    call_llm: Callable[[str], tuple[str, dict[str, Any]]],
    write_files: Callable[[Path, list[tuple[str, str]]], list[str]],
    extract_files: Callable[[str], list[tuple[str, str]]],
    emit_progress: Callable[[str, str | None], None] | None = None,
) -> dict[str, Any]:
    """Execute a TDD task as red->green with real test execution.

    Args:
      task:        the task dict from PLAN.tasks.yaml
      root:        workspace Path
      base_prompt: the full task prompt the orchestrator built (includes
                   skill content, file list, output protocol). We wrap
                   it with the phase-specific TDD framing.
      call_llm:    invocation closure that returns (response_text, raw_result_dict)
      write_files: closure that writes [(path, content), ...] under root
      extract_files: closure that parses LLM response -> [(path, content), ...]
      emit_progress: optional progress emitter (label, detail)

    Returns a result dict shaped like harness.run_step's output:
      { status: "completed"|"failed", failure: str|None, files_written: [...] }
    """
    def progress(label: str, detail: str | None = None) -> None:
        if emit_progress:
            try:
                emit_progress(label, detail)
            except Exception:  # pragma: no cover
                pass

    runner = detect_test_runner(root)
    if runner is None:
        # Can't run tests, so TDD enforcement collapses to advisory.
        # Mark with a warning + run as a normal task.
        progress("tdd-skip", "no supported test runner detected; running as normal task")
        response_text, raw = call_llm(base_prompt)
        files = extract_files(response_text)
        written = write_files(root, files)
        return {
            **raw,
            "status": "completed" if written else (raw.get("status") or "failed"),
            "files_written": written,
            "tdd_note": "no test runner; ran as single-pass task",
        }

    runner_name = runner[0]

    # ── PHASE 1: RED ──────────────────────────────────────────────────
    progress("tdd-red", f"writing failing test (using {runner_name})")
    red_prompt = _build_red_phase_prompt(task, base_prompt)
    red_response, red_raw = call_llm(red_prompt)
    red_files = extract_files(red_response)
    test_files, impl_files = _split_test_vs_impl(red_files)

    if not test_files:
        return {
            **red_raw,
            "status": "failed",
            "failure": (
                "TDD phase 1 (RED) failure: LLM produced no test files. "
                "Expected at least one file matching *.test.*, *.spec.*, "
                "test_*.py, or *_test.py."
            ),
        }
    if impl_files:
        progress(
            "tdd-red-warn",
            f"discarded {len(impl_files)} non-test file(s) emitted in RED phase",
        )

    test_written = write_files(root, test_files)
    if not test_written:
        return {
            **red_raw,
            "status": "failed",
            "failure": "TDD phase 1: write of test files failed.",
        }

    progress("tdd-red-run", f"running {len(test_written)} test file(s)")
    red_passed, red_output = run_tests_for_files(root, runner, test_written)

    if red_passed:
        return {
            **red_raw,
            "status": "failed",
            "failure": (
                "TDD phase 1 (RED) violation: tests PASSED before the "
                "implementation was written. Either the test doesn't "
                "actually exercise the new behaviour, or there's no bug "
                "to test for. Rewrite the test so it fails against "
                "current code, then we'll move to phase 2."
            ),
            "files_written": test_written,
            "tdd_red_output": red_output[:1500],
        }

    progress("tdd-red-ok", "tests failed as expected; proceeding to GREEN phase")

    # ── PHASE 2: GREEN ────────────────────────────────────────────────
    progress("tdd-green", "writing implementation")
    green_prompt = _build_green_phase_prompt(task, base_prompt, test_written, red_output)
    green_response, green_raw = call_llm(green_prompt)
    green_files = extract_files(green_response)
    _green_tests, green_impl = _split_test_vs_impl(green_files)

    if not green_impl:
        return {
            **green_raw,
            "status": "failed",
            "failure": (
                "TDD phase 2 (GREEN) failure: LLM produced no implementation "
                "files. The failing tests still need code that makes them pass."
            ),
            "files_written": test_written,
        }

    impl_written = write_files(root, green_impl)
    if not impl_written:
        return {
            **green_raw,
            "status": "failed",
            "failure": "TDD phase 2: write of implementation files failed.",
            "files_written": test_written,
        }

    progress("tdd-green-run", "running tests against new implementation")
    green_passed, green_output = run_tests_for_files(root, runner, test_written)

    all_written = test_written + impl_written

    if not green_passed:
        return {
            **green_raw,
            "status": "failed",
            "failure": (
                "TDD phase 2 (GREEN) failure: tests still failing after "
                "implementation was written. Failure output:\n\n"
                + green_output[:1500]
            ),
            "files_written": all_written,
            "tdd_green_output": green_output[:1500],
        }

    progress("tdd-done", f"red->green complete; {len(all_written)} files written")
    return {
        **green_raw,
        "status": "completed",
        "files_written": all_written,
        "tdd_phases": "red->green",
        "summary": f"TDD: {len(test_written)} test file(s) failed, then {len(impl_written)} impl file(s) made them pass",
    }
