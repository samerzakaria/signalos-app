"""test_orchestrator_gate_floor.py — AMD-CORE-110 Layer 3 router tests.

Per WAVE-ENGINE-DESIGN §10: the orchestrator's gate-state function is the
wave-engine router, not a refuse-by-default check. It decides:

  - "build"               → all prior gates (G0..G3) signed; proceed
  - "fire-agent-G{N}"     → gate N is the next unsigned gate; caller must
                             route into that gate's agent before build
  - "refuse-pathological" → status read failed or gate state corrupt;
                             fail closed for safety
  - "override-with-audit" → SIGNALOS_GATE_OVERRIDE=1 set (CI/headless
                             only) → proceed + log violation

These tests don't run an LLM — they exercise the router function directly
and assert correct branching + audit-trail entries.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.orchestrator import (
    _route_next_gate_action,
    run_wave,
)


class RouteNextGateActionTests(unittest.TestCase):
    """Direct tests on _route_next_gate_action — the router."""

    def setUp(self):
        self._saved_override = os.environ.pop("SIGNALOS_GATE_OVERRIDE", None)

    def tearDown(self):
        if self._saved_override is not None:
            os.environ["SIGNALOS_GATE_OVERRIDE"] = self._saved_override

    def _mk_workspace(self):
        return Path(tempfile.mkdtemp(prefix="signalos-router-"))

    def test_routes_to_g0_agent_when_g0_unsigned(self):
        root = self._mk_workspace()
        with mock.patch("signalos_lib.status.get_wave_status",
                        return_value={"gates": {"G0": False, "G1": False, "G2": False, "G3": False}}):
            result = _route_next_gate_action(root, "1", "session-x")
        self.assertEqual(result["action"], "fire-agent-G0")
        self.assertEqual(result["current_gate"], "G0")
        self.assertIn("G0", result["evidence"])
        # No enforcement-refused audit entry — routing is normal flow, not a refusal.
        trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
        if trail.is_file():
            entries = [json.loads(line) for line in trail.read_text().splitlines() if line.strip()]
            refusals = [e for e in entries if e.get("action") == "enforcement-refused-orchestrate"]
            self.assertEqual(len(refusals), 0, "routing must not produce refusal audit entries")

    def test_routes_to_g1_agent_when_g0_signed_g1_unsigned(self):
        root = self._mk_workspace()
        with mock.patch("signalos_lib.status.get_wave_status",
                        return_value={"gates": {"G0": True, "G1": False, "G2": False, "G3": False}}):
            result = _route_next_gate_action(root, "1", "s")
        self.assertEqual(result["action"], "fire-agent-G1")
        self.assertEqual(result["current_gate"], "G1")

    def test_routes_to_g3_agent_when_g0_g1_g2_signed_g3_unsigned(self):
        """G3 (design) is the new gate per WAVE-ENGINE-DESIGN — it's a
        prior of G4, so the router asks for G3 before allowing build."""
        root = self._mk_workspace()
        with mock.patch("signalos_lib.status.get_wave_status",
                        return_value={"gates": {"G0": True, "G1": True, "G2": True, "G3": False}}):
            result = _route_next_gate_action(root, "1", "s")
        self.assertEqual(result["action"], "fire-agent-G3")
        self.assertEqual(result["current_gate"], "G3")

    def test_routes_to_build_when_g0_through_g3_all_signed(self):
        root = self._mk_workspace()
        with mock.patch("signalos_lib.status.get_wave_status",
                        return_value={"gates": {"G0": True, "G1": True, "G2": True, "G3": True}}):
            result = _route_next_gate_action(root, "1", "s")
        self.assertEqual(result["action"], "build")
        self.assertEqual(result["current_gate"], "G4")

    def test_override_proceeds_but_logs_violation(self):
        """Per AMD-CORE-111: SIGNALOS_GATE_OVERRIDE=1 (headless / CI) lets
        the orchestrator proceed even with unsigned prior gates, but the
        skip is recorded as a violation."""
        root = self._mk_workspace()
        os.environ["SIGNALOS_GATE_OVERRIDE"] = "1"
        try:
            with mock.patch("signalos_lib.status.get_wave_status",
                            return_value={"gates": {"G0": False, "G1": False, "G2": False, "G3": False}}):
                result = _route_next_gate_action(root, "1", "s")
        finally:
            os.environ.pop("SIGNALOS_GATE_OVERRIDE", None)
        self.assertEqual(result["action"], "override-with-audit")
        # Violation logged.
        trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
        entries = [json.loads(line) for line in trail.read_text().splitlines() if line.strip()]
        violations = [e for e in entries if e.get("action") == "violation:orchestrate-gate-skip"]
        self.assertEqual(len(violations), 1)
        # Violation entry names the gate that was skipped.
        self.assertEqual(violations[0]["missing_gate"], "G0")

    def test_refuse_pathological_when_status_read_raises(self):
        """A broken status module must NOT silently allow dispatch."""
        root = self._mk_workspace()
        with mock.patch("signalos_lib.status.get_wave_status",
                        side_effect=OSError("disk failure")):
            result = _route_next_gate_action(root, "1", "s")
        self.assertEqual(result["action"], "refuse-pathological")
        self.assertIsNone(result["current_gate"])
        # The pathological-refusal IS recorded — this distinguishes a
        # genuine system failure from routine routing.
        trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
        entries = [json.loads(line) for line in trail.read_text().splitlines() if line.strip()]
        errors = [e for e in entries if e.get("action") == "enforcement-error-orchestrate-gate-check"]
        self.assertEqual(len(errors), 1)

    def test_project_id_passed_through_to_audit_entries(self):
        """The project_id parameter (plumbing for future multi-project UI
        per design §3.2) appears in every audit entry the router writes."""
        root = self._mk_workspace()
        os.environ["SIGNALOS_GATE_OVERRIDE"] = "1"
        try:
            with mock.patch("signalos_lib.status.get_wave_status",
                            return_value={"gates": {"G0": False}}):
                _route_next_gate_action(root, "1", "s", project_id="alpha")
        finally:
            os.environ.pop("SIGNALOS_GATE_OVERRIDE", None)
        trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
        entries = [json.loads(line) for line in trail.read_text().splitlines() if line.strip()]
        self.assertEqual(entries[0]["project_id"], "alpha")

    def test_project_id_defaults_to_default(self):
        """When the caller doesn't pass project_id, audit entries record
        'default' — preserving today's single-project workspace behaviour."""
        root = self._mk_workspace()
        with mock.patch("signalos_lib.status.get_wave_status",
                        side_effect=OSError("disk failure")):
            _route_next_gate_action(root, "1", "s")
        trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
        entries = [json.loads(line) for line in trail.read_text().splitlines() if line.strip()]
        self.assertEqual(entries[0]["project_id"], "default")


