from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from signalos_lib import fleet_runtime
from signalos_lib.fleet_runtime import (
    GC_META_NAME,
    detect_runtimes,
    gc_task_workspaces,
    governed_dispatch,
)
from signalos_lib.cli import _build_parser
from signalos_lib.commands import fleet as fleet_cmd
from signalos_lib.skill_catalog import BUNDLE_ROOT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_exe(directory: Path, name: str, *, is_windows: bool) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fname = f"{name}.exe" if is_windows else name
    target = directory / fname
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    if not is_windows:
        target.chmod(target.stat().st_mode | stat.S_IXUSR)


def _valid_packet() -> dict:
    """A structurally-valid agent packet satisfying the SignalOS contract."""
    return {
        "run_id": "run-fleet-test",
        "quality_bar": {"standard": "production"},
        "success_criteria": ["do the thing"],
        "evidence_required": ["RESULT.json"],
        "forbidden_rules": ["never write outside scope"],
        "repair_policy": {"quality_failure": "rework"},
        "escalation_policy": ["escalate on ambiguity"],
        "source_policy": {"repo_facts": "use repo"},
        "team_contract": {"agents_are_signalos_team": True},
    }


def _gate_inactive(_root: Path) -> dict:
    return {"active": False}


def _gate_active(_root: Path) -> dict:
    return {"active": True, "wave": "W01"}


def _touch_dir_mtime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


# ---------------------------------------------------------------------------
# detect_runtimes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("is_windows", [False, True])
def test_detect_runtimes_finds_injected_and_reports_others_missing(
    tmp_path: Path, is_windows: bool
) -> None:
    bin_dir = tmp_path / "bin"
    _make_fake_exe(bin_dir, "claude", is_windows=is_windows)
    _make_fake_exe(bin_dir, "codex", is_windows=is_windows)

    path_env = str(bin_dir)
    records = detect_runtimes(path_env=path_env, is_windows=is_windows)
    by_id = {r["id"]: r for r in records}

    # Found the injected ones.
    assert by_id["claude-code"]["detected"] is True
    assert by_id["claude-code"]["executable"] is not None
    assert by_id["codex"]["detected"] is True

    # Others are not detected (deterministic — no host PATH leakage).
    assert by_id["cursor"]["detected"] is False
    assert by_id["cursor"]["executable"] is None
    assert by_id["gemini"]["detected"] is False
    assert by_id["github-copilot"]["detected"] is False

    # Every known runtime is reported with the required keys.
    for r in records:
        assert set(r) == {"id", "cli", "executable", "kind", "detected"}


def test_detect_runtimes_empty_path_detects_nothing() -> None:
    records = detect_runtimes(path_env="", is_windows=False)
    assert records  # still reports the full known set
    assert all(r["detected"] is False for r in records)


def test_detect_runtimes_covers_emitter_derived_set() -> None:
    records = detect_runtimes(path_env="", is_windows=False)
    ids = {r["id"] for r in records}
    for required in (
        "claude-code", "codex", "cursor", "github-copilot",
        "windsurf", "gemini",
    ):
        assert required in ids


# ---------------------------------------------------------------------------
# governed_dispatch
# ---------------------------------------------------------------------------

def test_governed_dispatch_refuses_with_no_packet_or_gate(tmp_path: Path) -> None:
    decision = governed_dispatch(
        tmp_path,
        {"id": "T1", "title": "build it"},
        packet=None,
        gate_check=_gate_inactive,
    )

    assert decision["admitted"] is False
    assert "refused" in decision["reason"].lower()
    assert decision["executed"] is False

    # Evidence row written.
    dispatch = tmp_path / ".signalos" / "evidence" / "fleet" / "dispatch.jsonl"
    assert dispatch.is_file()
    rows = [json.loads(l) for l in dispatch.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["admitted"] is False

    # Audit row written with the refused action.
    audit = tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl"
    assert audit.is_file()
    audit_rows = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines()]
    assert audit_rows[-1]["action"] == fleet_runtime.AUDIT_DISPATCH_REFUSED
    assert audit_rows[-1]["verdict"] == "refused"


