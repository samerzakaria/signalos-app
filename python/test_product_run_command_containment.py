# test_product_run_command_containment.py
# Security guard: run_command path containment.
#
# The build agent's run_command tool runs shell=True, cwd=repo_root with read
# utilities (cat/type/ls/head/tail/npx vitest/npx tsc ...) on its allowlist. A
# model could otherwise read files OUTSIDE the repo via `cat ../../gold/x`,
# `type ..\\..\\gold\\x`, `ls c:/tmp/prove-a`, or
# `npx vitest run ../../gold/x.test.ts` -- exfiltrating a hidden gold test
# suite. _command_escapes_workspace closes this; these tests exercise it
# directly (returns the offending token, or None when the command is safe).

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.agent_loop import _command_escapes_workspace


class CommandContainmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _check(self, command: str) -> str | None:
        return _command_escapes_workspace(command, self.repo)

    # --- commands that ESCAPE the workspace -> offending token returned ------

    def test_cat_parent_traversal_escapes(self) -> None:
        self.assertEqual(
            self._check("cat ../../gold/x.test.ts"), "../../gold/x.test.ts"
        )

    def test_type_backslash_traversal_escapes(self) -> None:
        # Windows-style: `type ..\..\gold\x`
        self.assertEqual(
            self._check("type ..\\..\\gold\\x"), "..\\..\\gold\\x"
        )

    def test_ls_absolute_path_escapes(self) -> None:
        self.assertEqual(self._check("ls c:/tmp/prove-a"), "c:/tmp/prove-a")

    def test_vitest_run_traversal_escapes(self) -> None:
        self.assertEqual(
            self._check("npx vitest run ../../gold/a.test.ts"),
            "../../gold/a.test.ts",
        )

    def test_bare_dotdot_escapes(self) -> None:
        # A lone `..` references the parent directory.
        self.assertEqual(self._check("cat .."), "..")

    # --- commands that stay INSIDE the workspace -> None ---------------------

    def test_in_repo_relative_ok(self) -> None:
        self.assertIsNone(self._check("cat src/foo.ts"))

    def test_vitest_run_dir_ok(self) -> None:
        self.assertIsNone(self._check("npx vitest run src"))

    def test_bare_ls_ok(self) -> None:
        self.assertIsNone(self._check("ls"))

    def test_tsc_flag_ok(self) -> None:
        self.assertIsNone(self._check("npx tsc --noEmit"))

    def test_echo_ok(self) -> None:
        self.assertIsNone(self._check("echo hello"))

    def test_dot_relative_ok(self) -> None:
        self.assertIsNone(self._check("cat ./README.md"))

    def test_resolves_back_inside_ok(self) -> None:
        # src/../src/x normalizes to src/x -- still inside the workspace.
        self.assertIsNone(self._check("cat src/../src/x"))

    def test_glob_ok(self) -> None:
        # Globs are not separator-escapes and must not be blocked.
        self.assertIsNone(self._check("npx vitest run src/**"))
        self.assertIsNone(self._check("ls *.ts"))

    def test_empty_ok(self) -> None:
        self.assertIsNone(self._check(""))
        self.assertIsNone(self._check("   "))


if __name__ == "__main__":
    unittest.main()
