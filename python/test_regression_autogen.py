"""test_regression_autogen.py — Milestone 5 / §6.8.4 regression auto-gen.

Three load-bearing behaviours:

  1. A failing scenario must auto-generate a regression entry under
     core/governance/QA/regressions/<slug>.yaml. This is the
     system-emitted half of regression governance — manual --generate
     is the escape hatch, the QA runner is the primary path.

  2. A passing scenario must NOT generate a regression entry. Auto-gen
     must be gated on the FAIL status, otherwise every QA run would
     produce noise.

  3. `signalos qa regression --run --pattern <glob>` must filter the
     replay set by filename glob, so users can re-run just the new
     regression without re-executing the whole suite.

We mock _run_scenario at the module level so we don't need a working
playwright browser; that's covered by the integration test suite.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import qa_runner as qa_runner_mod
from signalos_lib.qa_runner import (
    ScenarioResult,
    SCENARIO_FAIL,
    SCENARIO_PASS,
    run_scenario_suite,
)
from signalos_lib.regression import REGRESSIONS_DIR

# The QA runner uses PyYAML inside load_scenarios(). PyYAML is the
# scenario file format and an existing project dependency (see
# qa_runner._require_yaml). If it is not installed locally, skip these
# integration tests — auto-gen behaviour cannot be exercised without
# the runner being able to load the staged scenario YAML.
try:
    import yaml  # noqa: F401
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_scenario(dir_: Path, sid: str, name: str, url: str = "https://example.test/x") -> Path:
    """Write a minimal QA scenario YAML and return its path."""
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / f"{sid}.yaml"
    p.write_text(
        f"""id: {sid}
name: "{name}"
url: "{url}"
steps: []
assertions:
  - type: console_errors
evidence:
  screenshot: false
  vitals: false
