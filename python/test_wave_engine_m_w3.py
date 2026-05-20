"""test_wave_engine_m_w3.py — M-W3 tests for per-gate agent loading,
affirmation classification, and re-route system bubbles.

Per WAVE-ENGINE-DESIGN §4, §5, §8.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.agent_loader import (
    GATE_AGENT_FILES,
    list_available_agents,
    load_agent,
)
from signalos_lib.wave_engine import (
    AFFIRMATION_ALLOWLIST,
    WaveEngine,
    WaveState,
    build_system_bubble,
    classify_user_reply,
)


def _mk_workspace_with_soul(soul_text: str | None = None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="signalos-m-w3-"))
    (root / ".signalos").mkdir()
    if soul_text is not None:
        soul_dir = root / "core" / "governance" / "Governance"
        soul_dir.mkdir(parents=True, exist_ok=True)
        if soul_text.count("\n") < 3:
            soul_text = (
                soul_text.rstrip("\n")
                + "\n" + "Owner: PO.\nReviewer: lead engineer.\nReady when signed.\n"
            )
        (soul_dir / "SOUL-DOCUMENT.md").write_text(soul_text, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Agent loader (§4)
# ---------------------------------------------------------------------------

class AgentLoaderTests(unittest.TestCase):
    def test_g0_onboarding_agent_loads_with_content(self):
        result = load_agent("G0")
        self.assertTrue(result["exists"])
        self.assertEqual(result["gate"], "G0")
        self.assertEqual(result["filename"], "onboarding.md")
        self.assertIn("Onboarding", result["content"])

    def test_g1_brainstorm_agent_loads_with_content(self):
        result = load_agent("G1")
        self.assertTrue(result["exists"])
        self.assertEqual(result["filename"], "brainstorm.md")
        self.assertIn("Brainstorm", result["content"])

    def test_g3_design_agent_now_loads_after_m_w4(self):
        """M-W4 shipped design.md. The agent loader returns it with content."""
        result = load_agent("G3")
        self.assertEqual(result["filename"], "design.md")
        self.assertTrue(result["exists"])
        self.assertIn("Design", result["content"])

    def test_unknown_gate_raises_key_error(self):
        with self.assertRaises(KeyError):
            load_agent("G99")

    def test_list_available_reports_per_gate_existence(self):
        available = list_available_agents()
        self.assertEqual(set(available.keys()), set(GATE_AGENT_FILES.keys()))
        # M-W4 shipped design.md; all six gates now have an agent file.
        for gate in ("G0", "G1", "G2", "G3", "G4", "G5"):
            self.assertTrue(available[gate], f"{gate} agent file missing")


# ---------------------------------------------------------------------------
# Affirmation classifier (§8)
# ---------------------------------------------------------------------------

class ClassifyReplyTests(unittest.TestCase):
    def test_yes_is_affirm(self):
        self.assertEqual(classify_user_reply("yes")["kind"], "affirm")
        self.assertEqual(classify_user_reply("Yes")["kind"], "affirm")
        self.assertEqual(classify_user_reply("YES")["kind"], "affirm")

    def test_punctuation_does_not_block_affirm(self):
        self.assertEqual(classify_user_reply("yes!")["kind"], "affirm")
        self.assertEqual(classify_user_reply("yes.")["kind"], "affirm")
        self.assertEqual(classify_user_reply(" confirm ")["kind"], "affirm")

    def test_multi_word_phrases_affirm(self):
        self.assertEqual(classify_user_reply("looks good")["kind"], "affirm")
        self.assertEqual(classify_user_reply("looks good to me")["kind"], "affirm")
        self.assertEqual(classify_user_reply("ship it")["kind"], "affirm")
        self.assertEqual(classify_user_reply("go ahead")["kind"], "affirm")

    def test_matched_phrase_is_recorded(self):
        result = classify_user_reply("approve")
        self.assertEqual(result["matched_phrase"], "approve")
        result2 = classify_user_reply("looks good lgtm")
        self.assertEqual(result2["matched_phrase"], "looks good")

    def test_question_never_auto_signs(self):
        self.assertEqual(classify_user_reply("yes?")["kind"], "question")
        self.assertEqual(
            classify_user_reply("does this look right?")["kind"], "question",
        )

    def test_refinement_request_classified_as_refine(self):
        self.assertEqual(classify_user_reply("change the title")["kind"], "refine")
        self.assertEqual(classify_user_reply("no, rewrite it")["kind"], "refine")
        self.assertEqual(
            classify_user_reply("actually use a different approach")["kind"], "refine",
        )

    def test_ambiguous_reply_is_other_not_affirm(self):
        """Per §8 the v1 classifier is strict — ambiguous replies fall
        through to 'other' so the chat layer asks for explicit
        confirmation rather than silently signing."""
        self.assertEqual(classify_user_reply("hmm")["kind"], "other")
        self.assertEqual(classify_user_reply("interesting")["kind"], "other")

    def test_empty_reply_is_other(self):
        self.assertEqual(classify_user_reply("")["kind"], "other")

    def test_allowlist_is_frozen(self):
        # Hardening — design §8 v1 says affirmation set is fixed; v2
        # may expand via LLM-judge but the strict set is locked.
        self.assertIsInstance(AFFIRMATION_ALLOWLIST, frozenset)
        self.assertIn("yes", AFFIRMATION_ALLOWLIST)
        self.assertIn("confirm", AFFIRMATION_ALLOWLIST)


# ---------------------------------------------------------------------------
# System bubbles (§5)
# ---------------------------------------------------------------------------

class SystemBubbleTests(unittest.TestCase):
    def test_reroute_bubble_names_the_gate(self):
        bubble = build_system_bubble(kind="reroute", gate="G4")
        self.assertEqual(bubble["kind"], "reroute")
        self.assertEqual(bubble["gate"], "G4")
        self.assertIn("Build", bubble["text"])
        self.assertIn("G4", bubble["text"])

    def test_sign_recorded_bubble_carries_gate_and_audit_phrase(self):
        bubble = build_system_bubble(kind="sign-recorded", gate="G1")
        self.assertIn("Belief", bubble["text"])
        self.assertIn("audit", bubble["text"].lower())

    def test_scope_drift_bubble_mentions_soul(self):
        bubble = build_system_bubble(kind="scope-drift")
        self.assertIn("Soul", bubble["text"])

    def test_complete_bubble_signals_wave_done(self):
        bubble = build_system_bubble(kind="complete")
        self.assertIn("complete", bubble["text"].lower())


# ---------------------------------------------------------------------------
# Wave-engine integration — agent loading + auto-sign + bubbles
# ---------------------------------------------------------------------------

class EngineAgentDispatchTests(unittest.TestCase):
    def test_begin_attaches_loaded_agent_to_dispatch_result(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        result = eng.begin("Build a todo app")
        self.assertEqual(result["action"], "fire-agent-G0")
        self.assertIsNotNone(result["agent"])
        self.assertTrue(result["agent"]["exists"])
        self.assertEqual(result["agent"]["gate"], "G0")
        self.assertIn("Onboarding", result["agent"]["content"])

    def test_begin_attaches_reroute_system_bubble(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        result = eng.begin("Build a todo app")
        bubble = result["system_bubble"]
        self.assertEqual(bubble["kind"], "reroute")
        self.assertEqual(bubble["gate"], "G0")

    def test_begin_with_scope_drift_attaches_drift_bubble(self):
        soul = (
            "Personal todo app for me — daily tasks reminders only, "
            "nothing else, just for my own use day to day."
        )
        root = _mk_workspace_with_soul(soul)
        eng = WaveEngine(root)
        result = eng.begin(
            "Customer-facing enterprise dashboard for our clients",
        )
        self.assertEqual(result["system_bubble"]["kind"], "scope-drift")


class EngineAutoSignTests(unittest.TestCase):
    def test_affirmative_reply_auto_signs(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        eng.begin("Build a todo app")  # DISPATCH @ G0
        result = eng.handle_user_reply("yes")
        self.assertTrue(result["auto_signed"])
        self.assertEqual(result["signed_gate"], "G0")
        self.assertEqual(result["action"], "fire-agent-G1")
        # Sign bubble carries the gate.
        self.assertEqual(result["system_bubble"]["kind"], "sign-recorded")
        self.assertEqual(result["system_bubble"]["gate"], "G0")

    def test_multiword_affirmative_auto_signs(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        eng.begin("Build a todo app")
        result = eng.handle_user_reply("looks good")
        self.assertTrue(result["auto_signed"])
        self.assertEqual(result["signed_gate"], "G0")

    def test_refine_reply_does_not_sign(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        eng.begin("Build a todo app")
        result = eng.handle_user_reply("change the soul to mention team use")
        self.assertEqual(result["action"], "refine")
        self.assertEqual(result["current_gate"], "G0")
        self.assertNotIn("auto_signed", result)
        # State unchanged — still DISPATCH @ G0.
        self.assertEqual(eng.state, WaveState.DISPATCH)
        self.assertEqual(eng.current_gate, "G0")

    def test_question_reply_does_not_sign(self):
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        eng.begin("Build a todo app")
        result = eng.handle_user_reply("does this look right?")
        self.assertEqual(result["action"], "answer-question")
        self.assertEqual(eng.state, WaveState.DISPATCH)

    def test_ambiguous_reply_does_not_sign(self):
        """The design's false-positive rule: ambiguous never signs."""
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        eng.begin("Build a todo app")
        result = eng.handle_user_reply("hmm I think maybe")
        self.assertEqual(result["action"], "ambiguous")
        self.assertEqual(eng.state, WaveState.DISPATCH)

    def test_affirm_outside_dispatch_returns_ambiguous(self):
        """If the user replies 'yes' when the engine isn't waiting for
        a sign (e.g., still ENTRY), don't blindly sign — return ambiguous."""
        root = _mk_workspace_with_soul(None)
        eng = WaveEngine(root)
        # No begin() — engine is still in ENTRY.
        result = eng.handle_user_reply("yes")
        self.assertEqual(result["action"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
