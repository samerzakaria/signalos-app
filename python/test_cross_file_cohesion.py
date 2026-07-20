# Tests for fix #12: cross-file generation cohesion.
#
# The chunked per-file dispatch used to generate each file with an independent
# mental model, so nothing wired together and `tsc && vite build` failed. This
# fix enforces ONE shared, authoritative cross-file CONTRACT that every per-file
# generation call must obey:
#   A. src/types.ts is ALWAYS in the react-vite file_specs + a vitest setup file.
#   B. a canonical component manifest (filePath/componentName/importPath) is
#      injected into the App.tsx prompt AND every component test prompt.
#   C. the entity's exact field names flow into BOTH component and test prompts.
#   D. src/test/setup.ts (jest-dom) is a spec, referenced by vite.config setupFiles.
#   F. a cross-file consistency check fails when a generated file imports a
#      module/path that no generated file (or dependency) provides.
#
# unittest-compatible (runs under `python -m unittest test_cross_file_cohesion`).

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.generation import (
    build_generation_packet,
    check_cross_file_consistency,
    validate_generation_output,
)
from signalos_lib.product import stacks
from signalos_lib.product.agent_dispatch import _render_react_vite_files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent() -> dict:
    return {
        "product_name": "Expense Tracker",
        "product_type": "expense-tracker",
        "entities": ["Expense", "Category"],
        "primary_workflows": ["add expense", "categorize expense"],
    }


def _blueprint() -> dict:
    return {
        "id": "expense-tracker",
        "entities": [
            {
                "name": "Expense",
                "fields": ["id", "amount", "category", "reimbursed", "date"],
            },
            {"name": "Category", "fields": ["id", "name", "color"]},
        ],
        "workflows": [{"name": "add expense"}, {"name": "categorize expense"}],
        "ui_detail": {
            "surfaces": [
                {"id": "expense-manager", "component": "ExpenseManager", "entity": "Expense"},
                {"id": "category-manager", "component": "CategoryManager", "entity": "Category"},
            ]
        },
    }


def _react_packet(repo_root: Path) -> dict:
    return build_generation_packet(
        repo_root=repo_root,
        intent=_intent(),
        blueprint=_blueprint(),
        profile="react-vite",
        design={
            "ui_library": {"name": "Mantine"},
            "state_management": {"name": "zustand"},
            "form_handling": {"name": "react-hook-form"},
            "design_tokens": {"primary_color": "#2563eb", "font_family": "Inter"},
        },
    )


def _spec_paths(packet: dict) -> set[str]:
    return {s["path"].replace("\\", "/") for s in packet["file_specs"]}


def _spec_for(packet: dict, path: str) -> dict:
    for s in packet["file_specs"]:
        if s["path"].replace("\\", "/") == path:
            return s
    raise AssertionError(f"no spec for {path}")


# ---------------------------------------------------------------------------
# A + D: shared foundation specs always present
# ---------------------------------------------------------------------------

