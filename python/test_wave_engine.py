"""test_wave_engine.py — M-W2 tests for the wave-engine state machine,
INSPECT, and scope-drift detection.

Per WAVE-ENGINE-DESIGN §3.1 / §6 / §7.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.wave_engine import (
    GATE_ORDER,
    WaveEngine,
    WaveState,
    _LEGAL_TRANSITIONS,
    detect_scope_drift,
    inspect,
)


def _mk_workspace_with_soul(soul_text: str | None = None) -> Path:
    """Make a temp workspace with optional signed Soul artifact.

    status._is_non_template requires ≥3 non-empty, non-comment, non-heading
    lines for an artifact to count as "signed" (filled). The helper pads
    short soul bodies with neutral stakeholder/success lines so test
    intent stays focused on the body text rather than scaffolding lines.
    """
    root = Path(tempfile.mkdtemp(prefix="signalos-wave-engine-"))
    (root / ".signalos").mkdir()
    if soul_text is not None:
        soul_dir = root / "core" / "governance" / "Governance"
        soul_dir.mkdir(parents=True, exist_ok=True)
        # Pad to ≥3 content lines if caller passed a short body.
        if soul_text.count("\n") < 3:
            soul_text = (
                soul_text.rstrip("\n")
                + "\n"
                + "Owner: PO.\nReviewer: lead engineer.\nReady when signed.\n"
            )
        (soul_dir / "SOUL-DOCUMENT.md").write_text(soul_text, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# State-machine transition table (§3.1)
# ---------------------------------------------------------------------------

class StateTransitionTests(unittest.TestCase):
    def test_default_initial_state_is_entry(self):
        eng = WaveEngine(Path("."))
        self.assertEqual(eng.state, WaveState.ENTRY)

    def test_legal_transition_succeeds(self):
        eng = WaveEngine(Path("."))
        eng.transition(WaveState.INSPECT)
        self.assertEqual(eng.state, WaveState.INSPECT)

    def test_illegal_transition_raises(self):
        eng = WaveEngine(Path("."))
        # ENTRY → DISPATCH is not legal (must go through INSPECT/DECIDE).
        with self.assertRaisesRegex(RuntimeError, "Illegal wave-engine transition"):
            eng.transition(WaveState.DISPATCH)

    def test_transition_history_is_recorded(self):
        eng = WaveEngine(Path("."))
        eng.transition(WaveState.INSPECT)
        eng.transition(WaveState.DECIDE)
        self.assertEqual(
            eng.history,
            [(WaveState.ENTRY, WaveState.INSPECT),
             (WaveState.INSPECT, WaveState.DECIDE)],
        )

    def test_terminal_state_has_no_outgoing_edges(self):
        self.assertEqual(_LEGAL_TRANSITIONS[WaveState.COMPLETE], set())

    def test_scope_drift_can_re_enter_inspect_or_entry(self):
        """§3.1 — SCOPE_DRIFT resolutions either re-fire G0 (INSPECT)
        or create a new project (ENTRY)."""
        legal = _LEGAL_TRANSITIONS[WaveState.SCOPE_DRIFT]
        self.assertIn(WaveState.INSPECT, legal)
        self.assertIn(WaveState.ENTRY, legal)


# ---------------------------------------------------------------------------
# INSPECT (§7)
# ---------------------------------------------------------------------------

class InspectTests(unittest.TestCase):
    def test_empty_workspace_reports_no_signed_gates(self):
        root = _mk_workspace_with_soul(None)
        result = inspect(root)
        self.assertEqual(result["project_id"], "default")
        self.assertEqual(result["next_gate"], "G0")
        self.assertFalse(result["all_signed"])
        for gate in GATE_ORDER:
            self.assertFalse(result["gates"][gate])
            self.assertFalse(result["artifacts"][gate]["signed"])
            self.assertFalse(result["artifacts"][gate]["exists"])

    def test_soul_present_marks_g0_signed_with_snippet(self):
        soul = (
            "For the team — a customer onboarding helper that captures "
            "their first wave of feature requests and routes them into "
            "our planning loop.\n"
            "Personal use only, but written for production team adoption.\n"
            "Stakeholders are the engineering team plus the product lead.\n"
            "Success means we ingest 10 onboarding tickets per day.\n"
        )
        root = _mk_workspace_with_soul(soul)
        result = inspect(root)
        self.assertTrue(result["gates"]["G0"])
        self.assertTrue(result["artifacts"]["G0"]["exists"])
        self.assertTrue(result["artifacts"]["G0"]["signed"])
        self.assertIn("customer onboarding helper", result["artifacts"]["G0"]["snippet"])
        # Next unsigned gate is G1.
        self.assertEqual(result["next_gate"], "G1")

    def test_project_id_is_round_tripped(self):
        root = _mk_workspace_with_soul(None)
        result = inspect(root, project_id="alpha")
        self.assertEqual(result["project_id"], "alpha")


# ---------------------------------------------------------------------------
# Scope-drift detection (§6)
# ---------------------------------------------------------------------------

class ScopeDriftHeuristicsTests(unittest.TestCase):
    def test_no_soul_means_no_drift(self):
        root = _mk_workspace_with_soul(None)
        result = detect_scope_drift(root, "Build me a todo app")
        self.assertFalse(result["drifted"])
        self.assertEqual(result["method"], "no-soul")
        self.assertEqual(result["recommended_action"], "keep")

    def test_high_token_overlap_means_no_drift(self):
        soul = (
            "Customer onboarding helper for the team. Captures customer "
            "feedback and routes to planning. Personal use first."
        )
        root = _mk_workspace_with_soul(soul)
        result = detect_scope_drift(
            root, "Add new onboarding flow for customer feedback routing",
        )
        self.assertFalse(result["drifted"])
        self.assertEqual(result["method"], "heuristic")
        self.assertEqual(result["recommended_action"], "keep")
        self.assertTrue(any(s.startswith("token-overlap=") for s in result["signals"]))

    def test_zero_overlap_means_drift(self):
        soul = (
            "Todo app for personal use only. Captures my daily tasks "
            "and reminders so I don't forget what matters today."
        )
        root = _mk_workspace_with_soul(soul)
        result = detect_scope_drift(
            root, "Financial dashboard tracking quarterly investor returns",
        )
        self.assertTrue(result["drifted"])
        self.assertEqual(result["method"], "heuristic")
        self.assertIn(result["recommended_action"], {"amend", "new-project"})

    def test_stakeholder_mismatch_recommends_new_project(self):
        soul = "Personal use only — just for me to track my own todos."
        root = _mk_workspace_with_soul(soul)
        result = detect_scope_drift(
            root, "Multi-user collaboration platform for our customers",
        )
        self.assertTrue(result["drifted"])
        self.assertEqual(result["recommended_action"], "new-project")
        self.assertTrue(any("stakeholder-mismatch" in s for s in result["signals"]))

    def test_ambiguous_zone_defers_to_llm_when_available(self):
        # Designed to land in the ambiguous overlap zone (0.1 < x < 0.4).
        soul = "Personal helper application customer onboarding workflows daily"
        root = _mk_workspace_with_soul(soul)

        captured = {}

        def fake_judge(soul_text: str, request: str) -> dict:
            captured["soul"] = soul_text
            captured["request"] = request
            return {"drifted": True, "confidence": 0.85, "reasoning": "domain shift"}

        result = detect_scope_drift(
            root,
            "Personal helper but inventory tracking warehouse manifests forklift",
            llm_judge=fake_judge,
        )
        self.assertEqual(result["method"], "llm-judged")
        self.assertTrue(result["drifted"])
        self.assertIn("soul", captured)

    def test_ambiguous_zone_without_judge_returns_ambiguous_no_false_positive(self):
        soul = "Personal helper application customer onboarding workflows daily"
        root = _mk_workspace_with_soul(soul)
        result = detect_scope_drift(
            root,
            "Personal helper but inventory tracking warehouse manifests forklift",
        )
        self.assertEqual(result["method"], "ambiguous")
        self.assertFalse(result["drifted"])  # conservative default
        self.assertEqual(result["recommended_action"], "ambiguous")

    def test_llm_judge_exception_falls_back_to_ambiguous(self):
        soul = "Personal helper application customer onboarding workflows daily"
        root = _mk_workspace_with_soul(soul)

        def broken_judge(soul_text: str, request: str) -> dict:
            raise RuntimeError("network down")

        result = detect_scope_drift(
            root,
            "Personal helper but inventory tracking warehouse manifests forklift",
            llm_judge=broken_judge,
        )
        self.assertEqual(result["method"], "ambiguous")
        self.assertFalse(result["drifted"])
        self.assertTrue(any("llm-judge-failed" in s for s in result["signals"]))


# ---------------------------------------------------------------------------
# Engine integration — begin() / resolve_scope_drift() / sign_current_gate()
# ---------------------------------------------------------------------------

class EngineBeginTests(unittest.TestCase):
    def test_begin_on_empty_workspace_dispatches_g0(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        result = eng.begin("Build me a todo app")
        self.assertEqual(result["action"], "fire-agent-G0")
        self.assertEqual(result["current_gate"], "G0")
        self.assertEqual(eng.state, WaveState.DISPATCH)

    def test_begin_with_g0_signed_dispatches_g1(self):
        soul = "Customer onboarding helper for the team. Personal use first."
        root = _mk_workspace_with_soul(soul)
        eng = WaveEngine(root)
        result = eng.begin("More customer onboarding for the team")
        # G1 unsigned → next dispatch is G1.
        self.assertEqual(result["action"], "fire-agent-G1")
        self.assertEqual(result["current_gate"], "G1")

    def test_begin_with_scope_drift_returns_drift_prompt(self):
        soul = (
            "Personal todo app for me — daily tasks reminders only, "
            "nothing else, just for my own use day to day."
        )
        root = _mk_workspace_with_soul(soul)
        eng = WaveEngine(root)
        result = eng.begin(
            "Build a customer-facing dashboard for our enterprise clients",
        )
        self.assertEqual(result["action"], "scope-drift-prompt")
        self.assertEqual(eng.state, WaveState.SCOPE_DRIFT)
        self.assertTrue(result["drift"]["drifted"])


class EngineScopeDriftResolutionTests(unittest.TestCase):
    def _drifted_engine(self) -> WaveEngine:
        soul = (
            "Personal todo app for me — daily tasks reminders only, "
            "nothing else, just for my own use day to day."
        )
        root = _mk_workspace_with_soul(soul)
        eng = WaveEngine(root)
        eng.begin("Customer-facing enterprise dashboard for our clients")
        assert eng.state is WaveState.SCOPE_DRIFT, eng.state
        return eng

    def test_amend_re_fires_g0(self):
        eng = self._drifted_engine()
        result = eng.resolve_scope_drift("a")
        self.assertEqual(result["action"], "fire-agent-G0")
        self.assertEqual(result["mode"], "amend")
        self.assertEqual(eng.state, WaveState.INSPECT)

    def test_new_parallel_returns_new_project_action(self):
        eng = self._drifted_engine()
        result = eng.resolve_scope_drift("b")
        self.assertEqual(result["action"], "new-project-same-workspace")
        self.assertEqual(eng.state, WaveState.ENTRY)

    def test_new_folder_returns_new_workspace_action(self):
        eng = self._drifted_engine()
        result = eng.resolve_scope_drift("c")
        self.assertEqual(result["action"], "new-project-new-workspace")

    def test_keep_treats_as_refinement(self):
        eng = self._drifted_engine()
        result = eng.resolve_scope_drift("d")
        self.assertEqual(result["action"], "treat-as-refinement")
        self.assertEqual(eng.state, WaveState.INSPECT)

    def test_unknown_choice_raises(self):
        eng = self._drifted_engine()
        with self.assertRaises(ValueError):
            eng.resolve_scope_drift("zzz")

    def test_resolve_outside_scope_drift_state_raises(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        with self.assertRaisesRegex(RuntimeError, "SCOPE_DRIFT state"):
            eng.resolve_scope_drift("a")


class EngineSignAdvanceTests(unittest.TestCase):
    def test_sign_advances_to_next_gate(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        eng.begin("Build a todo app")
        result = eng.sign_current_gate(evidence="yes")
        self.assertEqual(result["signed_gate"], "G0")
        self.assertEqual(result["action"], "fire-agent-G1")
        self.assertEqual(eng.current_gate, "G1")
        self.assertEqual(eng.state, WaveState.INSPECT)

    def test_signing_g5_completes_wave(self):
        """Driving the engine through all 6 gate signs lands on COMPLETE.

        After each sign the engine returns to INSPECT for the next gate;
        a real M-W3+ caller would re-inspect and re-dispatch. Here we
        drive the transitions manually to focus on the COMPLETE terminus.
        """
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        eng.begin("Build a todo app")  # state: DISPATCH @ G0

        for gate in ["G0", "G1", "G2", "G3", "G4"]:
            self.assertEqual(eng.current_gate, gate)
            eng.sign_current_gate(evidence=f"sign-{gate}")
            # sign_current_gate ends in INSPECT for the next gate;
            # drive through DECIDE → DISPATCH for the next iteration.
            eng.transition(WaveState.DECIDE)
            eng.transition(WaveState.DISPATCH)

        self.assertEqual(eng.current_gate, "G5")
        result = eng.sign_current_gate(evidence="ship")
        self.assertEqual(result["action"], "complete")
        self.assertEqual(eng.state, WaveState.COMPLETE)

    def test_sign_outside_dispatch_or_await_raises(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        # ENTRY state — sign should refuse.
        with self.assertRaisesRegex(RuntimeError, "DISPATCH or AWAIT_USER_CONFIRM"):
            eng.sign_current_gate(evidence="yes")


if __name__ == "__main__":
    unittest.main()
