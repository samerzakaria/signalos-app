"""Focused tests for Phase 13 wave-velocity metrics.

Covers the new helpers in `signalos_lib.velocity` that surface on the
dashboard sidebar:

  * compute_wave_velocity      — top-level payload
  * compute_sessions_per_day   — derived from AUDIT_TRAIL.jsonl
  * compute_scope_card_burndown — derived from autoplan tasks
  * compute_eta_days           — predicted days at current velocity
  * cmd_signal_velocity        — `signal-velocity --json` CLI surface

These tests deliberately exercise the failure modes (missing trail,
malformed audit line, zero-data) so the dashboard never crashes on
real-world repos that haven't yet captured velocity signal.
"""

from __future__ import annotations

import contextlib
import datetime as _datetime
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import velocity as velocity_lib
from signalos_lib.commands.velocity import cmd_signal_velocity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_audit_line(root: Path, entry: dict) -> None:
    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    trail.parent.mkdir(parents=True, exist_ok=True)
    with trail.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _write_autoplan(root: Path, wave: str, tasks: list[dict]) -> None:
    """Write a YAML-like autoplan file matching the existing format."""
    plan_path = root / ".signalos" / "plans" / f"autoplan-{wave}.yaml"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for task in tasks:
        lines.append("---")
        for key in ("id", "title", "description", "wave", "tier", "effort_days", "status"):
            value = task.get(key, "")
            if key == "wave":
                lines.append(f'wave: "{value}"')
            else:
                lines.append(f"{key}: {value}")
        lines.append("")
    plan_path.write_text("\n".join(lines), encoding="utf-8")


