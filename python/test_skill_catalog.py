from __future__ import annotations

import unittest

from signalos_lib.orchestrator import _SKILL_KEY_TO_PATH
from signalos_lib.skill_catalog import (
    canonical_skill_catalog,
    duplicate_orchestrator_skill_keys,
    load_tool_adapter_skills,
    render_tool_adapter_skills,
    validate_catalog_paths_exist,
    validate_tool_adapter_registry_sync,
)


class SkillCatalogGeneration(unittest.TestCase):
    def test_catalog_exposes_orchestrator_keys_names_and_paths(self) -> None:
        expected = [
            (key, name, path)
            for key, (name, path) in _SKILL_KEY_TO_PATH.items()
        ]
        actual = [
            (entry.key, entry.name, entry.path)
            for entry in canonical_skill_catalog()
        ]
        self.assertEqual(actual, expected)

    def test_every_catalog_path_exists_under_bundle(self) -> None:
        self.assertEqual(validate_catalog_paths_exist(), [])

    def test_current_registry_matches_canonical_keys_and_paths(self) -> None:
        self.assertEqual(validate_tool_adapter_registry_sync(), [])

    def test_orchestrator_catalog_has_no_duplicate_literal_keys(self) -> None:
        self.assertEqual(duplicate_orchestrator_skill_keys(), [])

    def test_renderer_output_count_matches_catalog(self) -> None:
        rendered = render_tool_adapter_skills(load_tool_adapter_skills())
        self.assertEqual(len(rendered), len(canonical_skill_catalog()))

    def test_renderer_preserves_descriptions_and_fills_missing_ones(self) -> None:
        catalog = canonical_skill_catalog()[:2]
        rendered = render_tool_adapter_skills(
            [
                {
                    "name": catalog[0].key,
                    "source": "stale/path/SKILL.md",
                    "description": "Keep this description.",
                    "wave": "W-test",
                },
                {
                    "name": catalog[1].key,
                    "source": catalog[1].path,
                },
            ],
            catalog=catalog,
        )

        self.assertEqual(rendered[0]["name"], catalog[0].key)
        self.assertEqual(rendered[0]["source"], catalog[0].path)
        self.assertEqual(rendered[0]["description"], "Keep this description.")
        self.assertEqual(rendered[0]["wave"], "W-test")
        self.assertEqual(rendered[1]["name"], catalog[1].key)
        self.assertEqual(rendered[1]["source"], catalog[1].path)
        self.assertIn(catalog[1].name, rendered[1]["description"])


if __name__ == "__main__":
    unittest.main()