class TestFoundationSpecsAlwaysPresent(unittest.TestCase):
    def test_types_module_always_in_file_specs(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            self.assertIn("src/types.ts", _spec_paths(packet))

    def test_types_module_present_even_without_blueprint_entities(self):
        # No blueprint at all -- types.ts must STILL be a foundation spec so
        # components importing ../types resolve (the TS2307 drift).
        with tempfile.TemporaryDirectory() as d:
            packet = build_generation_packet(
                repo_root=Path(d),
                intent=_intent(),
                blueprint=None,
                profile="react-vite",
                design={"ui_library": {"name": "Mantine"}},
            )
            self.assertIn("src/types.ts", _spec_paths(packet))

    def test_vitest_setup_file_in_file_specs(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            self.assertIn("src/test/setup.ts", _spec_paths(packet))

    def test_setup_spec_references_jest_dom(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            spec = _spec_for(packet, "src/test/setup.ts")
            blob = spec["description"] + " " + " ".join(spec.get("constraints", []))
            self.assertIn("jest-dom", blob)

    def test_vite_config_wires_setupfiles(self):
        # The emitted vite.config.cjs must reference the setup file via
        # setupFiles so jest-dom matchers resolve at test time.
        self.assertIn("setupFiles", stacks._VITE_CONFIG)
        self.assertIn("src/test/setup.ts", stacks._VITE_CONFIG)

    def test_scaffold_ships_user_event(self):
        # #40: the interaction-test prompt permits `userEvent`, so the scaffold
        # MUST declare @testing-library/user-event -- else a generated test that
        # imports it fails to resolve (TS2307) and vitest collects ZERO tests.
        # (Surfaced by the funded e2e: 2 test files failed to collect on a
        # missing user-event import.)
        dev = stacks._PACKAGE_JSON_TEMPLATE["devDependencies"]
        self.assertIn("@testing-library/user-event", dev)

    def test_app_test_is_deterministic_composition_smoke(self):
        # #48: App.test.tsx is rendered deterministically (like the foundation
        # files) -- a minimal "App mounts" smoke that can't drift the way an
        # LLM-written App.test.tsx did (phantom store imports, malformed JSX).
        from signalos_lib.product.agent_dispatch import _render_foundation_file
        out = _render_foundation_file(
            "src/App.test.tsx", {"product": "Task Tracker"}, [],
        )
        self.assertIsNotNone(out)                     # deterministic, not LLM
        self.assertIn("import App from './App'", out)
        self.assertIn("render(<App />)", out)
        self.assertIn("toBeTruthy", out)
        # no coupling to the deterministic shell's jargon -> holds for any App
        self.assertNotIn("Governed delivery scope", out)

    def test_vitest_setup_stubs_matchmedia_and_resizeobserver(self):
        # #42: jsdom implements neither window.matchMedia nor ResizeObserver,
        # which Mantine's hooks call on render -- without the stubs EVERY test
        # of a Mantine component throws at render (funded e2e: 0/9 rendered).
        from signalos_lib.product.agent_dispatch import _render_vitest_setup
        setup = _render_vitest_setup()
        self.assertIn("matchMedia", setup)
        self.assertIn("ResizeObserver", setup)
        # the scaffold-time setup template matches the generated one
        self.assertIn("matchMedia", stacks._VITEST_SETUP)
        self.assertIn("ResizeObserver", stacks._VITEST_SETUP)

    def test_types_spec_names_exact_interfaces(self):
        # Fix A: the exact interface names must be injectable into every
        # component/test prompt -> the types spec must name them.
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            spec = _spec_for(packet, "src/types.ts")
            self.assertIn("Expense", spec["description"])
            self.assertIn("Category", spec["description"])


# ---------------------------------------------------------------------------
# B: canonical component manifest on the packet
# ---------------------------------------------------------------------------

class TestComponentManifest(unittest.TestCase):
    def test_packet_carries_component_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            manifest = packet.get("component_manifest")
            self.assertIsInstance(manifest, list)
            names = {m["componentName"] for m in manifest}
            self.assertIn("ExpenseManager", names)
            self.assertIn("CategoryManager", names)

    def test_manifest_entries_have_filepath_and_importpath(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            for entry in packet["component_manifest"]:
                self.assertIn("filePath", entry)
                self.assertIn("componentName", entry)
                self.assertIn("importPath", entry)
                # importPath is what App.tsx must import from (default export)
                self.assertTrue(
                    entry["importPath"].startswith("./components/"),
                    entry["importPath"],
                )
                self.assertTrue(entry["filePath"].endswith(".tsx"))

    def test_manifest_matches_generated_component_specs(self):
        # Every manifest component maps to a real generated source spec path,
        # so App can never import an invented ExpenseForm/ExpenseList.
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            paths = _spec_paths(packet)
            for entry in packet["component_manifest"]:
                self.assertIn(entry["filePath"].replace("\\", "/"), paths)


# ---------------------------------------------------------------------------
# C: exact entity field names flow into component + test specs
# ---------------------------------------------------------------------------

class TestEntityFieldsShared(unittest.TestCase):
    def test_component_spec_carries_exact_fields(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            comp = _spec_for(packet, "src/components/ExpenseManager.tsx")
            self.assertIn("reimbursed", comp["description"])
            # the wrong field name must NOT be introduced anywhere
            self.assertNotIn("isReimbursed", comp["description"])

    def test_test_spec_carries_same_fields_as_component(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            test = _spec_for(packet, "src/components/ExpenseManager.test.tsx")
            self.assertIn("reimbursed", test["description"])
            self.assertNotIn("isReimbursed", test["description"])


# ---------------------------------------------------------------------------
# F: cross-file consistency check
# ---------------------------------------------------------------------------

class TestCrossFileConsistency(unittest.TestCase):
    def _write(self, root: Path, rel: str, content: str) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def _min_packet(self, specs: list[dict]) -> dict:
        return {"file_specs": specs, "profile": "react-vite"}

    def test_catches_import_of_nonexistent_local_module(self):
        # App imports ./components/ExpenseManager, but only Expense.tsx exists.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write(root, "src/App.tsx",
                        "import ExpenseManager from './components/ExpenseManager';\n"
                        "export default function App(){ return <ExpenseManager/>; }\n")
            self._write(root, "src/components/Expense.tsx",
                        "export default function Expense(){ return null; }\n")
            packet = self._min_packet([
                {"path": "src/App.tsx", "kind": "registration"},
                {"path": "src/components/Expense.tsx", "kind": "source"},
            ])
            result = check_cross_file_consistency(root, packet)
            self.assertFalse(result["valid"])
            joined = " ".join(result["violations"])
            self.assertIn("ExpenseManager", joined)

    def test_catches_missing_types_module(self):
        # A component imports ../types but no types.ts was generated (TS2307).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write(root, "src/components/Expense.tsx",
                        "import { Expense } from '../types';\n"
                        "export default function C(){ return null; }\n")
            packet = self._min_packet([
                {"path": "src/components/Expense.tsx", "kind": "source"},
            ])
            result = check_cross_file_consistency(root, packet)
            self.assertFalse(result["valid"])
            self.assertTrue(any("types" in v for v in result["violations"]))

    def test_passes_when_all_local_imports_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write(root, "src/types.ts", "export interface Expense { id: string; }\n")
            self._write(root, "src/App.tsx",
                        "import ExpenseManager from './components/ExpenseManager';\n"
                        "export default function App(){ return <ExpenseManager/>; }\n")
            self._write(root, "src/components/ExpenseManager.tsx",
                        "import { Expense } from '../types';\n"
                        "export default function ExpenseManager(){ return null; }\n")
            packet = self._min_packet([
                {"path": "src/types.ts", "kind": "config"},
                {"path": "src/App.tsx", "kind": "registration"},
                {"path": "src/components/ExpenseManager.tsx", "kind": "source"},
            ])
            result = check_cross_file_consistency(root, packet)
            self.assertTrue(result["valid"], result["violations"])

    def test_ignores_bare_package_imports(self):
        # Third-party imports (react, @mantine/core) are provided by deps, not
        # generated files -- they must NOT be flagged.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write(root, "src/App.tsx",
                        "import React from 'react';\n"
                        "import { Button } from '@mantine/core';\n"
                        "export default function App(){ return <Button/>; }\n")
            packet = self._min_packet([
                {"path": "src/App.tsx", "kind": "registration"},
            ])
            result = check_cross_file_consistency(root, packet)
            self.assertTrue(result["valid"], result["violations"])

    def test_validate_generation_output_includes_cross_file_check(self):
        # Governance-level: validate_generation_output must surface the same
        # import-resolution drift as a violation (advisory-then-enforced).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write(root, "src/App.tsx",
                        "import Ghost from './components/Ghost';\n"
                        "export default function App(){ return <Ghost/>; }\n")
            packet = {
                "file_specs": [{"path": "src/App.tsx", "kind": "registration"}],
                "allowed_paths": ["src/**"],
                "forbidden_paths": [],
                "profile": "react-vite",
            }
            result = validate_generation_output(root, packet)
            self.assertFalse(result["valid"])
            self.assertTrue(
                any("Ghost" in v for v in result["violations"]),
                result["violations"],
            )


# ---------------------------------------------------------------------------
# A + D on the deterministic local build path (no-API-key delivery). The e2e
# greenfield delivery renders these files; they must be produced so tsc passes.
# ---------------------------------------------------------------------------

class TestLocalBuildRendersFoundation(unittest.TestCase):
    def _gen(self, packet: dict) -> dict:
        # build_generation_packet returns the packet directly (flat), the
        # local renderer reads it as the generation dict.
        return packet

    def test_local_render_emits_types_and_setup(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            files = _render_react_vite_files(self._gen(packet))
            self.assertIn("src/types.ts", files)
            self.assertIn("src/test/setup.ts", files)
            self.assertIn("jest-dom", files["src/test/setup.ts"])

    def test_local_render_types_nonempty(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            files = _render_react_vite_files(self._gen(packet))
            self.assertTrue(files["src/types.ts"].strip())
            self.assertIn("Expense", files["src/types.ts"])

    def test_local_render_covers_every_spec_path(self):
        # The local build agent must render EVERY react-vite spec path, else
        # validate_generation_output reports missing files.
        with tempfile.TemporaryDirectory() as d:
            packet = _react_packet(Path(d))
            files = _render_react_vite_files(self._gen(packet))
            for spec in packet["file_specs"]:
                p = spec["path"].replace("\\", "/")
                self.assertIn(p, files, p)


if __name__ == "__main__":
    unittest.main()
