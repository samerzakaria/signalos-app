"""test_status_gate_emissions.py — Milestone 3 (audit completion plan).

Verifies that `build_status_json` emits per-gate `activities` and `criteria`
arrays so DashboardView stops showing the "No activities yet" placeholder.

Each gate detail dict has the shape:
    {
        "id": int (0..5),
        "key": "G<n>",
        "signed": bool,
        "activities": list[dict],
        "criteria": list[dict],
    }

Activities are derived from PLAN.tasks.yaml; criteria are derived from the
union of skill_validators.VALIDATORS and the skills tagged on plan tasks.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.status import (  # noqa: E402
    build_status_json,
    _collect_gate_activities,
    _collect_gate_criteria,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PLAN_YAML = """# Test plan
wave: "1"
tasks:
  - id: 01HAAAAAAAAAAAAAAAAAAAAAAA
    title: "Draft expectation map"
    status: in_progress
    tier: T3
    skills: [writing-plans]
    gate: G2
  - id: 01HBBBBBBBBBBBBBBBBBBBBBBB
    title: "Implement feature X with tests"
    status: pending
    tier: T2
    skills: [test-generation, security-audit]
    gate: G4
  - id: 01HCCCCCCCCCCCCCCCCCCCCCCC
    title: "Write code review notes"
    status: done
    tier: T2
    skills: [comprehensive-code-review]
    gate: G5
"""


def _stage_workspace(d: Path) -> Path:
    """Drop a minimal .signalos workspace + PLAN.tasks.yaml into *d*."""
    (d / ".signalos").mkdir(parents=True, exist_ok=True)
    (d / "PLAN.tasks.yaml").write_text(_PLAN_YAML, encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class BuildStatusJsonGateEmissions(unittest.TestCase):
    """End-to-end shape assertions for build_status_json()."""

    def test_returns_gate_details_list_of_six_gates(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _stage_workspace(Path(d))
            data = build_status_json(root)
        self.assertIn("gate_details", data)
        gd = data["gate_details"]
        self.assertIsInstance(gd, list)
        self.assertEqual(len(gd), 6)
        for i, entry in enumerate(gd):
            self.assertEqual(entry["id"], i)
            self.assertEqual(entry["key"], f"G{i}")
            self.assertIn("activities", entry)
            self.assertIn("criteria", entry)
            self.assertIsInstance(entry["activities"], list)
            self.assertIsInstance(entry["criteria"], list)

    def test_activities_match_plan_tasks_per_gate(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _stage_workspace(Path(d))
            data = build_status_json(root)
        details = {g["key"]: g for g in data["gate_details"]}

        # G2 has the planning task
        g2_titles = {a["title"] for a in details["G2"]["activities"]}
        self.assertIn("Draft expectation map", g2_titles)

        # G4 has the build task
        g4_titles = {a["title"] for a in details["G4"]["activities"]}
        self.assertIn("Implement feature X with tests", g4_titles)

        # G5 has the review task
        g5_titles = {a["title"] for a in details["G5"]["activities"]}
        self.assertIn("Write code review notes", g5_titles)

    def test_activity_shape_has_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _stage_workspace(Path(d))
            data = build_status_json(root)
        # Find any non-empty activities list and inspect a member
        sample = None
        for g in data["gate_details"]:
            if g["activities"]:
                sample = g["activities"][0]
                break
        self.assertIsNotNone(sample, "no activities emitted for any gate")
        for key in ("task_id", "title", "status", "skills"):
            self.assertIn(key, sample, f"activity missing required key {key!r}")
        # DashboardView reads `name` — must alias the title
        self.assertIn("name", sample)
        self.assertEqual(sample["name"], sample["title"])
        # status must be in the UI-normalized vocabulary
        self.assertIn(
            sample["status"],
            {"pending", "in_progress", "completed", "failed"},
            f"unexpected activity.status: {sample['status']!r}",
        )
        self.assertIsInstance(sample["skills"], list)

    def test_status_translation_done_becomes_completed(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _stage_workspace(Path(d))
            data = build_status_json(root)
        details = {g["key"]: g for g in data["gate_details"]}
        # The G5 task has plan status `done` → UI status `completed`
        g5_acts = details["G5"]["activities"]
        self.assertTrue(g5_acts, "G5 should have at least one activity")
        review = next(
            a for a in g5_acts if a["title"] == "Write code review notes"
        )
        self.assertEqual(review["status"], "completed")
        # The G2 task has plan status `in_progress` → UI status `in_progress`
        g2_acts = details["G2"]["activities"]
        plan_task = next(
            a for a in g2_acts if a["title"] == "Draft expectation map"
        )
        self.assertEqual(plan_task["status"], "in_progress")

    def test_criteria_emitted_for_validator_skills(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _stage_workspace(Path(d))
            data = build_status_json(root)
        details = {g["key"]: g for g in data["gate_details"]}

        # G4 build task has skills test-generation + security-audit — both
        # have registered validators, so they should appear as criteria on G4
        g4_crit_names = {c["name"] for c in details["G4"]["criteria"]}
        self.assertIn("test-generation", g4_crit_names)
        self.assertIn("security-audit", g4_crit_names)

        # G2 planning task has skill writing-plans → criterion on G2
        g2_crit_names = {c["name"] for c in details["G2"]["criteria"]}
        self.assertIn("writing-plans", g2_crit_names)

        # G5 review task has skill comprehensive-code-review → criterion on G5
        g5_crit_names = {c["name"] for c in details["G5"]["criteria"]}
        self.assertIn("comprehensive-code-review", g5_crit_names)

    def test_criterion_shape_has_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _stage_workspace(Path(d))
            data = build_status_json(root)
        sample = None
        for g in data["gate_details"]:
            if g["criteria"]:
                sample = g["criteria"][0]
                break
        self.assertIsNotNone(sample, "no criteria emitted for any gate")
        for key in ("name", "description", "status", "evidence"):
            self.assertIn(key, sample, f"criterion missing required key {key!r}")
        # No validator output persisted → status falls back to pending
        self.assertIn(
            sample["status"],
            {"pending", "passing", "failing"},
            f"unexpected criterion.status: {sample['status']!r}",
        )

    def test_criteria_status_falls_back_to_pending_with_no_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _stage_workspace(Path(d))
            data = build_status_json(root)
        for g in data["gate_details"]:
            for crit in g["criteria"]:
                self.assertEqual(
                    crit["status"], "pending",
                    f"expected criterion {crit['name']!r} status=pending with no evidence file",
                )
                self.assertIsNone(crit["evidence"])

    def test_criteria_status_reads_persisted_validator_output(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _stage_workspace(Path(d))
            # Persist a validator output for security-audit on wave 1.
            sv = root / ".signalos" / "skill-validation" / "1"
            sv.mkdir(parents=True, exist_ok=True)
            (sv / "security-audit.json").write_text(
                json.dumps({"ok": True, "violations": []}),
                encoding="utf-8",
            )
            (sv / "test-generation.json").write_text(
                json.dumps({"ok": False, "violations": ["no test file"]}),
                encoding="utf-8",
            )
            data = build_status_json(root)

        details = {g["key"]: g for g in data["gate_details"]}
        g4_crit = {c["name"]: c for c in details["G4"]["criteria"]}
        self.assertEqual(g4_crit["security-audit"]["status"], "passing")
        self.assertIsNotNone(g4_crit["security-audit"]["evidence"])
        self.assertEqual(g4_crit["test-generation"]["status"], "failing")

    def test_gates_legacy_field_preserved(self) -> None:
        """The top-level boolean `gates` dict must still be present for
        backwards compatibility with IPC consumers that read it directly."""
        with tempfile.TemporaryDirectory() as d:
            root = _stage_workspace(Path(d))
            data = build_status_json(root)
        self.assertIn("gates", data)
        self.assertIsInstance(data["gates"], dict)
        for i in range(6):
            self.assertIn(f"G{i}", data["gates"])
            self.assertIsInstance(data["gates"][f"G{i}"], bool)

    def test_empty_workspace_emits_empty_arrays(self) -> None:
        """Gate emissions still produce a 6-entry list with empty arrays
        when there is no PLAN.tasks.yaml — empty is valid, not a crash."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir(parents=True, exist_ok=True)
            data = build_status_json(root)
        gd = data["gate_details"]
        self.assertEqual(len(gd), 6)
        for entry in gd:
            self.assertEqual(entry["activities"], [])
            self.assertEqual(entry["criteria"], [])


