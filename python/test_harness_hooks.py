# test_harness_hooks.py
# Security/hooks cluster (#19): the harness hook shell-out must EXPORT the
# step-spec so step-pause-check.sh's pause gate can actually engage.
#
# step-pause-check.sh is env-driven and requires SIGNALOS_PLAN_STEP_JSON
# (step-pause-check.sh:47). step-started.sh only sources it when that env var
# is set (step-started.sh:129). Before this fix, harness._fire_hook ran the
# hook subprocess with no env= and never exported SIGNALOS_PLAN_STEP_JSON, so
# the pause gate could never fire — an inert hook masquerading as live.
#
# These tests pin the wiring: for a step-started hook with a known step-spec,
# _fire_hook must pass env with SIGNALOS_PLAN_STEP_JSON = json(step_spec).

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import harness


def _make_hook(root: Path, event: str) -> None:
    """Create a stub hook script so _fire_hook does not fail-open."""
    hook_dir = root / "core" / "execution" / "hooks" / event
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / f"{event}.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")


class _CapturingRun:
    """Stand-in for subprocess.run that records the kwargs it was called with."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, argv, *args, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        return types.SimpleNamespace(returncode=0)


class TestFireHookExportsStepSpec(unittest.TestCase):
    def _fire(self, event: str, step_spec=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_hook(root, event)
            cap = _CapturingRun()
            # Neutralize the sandbox wrapper so argv/env are unchanged.
            with patch.object(harness.subprocess, "run", cap), patch(
                "signalos_lib.sandbox.maybe_wrap_for_sandbox",
                lambda root, argv: (argv, None),
            ):
                rc = harness._fire_hook(
                    root,
                    event,
                    session_id="sid-1",
                    step_id="phase-3a.build-4",
                    step_spec=step_spec,
                )
            self.assertEqual(rc, 0)
            self.assertEqual(len(cap.calls), 1)
            return cap.calls[0]

    def test_step_started_exports_plan_step_json(self):
        spec = {"pause": True, "tier": "T2"}
        call = self._fire("step-started", step_spec=spec)
        env = call["kwargs"].get("env")
        self.assertIsNotNone(
            env,
            "step-started _fire_hook must pass an env= so SIGNALOS_PLAN_STEP_JSON "
            "can be exported to step-pause-check.sh",
        )
        self.assertIn("SIGNALOS_PLAN_STEP_JSON", env)
        self.assertEqual(json.loads(env["SIGNALOS_PLAN_STEP_JSON"]), spec)
        # The parent environment must be inherited, not replaced wholesale.
        import os

        for key in list(os.environ)[:1]:
            self.assertIn(key, env)

    def test_no_step_spec_does_not_export_plan_step_json(self):
        # No step-spec means no pause contract — must NOT inject the var
        # (an empty/garbage value would make step-pause-check.sh error out).
        call = self._fire("step-started", step_spec=None)
        env = call["kwargs"].get("env")
        if env is not None:
            self.assertNotIn("SIGNALOS_PLAN_STEP_JSON", env)

    def test_non_step_started_event_does_not_export_plan_step_json(self):
        # The pause gate is only sourced by step-started.sh; other events must
        # not carry the step-spec env even if one is threaded through.
        call = self._fire("step-completed", step_spec={"pause": True})
        env = call["kwargs"].get("env")
        if env is not None:
            self.assertNotIn("SIGNALOS_PLAN_STEP_JSON", env)


if __name__ == "__main__":
    unittest.main()