def _iso(ts: _datetime.datetime) -> str:
    return ts.astimezone(_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class WaveVelocityEmptyStateTests(unittest.TestCase):
    """A brand-new workspace must report zero / null values without raising."""

    def test_empty_workspace_returns_zero_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = velocity_lib.compute_wave_velocity(root)

            self.assertEqual(payload["sessions_per_day"], 0.0)
            self.assertEqual(payload["scope_card_burndown"], [])
            self.assertIsNone(payload["eta_days"])
            self.assertIsNone(payload["last_session_at"])
            self.assertIn("generated_at", payload)
            self.assertIn("window_days", payload)

    def test_iter_audit_entries_on_missing_trail_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(list(velocity_lib.iter_audit_entries(root)), [])

    def test_eta_is_none_with_no_velocity(self) -> None:
        eta = velocity_lib.compute_eta_days(
            sessions_per_day=0.0,
            burndown=[{"wave": "1", "total": 10, "completed": 2}],
        )
        self.assertIsNone(eta)


class WaveVelocitySingleWaveTests(unittest.TestCase):
    """A single wave with audit entries and tasks should compute coherent numbers."""

    def test_sessions_per_day_counts_in_window(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            now = _datetime.datetime(2026, 5, 23, 12, 0, 0, tzinfo=_datetime.timezone.utc)
            # 3 begin events inside the 14-day window, 1 outside (should not count).
            for offset_days in (1, 3, 6):
                ts = now - _datetime.timedelta(days=offset_days)
                _write_audit_line(root, {"ts": _iso(ts), "action": "wave:begin"})
            _write_audit_line(
                root,
                {"ts": _iso(now - _datetime.timedelta(days=30)), "action": "wave:begin"},
            )
            # Unrelated action — must not count.
            _write_audit_line(
                root,
                {"ts": _iso(now - _datetime.timedelta(days=2)), "action": "secret:reveal"},
            )

            per_day, last_iso = velocity_lib.compute_sessions_per_day(
                root, window_days=14, now=now,
            )

            # 3 in-window / 14 days
            self.assertAlmostEqual(per_day, 3 / 14, places=6)
            # Last seen reflects the most recent qualifying session (out-of-window
            # entries still set last_session_at, by design — the dashboard
            # uses it as a "last activity" pointer, not a windowed value).
            self.assertIsNotNone(last_iso)

    def test_single_wave_burndown_and_eta(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_autoplan(root, "1", [
                {"id": "task-001", "title": "A", "description": "A",
                 "wave": "1", "tier": "T2", "effort_days": 0.5, "status": "done"},
                {"id": "task-002", "title": "B", "description": "B",
                 "wave": "1", "tier": "T2", "effort_days": 0.5, "status": "pending"},
                {"id": "task-003", "title": "C", "description": "C",
                 "wave": "1", "tier": "T2", "effort_days": 0.5, "status": "pending"},
            ])

            burndown = velocity_lib.compute_scope_card_burndown(root)
            self.assertEqual(burndown, [{"wave": "1", "total": 3, "completed": 1}])

            # 1 session/day → 2 remaining cards → 2.0 days
            eta = velocity_lib.compute_eta_days(sessions_per_day=1.0, burndown=burndown)
            self.assertEqual(eta, 2.0)


class WaveVelocityMultiWaveTests(unittest.TestCase):
    """Multiple autoplan waves contribute to a combined burndown + ETA."""

    def test_multi_wave_aggregates_remaining_cards(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_autoplan(root, "1", [
                {"id": "task-001", "title": "A", "description": "A",
                 "wave": "1", "tier": "T2", "effort_days": 0.5, "status": "completed"},
                {"id": "task-002", "title": "B", "description": "B",
                 "wave": "1", "tier": "T2", "effort_days": 0.5, "status": "pending"},
            ])
            _write_autoplan(root, "2", [
                {"id": "task-001", "title": "X", "description": "X",
                 "wave": "2", "tier": "T2", "effort_days": 0.5, "status": "pending"},
                {"id": "task-002", "title": "Y", "description": "Y",
                 "wave": "2", "tier": "T2", "effort_days": 0.5, "status": "pending"},
                {"id": "task-003", "title": "Z", "description": "Z",
                 "wave": "2", "tier": "T2", "effort_days": 0.5, "status": "shipped"},
            ])

            burndown = velocity_lib.compute_scope_card_burndown(root)
            waves = {row["wave"]: row for row in burndown}
            self.assertEqual(waves["1"], {"wave": "1", "total": 2, "completed": 1})
            self.assertEqual(waves["2"], {"wave": "2", "total": 3, "completed": 1})

            # Remaining across both waves: 1 + 2 = 3. At 0.5 sessions/day → 6 days.
            eta = velocity_lib.compute_eta_days(sessions_per_day=0.5, burndown=burndown)
            self.assertEqual(eta, 6.0)


class WaveVelocityMalformedAuditTests(unittest.TestCase):
    """Malformed / partial audit lines must not crash the velocity computation."""

    def test_malformed_audit_lines_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
            trail.parent.mkdir(parents=True, exist_ok=True)
            now = _datetime.datetime(2026, 5, 23, 12, 0, 0, tzinfo=_datetime.timezone.utc)
            good = {"ts": _iso(now - _datetime.timedelta(days=1)), "action": "wave:begin"}
            trail.write_text(
                json.dumps(good) + "\n"
                "this is not json at all\n"
                "{\"truncated\": true\n"            # invalid JSON
                "[\"a\", \"list\", \"not\", \"dict\"]\n"  # parses but not a dict
                "\n"                                # blank line
                + json.dumps({"ts": "not-a-timestamp", "action": "wave:begin"}) + "\n"
                + json.dumps({"ts": _iso(now - _datetime.timedelta(days=2)), "action": "wave:begin"}) + "\n",
                encoding="utf-8",
            )

            # Must not raise.
            per_day, last_iso = velocity_lib.compute_sessions_per_day(
                root, window_days=14, now=now,
            )
            # Two valid session-start entries should be counted (the
            # `wave:begin` with `ts="not-a-timestamp"` is dropped).
            self.assertAlmostEqual(per_day, 2 / 14, places=6)
            self.assertIsNotNone(last_iso)


class SignalVelocityCommandTests(unittest.TestCase):
    """`signal-velocity --json` CLI must emit a parseable payload."""

    def test_signal_velocity_json_output_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cmd_signal_velocity(["--json", "--repo-root", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            for key in (
                "sessions_per_day", "scope_card_burndown",
                "eta_days", "last_session_at",
                "window_days", "generated_at",
            ):
                self.assertIn(key, payload)

    def test_signal_velocity_human_output_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cmd_signal_velocity(["--repo-root", str(root)])
            self.assertEqual(rc, 0)
            text = buf.getvalue()
            self.assertIn("sessions/day", text)
            self.assertIn("eta", text.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
