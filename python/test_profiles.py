from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.profiles import (  # noqa: E402
    ProfileError,
    ProfileNotFoundError,
    list_profile_ids,
    list_profiles,
    load_profile,
    profile_exists,
)
from signalos_lib.profiles.loader import PROFILE_SCHEMA_VERSION  # noqa: E402


class ProfileLoaderTests(unittest.TestCase):
    def test_lists_builtin_profiles(self) -> None:
        self.assertEqual(list_profile_ids(), ["generic", "react-vite"])
        self.assertEqual([profile.id for profile in list_profiles()], ["generic", "react-vite"])

    def test_loads_generic_profile_with_disabled_ci_and_preview(self) -> None:
        profile = load_profile("generic")

        self.assertEqual(profile.schema_version, PROFILE_SCHEMA_VERSION)
        self.assertEqual(profile.id, "generic")
        self.assertFalse(profile.ci.enabled)
        self.assertIn("Generic repos", profile.ci.disabled_reason or "")
        self.assertEqual(profile.preview.mode, "none")
        self.assertIn("No preview command", profile.preview.disabled_reason or "")
        self.assertIsNone(profile.command("install"))
        self.assertIsNone(profile.command("preview"))
        self.assertIn("layer1", profile.validator_groups)
        self.assertIn(
            "core/governance/Governance/SOUL-DOCUMENT.md",
            {template.destination for template in profile.required_templates},
        )

    def test_loads_react_vite_profile_with_commands_and_ci(self) -> None:
        profile = load_profile("react-vite")

        self.assertEqual(profile.id, "react-vite")
        self.assertTrue(profile.ci.enabled)
        self.assertIn(".github/workflows/signalos-ci.yml", profile.ci.files)
        self.assertEqual(profile.command("install").argv, ("npm", "install"))  # type: ignore[union-attr]
        self.assertEqual(profile.command("build").argv, ("npm", "run", "build"))  # type: ignore[union-attr]
        self.assertEqual(profile.preview.mode, "npm-script")
        self.assertEqual(profile.preview.command, "preview")
        self.assertTrue(profile.preview.requires_install)

    def test_missing_profile_raises_specific_error(self) -> None:
        self.assertFalse(profile_exists("missing"))
        with self.assertRaises(ProfileNotFoundError):
            load_profile("missing")

    def test_rejects_manifest_missing_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture_dir = Path(tmp)
            (fixture_dir / "broken.json").write_text(
                json.dumps({"schema_version": PROFILE_SCHEMA_VERSION, "id": "broken"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ProfileError, "missing required keys"):
                load_profile("broken", profile_dir=fixture_dir)

    def test_rejects_unknown_manifest_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture_dir = Path(tmp)
            manifest = load_profile("generic").to_dict()
            manifest["id"] = "unknown"
            manifest["extra"] = "not part of the contract"
            (fixture_dir / "unknown.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ProfileError, "unknown keys"):
                load_profile("unknown", profile_dir=fixture_dir)

    def test_rejects_unsafe_template_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture_dir = Path(tmp)
            manifest = load_profile("generic").to_dict()
            manifest["id"] = "unsafe"
            manifest["required_templates"][0]["destination"] = "../outside.md"
            (fixture_dir / "unsafe.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ProfileError, "safe relative path"):
                load_profile("unsafe", profile_dir=fixture_dir)

    def test_schema_file_is_machine_readable_json(self) -> None:
        schema_path = HERE / "signalos_lib" / "profiles" / "profile.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(schema["properties"]["schema_version"]["const"], PROFILE_SCHEMA_VERSION)
        self.assertIn("commands", schema["required"])
        self.assertIn("preview", schema["required"])


if __name__ == "__main__":
    unittest.main()