class CollectHelpersDirect(unittest.TestCase):
    """Lower-level checks against _collect_gate_activities / _criteria.

    These exercise the helpers without going through build_status_json so
    we get clean failure messages when a regression breaks the mapping.
    """

    def _tasks(self) -> list[dict]:
        return [
            {
                "id": "A",
                "title": "Plan task",
                "status": "in_progress",
                "skills": ["writing-plans"],
                "gate": "G2",
            },
            {
                "id": "B",
                "title": "Build task",
                "status": "pending",
                "skills": ["test-generation"],
                "gate": "G4",
            },
        ]

    def test_collect_activities_groups_by_gate(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            by_gate = _collect_gate_activities(root, self._tasks())
        self.assertEqual(len(by_gate[2]), 1)
        self.assertEqual(by_gate[2][0]["task_id"], "A")
        self.assertEqual(len(by_gate[4]), 1)
        self.assertEqual(by_gate[4][0]["task_id"], "B")
        # Other gates are empty
        for i in (0, 1, 3, 5):
            self.assertEqual(by_gate[i], [])

    def test_collect_criteria_emits_per_skill_per_gate(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            by_gate = _collect_gate_criteria(root, "1", self._tasks())
        # writing-plans → G2; test-generation → G4 (default skill→gate map)
        g2_names = {c["name"] for c in by_gate[2]}
        g4_names = {c["name"] for c in by_gate[4]}
        self.assertIn("writing-plans", g2_names)
        self.assertIn("test-generation", g4_names)


if __name__ == "__main__":
    unittest.main()
