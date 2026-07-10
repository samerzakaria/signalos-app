"""test_wave_engine_m_w4.py — M-W4: G2/G3 agents wired; design.md shipped.

Per WAVE-ENGINE-DESIGN §2 / §4 and v0.2 audit §6.7.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from conftest import seed_signed_gate
from signalos_lib.agent_loader import load_agent
from signalos_lib.wave_engine import GATE_ORDER, WaveEngine, WaveState


def _pad(text: str) -> str:
    """Pad short text to ≥3 non-comment lines so status._is_non_template
    counts the artifact as filled."""
    if text.count("\n") >= 3:
        return text
    return text.rstrip("\n") + "\nOwner: PO.\nReviewer: lead.\nReady.\n"


def _mk_workspace_signed_through(gate_signed_through: str) -> Path:
    """Make a temp workspace with artifacts seeded AND SIGNED so all gates
    up to and including *gate_signed_through* are detected as signed.

    Gate detection is signature-based and fail-closed on the WHOLE manifest,
    so seed_signed_gate signs EVERY required artifact of each gate (not just
    the primary). The primary artifact of each gate carries a flavour body;
    every other required artifact gets neutral signed content."""
    root = Path(tempfile.mkdtemp(prefix="signalos-m-w4-"))
    (root / ".signalos").mkdir()

    # Flavour body for each gate's PRIMARY manifest artifact.
    primary_body = {
        "G0": ("core/governance/Governance/SOUL-DOCUMENT.md",
               _pad("Customer onboarding helper for the team.")),
        "G1": ("core/strategy/BELIEF.md",
               _pad("Belief: customer ingestion improves by 20%.")),
        "G2": ("core/strategy/EXPECTATION_MAP.md",
               _pad("Expectation Map: 10 tickets/day.")),
        "G3": ("core/strategy/DESIGN_NOTE.md",
               _pad("Design Note: chosen approach.")),
        "G4": ("core/execution/BUILD_EVIDENCE.md",
               _pad("Build Evidence: tests passed.")),
        "G5": ("core/governance/QUALITY_CHECK.md",
               _pad("Quality Check: passed.")),
    }

    target_idx = GATE_ORDER.index(gate_signed_through)
    for gate in GATE_ORDER[:target_idx + 1]:
        rel, body = primary_body[gate]
        seed_signed_gate(root, gate, bodies={rel: body})
    return root


# ---------------------------------------------------------------------------
# Design agent file shipped (§6.7)
# ---------------------------------------------------------------------------

class DesignAgentShipTests(unittest.TestCase):
    def test_design_agent_file_exists(self):
        result = load_agent("G3")
        self.assertTrue(result["exists"], "design.md should ship in M-W4")
        self.assertEqual(result["filename"], "design.md")

    def test_design_agent_declares_three_shapes(self):
        """Per v0.2 audit §6.7, design.md must enumerate the three
        valid shapes for the G3 output (doc+prototype, doc+external-ref,
        doc+no-UI-attestation)."""
        result = load_agent("G3")
        content = result["content"]
        self.assertIn("doc + prototype", content)
        self.assertIn("doc + external-design-ref", content)
        self.assertIn("doc + no-UI-attestation", content)

    def test_design_agent_lists_required_prerequisites(self):
        content = load_agent("G3")["content"]
        # Per design §4 each agent declares its prerequisites; G3 needs
        # signed Belief + signed Expectation Map.
        self.assertIn("BELIEF.md", content)
        self.assertIn("EXPECTATION_MAP.md", content)


# ---------------------------------------------------------------------------
# Engine dispatches G2 / G3 with the loaded agent
# ---------------------------------------------------------------------------

class EngineDispatchG2G3Tests(unittest.TestCase):
    # User requests share tokens with the seeded Soul ("Customer onboarding
    # helper for the team") so the scope-drift heuristic doesn't false-fire.
    _REQUEST_PLAN = "Plan the customer onboarding helper tasks for the team"
    _REQUEST_DESIGN = "Design the customer onboarding helper interface for the team"

    def test_engine_dispatches_g2_with_plan_agent_loaded(self):
        # G0 + G1 signed → engine should fire G2 plan agent next.
        root = _mk_workspace_signed_through("G1")
        eng = WaveEngine(root)
        result = eng.begin(self._REQUEST_PLAN)
        self.assertEqual(result["action"], "fire-agent-G2")
        self.assertEqual(result["current_gate"], "G2")
        self.assertIsNotNone(result["agent"])
        self.assertTrue(result["agent"]["exists"])
        self.assertEqual(result["agent"]["filename"], "plan.md")
        self.assertIn("Plan", result["agent"]["content"])

    def test_engine_dispatches_g3_with_design_agent_loaded(self):
        # G0..G2 signed → engine should fire G3 design agent next.
        root = _mk_workspace_signed_through("G2")
        eng = WaveEngine(root)
        result = eng.begin(self._REQUEST_DESIGN)
        self.assertEqual(result["action"], "fire-agent-G3")
        self.assertEqual(result["current_gate"], "G3")
        self.assertTrue(result["agent"]["exists"])
        self.assertEqual(result["agent"]["filename"], "design.md")
        self.assertIn("Design", result["agent"]["content"])

    def test_g3_dispatch_attaches_reroute_bubble_for_design_gate(self):
        root = _mk_workspace_signed_through("G2")
        eng = WaveEngine(root)
        result = eng.begin(self._REQUEST_DESIGN)
        bubble = result["system_bubble"]
        self.assertEqual(bubble["kind"], "reroute")
        self.assertEqual(bubble["gate"], "G3")
        self.assertIn("Design", bubble["text"])

    def test_g2_to_g3_auto_advance_on_affirm(self):
        """When G2 is the current gate and the user affirms, the engine
        should auto-sign G2 and dispatch G3 with its design.md agent."""
        root = _mk_workspace_signed_through("G1")
        eng = WaveEngine(root)
        eng.begin(self._REQUEST_PLAN)  # DISPATCH @ G2
        result = eng.handle_user_reply("approve")
        self.assertTrue(result["auto_signed"])
        self.assertEqual(result["signed_gate"], "G2")
        self.assertEqual(result["action"], "fire-agent-G3")
        self.assertEqual(result["current_gate"], "G3")


if __name__ == "__main__":
    unittest.main()
