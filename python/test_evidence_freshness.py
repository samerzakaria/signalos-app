"""Tests for mechanical-verification Layer 2: evidence freshness binding.

- snapshot/verify happy path;
- drift detection (changed + added + removed);
- .signalos/** exclusion (evidence never invalidates itself);
- repair-loop ordering semantics: a snapshot captured AFTER the repair
  rewrite is fresh at closeout (no false positive), one captured before
  is not;
- _apply_evidence_freshness: strict blocks (closure_level downgraded),
  warn records only.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.evidence_freshness import (
    snapshot_workspace,
    verify_workspace_snapshot,
    workspace_snapshot_files,
)
from signalos_lib.product.delivery import _apply_evidence_freshness


def _seed(tmp_path: Path) -> dict:
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "App.tsx").write_text("export const App = 1;", encoding="utf-8")
    (tmp_path / "src" / "App.test.tsx").write_text(
        "it('x', () => expect(1).toBe(1));", encoding="utf-8",
    )
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    return {
        "files": [
            {"path": "src/App.tsx", "kind": "source"},
            {"path": "src/App.test.tsx", "kind": "test"},
            {"path": "src/Ghost.tsx", "kind": "source"},  # never written
        ],
    }


# ---------------------------------------------------------------------------
# workspace_snapshot_files
# ---------------------------------------------------------------------------

class TestWorkspaceSnapshotFiles:
    def test_manifest_on_disk_plus_config_minus_ghosts(self, tmp_path: Path) -> None:
        manifest = _seed(tmp_path)
        files = workspace_snapshot_files(tmp_path, manifest)
        assert "src/App.tsx" in files
        assert "src/App.test.tsx" in files
        assert "package.json" in files
        assert "src/Ghost.tsx" not in files  # not on disk

    def test_signalos_is_never_a_candidate(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        evidence = tmp_path / ".signalos" / "product"
        evidence.mkdir(parents=True)
        (evidence / "X.json").write_text("{}", encoding="utf-8")
        manifest = {"files": [{"path": ".signalos/product/X.json"}]}
        assert workspace_snapshot_files(tmp_path, manifest) == ["package.json"]

    def test_none_manifest_yields_config_files_only(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        assert workspace_snapshot_files(tmp_path, None) == ["package.json"]


# ---------------------------------------------------------------------------
# snapshot + verify
# ---------------------------------------------------------------------------

class TestSnapshotAndVerify:
    def test_happy_path_is_fresh(self, tmp_path: Path) -> None:
        manifest = _seed(tmp_path)
        files = workspace_snapshot_files(tmp_path, manifest)
        snapshot = snapshot_workspace(tmp_path, files)
        assert snapshot["algo"] == "sha256"
        assert set(snapshot["files"]) == set(files)
        assert all(len(h) == 64 for h in snapshot["files"].values())

        report = verify_workspace_snapshot(
            tmp_path, snapshot, workspace_snapshot_files(tmp_path, manifest),
        )
        assert report["fresh"] is True
        assert report["changed"] == []
        assert report["added"] == []
        assert report["removed"] == []
        assert report["files_verified"] == len(files)

    def test_changed_file_detected(self, tmp_path: Path) -> None:
        manifest = _seed(tmp_path)
        snapshot = snapshot_workspace(
            tmp_path, workspace_snapshot_files(tmp_path, manifest),
        )
        (tmp_path / "src" / "App.tsx").write_text(
            "export const App = 2; // tampered after proof", encoding="utf-8",
        )
        report = verify_workspace_snapshot(
            tmp_path, snapshot, workspace_snapshot_files(tmp_path, manifest),
        )
        assert report["fresh"] is False
        assert report["changed"] == ["src/App.tsx"]
        assert report["added"] == []
        assert report["removed"] == []

    def test_added_file_detected(self, tmp_path: Path) -> None:
        manifest = _seed(tmp_path)
        snapshot = snapshot_workspace(
            tmp_path, workspace_snapshot_files(tmp_path, manifest),
        )
        # A generated file lands AFTER the snapshot (post-proof) and shows up
        # in the manifest-derived candidate list at verify time.
        (tmp_path / "src" / "Late.tsx").write_text("late", encoding="utf-8")
        manifest["files"].append({"path": "src/Late.tsx", "kind": "source"})
        report = verify_workspace_snapshot(
            tmp_path, snapshot, workspace_snapshot_files(tmp_path, manifest),
        )
        assert report["fresh"] is False
        assert report["added"] == ["src/Late.tsx"]
        assert report["changed"] == []

    def test_removed_file_detected(self, tmp_path: Path) -> None:
        manifest = _seed(tmp_path)
        snapshot = snapshot_workspace(
            tmp_path, workspace_snapshot_files(tmp_path, manifest),
        )
        (tmp_path / "src" / "App.test.tsx").unlink()
        report = verify_workspace_snapshot(
            tmp_path, snapshot, workspace_snapshot_files(tmp_path, manifest),
        )
        assert report["fresh"] is False
        assert report["removed"] == ["src/App.test.tsx"]

    def test_all_three_drift_kinds_together(self, tmp_path: Path) -> None:
        manifest = _seed(tmp_path)
        snapshot = snapshot_workspace(
            tmp_path, workspace_snapshot_files(tmp_path, manifest),
        )
        (tmp_path / "src" / "App.tsx").write_text("changed", encoding="utf-8")
        (tmp_path / "src" / "App.test.tsx").unlink()
        (tmp_path / "src" / "New.tsx").write_text("new", encoding="utf-8")
        manifest["files"].append({"path": "src/New.tsx", "kind": "source"})
        report = verify_workspace_snapshot(
            tmp_path, snapshot, workspace_snapshot_files(tmp_path, manifest),
        )
        assert report["fresh"] is False
        assert report["changed"] == ["src/App.tsx"]
        assert report["added"] == ["src/New.tsx"]
        assert report["removed"] == ["src/App.test.tsx"]

    def test_signalos_writes_never_drift(self, tmp_path: Path) -> None:
        # Later evidence writes into .signalos/** must not invalidate the
        # snapshot (the pipeline keeps writing evidence after proof).
        manifest = _seed(tmp_path)
        snapshot = snapshot_workspace(
            tmp_path, workspace_snapshot_files(tmp_path, manifest),
        )
        evidence = tmp_path / ".signalos" / "product"
        evidence.mkdir(parents=True)
        (evidence / "CLOSEOUT.json").write_text("{}", encoding="utf-8")
        report = verify_workspace_snapshot(
            tmp_path, snapshot, workspace_snapshot_files(tmp_path, manifest),
        )
        assert report["fresh"] is True

    def test_repair_loop_ordering_no_false_positive(self, tmp_path: Path) -> None:
        """The pipeline captures the snapshot AFTER the repair loop finishes;
        this test pins the semantics that ordering relies on: a repair
        rewrite BEFORE the snapshot is invisible (fresh), the same rewrite
        AFTER an (incorrectly early) snapshot would be drift."""
        manifest = _seed(tmp_path)
        early = snapshot_workspace(
            tmp_path, workspace_snapshot_files(tmp_path, manifest),
        )
        # repair cycle legitimately rewrites a generated file
        (tmp_path / "src" / "App.tsx").write_text(
            "export const App = 'repaired';", encoding="utf-8",
        )
        # snapshot at the pipeline's actual capture point (post-repair)
        late = snapshot_workspace(
            tmp_path, workspace_snapshot_files(tmp_path, manifest),
        )
        current = workspace_snapshot_files(tmp_path, manifest)
        assert verify_workspace_snapshot(tmp_path, late, current)["fresh"] is True
        assert verify_workspace_snapshot(tmp_path, early, current)["fresh"] is False


# ---------------------------------------------------------------------------
# _apply_evidence_freshness (closeout folding: strict blocks, warn records)
# ---------------------------------------------------------------------------

def _stale_report(mode: str) -> dict:
    return {
        "schema_version": "signalos.evidence_freshness.v1",
        "fresh": False,
        "mode": mode,
        "changed": ["src/App.tsx"],
        "added": ["src/New.tsx"],
        "removed": [],
    }


class TestApplyEvidenceFreshness:
    def test_strict_stale_downgrades_and_lists_drift(self) -> None:
        closeout = {"closure_level": "ready", "known_limitations": []}
        result = _apply_evidence_freshness(closeout, _stale_report("strict"))
        assert result["closure_level"] == "partial"
        stale = [l for l in result["known_limitations"] if "evidence is stale" in l]
        assert len(stale) == 1
        assert "changed: src/App.tsx" in stale[0]
        assert "added: src/New.tsx" in stale[0]
        assert result["evidence_freshness"]["fresh"] is False

    def test_warn_stale_records_without_downgrade(self) -> None:
        closeout = {"closure_level": "ready", "known_limitations": []}
        result = _apply_evidence_freshness(closeout, _stale_report("warn"))
        assert result["closure_level"] == "ready"
        assert any(
            "evidence is stale" in l for l in result["known_limitations"]
        )

    def test_fresh_report_only_attaches_evidence(self) -> None:
        closeout = {"closure_level": "ready", "known_limitations": []}
        report = {"fresh": True, "mode": "strict", "changed": [], "added": [], "removed": []}
        result = _apply_evidence_freshness(closeout, report)
        assert result["closure_level"] == "ready"
        assert result["known_limitations"] == []
        assert result["evidence_freshness"] is report

    def test_no_snapshot_attaches_none(self) -> None:
        closeout = {"closure_level": "partial", "known_limitations": ["x"]}
        result = _apply_evidence_freshness(closeout, None)
        assert result["evidence_freshness"] is None
        assert result["known_limitations"] == ["x"]

    def test_strict_stale_does_not_upgrade_already_partial(self) -> None:
        closeout = {"closure_level": "blocked", "known_limitations": []}
        result = _apply_evidence_freshness(closeout, _stale_report("strict"))
        assert result["closure_level"] == "blocked"
        assert any(
            "evidence is stale" in l for l in result["known_limitations"]
        )
