"""Tests for `signalos defer count|harvest` (Phase 13 hardening)."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import cli
from signalos_lib.commands import defer


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class DeferCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-defer-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = defer.main(argv)
        return code, json.loads(stdout.getvalue())

    def test_count_finds_full_todo_defer_form_and_bare_form(self) -> None:
        _write(self.tmp / "src" / "a.ts", "// TODO: ship it — DEFER: add caching\n")
        _write(self.tmp / "src" / "b.py", "# DEFER: revisit error handling\n")
        _write(self.tmp / "src" / "c.rs", "// defer: lowercase note\n")  # case insensitive

        code, payload = self._run(["count", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["schema_version"], "signalos.defer.count.v1")
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["files"], 3)
        self.assertIn("src/a.ts", payload["by_file"])
        self.assertIn("src/b.py", payload["by_file"])
        self.assertIn("src/c.rs", payload["by_file"])
        self.assertEqual(payload["by_file"]["src/a.ts"][0]["note"], "add caching")
        self.assertEqual(payload["by_file"]["src/b.py"][0]["note"], "revisit error handling")

    def test_count_reconciles_wave_markers_against_prd_defer_rows(self) -> None:
        _write(self.tmp / "src" / "feature.ts", "// DEFER: W02+ add caching\n")
        _write(
            self.tmp / ".signalos" / "PRD_TRACEABILITY.md",
            "| Claim | Destination | Source |\n"
            "| C-1 | DEFER -> W02+ | src/feature.ts |\n",
        )

        code, payload = self._run(["count", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["target_markers"], 1)
        self.assertEqual(payload["reconciled_count"], 1)
        self.assertEqual(payload["unreconciled_count"], 0)
        self.assertEqual(payload["markers"][0]["target_wave"], "W02+")
        self.assertEqual(payload["by_file"]["src/feature.ts"][0]["target_wave"], "W02+")

    def test_count_returns_review_code_for_unreconciled_wave_marker(self) -> None:
        _write(self.tmp / "src" / "feature.ts", "// DEFER: W03+ add audit export\n")

        code, payload = self._run(["count", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, defer.EXIT_UNRECONCILED)
        self.assertEqual(payload["target_markers"], 1)
        self.assertEqual(payload["reconciled_count"], 0)
        self.assertEqual(payload["unreconciled_count"], 1)
        self.assertEqual(payload["unreconciled_markers"][0]["file"], "src/feature.ts")
        self.assertEqual(payload["unreconciled_markers"][0]["target_wave"], "W03+")

    def test_count_reconciles_when_prd_row_cites_only_file_name(self) -> None:
        _write(self.tmp / "proof" / "release.md", "// DEFER: W04+ publish proof\n")
        _write(
            self.tmp / ".signalos" / "PRD_TRACEABILITY.md",
            "| Claim | Destination | Source |\n"
            "| C-2 | DEFER | release.md |\n",
        )

        code, payload = self._run(["count", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["reconciled_count"], 1)
        self.assertEqual(payload["unreconciled_count"], 0)
        # The bare scalar duplicates must be gone -- only the canonical
        # `_count` keys remain.
        self.assertNotIn("reconciled", payload)
        self.assertNotIn("unreconciled", payload)

    def test_count_payload_omits_bare_scalar_duplicates(self) -> None:
        """Schema is de-duplicated: only canonical *_count keys, no bare scalars."""
        _write(self.tmp / "src" / "feature.ts", "// DEFER: W03+ add audit export\n")

        code, payload = self._run(["count", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, defer.EXIT_UNRECONCILED)
        # Canonical keys present...
        self.assertIn("reconciled_count", payload)
        self.assertIn("unreconciled_count", payload)
        self.assertIn("unreconciled_markers", payload)
        # ...and the bare duplicates are removed.
        self.assertNotIn("reconciled", payload)
        self.assertNotIn("unreconciled", payload)

    def test_count_groups_multiple_hits_per_file(self) -> None:
        body = (
            "// DEFER: one\n"
            "let x = 1;\n"
            "// TODO: y — DEFER: two\n"
            "let y = 2;\n"
            "# DEFER: three (only triggers in shell-like files; ts uses //)\n"
        )
        _write(self.tmp / "src" / "multi.ts", body)

        code, payload = self._run(["count", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        # ts file: only // markers; the `# DEFER:` in a ts file still matches
        # the bare-form pattern (we accept # as a comment marker generically).
        self.assertEqual(payload["files"], 1)
        self.assertEqual(payload["total"], 3)
        notes = [item["note"] for item in payload["by_file"]["src/multi.ts"]]
        self.assertEqual(notes, ["one", "two", "three (only triggers in shell-like files; ts uses //)"])

    def test_count_ignores_skip_dirs_and_binary_like_suffixes(self) -> None:
        _write(self.tmp / "node_modules" / "pkg" / "x.ts", "// DEFER: ignore me\n")
        _write(self.tmp / ".git" / "info" / "x.py", "# DEFER: ignore me\n")
        _write(self.tmp / "image.png", "// DEFER: not source\n")
        _write(self.tmp / "src" / "real.ts", "// DEFER: real\n")

        code, payload = self._run(["count", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(list(payload["by_file"].keys()), ["src/real.ts"])

    def test_count_zero_when_no_markers(self) -> None:
        _write(self.tmp / "src" / "clean.py", "def f():\n    return 1\n")
        code, payload = self._run(["count", "--repo-root", str(self.tmp), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["files"], 0)


class DeferHarvestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-defer-harvest-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = defer.main(argv)
        return code, json.loads(stdout.getvalue())

    def test_harvest_writes_backlog_yaml_with_schema_shape(self) -> None:
        _write(self.tmp / "src" / "a.ts", "// TODO: x — DEFER: add metric\n")
        _write(self.tmp / "src" / "b.py", "# DEFER: handle empty case\n")

        code, payload = self._run(["harvest", "--wave", "7", "--repo-root", str(self.tmp), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["schema_version"], "signalos.defer.harvest.v1")
        self.assertEqual(payload["wave"], "7")
        self.assertEqual(payload["harvested"], 2)

        out_path = self.tmp / ".signalos" / "backlog" / "wave-7.yaml"
        self.assertTrue(out_path.is_file())
        text = out_path.read_text(encoding="utf-8")
        self.assertIn("backlog:", text)
        self.assertIn('id: "wave-7-defer-001"', text)
        self.assertIn('title: "add metric"', text)
        self.assertIn("status: raw", text)
        self.assertIn("wave: 7", text)
        self.assertIn('source_path: "src/a.ts"', text)

    def test_harvest_appends_audit_trail_entry(self) -> None:
        _write(self.tmp / "src" / "x.ts", "// DEFER: do it\n")

        code, _ = self._run(["harvest", "--wave", "3", "--repo-root", str(self.tmp), "--json"])
        self.assertEqual(code, 0)

        trail = self.tmp / ".signalos" / "AUDIT_TRAIL.jsonl"
        self.assertTrue(trail.is_file())
        rows = [json.loads(line) for line in trail.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "defer-harvest")
        self.assertEqual(rows[0]["wave"], "3")
        self.assertEqual(rows[0]["count"], 1)

    def test_harvest_empty_workspace_writes_empty_backlog(self) -> None:
        code, payload = self._run(["harvest", "--wave", "1", "--repo-root", str(self.tmp), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["harvested"], 0)
        out_path = self.tmp / ".signalos" / "backlog" / "wave-1.yaml"
        self.assertTrue(out_path.is_file())
        text = out_path.read_text(encoding="utf-8")
        self.assertIn("backlog:", text)


class DeferCliRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-defer-cli-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_top_level_cli_forwards_count(self) -> None:
        _write(self.tmp / "src" / "a.ts", "// DEFER: top-level\n")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos", "defer", "count",
                "--repo-root", str(self.tmp),
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["total"], 1)


if __name__ == "__main__":
    unittest.main()
