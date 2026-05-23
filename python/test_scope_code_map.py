"""Tests for the scope-card -> code map index (Phase 13).

The scanner walks a workspace for ``# SC-NNN`` and ``// SC-NNN`` markers and
produces an SC-NNN -> [file paths] dict, which round-trips through the shared
``gate_artifacts.json`` manifest. See ``signalos_lib/artifacts.py`` for the
implementation notes (docstring-skip behavior, scanned extensions, etc.).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import artifacts


def _write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class ScopeCardCodeMapTests(unittest.TestCase):
    def test_empty_workspace_returns_empty_map(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            self.assertEqual(artifacts.build_scope_card_code_map(root), {})

    def test_missing_workspace_returns_empty_map(self) -> None:
        # Non-existent path must not blow up — Layer 1 validator can call this
        # against a workspace that has not been initialized yet.
        bogus = Path(tempfile.gettempdir()) / "signalos-sc-does-not-exist-xyz"
        if bogus.exists():
            self.skipTest("temp probe path unexpectedly exists")
        self.assertEqual(artifacts.build_scope_card_code_map(bogus), {})

    def test_single_file_single_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            _write(
                root,
                "src/feature_a.py",
                "# SC-001 — implements feature A\n" "def feature_a():\n    pass\n",
            )
            result = artifacts.build_scope_card_code_map(root)
            self.assertEqual(result, {"SC-001": ["src/feature_a.py"]})

    def test_multiple_markers_in_one_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            _write(
                root,
                "src/multi.ts",
                "// SC-010 part 1\n"
                "function a() {}\n"
                "// SC-011 part 2\n"
                "function b() {}\n"
                "    // SC-010 reused later\n"
                "function c() {}\n",
            )
            result = artifacts.build_scope_card_code_map(root)
            # Same SC ID dedupes to one entry; both IDs are returned.
            self.assertEqual(
                result,
                {"SC-010": ["src/multi.ts"], "SC-011": ["src/multi.ts"]},
            )

    def test_multiple_files_one_marker_each(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            _write(root, "a/one.rs", "// SC-042 one\nfn one() {}\n")
            _write(root, "b/two.rs", "// SC-042 two\nfn two() {}\n")
            _write(root, "c/three.py", "# SC-099 only here\n")
            result = artifacts.build_scope_card_code_map(root)
            self.assertEqual(
                result,
                {
                    "SC-042": ["a/one.rs", "b/two.rs"],
                    "SC-099": ["c/three.py"],
                },
            )

    def test_marker_inside_python_docstring_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            _write(
                root,
                "src/docstring_only.py",
                '"""\n'
                "Example usage:\n\n"
                "    # SC-777 this is illustrative only, not an implementation\n"
                '"""\n'
                "def thing():\n"
                "    return 1\n",
            )
            result = artifacts.build_scope_card_code_map(root)
            self.assertEqual(result, {})

    def test_marker_in_real_comment_but_after_docstring_is_kept(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            _write(
                root,
                "src/mixed.py",
                '"""Module doc — pretend SC-100 lives here for an example."""\n'
                "# SC-200 the real one\n"
                "def thing():\n"
                "    return 1\n",
            )
            result = artifacts.build_scope_card_code_map(root)
            self.assertEqual(result, {"SC-200": ["src/mixed.py"]})

    def test_hash_marker_in_non_hash_comment_language_is_ignored(self) -> None:
        # In JSON or TSX, ``#`` is not a comment character, so we refuse to
        # treat ``# SC-001`` in those files as a marker. Only ``//`` markers
        # are honored outside hash-comment languages.
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            _write(
                root,
                "src/template.tsx",
                "// SC-300 a real marker\n"
                "const heading = '# SC-301 looks like a marker but is a string';\n",
            )
            result = artifacts.build_scope_card_code_map(root)
            self.assertEqual(result, {"SC-300": ["src/template.tsx"]})

    def test_skip_dirs_are_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            # Inside skipped dirs — must be ignored.
            _write(root, "node_modules/lib/inner.js", "// SC-500 ignored\n")
            _write(root, ".git/hooks/post.py", "# SC-501 ignored\n")
            _write(root, "target/build/out.rs", "// SC-502 ignored\n")
            # Real source — counted.
            _write(root, "src/real.js", "// SC-600 counted\n")
            result = artifacts.build_scope_card_code_map(root)
            self.assertEqual(result, {"SC-600": ["src/real.js"]})

    def test_non_source_extensions_are_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            _write(root, "notes.md", "# SC-700 in markdown heading, must ignore\n")
            _write(root, "data.json", '{ "x": "# SC-701" }\n')
            _write(root, "src/keeper.py", "# SC-800 kept\n")
            result = artifacts.build_scope_card_code_map(root)
            self.assertEqual(result, {"SC-800": ["src/keeper.py"]})

    def test_paths_are_posix_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            _write(root, "zeta/last.py", "# SC-001 z\n")
            _write(root, "alpha/first.py", "# SC-001 a\n")
            _write(root, "mid/middle.py", "# SC-001 m\n")
            result = artifacts.build_scope_card_code_map(root)
            self.assertEqual(
                result["SC-001"],
                ["alpha/first.py", "mid/middle.py", "zeta/last.py"],
            )
            for path in result["SC-001"]:
                self.assertNotIn("\\", path, "paths must use POSIX separators")

    def test_round_trip_through_manifest_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="signalos-sc-") as tmp:
            root = Path(tmp)
            # Workspace with a few markers.
            _write(root, "src/one.py", "# SC-001 first\n")
            _write(root, "src/two.rs", "// SC-002 second\n")

            # A throwaway manifest that looks like the real one. We use a
            # tmp manifest so the packaged module's gate_artifacts.json is
            # never mutated by tests.
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "gate_labels": {"G0": "Gate 0"},
                        "gates": {"G0": []},
                        "scope_card_code_map": {},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            written = artifacts.write_scope_card_code_map(root, manifest_path)
            self.assertEqual(
                written,
                {"SC-001": ["src/one.py"], "SC-002": ["src/two.rs"]},
            )
            reloaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["scope_card_code_map"], written)
            # Existing keys must be untouched.
            self.assertEqual(reloaded["gate_labels"], {"G0": "Gate 0"})
            self.assertEqual(reloaded["gates"], {"G0": []})
            self.assertEqual(reloaded["schema_version"], 1)

    def test_packaged_manifest_exposes_scope_card_accessor(self) -> None:
        # The shipped manifest must expose the new top-level key, even if
        # the initial value is the empty dict. Consumers (Layer 1 validator,
        # release-readiness) read it through this accessor.
        sc_map = artifacts.scope_card_code_map()
        self.assertIsInstance(sc_map, dict)
        for sc_id, paths in sc_map.items():
            self.assertRegex(sc_id, r"^SC-\d+$")
            self.assertIsInstance(paths, list)
            for path in paths:
                self.assertIsInstance(path, str)
                self.assertNotIn("\\", path)

        # Returned dict is a copy — mutating it must not pollute module state.
        sc_map["SC-9999"] = ["bogus.py"]
        self.assertNotIn("SC-9999", artifacts.scope_card_code_map())


if __name__ == "__main__":
    unittest.main()