def test_governed_dispatch_admits_with_valid_packet(tmp_path: Path) -> None:
    decision = governed_dispatch(
        tmp_path,
        {"id": "T2", "title": "build it"},
        packet=_valid_packet(),
        gate_check=_gate_inactive,
        runtime_id="claude-code",
    )

    assert decision["admitted"] is True
    assert decision["governance"]["packet_ok"] is True
    assert decision["governance"]["packet_run_id"] == "run-fleet-test"
    assert decision["executed"] is False  # live executor is roadmap

    audit = tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit_rows = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines()]
    assert audit_rows[-1]["action"] == fleet_runtime.AUDIT_DISPATCH_ADMITTED
    assert audit_rows[-1]["verdict"] == "admitted"


def test_governed_dispatch_admits_with_active_gate_no_packet(tmp_path: Path) -> None:
    decision = governed_dispatch(
        tmp_path,
        {"id": "T3"},
        packet=None,
        gate_check=_gate_active,
    )

    assert decision["admitted"] is True
    assert decision["governance"]["gate_active"] is True
    assert decision["governance"]["packet_ok"] is False


def test_governed_dispatch_refuses_incomplete_packet(tmp_path: Path) -> None:
    # Packet missing required contract fields -> fail closed.
    bad_packet = {"run_id": "run-x", "success_criteria": ["x"]}
    decision = governed_dispatch(
        tmp_path,
        {"id": "T4"},
        packet=bad_packet,
        gate_check=_gate_inactive,
    )
    assert decision["admitted"] is False
    assert "missing" in decision["governance"]["packet_reason"].lower()


# ---------------------------------------------------------------------------
# gc_task_workspaces
# ---------------------------------------------------------------------------

NOW = 1_000_000.0
HOUR = 3600.0
DAY = 24 * HOUR


def _write_meta(task_dir: Path, status: str, last_active_ts: float) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / GC_META_NAME).write_text(
        json.dumps({"status": status, "last_active_ts": last_active_ts}),
        encoding="utf-8",
    )


