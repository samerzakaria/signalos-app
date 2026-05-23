from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.commands import init


class InitAdoptionTests(unittest.TestCase):
    def test_init_profile_writes_metadata_and_ci_templates(self):
        with tempfile.TemporaryDirectory(prefix="signalos-profile-") as tmp:
            root = Path(tmp)

            rc = init.main([
                str(root),
                "--yes",
                "--no-git",
                "--name",
                "Profiled Product",
                "--profile",
                "react-vite",
            ])

            self.assertEqual(rc, 0)
            profile = json.loads((root / ".signalos" / "profile.json").read_text(encoding="utf-8"))
            validation = json.loads((root / ".signalos" / "profile-validation.json").read_text(encoding="utf-8"))

            self.assertEqual(profile["profile_id"], "react-vite")
            self.assertEqual(profile["preview"]["mode"], "npm-script")
            self.assertTrue((root / ".github" / "workflows" / "signalos-ci.yml").is_file())
            self.assertTrue(validation["ok"], validation)

    def test_keep_existing_writes_adoption_artifacts_and_preserves_files(self):
        with tempfile.TemporaryDirectory(prefix="signalos-adopt-") as tmp:
            root = Path(tmp)
            package = root / "package.json"
            readme = root / "README.md"
            source = root / "src" / "App.tsx"
            source.parent.mkdir()
            package.write_text(
                json.dumps({
                    "scripts": {"build": "vite build", "dev": "vite"},
                    "dependencies": {"@vitejs/plugin-react": "^5.0.0", "react": "^19.0.0"},
                    "devDependencies": {"vite": "^7.0.0"},
                }),
                encoding="utf-8",
            )
            readme.write_bytes(b"USER OWN README\n")
            source.write_bytes(b"export function App() { return <main />; }\n")
            before = {
                "package.json": package.read_bytes(),
                "README.md": readme.read_bytes(),
                "src/App.tsx": source.read_bytes(),
            }

            rc = init.main([
                str(root),
                "--yes",
                "--keep-existing",
                "--minimal",
                "--no-git",
                "--name",
                "Task Factory",
            ])

            self.assertEqual(rc, 0)
            self.assertEqual(package.read_bytes(), before["package.json"])
            self.assertEqual(readme.read_bytes(), before["README.md"])
            self.assertEqual(source.read_bytes(), before["src/App.tsx"])

            adoption = root / ".signalos" / "adoption"
            surface = json.loads((adoption / "surface-inventory.json").read_text(encoding="utf-8"))
            unknowns = json.loads((adoption / "unknowns.json").read_text(encoding="utf-8"))
            source_intent = json.loads(
                (root / ".signalos" / "sources" / "initial-intent.json").read_text(encoding="utf-8")
            )

            self.assertEqual(surface["schema_version"], 1)
            self.assertEqual(surface["project_name"], "Task Factory")
            self.assertEqual(surface["detected_profile"], "react-vite")
            self.assertIn("package", {entry["type"] for entry in surface["surfaces"]})
            self.assertIn("frontend", {entry["type"] for entry in surface["surfaces"]})
            self.assertEqual(unknowns["schema_version"], 1)
            self.assertGreaterEqual(len(unknowns["items"]), 2)
            self.assertEqual(source_intent["source_type"], "existing_repo_adoption")
            self.assertTrue((adoption / "onboarding-draft.md").is_file())
            self.assertTrue((adoption / "next-steps.md").is_file())

    def test_keep_existing_empty_repo_does_not_emit_adoption_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="signalos-empty-") as tmp:
            root = Path(tmp)

            rc = init.main([
                str(root),
                "--yes",
                "--keep-existing",
                "--minimal",
                "--no-git",
                "--name",
                "Empty Product",
            ])

            self.assertEqual(rc, 0)
            self.assertFalse((root / ".signalos" / "adoption" / "surface-inventory.json").exists())


if __name__ == "__main__":
    unittest.main()
