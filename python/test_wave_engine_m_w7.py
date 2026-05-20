"""test_wave_engine_m_w7.py — M-W7: refusal taxonomy + violation-confirmation.

Per WAVE-ENGINE-DESIGN §8 + §9.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.refusal_taxonomy import (
    VIOLATION_OPTIONS,
    RefusalCategory,
    build_violation_prompt,
    record_violation_confirmation,
)
from signalos_lib.wave_engine import WaveEngine


# ---------------------------------------------------------------------------
# Refusal taxonomy enum (§9)
# ---------------------------------------------------------------------------

class RefusalCategoryTests(unittest.TestCase):
    def test_five_categories_per_design(self):
        # §9 enumerates A/B/C/D/E. Verify all are present.
        labels = {c.value.split(":", 1)[0] for c in RefusalCategory}
        self.assertEqual(labels, {"A", "B", "C", "D", "E"})

    def test_categories_have_descriptive_suffix(self):
        for cat in RefusalCategory:
            self.assertIn(":", cat.value)
            label, name = cat.value.split(":", 1)
            self.assertTrue(name, f"{cat} missing descriptive suffix")


# ---------------------------------------------------------------------------
# build_violation_prompt
# ---------------------------------------------------------------------------

class BuildViolationPromptTests(unittest.TestCase):
    def test_prompt_includes_all_three_options(self):
        prompt = build_violation_prompt(
            violation_kind="code-review",
            findings=["uses eval()", "missing null check"],
        )
        self.assertEqual(prompt["options"], list(VIOLATION_OPTIONS))
        # Rendered text mentions each option's intent in user-facing wording.
        body = prompt["text"].lower()
        self.assertIn("fix now", body)
        self.assertIn("defer", body)
        self.assertIn("override", body)
        # And each numbered choice label (a/b/c).
        self.assertIn("(a)", body)
        self.assertIn("(b)", body)
        self.assertIn("(c)", body)

    def test_prompt_lists_first_5_findings_and_overflow_count(self):
        prompt = build_violation_prompt(
            violation_kind="security-audit",
            findings=[f"finding {i}" for i in range(1, 8)],
        )
        self.assertIn("finding 1", prompt["text"])
        self.assertIn("finding 5", prompt["text"])
        self.assertNotIn("finding 6", prompt["text"])
        self.assertIn("+2 more", prompt["text"])

    def test_prompt_category_is_override_with_audit(self):
        prompt = build_violation_prompt(violation_kind="x")
        self.assertEqual(prompt["category"], RefusalCategory.OVERRIDE_WITH_AUDIT.value)

    def test_prompt_handles_empty_findings(self):
        prompt = build_violation_prompt(violation_kind="x", findings=[])
        # Empty findings shouldn't crash — text just says "a finding".
        self.assertIn("no specific findings", prompt["text"].lower())

    def test_prompt_id_is_namespaced_by_kind(self):
        prompt = build_violation_prompt(violation_kind="test-coverage")
        self.assertEqual(prompt["prompt_id"], "violation:test-coverage")


# ---------------------------------------------------------------------------
# record_violation_confirmation
# ---------------------------------------------------------------------------

class RecordViolationConfirmationTests(unittest.TestCase):
    def test_full_option_names_accepted(self):
        for opt in VIOLATION_OPTIONS:
            entry = record_violation_confirmation(
                violation_kind="code-review", choice=opt,
                user_reply="confirming the choice",
            )
            self.assertEqual(entry["choice"], opt)
            self.assertEqual(entry["action"], f"violation:code-review:{opt}")

    def test_single_letter_shortcuts_map_to_full_names(self):
        for letter, full in [("a", "fix-now"), ("b", "defer"), ("c", "override-with-log")]:
            entry = record_violation_confirmation(
                violation_kind="x", choice=letter, user_reply="ok",
            )
            self.assertEqual(entry["choice"], full)

    def test_unknown_choice_raises(self):
        with self.assertRaises(ValueError):
            record_violation_confirmation(
                violation_kind="x", choice="maybe", user_reply="ok",
            )

    def test_user_reply_recorded_verbatim_as_evidence(self):
        entry = record_violation_confirmation(
            violation_kind="security-audit", choice="c",
            user_reply="ship anyway — risk accepted, will fix post-launch",
        )
        self.assertEqual(
            entry["evidence"],
            "ship anyway — risk accepted, will fix post-launch",
        )

    def test_findings_preserved_in_audit_entry(self):
        findings = ["uses eval()", "no input validation"]
        entry = record_violation_confirmation(
            violation_kind="security-audit", choice="c", user_reply="override",
            findings=findings,
        )
        self.assertEqual(entry["findings"], findings)


# ---------------------------------------------------------------------------
# WaveEngine integration
# ---------------------------------------------------------------------------

def _mk_workspace() -> Path:
    root = Path(tempfile.mkdtemp(prefix="signalos-m-w7-")).resolve()
    (root / ".signalos").mkdir()
    return root


class EngineViolationFlowTests(unittest.TestCase):
    def test_request_violation_confirmation_returns_prompt_and_bubble(self):
        eng = WaveEngine(_mk_workspace())
        result = eng.request_violation_confirmation(
            violation_kind="code-review",
            findings=["foo", "bar"],
            gate="G4",
        )
        self.assertEqual(result["prompt"]["violation_kind"], "code-review")
        self.assertEqual(result["prompt"]["gate"], "G4")
        # System bubble carries the same gate and surfaces the prompt text.
        self.assertEqual(result["system_bubble"]["gate"], "G4")
        self.assertIn("code-review", result["system_bubble"]["detail"])

    def test_request_uses_current_gate_when_gate_omitted(self):
        eng = WaveEngine(_mk_workspace())
        eng.current_gate = "G2"  # simulating mid-wave state
        result = eng.request_violation_confirmation(violation_kind="test-coverage")
        self.assertEqual(result["prompt"]["gate"], "G2")

    def test_confirm_violation_records_evidence_and_bubble(self):
        eng = WaveEngine(_mk_workspace())
        eng.current_gate = "G4"
        result = eng.confirm_violation(
            violation_kind="security-audit", choice="c",
            user_reply="risk accepted; will fix in W7.2",
            findings=["xss in title field"],
        )
        entry = result["audit_entry"]
        self.assertEqual(entry["action"], "violation:security-audit:override-with-log")
        self.assertEqual(entry["choice"], "override-with-log")
        self.assertIn("risk accepted", entry["evidence"])
        self.assertEqual(entry["gate"], "G4")
        # Bubble explains what happened.
        self.assertIn("Override recorded", result["system_bubble"]["text"])

    def test_confirm_fix_now_bubble_says_holding_ship(self):
        eng = WaveEngine(_mk_workspace())
        eng.current_gate = "G4"
        result = eng.confirm_violation(
            violation_kind="code-review", choice="a",
            user_reply="yes fix it first",
        )
        self.assertEqual(result["audit_entry"]["choice"], "fix-now")
        self.assertIn("Holding ship", result["system_bubble"]["text"])

    def test_confirm_defer_bubble_mentions_backlog(self):
        eng = WaveEngine(_mk_workspace())
        eng.current_gate = "G4"
        result = eng.confirm_violation(
            violation_kind="code-review", choice="b", user_reply="defer it",
        )
        self.assertEqual(result["audit_entry"]["choice"], "defer")
        self.assertIn("backlog", result["system_bubble"]["text"])

    def test_confirm_invalid_choice_raises(self):
        eng = WaveEngine(_mk_workspace())
        with self.assertRaises(ValueError):
            eng.confirm_violation(
                violation_kind="x", choice="maybe", user_reply="huh",
            )


if __name__ == "__main__":
    unittest.main()
