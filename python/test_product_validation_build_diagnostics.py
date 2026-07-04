"""Tests for PART 1: real build-based validation with per-file diagnostics.

For the react-vite profile, validation must run a REAL build of the
generated app and capture compile/test errors as STRUCTURED, per-file
failures the repair loop can act on:

  {file, line, code, message}

These tests are unittest-based (per the required suite runner) and are
split into two tiers:

  * Pure-parser tests: feed known-bad tsc/vitest output text and assert
    the structured per-file failures are extracted. These run everywhere,
    fast, no toolchain required.

  * run_validation integration tests: assert that a react-vite result
    surfaces structured ``violations`` and ``can_close_delivery=False``
    when the build fails, and that dry-run stays fast (no build). The
    real-npm end-to-end build test is opt-in behind an env flag so the
    default suite stays fast/hermetic, but the wiring (parser -> result)
    is asserted with injected output.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product import validation as V
from signalos_lib.product.validation import (
    build_validation_plan,
    parse_build_diagnostics,
    parse_test_diagnostics,
    run_validation,
)


# Real tsc output captured from `npx tsc --noEmit` on a strict project.
_TSC_OUTPUT = (
    "src/bad.ts(1,7): error TS2322: Type 'string' is not assignable to type "
    "'number'.\n"
    "src/components/Widget.tsx(42,13): error TS2339: Property 'foo' does not "
    "exist on type 'Props'.\n"
)

# vitest run output includes ANSI color codes and FAIL blocks.
_VITEST_OUTPUT = (
    "\x1b[41m\x1b[1m FAIL \x1b[22m\x1b[49m src/math.test.ts\x1b[2m > "
    "\x1b[22mfails on purpose\n"
    "\x1b[31m\x1b[1mAssertionError\x1b[22m: expected 2 to be 3 // Object.is "
    "equality\x1b[39m\n"
    "\x1b[36m \x1b[2m\xe2\x9d\xaf\x1b[22m src/math.test.ts:\x1b[2m2:48"
    "\x1b[22m\x1b[39m\n"
)


class TestParseBuildDiagnostics(unittest.TestCase):
    def test_parses_tsc_per_file_errors(self):
        failures = parse_build_diagnostics(_TSC_OUTPUT)
        self.assertEqual(len(failures), 2)
        first = failures[0]
        self.assertEqual(first["file"], "src/bad.ts")
        self.assertEqual(first["line"], 1)
        self.assertEqual(first["code"], "TS2322")
        self.assertIn("not assignable", first["message"])

    def test_parses_second_file(self):
        failures = parse_build_diagnostics(_TSC_OUTPUT)
        second = failures[1]
        self.assertEqual(second["file"], "src/components/Widget.tsx")
        self.assertEqual(second["line"], 42)
        self.assertEqual(second["code"], "TS2339")

    def test_shape_is_structured(self):
        for f in parse_build_diagnostics(_TSC_OUTPUT):
            self.assertEqual(set(f) >= {"file", "line", "code", "message"}, True)
            self.assertIsInstance(f["file"], str)
            self.assertIsInstance(f["line"], int)

    def test_clean_output_yields_no_failures(self):
        self.assertEqual(parse_build_diagnostics("Build complete.\n"), [])
        self.assertEqual(parse_build_diagnostics(""), [])


class TestParseTestDiagnostics(unittest.TestCase):
    def test_parses_vitest_failure_with_file_and_line(self):
        failures = parse_test_diagnostics(_VITEST_OUTPUT)
        self.assertTrue(failures, "expected at least one vitest failure")
        f = failures[0]
        self.assertEqual(f["file"], "src/math.test.ts")
        # ANSI stripped, message readable.
        self.assertNotIn("\x1b", f["message"])
        self.assertIn("src/math.test.ts", " ".join(
            str(x.get("file")) for x in failures
        ))

    def test_captures_line_when_present(self):
        failures = parse_test_diagnostics(_VITEST_OUTPUT)
        lines = [f.get("line") for f in failures]
        self.assertIn(2, lines)

    def test_clean_output_yields_no_failures(self):
        clean = "\x1b[32m Test Files 1 passed\x1b[39m\n Tests 3 passed\n"
        self.assertEqual(parse_test_diagnostics(clean), [])


class TestReactViteBadDirReportsStructuredErrors(unittest.TestCase):
    """A known-bad generated dir -> per-file tsc errors + cannot close.

    Wiring test: we do NOT require npm here. We inject a failed build
    category output (real tsc text) and assert run_validation normalizes
    it into structured, per-file violations and blocks closure.
    """

    def test_bad_build_output_becomes_structured_violations(self):
        # Inject a failing build by running a script file that emits real
        # tsc text to stdout and exits non-zero, so no npm toolchain is
        # needed but the react-vite diagnostic parser still runs on the
        # output. A file avoids fragile inline shell quoting.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            script = repo / "emit_tsc.py"
            script.write_text(
                "import sys\n"
                "sys.stdout.write("
                "\"src/App.tsx(3,10): error TS2304: Cannot find name 'Widget'.\\n\")\n"
                "sys.exit(2)\n",
                encoding="utf-8",
            )
            plan = build_validation_plan(repo, "react-vite")
            plan["install"] = []
            plan["build"] = [f'{_py()} {_q(script)}']
            plan["test"] = []
            result = run_validation(repo, plan)

        self.assertEqual(result["results"]["build"]["status"], "failed")
        self.assertFalse(result["can_close_delivery"])
        violations = result.get("violations", [])
        self.assertTrue(violations, "expected structured per-file violations")
        v = violations[0]
        self.assertEqual(v["file"], "src/App.tsx")
        self.assertEqual(v["line"], 3)
        self.assertEqual(v["code"], "TS2304")
        self.assertEqual(v["category"], "build")
        # Per-category failures also attached.
        self.assertTrue(result["results"]["build"].get("failures"))

    def test_non_react_vite_does_not_add_violations_key_falsely(self):
        # generic profile keeps existing behavior: no structured tsc parse.
        plan = build_validation_plan(Path("."), "generic")
        result = run_validation(Path("."), plan)
        # violations key exists but is empty for non-JS profiles.
        self.assertEqual(result.get("violations", []), [])


class TestReactViteCleanDirPasses(unittest.TestCase):
    def test_clean_build_and_test_can_close(self):
        plan = build_validation_plan(Path("."), "react-vite")
        plan["install"] = []
        plan["build"] = [f'{_py()} -c "print(\'Build complete\')"']
        plan["test"] = [f'{_py()} -c "print(\'ok\')"']
        result = run_validation(Path("."), plan)
        self.assertEqual(result["results"]["build"]["status"], "passed")
        self.assertEqual(result["results"]["test"]["status"], "passed")
        self.assertTrue(result["can_close_delivery"])
        self.assertEqual(result.get("violations", []), [])


class TestDryRunStaysFast(unittest.TestCase):
    def test_dry_run_skips_build_and_has_no_violations(self):
        plan = build_validation_plan(Path("."), "react-vite")
        result = run_validation(Path("."), plan, dry_run=True)
        for cat_result in result["results"].values():
            self.assertEqual(cat_result["status"], "skipped")
        self.assertFalse(result["can_close_delivery"])
        self.assertEqual(result.get("violations", []), [])


@unittest.skipUnless(
    os.environ.get("SIGNALOS_RUN_REAL_BUILD") == "1",
    "opt-in real npm build (set SIGNALOS_RUN_REAL_BUILD=1)",
)
class TestReactViteRealBuildEndToEnd(unittest.TestCase):
    """Opt-in: scaffold a react-vite app, inject a type error, real build."""

    def test_real_tsc_error_is_captured(self):
        import tempfile
        from signalos_lib.product.stacks import get_adapter

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            get_adapter("react-vite").scaffold(repo, {"product_name": "x"})
            (repo / "src" / "App.tsx").write_text(
                "function App(){ const n: number = 'x'; return <h1>{n}</h1>; }\n"
                "export default App;\n",
                encoding="utf-8",
            )
            plan = build_validation_plan(repo, "react-vite")
            result = run_validation(repo, plan)
            self.assertFalse(result["can_close_delivery"])
            self.assertTrue(result.get("violations"))


def _py() -> str:
    return _q(sys.executable)


def _q(path) -> str:
    """Quote a path for the validation command splitter (forward slashes)."""
    return '"' + str(path).replace("\\", "/") + '"'


if __name__ == "__main__":
    unittest.main()


class InstallCommandTimeout(unittest.TestCase):
    """#41-env: dependency install gets a larger, separate timeout so a slow
    install is not misread as a build/test failure (the funded e2e cascade)."""

    def test_install_commands_detected(self):
        for cmd in ("npm install --legacy-peer-deps", "npm ci", "pnpm install",
                    "yarn install", "pip install -r requirements.txt",
                    "dotnet restore", "go mod download"):
            self.assertTrue(V._is_install_command(cmd), cmd)

    def test_non_install_commands_not_detected(self):
        for cmd in ("npm run build", "tsc --noEmit", "vitest run",
                    "npm run test", "go build ./..."):
            self.assertFalse(V._is_install_command(cmd), cmd)

    def test_install_timeout_is_at_least_base(self):
        self.assertGreaterEqual(
            V._validation_install_timeout_s(), V._validation_command_timeout_s()
        )

    def test_install_timeout_default_exceeds_300(self):
        # With no env override the install budget must exceed the 300s that
        # timed out Mantine's install in the funded run.
        import os as _os
        saved = _os.environ.pop("SIGNALOS_VALIDATION_INSTALL_TIMEOUT_S", None)
        saved_base = _os.environ.pop("SIGNALOS_VALIDATION_COMMAND_TIMEOUT_S", None)
        try:
            self.assertGreater(V._validation_install_timeout_s(), 300)
        finally:
            if saved is not None:
                _os.environ["SIGNALOS_VALIDATION_INSTALL_TIMEOUT_S"] = saved
            if saved_base is not None:
                _os.environ["SIGNALOS_VALIDATION_COMMAND_TIMEOUT_S"] = saved_base
