"""Tests for `signalos bundle list|extract`."""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import cli
from signalos_lib.commands import bundle


class BundleCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-bundle-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_stdout(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = bundle.main(argv)
        return code, stdout.getvalue()

    def test_bundle_list_commands_includes_known_command_doc(self) -> None:
        code, output = self._run_stdout(["list", "--category", "commands"])

        self.assertEqual(code, 0)
        self.assertIn("commands/signal-build.md", output)
        self.assertIn("commands/feature-gate.md", output)

    def test_bundle_list_count_reports_known_categories(self) -> None:
        code, output = self._run_stdout(["list", "--count"])

        self.assertEqual(code, 0)
        self.assertIn("commands:", output)
        self.assertIn("hooks:", output)
        self.assertIn("prompts:", output)

    def test_bundle_extract_copies_category_files(self) -> None:
        out_dir = self.tmp / "out"

        code, output = self._run_stdout([
            "extract",
            "--category",
            "commands",
            "--output",
            str(out_dir),
        ])

        self.assertEqual(code, 0)
        self.assertIn("extracted", output)
        self.assertTrue((out_dir / "signal-build.md").is_file())
        self.assertTrue((out_dir / "feature-gate.md").is_file())

    def test_bundle_unknown_category_fails(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = bundle.main(["list", "--category", "unknown"])

        self.assertEqual(code, bundle.EXIT_BAD_ARGS)
        self.assertIn("unknown category", stderr.getvalue())

    def test_bundle_list_known_but_empty_category_warns_and_fails(self) -> None:
        """A KNOWN category that resolves to zero on-disk files must refuse."""
        original = bundle._category_entries
        bundle._category_entries = lambda category: []  # type: ignore[assignment]
        try:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = bundle.main(["list", "--category", "commands"])
        finally:
            bundle._category_entries = original  # type: ignore[assignment]

        self.assertEqual(code, bundle.EXIT_EMPTY_CATEGORY)
        self.assertNotEqual(code, bundle.EXIT_OK)
        self.assertIn("zero", stderr.getvalue().lower())
        self.assertIn("commands", stderr.getvalue())

    def test_bundle_list_count_known_but_empty_category_fails(self) -> None:
        original = bundle._category_entries
        bundle._category_entries = lambda category: []  # type: ignore[assignment]
        try:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = bundle.main(["list", "--category", "commands", "--count"])
        finally:
            bundle._category_entries = original  # type: ignore[assignment]

        self.assertEqual(code, bundle.EXIT_EMPTY_CATEGORY)
        self.assertIn("commands: 0", stdout.getvalue())
        self.assertIn("zero", stderr.getvalue().lower())

    def test_bundle_extract_known_but_empty_category_warns_and_fails(self) -> None:
        original = bundle._category_entries
        bundle._category_entries = lambda category: []  # type: ignore[assignment]
        try:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = bundle.main([
                    "extract",
                    "--category",
                    "commands",
                    "--output",
                    str(self.tmp / "out"),
                ])
        finally:
            bundle._category_entries = original  # type: ignore[assignment]

        self.assertEqual(code, bundle.EXIT_EMPTY_CATEGORY)
        self.assertIn("zero", stderr.getvalue().lower())
        self.assertFalse((self.tmp / "out").exists())

    def test_top_level_cli_forwards_bundle_list(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main(["signalos", "bundle", "list", "--category", "commands"])

        self.assertEqual(code, 0)
        self.assertIn("commands/signal-build.md", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
