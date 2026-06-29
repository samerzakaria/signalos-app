from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from signalos_lib.orchestrator import _SKILL_KEY_TO_PATH
from signalos_lib.skill_catalog import (
    BUNDLE_ROOT,
    canonical_skill_catalog,
    duplicate_orchestrator_skill_keys,
    load_tool_adapter_skills,
    render_tool_adapter_skills,
    validate_catalog_paths_exist,
    validate_tool_adapter_registry_sync,
)
from signalos_lib.cli import _build_parser


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

    def test_guidance_catalog_maps_to_real_routable_skills(self) -> None:
        catalog_path = BUNDLE_ROOT / "core" / "tool-adapters" / "_shared" / "guidance-catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        skills = {entry.key: entry.path for entry in canonical_skill_catalog()}

        self.assertIsInstance(catalog, list)
        self.assertGreater(len(catalog), 0)
        for entry in catalog:
            self.assertIn(entry["id"], skills)
            self.assertEqual(entry["path"], skills[entry["id"]])
            self.assertTrue(entry["active"])

    def test_guidance_obligations_reference_active_catalog_ids(self) -> None:
        shared = BUNDLE_ROOT / "core" / "tool-adapters" / "_shared"
        catalog = json.loads((shared / "guidance-catalog.json").read_text(encoding="utf-8"))
        obligations = json.loads((shared / "obligations.json").read_text(encoding="utf-8"))
        active_ids = {
            entry["id"]
            for entry in catalog
            if isinstance(entry, dict) and entry.get("active") is True
        }

        self.assertIsInstance(obligations, list)
        self.assertGreater(len(obligations), 0)
        for rule in obligations:
            self.assertIn("rule_id", rule)
            self.assertIn(rule["mode"], {"autoload", "autoload_enforce"})
            self.assertTrue(rule["when"]["path_globs"])
            for guidance_id in rule["require"]:
                self.assertIn(guidance_id, active_ids)

    def test_command_catalog_sources_and_rules_exist(self) -> None:
        shared = BUNDLE_ROOT / "core" / "tool-adapters" / "_shared"
        commands = json.loads((shared / "commands.json").read_text(encoding="utf-8"))
        docs_dir = BUNDLE_ROOT / "core" / "execution" / "commands"
        rules_dir = BUNDLE_ROOT / "integrations" / "rules"

        self.assertIsInstance(commands, list)
        self.assertGreater(len(commands), 0)
        for command in commands:
            name = command["name"]
            source = command["source"]
            self.assertTrue((BUNDLE_ROOT / source).is_file(), name)
            self.assertTrue((docs_dir / f"{name}.md").is_file(), name)
            self.assertTrue((rules_dir / f"{name}.mdc").is_file(), name)

    def test_governance_runtime_validator_cli_commands_are_cataloged(self) -> None:
        parser = _build_parser()
        choices = {}
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices:
                choices.update(action.choices)
        shared = BUNDLE_ROOT / "core" / "tool-adapters" / "_shared"
        command_names = {
            entry["name"]
            for entry in json.loads((shared / "commands.json").read_text(encoding="utf-8"))
        }

        for command in ("detect-bypass", "validate-guidance-obligations"):
            self.assertIn(command, choices)
            self.assertIn(command, command_names)

    def test_every_tool_adapter_emitter_accepts_guidance_obligation_inputs(self) -> None:
        emitters = sorted(
            (BUNDLE_ROOT / "core" / "tool-adapters" / "emitters").glob("*/emit.sh")
        )
        self.assertGreaterEqual(len(emitters), 8)
        for emitter in emitters:
            text = emitter.read_text(encoding="utf-8")
            label = str(emitter.relative_to(BUNDLE_ROOT))
            self.assertIn("--obligations-json", text, label)
            self.assertIn("--guidance-catalog-json", text, label)
            self.assertIn("--stack", text, label)
            self.assertIn("guidance-emitter.sh", text, label)
            self.assertIn("write_signalos_guidance_file", text, label)

    def test_codex_emitter_writes_guidance_file_when_given_obligations(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("bash is not available")
        jq_check = subprocess.run(
            ["bash", "-lc", "command -v jq"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if jq_check.returncode != 0:
            self.skipTest("jq is not available in bash")

        shared = BUNDLE_ROOT / "core" / "tool-adapters" / "_shared"
        emitter = BUNDLE_ROOT / "core" / "tool-adapters" / "emitters" / "codex" / "emit.sh"
        app_root = BUNDLE_ROOT.parents[2]
        with tempfile.TemporaryDirectory(prefix="signalos-emitter-", dir=app_root) as tmp:
            output = Path(tmp)
            proc = subprocess.run(
                [
                    "bash",
                    emitter.relative_to(app_root).as_posix(),
                    "--commands-json",
                    (shared / "commands.json").relative_to(app_root).as_posix(),
                    "--skills-json",
                    (shared / "skills.json").relative_to(app_root).as_posix(),
                    "--hooks-json",
                    (shared / "hooks.json").relative_to(app_root).as_posix(),
                    "--preamble",
                    (shared / "session-preamble.md").relative_to(app_root).as_posix(),
                    "--output-dir",
                    output.relative_to(app_root).as_posix(),
                    "--obligations-json",
                    (shared / "obligations.json").relative_to(app_root).as_posix(),
                    "--guidance-catalog-json",
                    (shared / "guidance-catalog.json").relative_to(app_root).as_posix(),
                    "--stack",
                    "react-vite",
                ],
                cwd=app_root,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            guidance = output / ".signalos" / "GUIDANCE.md"
            self.assertTrue(guidance.is_file(), proc.stdout)
            text = guidance.read_text(encoding="utf-8")
            self.assertIn("Stack: react-vite", text)
            self.assertIn("OBL-APP-001", text)
            self.assertIn("test-driven-development", text)


if __name__ == "__main__":
    unittest.main()
