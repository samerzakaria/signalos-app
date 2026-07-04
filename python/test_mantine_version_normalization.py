# test_mantine_version_normalization.py
# #27 end-to-end: the shipped @mantine/* versions must be internally consistent
# AND compatible with the React major the react-vite template ships. A remote
# build agent regenerates its own package.json and can re-skew majors (core@9 +
# hooks@7); enforce_dependency_versions()/_enforce_design_deps() must force every
# @mantine/* back onto the single _MANTINE_VERSION before install/build.
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.design import (
    _MANTINE_VERSION,
    enforce_dependency_versions,
    get_design_dependencies,
)
from signalos_lib.product.delivery import _enforce_design_deps
from signalos_lib.product.stacks import _PACKAGE_JSON_TEMPLATE


def _mantine_design() -> dict:
    return {"ui_library": {"name": "@mantine/core"}}


class TestMantineVersionCompatibility(unittest.TestCase):
    def test_mantine_major_matches_template_react_major(self) -> None:
        # Mantine v7 -> React 18; Mantine v8/v9 -> React 19. The react-vite
        # template ships React 18.x, so Mantine MUST be v7 or the generated app
        # dies with `SyntaxError: Named export 'use' not found`.
        react_spec = _PACKAGE_JSON_TEMPLATE["dependencies"]["react"]
        react_major = int(react_spec.lstrip("^~").split(".")[0])
        mantine_major = int(_MANTINE_VERSION.split(".")[0])
        compat = {18: 7, 19: 9}
        self.assertIn(react_major, compat, f"unhandled react major {react_major}")
        self.assertEqual(
            mantine_major,
            compat[react_major],
            f"Mantine {mantine_major}.x is incompatible with React {react_major}.x",
        )

    def test_mantine_version_is_exact_pinned(self) -> None:
        self.assertFalse(_MANTINE_VERSION.startswith(("^", "~")))

    def test_design_deps_all_mantine_identical(self) -> None:
        deps = get_design_dependencies(_mantine_design())
        mantine = {k: v for k, v in deps.items() if k.startswith("@mantine/")}
        self.assertTrue(mantine)
        self.assertEqual(len(set(mantine.values())), 1)
        self.assertEqual(set(mantine.values()), {_MANTINE_VERSION})

    def test_mantine_charts_has_recharts_peer(self) -> None:
        # @mantine/charts requires recharts; it must ship on the Mantine branch.
        deps = get_design_dependencies(_mantine_design())
        self.assertIn("@mantine/charts", deps)
        self.assertIn("recharts", deps)


class TestEnforceDependencyVersions(unittest.TestCase):
    def test_repins_skewed_majors(self) -> None:
        design_deps = get_design_dependencies(_mantine_design())
        # Simulate a remote agent's self-written package.json: core+form on v9,
        # hooks/dates/charts on a stale v7 patch.
        deps = {
            "react": "^18.3.1",
            "@mantine/core": "9.4.1",
            "@mantine/form": "9.4.1",
            "@mantine/hooks": "7.13.2",
            "@mantine/dates": "7.13.2",
            "@mantine/charts": "7.13.2",
        }
        changed = enforce_dependency_versions(deps, design_deps)
        self.assertTrue(changed)
        mantine = {k: v for k, v in deps.items() if k.startswith("@mantine/")}
        self.assertEqual(set(mantine.values()), {_MANTINE_VERSION})
        # Non-design deps are left untouched.
        self.assertEqual(deps["react"], "^18.3.1")

    def test_coerces_stray_mantine_subpackage_not_in_design_deps(self) -> None:
        design_deps = get_design_dependencies(_mantine_design())
        deps = {"@mantine/notifications": "9.9.9"}  # agent added, not in design set
        changed = enforce_dependency_versions(deps, design_deps)
        self.assertTrue(changed)
        self.assertEqual(deps["@mantine/notifications"], _MANTINE_VERSION)

    def test_no_change_when_already_canonical(self) -> None:
        design_deps = get_design_dependencies(_mantine_design())
        deps = dict(design_deps)
        self.assertFalse(enforce_dependency_versions(deps, design_deps))


class TestEnforceDesignDepsOnDisk(unittest.TestCase):
    def test_rewrites_skewed_package_json(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "package.json").write_text(
                json.dumps(
                    {
                        "name": "x",
                        "dependencies": {
                            "@mantine/core": "9.4.1",
                            "@mantine/hooks": "7.13.2",
                        },
                    }
                ),
                encoding="utf-8",
            )
            design_deps = get_design_dependencies(_mantine_design())
            _enforce_design_deps(repo, design_deps)
            pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
            mantine = {
                k: v for k, v in pkg["dependencies"].items() if k.startswith("@mantine/")
            }
            self.assertEqual(set(mantine.values()), {_MANTINE_VERSION})

    def test_missing_package_json_is_noop(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            # No package.json written -> must not raise.
            _enforce_design_deps(Path(td), {"@mantine/core": _MANTINE_VERSION})


if __name__ == "__main__":
    unittest.main()