""",
        encoding="utf-8",
    )
    return p


def _write_regression(dir_: Path, sid: str, name: str, url: str = "https://example.test/r") -> Path:
    """Write a regression scenario YAML (regressions are just scenarios
    that live in the regressions/ dir; the YAML shape is identical)."""
    return _write_scenario(dir_, sid, name, url)


class _ChdirTemp:
    """Context manager: enter a fresh temp dir as CWD (so the relative
    REGRESSIONS_DIR / evidence paths land somewhere disposable), restore
    afterward."""

    def __init__(self) -> None:
        self.tmp: tempfile.TemporaryDirectory | None = None
        self.prev: str | None = None

    def __enter__(self) -> Path:
        self.tmp = tempfile.TemporaryDirectory()
        self.prev = os.getcwd()
        os.chdir(self.tmp.name)
        return Path(self.tmp.name)

    def __exit__(self, *exc: object) -> None:
        if self.prev is not None:
            os.chdir(self.prev)
        if self.tmp is not None:
            # Windows can leave file handles open momentarily; if cleanup
            # raises, swallow it — the temp dir will be reaped by the OS.
            try:
                self.tmp.cleanup()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_YAML, "PyYAML not installed — QA runner integration skipped")
class TestFailingScenarioAutoGen(unittest.TestCase):
    """A failing scenario must auto-generate a regression entry."""

    def test_failing_scenario_auto_generates_regression_entry(self) -> None:
        with _ChdirTemp() as root:
            # Stage one scenario that will fail.
            scenarios_dir = root / "core" / "governance" / "QA" / "scenarios"
            _write_scenario(scenarios_dir, "qa-fail-001", "Failing login")

            # Force the scenario to FAIL by patching _run_scenario.
            def fake_run(scenario, scr_dir, capture_vitals):
                return ScenarioResult(
                    id=scenario["id"],
                    name=scenario["name"],
                    status=SCENARIO_FAIL,
                    duration_ms=10.0,
                    screenshot="",
                    vitals={},
                    assertions=[],
                    error="injected failure for test",
                )

            with patch.object(qa_runner_mod, "_run_scenario", side_effect=fake_run):
                pack = run_scenario_suite(
                    scenario_pattern=str(scenarios_dir / "*.yaml"),
                    regression_pattern=None,
                    wave="test",
                    output_path=str(root / "evidence.json"),
                    gating=False,
                    verbose=False,
                )

            self.assertEqual(pack.fail_count, 1)
            self.assertEqual(pack.pass_count, 0)

            # Auto-gen must have created a YAML under
            # core/governance/QA/regressions/. The filename starts with
            # reg-NNN-<slug>.yaml; we just assert the dir is non-empty.
            regressions_dir = root / REGRESSIONS_DIR
            self.assertTrue(
                regressions_dir.is_dir(),
                f"regressions dir not created: {regressions_dir}",
            )
            entries = sorted(regressions_dir.glob("reg-*.yaml"))
            self.assertEqual(
                len(entries), 1,
                f"expected exactly one regression file, got {entries}",
            )
            content = entries[0].read_text(encoding="utf-8")
            # The generated YAML must carry auto_generated: true and a
            # bug_ref that links back to the failing scenario id.
            self.assertIn("auto_generated: true", content)
            self.assertIn("qa-fail-001", content)


@unittest.skipUnless(_HAS_YAML, "PyYAML not installed — QA runner integration skipped")
class TestPassingScenarioDoesNotAutoGen(unittest.TestCase):
    """A passing scenario must not produce a regression entry."""

    def test_passing_scenario_does_not_generate_regression(self) -> None:
        with _ChdirTemp() as root:
            scenarios_dir = root / "core" / "governance" / "QA" / "scenarios"
            _write_scenario(scenarios_dir, "qa-pass-001", "Happy path")

            def fake_run(scenario, scr_dir, capture_vitals):
                return ScenarioResult(
                    id=scenario["id"],
                    name=scenario["name"],
                    status=SCENARIO_PASS,
                    duration_ms=5.0,
                    screenshot="",
                    vitals={},
                    assertions=[],
                    error=None,
                )

            with patch.object(qa_runner_mod, "_run_scenario", side_effect=fake_run):
                pack = run_scenario_suite(
                    scenario_pattern=str(scenarios_dir / "*.yaml"),
                    regression_pattern=None,
                    wave="test",
                    output_path=str(root / "evidence.json"),
                    gating=False,
                    verbose=False,
                )

            self.assertEqual(pack.pass_count, 1)
            self.assertEqual(pack.fail_count, 0)

            # No regressions dir should be auto-created on a clean pass.
            # (If something else makes it, the dir is empty.)
            regressions_dir = root / REGRESSIONS_DIR
            if regressions_dir.is_dir():
                entries = list(regressions_dir.glob("reg-*.yaml"))
                self.assertEqual(
                    entries, [],
                    f"passing scenarios must not generate regressions, found: {entries}",
                )


@unittest.skipUnless(_HAS_YAML, "PyYAML not installed — QA runner integration skipped")
class TestSignalosQaRegressionRunFilter(unittest.TestCase):
    """`signalos qa regression --run --pattern BUG-042*` runs only the
    regression scenarios whose filename matches the glob."""

    def test_signalos_qa_regression_run_filters_by_pattern(self) -> None:
        from signalos_lib.cli import main as cli_main

        with _ChdirTemp() as root:
            # Stage three regression scenarios with distinct bug ids in
            # their filename. The pattern "BUG-042*" should match only
            # the second.
            regressions_dir = root / REGRESSIONS_DIR
            _write_regression(regressions_dir, "reg-001-bug-041", "Login rate-limit regression")
            _write_regression(regressions_dir, "BUG-042-empty-list", "Empty list crash regression")
            _write_regression(regressions_dir, "reg-003-bug-043", "Stale token regression")

            # Capture every scenario id that gets executed.
            executed_ids: list[str] = []

            def fake_run(scenario, scr_dir, capture_vitals):
                executed_ids.append(scenario["id"])
                return ScenarioResult(
                    id=scenario["id"],
                    name=scenario["name"],
                    status=SCENARIO_PASS,
                    duration_ms=1.0,
                    screenshot="",
                    vitals={},
                    assertions=[],
                    error=None,
                )

            with patch.object(qa_runner_mod, "_run_scenario", side_effect=fake_run):
                rc = cli_main([
                    "signalos", "qa", "regression",
                    "--run", "--pattern", "BUG-042*",
                ])

            self.assertEqual(rc, 0, "qa regression --run should exit 0 when all pass")
            # Only the BUG-042 regression should have been executed.
            self.assertEqual(
                executed_ids,
                ["BUG-042-empty-list"],
                f"expected only BUG-042 scenario to run, got: {executed_ids}",
            )


if __name__ == "__main__":
    unittest.main()