class RunWaveRoutingTests(unittest.TestCase):
    """Higher-level: run_wave returns the right status based on routing."""

    def setUp(self):
        self._saved_override = os.environ.pop("SIGNALOS_GATE_OVERRIDE", None)

    def tearDown(self):
        if self._saved_override is not None:
            os.environ["SIGNALOS_GATE_OVERRIDE"] = self._saved_override

    def test_run_wave_returns_needs_gate_when_prior_gate_unsigned(self):
        """needs_gate is the non-refusal signal that a gate-agent must
        fire first. The contract change vs the earlier 'blocked_by_gate'
        is semantic: the orchestrator isn't refusing, it's pointing at
        what's next."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir()
            with mock.patch("signalos_lib.status.get_wave_status",
                            return_value={"gates": {"G0": False, "G1": False, "G2": False, "G3": False}}):
                result = run_wave("1", "PLAN.tasks.yaml", session_id="s", cwd=root)
        self.assertEqual(result["status"], "needs_gate")
        self.assertEqual(result["completed"], 0)
        self.assertIn("route", result)
        self.assertEqual(result["route"]["action"], "fire-agent-G0")

    def test_run_wave_blocked_by_status_error_on_pathological_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir()
            with mock.patch("signalos_lib.status.get_wave_status",
                            side_effect=OSError("disk failure")):
                result = run_wave("1", "PLAN.tasks.yaml", session_id="s", cwd=root)
        self.assertEqual(result["status"], "blocked_by_status_error")
        self.assertEqual(result["route"]["action"], "refuse-pathological")


class ProjectIdPlumbingTests(unittest.TestCase):
    """M-W1 step 2: project_id threads through run_wave, status, IPC.

    Per WAVE-ENGINE-DESIGN §3.2 the parameter is plumbing for future
    multi-project UI exposure; today only 'default' flows from callers,
    but each state-touching surface must accept and round-trip it so
    that future UI changes don't need an engine refactor.
    """

    def test_run_wave_threads_project_id_into_router_audit_entries(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / ".signalos").mkdir()
            with mock.patch("signalos_lib.status.get_wave_status",
                            side_effect=OSError("disk failure")):
                run_wave("1", "PLAN.tasks.yaml", session_id="s",
                         cwd=root, project_id="alpha")
            trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
            entries = [json.loads(line) for line in trail.read_text().splitlines() if line.strip()]
        self.assertTrue(any(e.get("project_id") == "alpha" for e in entries),
                        "run_wave must forward project_id to the router's audit entries")

    def test_run_wave_default_project_id_when_not_passed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir()
            with mock.patch("signalos_lib.status.get_wave_status",
                            side_effect=OSError("disk failure")):
                result = run_wave("1", "PLAN.tasks.yaml", session_id="s", cwd=root)
        self.assertEqual(result["project_id"], "default")

    def test_run_wave_returns_project_id_in_needs_gate_summary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir()
            with mock.patch("signalos_lib.status.get_wave_status",
                            return_value={"gates": {"G0": False}}):
                result = run_wave("1", "PLAN.tasks.yaml", session_id="s",
                                  cwd=root, project_id="beta")
        self.assertEqual(result["status"], "needs_gate")
        self.assertEqual(result["project_id"], "beta")

    def test_get_wave_status_returns_project_id_in_payload(self):
        """status.get_wave_status round-trips project_id so downstream
        consumers (UI, IPC) can render it without re-querying."""
        from signalos_lib.status import get_wave_status
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir()
            data = get_wave_status(root, project_id="alpha")
        self.assertEqual(data["project_id"], "alpha")

    def test_build_status_json_returns_project_id_in_payload(self):
        from signalos_lib.status import build_status_json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir()
            data = build_status_json(root, project_id="gamma")
        self.assertEqual(data["project_id"], "gamma")

    def test_get_wave_status_defaults_project_id_to_default(self):
        from signalos_lib.status import get_wave_status
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir()
            data = get_wave_status(root)
        self.assertEqual(data["project_id"], "default")


if __name__ == "__main__":
    unittest.main()
