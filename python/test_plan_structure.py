"""Founder-facing plan structure (Wave 1.6): hierarchy, release, value, provenance."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import plan
from signalos_lib.plan import PlanDoc, Task

_ID_A = "A" * 26
_ID_B = "B" * 26
_ID_C = "C" * 26


class PlanStructureTests(unittest.TestCase):
    def test_new_fields_round_trip(self):
        t = Task(id=_ID_A, title="story", status="pending", tier="T3",
                 feature="Onboarding", epic="Sign-in", release="R1",
                 value=8, provenance="idea-ledger")
        t2 = Task.from_dict(t.to_dict())
        self.assertEqual(t2.feature, "Onboarding")
        self.assertEqual(t2.epic, "Sign-in")
        self.assertEqual(t2.release, "R1")
        self.assertEqual(t2.value, 8)
        self.assertEqual(t2.provenance, "idea-ledger")

    def test_unset_fields_are_omitted_from_yaml(self):
        # backward compatible: a plain task serialises without the new keys
        d = Task(id=_ID_A, title="t", status="pending", tier="T3").to_dict()
        for k in ("feature", "epic", "release", "value", "provenance"):
            self.assertNotIn(k, d)

    def test_roadmap_tree_groups_feature_epic_story_and_release(self):
        doc = PlanDoc(wave="W1", tasks=[
            Task(id=_ID_A, title="s1", status="pending", tier="T3",
                 feature="F1", epic="E1", release="R1", value=5, provenance="war-room"),
            Task(id=_ID_B, title="s2", status="pending", tier="T3",
                 feature="F1", epic="E1", release="R1"),
            Task(id=_ID_C, title="s3", status="pending", tier="T3",
                 feature="F1", epic="E2", release="R2"),
        ])
        tree = plan.roadmap_tree(doc)
        self.assertIn("F1", tree["features"])
        self.assertEqual(len(tree["features"]["F1"]["E1"]), 2)
        self.assertEqual(len(tree["features"]["F1"]["E2"]), 1)
        self.assertIn("R1", tree["releases"])
        self.assertIn("R2", tree["releases"])

    def test_provenance_defaults_to_founder_in_tree(self):
        doc = PlanDoc(wave="W1", tasks=[
            Task(id=_ID_A, title="s1", status="pending", tier="T3", feature="F", epic="E"),
        ])
        story = plan.roadmap_tree(doc)["features"]["F"]["E"][0]
        self.assertEqual(story["provenance"], "founder")


if __name__ == "__main__":
    unittest.main()
