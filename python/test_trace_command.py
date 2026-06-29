"""Tests for `signalos trace ticket`."""

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
from signalos_lib.commands import trace


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TraceTicketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-trace-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = trace.main(argv)
        return code, json.loads(stdout.getvalue())

    def test_ticket_finds_source_proof_and_governance_references(self) -> None:
        ticket = "T-W04-001"
        _write(self.tmp / "src" / "feature.ts", f"// {ticket}: implementation\n")
        _write(self.tmp / "proof" / "release.md", f"- {ticket} proof\n")
        _write(self.tmp / ".signalos" / "waves" / "W04" / "BACKLOG.yaml", f"id: {ticket}\n")

        code, payload = self._run([
            "ticket",
            "--id",
            ticket,
            "--repo-root",
            str(self.tmp),
            "--json",
        ])

        self.assertEqual(code, 0)
        self.assertEqual(payload["schema_version"], "signalos.trace.ticket.v1")
        self.assertEqual(payload["ticket"], ticket)
        self.assertEqual(payload["reference_count"], 3)
        files = {item["file"] for item in payload["files"]}
        self.assertIn("src/feature.ts", files)
        self.assertIn("proof/release.md", files)
        self.assertIn(".signalos/waves/W04/BACKLOG.yaml", files)

    def test_ticket_returns_review_code_when_no_matches_exist(self) -> None:
        _write(self.tmp / "src" / "feature.ts", "// no ticket here\n")

        code, payload = self._run([
            "ticket",
            "--id",
            "T-W04-002",
            "--repo-root",
            str(self.tmp),
            "--json",
        ])

        self.assertEqual(code, trace.EXIT_NO_MATCHES)
        self.assertEqual(payload["status"], "no_matches")
        self.assertEqual(payload["reference_count"], 0)

    def test_ticket_rejects_noncanonical_id(self) -> None:
        code, payload = self._run([
            "ticket",
            "--id",
            "W04-002",
            "--repo-root",
            str(self.tmp),
            "--json",
        ])

        self.assertEqual(code, trace.EXIT_BAD_ARGS)
        self.assertEqual(payload["status"], "bad_args")

    def test_ticket_in_generated_evidence_only_is_not_counted(self) -> None:
        """A ticket mentioned ONLY in generated `.signalos` outputs is not a real reference."""
        ticket = "T-W05-007"
        # Generated evidence/audit outputs -- must be ignored.
        _write(
            self.tmp / ".signalos" / "evidence" / "trace" / f"{ticket}.json",
            f'{{"ticket": "{ticket}"}}\n',
        )
        _write(
            self.tmp / ".signalos" / "proof" / "report.md",
            f"- {ticket} proved\n",
        )
        _write(
            self.tmp / ".signalos" / "AUDIT_TRAIL.jsonl",
            f'{{"action": "trace", "ticket": "{ticket}"}}\n',
        )

        code, payload = self._run([
            "ticket",
            "--id",
            ticket,
            "--repo-root",
            str(self.tmp),
            "--json",
        ])

        # No GENUINE references exist -> exit 100, zero references.
        self.assertEqual(code, trace.EXIT_NO_MATCHES)
        self.assertEqual(payload["status"], "no_matches")
        self.assertEqual(payload["reference_count"], 0)

    def test_ticket_counts_governance_inputs_but_not_generated_outputs(self) -> None:
        """Genuine governance inputs count; generated `.signalos` evidence does not."""
        ticket = "T-W05-008"
        # Genuine governance input (authored backlog) -- counts.
        _write(self.tmp / ".signalos" / "backlog" / "wave-05.yaml", f"id: {ticket}\n")
        # Generated evidence output -- does NOT count.
        _write(
            self.tmp / ".signalos" / "evidence" / "trace" / f"{ticket}.json",
            f'{{"ticket": "{ticket}"}}\n',
        )

        code, payload = self._run([
            "ticket",
            "--id",
            ticket,
            "--repo-root",
            str(self.tmp),
            "--json",
        ])

        self.assertEqual(code, 0)
        self.assertEqual(payload["reference_count"], 1)
        files = {item["file"] for item in payload["files"]}
        self.assertIn(".signalos/backlog/wave-05.yaml", files)
        self.assertNotIn(".signalos/evidence/trace/T-W05-008.json", files)

    def test_ticket_skips_vendor_and_generated_directories(self) -> None:
        ticket = "T-W04-003"
        _write(self.tmp / "node_modules" / "pkg" / "index.js", f"// {ticket}\n")
        _write(self.tmp / "src" / "dist" / "bundle.js", f"// {ticket}\n")
        _write(self.tmp / "src" / "real.py", f"# {ticket}\n")

        code, payload = self._run([
            "ticket",
            "--id",
            ticket,
            "--repo-root",
            str(self.tmp),
            "--json",
        ])

        self.assertEqual(code, 0)
        self.assertEqual(payload["reference_count"], 1)
        self.assertEqual(payload["files"][0]["file"], "src/real.py")

    def test_top_level_cli_forwards_trace_ticket(self) -> None:
        ticket = "T-W04-004"
        _write(self.tmp / "tests" / "feature.test.ts", f"test('{ticket}', () => {{}})\n")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "trace",
                "ticket",
                "--id",
                ticket,
                "--repo-root",
                str(self.tmp),
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["files"][0]["file"], "tests/feature.test.ts")


if __name__ == "__main__":
    unittest.main()
