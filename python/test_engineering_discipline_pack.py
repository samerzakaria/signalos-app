"""Focused catalog/sync tests for the Engineering Discipline guidance pack.

The pack adds four autoload guidance skills (think-before-coding,
simplicity-first, surgical-changes, goal-driven-execution) plus the
OBL-APP-008 obligation. A new guidance skill is only valid when every sync
point — orchestrator catalog, skills.json, guidance-catalog.json, and
obligations.json — moves in lockstep, so these tests assert all of them.
"""

from __future__ import annotations

import json
import unittest

from signalos_lib.skill_catalog import (
    BUNDLE_ROOT,
    canonical_skill_catalog,
    validate_catalog_paths_exist,
    validate_tool_adapter_registry_sync,
)

NEW_IDS = [
    "think-before-coding",
    "simplicity-first",
    "surgical-changes",
    "goal-driven-execution",
]

_SHARED = BUNDLE_ROOT / "core" / "tool-adapters" / "_shared"


class EngineeringDisciplinePack(unittest.TestCase):
    def test_new_ids_are_in_canonical_catalog(self) -> None:
        keys = {entry.key for entry in canonical_skill_catalog()}
        for new_id in NEW_IDS:
            self.assertIn(new_id, keys, new_id)

    def test_new_ids_resolve_to_real_skill_files(self) -> None:
        # All catalog paths (including the four new ones) must exist on disk.
        self.assertEqual(validate_catalog_paths_exist(), [])
        catalog = {entry.key: entry.path for entry in canonical_skill_catalog()}
        for new_id in NEW_IDS:
            self.assertTrue(
                (BUNDLE_ROOT / catalog[new_id]).is_file(), catalog[new_id]
            )

    def test_each_skill_has_frontmatter_and_attribution(self) -> None:
        catalog = {entry.key: entry.path for entry in canonical_skill_catalog()}
        for new_id in NEW_IDS:
            text = (BUNDLE_ROOT / catalog[new_id]).read_text(encoding="utf-8")
            self.assertIn(f"name: {new_id}", text, new_id)
            self.assertIn("description:", text, new_id)
            self.assertIn("## Attribution", text, new_id)
            self.assertIn("THIRD_PARTY_NOTICES.md", text, new_id)

    def test_tool_adapter_registry_in_sync(self) -> None:
        self.assertEqual(validate_tool_adapter_registry_sync(), [])

    def test_guidance_catalog_entries_map_to_routable_skills(self) -> None:
        catalog = json.loads(
            (_SHARED / "guidance-catalog.json").read_text(encoding="utf-8")
        )
        skills = {entry.key: entry.path for entry in canonical_skill_catalog()}
        by_id = {e["id"]: e for e in catalog}
        for new_id in NEW_IDS:
            self.assertIn(new_id, by_id, new_id)
            entry = by_id[new_id]
            self.assertEqual(entry["path"], skills[new_id])
            self.assertEqual(entry["category"], "skill")
            self.assertTrue(entry["active"])

    def test_obl_app_008_references_only_active_ids_in_autoload_mode(self) -> None:
        catalog = json.loads(
            (_SHARED / "guidance-catalog.json").read_text(encoding="utf-8")
        )
        obligations = json.loads(
            (_SHARED / "obligations.json").read_text(encoding="utf-8")
        )
        active_ids = {
            e["id"]
            for e in catalog
            if isinstance(e, dict) and e.get("active") is True
        }
        rule = next(
            (r for r in obligations if r.get("rule_id") == "OBL-APP-008"), None
        )
        self.assertIsNotNone(rule, "OBL-APP-008 missing from obligations.json")
        assert rule is not None  # for type-checkers
        self.assertEqual(rule["mode"], "autoload")
        self.assertEqual(sorted(rule["require"]), sorted(NEW_IDS))
        for guidance_id in rule["require"]:
            self.assertIn(guidance_id, active_ids, guidance_id)


if __name__ == "__main__":
    unittest.main()
