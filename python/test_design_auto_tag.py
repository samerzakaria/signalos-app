"""test_design_auto_tag.py — G3 design auto-tagging in the orchestrator.

Per v0.2 audit §6.7. The orchestrator emits a G3 sub-task on the
G2→G3 transition with the "design" skill attached so
`_validate_design` runs post-write. `ensure_design_skill_tagged` is
the safety net that adds the tag when the plan author forgot it but
the task still looks like a G3 design task.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.orchestrator import (
    _looks_like_design_task,
    ensure_design_skill_tagged,
)


class LooksLikeDesignTaskTests(unittest.TestCase):
    def test_gate_g3_field_triggers(self):
        self.assertTrue(_looks_like_design_task({"gate": "G3"}))
        self.assertTrue(_looks_like_design_task({"gate": "g3"}))  # case-insensitive

    def test_explicit_design_skill_triggers(self):
        self.assertTrue(_looks_like_design_task({"skills": ["design"]}))
        self.assertTrue(_looks_like_design_task({"skills": ["security-audit", "design"]}))
        self.assertTrue(_looks_like_design_task({"skills": ["DESIGN"]}))  # case-insensitive

    def test_design_output_path_triggers(self):
        """Any file under .signalos/designs/<wave>/ identifies the task."""
        self.assertTrue(_looks_like_design_task({
            "files": [".signalos/designs/W7.1/design-doc.md"],
        }))
        self.assertTrue(_looks_like_design_task({
            "files": [".signalos/designs/W7.1/prototype/Card.stories.tsx"],
        }))
        # Backslash paths (Windows) are normalized.
        self.assertTrue(_looks_like_design_task({
            "files": [".signalos\\designs\\W7.1\\design-doc.md"],
        }))

    def test_unrelated_task_does_not_trigger(self):
        self.assertFalse(_looks_like_design_task({"gate": "G2"}))
        self.assertFalse(_looks_like_design_task({"skills": ["test-generation"]}))
        self.assertFalse(_looks_like_design_task({"files": ["src/foo.ts"]}))
        self.assertFalse(_looks_like_design_task({}))

    def test_malformed_fields_do_not_crash(self):
        self.assertFalse(_looks_like_design_task({"skills": "not-a-list"}))
        self.assertFalse(_looks_like_design_task({"files": "not-a-list"}))
        self.assertFalse(_looks_like_design_task({"gate": 99}))


class EnsureDesignSkillTaggedTests(unittest.TestCase):
    def test_unrelated_task_passes_through_unchanged(self):
        tasks = [{"task": "T1", "skills": ["test-generation"], "files": ["src/foo.ts"]}]
        self.assertEqual(ensure_design_skill_tagged(tasks), tasks)

    def test_gate_g3_task_gets_design_tag_added(self):
        tasks = [{"task": "T1", "gate": "G3", "skills": ["writing-plans"]}]
        out = ensure_design_skill_tagged(tasks)
        self.assertEqual(out[0]["skills"], ["writing-plans", "design"])

    def test_design_path_task_gets_design_tag_added(self):
        tasks = [{
            "task": "T1",
            "files": [".signalos/designs/W7.1/design-doc.md"],
            "skills": [],
        }]
        out = ensure_design_skill_tagged(tasks)
        self.assertIn("design", out[0]["skills"])

    def test_already_tagged_task_is_not_duplicated(self):
        """Idempotent — re-running over a tagged task is a no-op."""
        tasks = [{"task": "T1", "gate": "G3", "skills": ["design", "writing-plans"]}]
        out = ensure_design_skill_tagged(tasks)
        # design appears exactly once.
        self.assertEqual(out[0]["skills"].count("design"), 1)

    def test_multiple_tasks_are_handled_independently(self):
        tasks = [
            {"task": "T1", "skills": ["test-generation"]},
            {"task": "T2", "gate": "G3"},
            {"task": "T3", "files": [".signalos/designs/W1/design-doc.md"]},
            {"task": "T4", "skills": ["design"]},
        ]
        out = ensure_design_skill_tagged(tasks)
        self.assertEqual(out[0]["skills"], ["test-generation"])  # unchanged
        self.assertIn("design", out[1]["skills"])
        self.assertIn("design", out[2]["skills"])
        self.assertEqual(out[3]["skills"].count("design"), 1)

    def test_missing_skills_field_is_initialized_to_list_with_design(self):
        tasks = [{"task": "T1", "gate": "G3"}]
        out = ensure_design_skill_tagged(tasks)
        self.assertEqual(out[0]["skills"], ["design"])

    def test_non_dict_entries_pass_through_unchanged(self):
        # Defensive — if state.json yields a malformed entry the
        # normalizer must not crash the orchestrator.
        tasks = ["not a dict", {"task": "T1", "gate": "G3"}]
        out = ensure_design_skill_tagged(tasks)
        self.assertEqual(out[0], "not a dict")
        self.assertIn("design", out[1]["skills"])

    def test_returns_new_list_does_not_mutate_input(self):
        tasks = [{"task": "T1", "gate": "G3", "skills": []}]
        original_skills = tasks[0]["skills"]
        out = ensure_design_skill_tagged(tasks)
        # Input task's skills list unchanged; output got a new merged dict.
        self.assertEqual(original_skills, [])
        self.assertIn("design", out[0]["skills"])
        # Source dict not mutated.
        self.assertNotIn("design", tasks[0]["skills"])


if __name__ == "__main__":
    unittest.main()
