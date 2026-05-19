"""test_e2e_runner.py - Smart-path tests for the E2E runner.

We can't reliably install Chromium + spawn a real dev server in unit
tests, so we cover:
  - capability detection (playwright_available, detect_dev_server_command)
  - task tagging (is_e2e_task)
  - selector extraction from task descriptions
  - the "skipped" / "advisory" paths (no dev script, no playwright)

The real-Playwright path is covered by the dedicated integration test
the user runs against an actual workspace -- not by these unit tests.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.e2e_runner import (
    detect_dev_server_command,
    is_e2e_task,
    playwright_available,
    run_e2e_task,
    _extract_selectors_from_description,
)


class IsE2eTask(unittest.TestCase):
    def test_true_when_skill_tagged(self) -> None:
        self.assertTrue(is_e2e_task({"skills": ["e2e-testing"]}))

    def test_false_when_only_unit_test_skill_tagged(self) -> None:
        self.assertFalse(is_e2e_task({"skills": ["test-generation"]}))

    def test_false_when_no_skills(self) -> None:
        self.assertFalse(is_e2e_task({}))


class DetectDevServerCommand(unittest.TestCase):
    def test_returns_npm_run_dev_when_dev_script_present(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "scripts": {"dev": "vite", "test": "vitest"},
            }))
            cmd = detect_dev_server_command(root)
            self.assertEqual(cmd, ["npm", "run", "dev"])

    def test_falls_back_to_start_then_serve(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "scripts": {"start": "node server"},
            }))
            self.assertEqual(detect_dev_server_command(root), ["npm", "run", "start"])

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "scripts": {"serve": "http-server"},
            }))
            self.assertEqual(detect_dev_server_command(root), ["npm", "run", "serve"])

    def test_none_when_no_package_json(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(detect_dev_server_command(Path(d)))

    def test_none_when_no_runnable_script(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "scripts": {"test": "vitest", "build": "tsc"},
            }))
            self.assertIsNone(detect_dev_server_command(root))


class PlaywrightAvailable(unittest.TestCase):
    def test_detects_at_playwright_test_in_devDependencies(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "devDependencies": {"@playwright/test": "^1.40.0"},
            }))
            self.assertTrue(playwright_available(root))

    def test_detects_playwright_in_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "dependencies": {"playwright": "^1.40.0"},
            }))
            self.assertTrue(playwright_available(root))

    def test_returns_false_when_no_package_and_no_binary(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            # On CI runners, `playwright` is unlikely on PATH; we accept
            # that this could be True locally. The test asserts only the
            # negative path -- no package.json, no PATH binary.
            root = Path(d)
            # If playwright happens to be on PATH (developer machine), skip.
            import shutil as _sh
            if _sh.which("playwright") is not None:
                self.skipTest("playwright on PATH; test only valid in clean env")
            self.assertFalse(playwright_available(root))


class ExtractSelectors(unittest.TestCase):
    def test_parses_single_selectors_line(self) -> None:
        desc = "Build the login form.\nSelectors: button[data-test=submit], input[name=email]"
        self.assertEqual(
            _extract_selectors_from_description(desc),
            ["button[data-test=submit]", "input[name=email]"],
        )

    def test_case_insensitive_header(self) -> None:
        desc = "Add a thing\nselectors: .primary-button"
        self.assertEqual(
            _extract_selectors_from_description(desc),
            [".primary-button"],
        )

    def test_no_selectors_line_returns_empty(self) -> None:
        desc = "Build the login form. No specific selectors."
        self.assertEqual(_extract_selectors_from_description(desc), [])

    def test_empty_description_returns_empty(self) -> None:
        self.assertEqual(_extract_selectors_from_description(""), [])


class RunE2eTaskAdvisoryFallbacks(unittest.TestCase):
    """The runner is advisory (returns ok=True, skipped=True) when the
    project isn't set up for e2e -- no dev script or no Playwright.
    This keeps tasks tagged e2e-testing in workspaces that just aren't
    web projects from breaking the wave."""

    def test_no_dev_script_returns_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = run_e2e_task(
                {"skills": ["e2e-testing"], "description": ""},
                Path(d),
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("skipped"))
            self.assertIn("no dev script", result["log"])

    def test_no_playwright_returns_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "scripts": {"dev": "vite"},
                # No @playwright/test in deps; no playwright on PATH.
            }))
            import shutil as _sh
            if _sh.which("playwright") is not None:
                self.skipTest("playwright on PATH; test only valid in clean env")
            result = run_e2e_task(
                {"skills": ["e2e-testing"], "description": ""},
                root,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("skipped"))
            self.assertIn("playwright", result["log"].lower())


if __name__ == "__main__":
    unittest.main()