def test_gc_removes_done_expired_task(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    done = root / "task-done"
    _write_meta(done, "done", last_active_ts=NOW - 2 * DAY)
    (done / "src").mkdir(parents=True, exist_ok=True)

    summary = gc_task_workspaces(
        root, now_ts=NOW, done_ttl_s=DAY, orphan_ttl_s=7 * DAY, artifact_ttl_s=HOUR
    )

    assert not done.exists()
    removed = {r["task"] for r in summary["removed_tasks"]}
    assert "task-done" in removed


def test_gc_prunes_expired_artifact_keeps_git_and_source(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    active = root / "task-active"
    _write_meta(active, "active", last_active_ts=NOW - 60)  # fresh task

    src = active / "src"
    git = active / ".git"
    logs = active / "logs"
    node_modules = active / "node_modules"
    for d in (src, git, logs, node_modules):
        d.mkdir(parents=True, exist_ok=True)
    (src / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (node_modules / "left-pad.js").write_text("module.exports={}\n", encoding="utf-8")

    # Make node_modules old enough to prune.
    _touch_dir_mtime(node_modules, NOW - 2 * HOUR)

    summary = gc_task_workspaces(
        root, now_ts=NOW, done_ttl_s=DAY, orphan_ttl_s=7 * DAY, artifact_ttl_s=HOUR
    )

    # Task kept, artifact pruned, source/.git/logs preserved.
    assert active.exists()
    assert not node_modules.exists()
    assert src.exists() and (src / "main.py").exists()
    assert git.exists()
    assert logs.exists()

    pruned = {(p["task"], p["artifact"]) for p in summary["pruned_artifacts"]}
    assert ("task-active", "node_modules") in pruned


def test_gc_keeps_fresh_task(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    fresh = root / "task-fresh"
    _write_meta(fresh, "done", last_active_ts=NOW - 60)  # done but within TTL
    (fresh / "src").mkdir(parents=True, exist_ok=True)

    summary = gc_task_workspaces(
        root, now_ts=NOW, done_ttl_s=DAY, orphan_ttl_s=7 * DAY, artifact_ttl_s=HOUR
    )

    assert fresh.exists()
    kept = {k["task"] for k in summary["kept_tasks"]}
    assert "task-fresh" in kept


def test_gc_removes_expired_orphan(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    orphan = root / "task-orphan"  # NO .gc_meta.json
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "stuff").mkdir()
    _touch_dir_mtime(orphan, NOW - 8 * DAY)

    summary = gc_task_workspaces(
        root, now_ts=NOW, done_ttl_s=DAY, orphan_ttl_s=7 * DAY, artifact_ttl_s=HOUR
    )

    assert not orphan.exists()
    removed = {(r["task"], r["reason"]) for r in summary["removed_tasks"]}
    assert ("task-orphan", "orphan-expired") in removed


def test_gc_keeps_fresh_orphan(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    orphan = root / "fresh-orphan"
    orphan.mkdir(parents=True, exist_ok=True)
    _touch_dir_mtime(orphan, NOW - 60)

    summary = gc_task_workspaces(
        root, now_ts=NOW, done_ttl_s=DAY, orphan_ttl_s=7 * DAY, artifact_ttl_s=HOUR
    )

    assert orphan.exists()


def test_gc_missing_root_returns_empty_summary(tmp_path: Path) -> None:
    summary = gc_task_workspaces(
        tmp_path / "nope", now_ts=NOW, done_ttl_s=DAY,
        orphan_ttl_s=7 * DAY, artifact_ttl_s=HOUR,
    )
    assert summary["scanned"] == 0
    assert summary["removed_tasks"] == []


# ---------------------------------------------------------------------------
# CLI + catalog wiring
# ---------------------------------------------------------------------------

def test_fleet_is_a_real_parser_command() -> None:
    parser = _build_parser()
    choices: dict = {}
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)
    assert "fleet" in choices


def test_fleet_detect_command_writes_evidence(tmp_path: Path) -> None:
    rc = fleet_cmd.main(["detect", "--repo-root", str(tmp_path), "--json"])
    assert rc == 0
    evidence = tmp_path / ".signalos" / "evidence" / "fleet" / "runtimes.json"
    assert evidence.is_file()
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["total_count"] >= 6


def test_fleet_gc_command_runs_and_audits(tmp_path: Path) -> None:
    root = tmp_path
    tasks_root = root / ".signalos" / "fleet" / "tasks"
    done = tasks_root / "old"
    _write_meta(done, "done", last_active_ts=NOW - 10 * DAY)
    (done / "src").mkdir(parents=True, exist_ok=True)

    rc = fleet_cmd.main([
        "gc",
        "--repo-root", str(root),
        "--now-ts", str(NOW),
        "--done-ttl", str(DAY),
        "--json",
    ])
    assert rc == 0
    assert not done.exists()

    gc_evidence = root / ".signalos" / "evidence" / "fleet" / "gc.json"
    assert gc_evidence.is_file()

    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit_rows = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines()]
    assert any(r["action"] == fleet_cmd.AUDIT_GC for r in audit_rows)


def test_command_catalog_contract_holds_for_fleet() -> None:
    shared = BUNDLE_ROOT / "core" / "tool-adapters" / "_shared"
    commands = json.loads((shared / "commands.json").read_text(encoding="utf-8"))
    entry = next((c for c in commands if c["name"] == "fleet"), None)
    assert entry is not None, "fleet missing from commands.json"

    source = entry["source"]
    assert (BUNDLE_ROOT / source).is_file(), source
    assert (BUNDLE_ROOT / "core" / "execution" / "commands" / "fleet.md").is_file()
    assert (BUNDLE_ROOT / "integrations" / "rules" / "fleet.mdc").is_file()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
