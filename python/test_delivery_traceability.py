"""Tests for #6: per-file acceptance traceability enforced in the delivery
pipeline.

Semantics under test (see delivery._apply_traceability_review):
- criteria -> file coverage is STRICT: an uncovered acceptance criterion is a
  blocking review finding under strict gate-compliance, recorded under warn.
- file -> criteria linkage is ADVISORY ONLY: helper files that trace to no
  criterion are recorded but NEVER block (the AgentLoop owns implementation
  shape; helpers serving a traced component are legitimate).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.delivery import (
    _apply_traceability_review,
    _link_acceptance_traces,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _acceptance_matrix() -> dict:
    return {
        "schema_version": "signalos.acceptance_matrix.v1",
        "criteria": [
            {
                "id": "AC-001",
                "source": "intent",
                "description": "CRUD operations for Task",
                "entity": "Task",
                "workflow": None,
                "test_ids": [],
                "status": "pending",
                "evidence": None,
            },
            {
                "id": "AC-002",
                "source": "intent",
                "description": "Workflow: track expenses",
                "entity": None,
                "workflow": "track expenses",
                "test_ids": [],
                "status": "pending",
                "evidence": None,
            },
        ],
        "test_scenarios": [],
    }


def _review_result(mode: str = "strict", status: str = "pass") -> dict:
    return {
        "schema_version": "signalos.review_gate.v1",
        "status": status,
        "mode": mode,
        "blocking": False,
        "components_reviewed": [],
        "checks": {
            "spec_coverage": True,
            "test_evidence": True,
            "build_correctness": True,
        },
        "findings": [],
    }


# ---------------------------------------------------------------------------
# _link_acceptance_traces
# ---------------------------------------------------------------------------

class TestLinkAcceptanceTraces:
    def test_links_on_disk_manifest_files_and_agent_extras(
        self, tmp_path: Path,
    ) -> None:
        # On-disk manifest file + a manifest ghost (never written) + an
        # agent-written helper not in the manifest.
        (tmp_path / "src" / "components").mkdir(parents=True)
        (tmp_path / "src" / "hooks").mkdir(parents=True)
        (tmp_path / "src" / "components" / "Task.tsx").write_text(
            "export const Task = () => null;", encoding="utf-8",
        )
        (tmp_path / "src" / "hooks" / "useHelper.ts").write_text(
            "export const useHelper = () => 1;", encoding="utf-8",
        )

        manifest = {
            "files": [
                {
                    "path": "src/components/Task.tsx",
                    "kind": "source",
                    "acceptance_id": None,
                },
                {
                    "path": "src/components/Ghost.tsx",
                    "kind": "source",
                    "acceptance_id": None,
                },
            ],
        }
        acceptance = _acceptance_matrix()
        agent_result = {
            "files_written": [
                "src/components/Task.tsx",
                "src/hooks/useHelper.ts",
            ],
        }

        trace = _link_acceptance_traces(
            tmp_path, manifest, acceptance, agent_result,
        )

        assert trace is not None
        paths = [f["path"] for f in trace["files"]]
        # Ghost.tsx never landed on disk -- excluded from the honest trace view
        assert "src/components/Ghost.tsx" not in paths
        assert "src/components/Task.tsx" in paths
        assert "src/hooks/useHelper.ts" in paths

        by_path = {f["path"]: f for f in trace["files"]}
        # Entity match: Task.tsx traces to AC-001
        assert by_path["src/components/Task.tsx"]["acceptance_id"] == "AC-001"
        # Helper file has no criterion -- stays unlinked (advisory territory)
        assert by_path["src/hooks/useHelper.ts"]["acceptance_id"] is None
        assert by_path["src/hooks/useHelper.ts"]["kind"] == "source"

        # The linkage persists onto the real manifest record (written back to
        # GENERATION_MANIFEST.json by the pipeline)
        assert manifest["files"][0]["acceptance_id"] == "AC-001"

    def test_returns_none_without_acceptance_matrix(
        self, tmp_path: Path,
    ) -> None:
        manifest = {"files": []}
        assert _link_acceptance_traces(tmp_path, manifest, None, {}) is None
        assert _link_acceptance_traces(tmp_path, None, _acceptance_matrix(), {}) is None

    def test_agent_extras_exclude_non_product_files(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / ".signalos").mkdir(parents=True)
        (tmp_path / ".signalos" / "junk.json").write_text("{}", encoding="utf-8")
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")

        trace = _link_acceptance_traces(
            tmp_path,
            {"files": []},
            _acceptance_matrix(),
            {"files_written": [".signalos/junk.json", "package.json"]},
        )
        assert trace is not None
        assert trace["files"] == []


# ---------------------------------------------------------------------------
# _apply_traceability_review
# ---------------------------------------------------------------------------

class TestApplyTraceabilityReview:
    def test_uncovered_criteria_block_in_strict_mode(self) -> None:
        review = _review_result(mode="strict", status="pass")
        trace = {
            "complete": False,
            "linked_files": 1,
            "unlinked_files": 0,
            "unlinked_paths": [],
            "covered_criteria": ["AC-001"],
            "uncovered_criteria": ["AC-002"],
        }

        result = _apply_traceability_review(review, trace)

        assert result["status"] == "blocked"
        assert result["blocking"] is True
        assert result["checks"]["acceptance_traceability"] is False
        assert any(
            "AC-002" in f and "traceability" in f for f in result["findings"]
        )

    def test_uncovered_criteria_recorded_not_blocking_in_warn_mode(self) -> None:
        review = _review_result(mode="warn", status="pass")
        trace = {
            "complete": False,
            "linked_files": 0,
            "unlinked_files": 0,
            "unlinked_paths": [],
            "covered_criteria": [],
            "uncovered_criteria": ["AC-001", "AC-002"],
        }

        result = _apply_traceability_review(review, trace)

        assert result["status"] == "warn"
        assert not result["blocking"]
        assert result["checks"]["acceptance_traceability"] is False
        assert sum("traceability" in f for f in result["findings"]) == 2

    def test_unlinked_helper_files_are_advisory_never_blocking(self) -> None:
        """The false-positive guard: helper files with no criterion must not
        block, even in strict mode -- they are advisory findings only."""
        review = _review_result(mode="strict", status="pass")
        trace = {
            "complete": False,
            "linked_files": 2,
            "unlinked_files": 2,
            "unlinked_paths": ["src/hooks/useHelper.ts", "src/lib/format.ts"],
            "covered_criteria": ["AC-001", "AC-002"],
            "uncovered_criteria": [],
        }

        result = _apply_traceability_review(review, trace)

        assert result["status"] == "pass"
        assert result["blocking"] is False
        assert result["checks"]["acceptance_traceability"] is True
        advisories = [f for f in result["findings"] if "advisory" in f]
        assert len(advisories) == 2
        assert any("useHelper" in f for f in advisories)

    def test_no_trace_report_leaves_review_untouched(self) -> None:
        review = _review_result()
        before = json.dumps(review, sort_keys=True)
        result = _apply_traceability_review(review, None)
        assert json.dumps(result, sort_keys=True) == before

    def test_blocked_review_stays_blocked_in_warn_mode(self) -> None:
        """Warn-mode traceability never upgrades but must not downgrade an
        already-warn verdict either."""
        review = _review_result(mode="warn", status="warn")
        trace = {
            "complete": False,
            "linked_files": 0,
            "unlinked_files": 0,
            "unlinked_paths": [],
            "covered_criteria": [],
            "uncovered_criteria": ["AC-001"],
        }
        result = _apply_traceability_review(review, trace)
        assert result["status"] == "warn"
        assert not result["blocking"]


# ---------------------------------------------------------------------------
# Pipeline integration: the check actually runs inside run_delivery
# ---------------------------------------------------------------------------

class TestTraceabilityPipelineIntegration:
    def test_delivery_records_traceability_and_review_check(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from signalos_lib.product.delivery import run_delivery

        monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "trace-product"
            closeout = run_delivery(
                prompt="Build me a task management app with projects and tasks",
                name="trace-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )

            signalos = repo_root / ".signalos"

            # Traceability report persisted with the acceptance matrix
            matrix = json.loads(
                (signalos / "product" / "ACCEPTANCE_MATRIX.json").read_text(
                    encoding="utf-8",
                )
            )
            trace = matrix.get("traceability")
            assert trace is not None
            for key in (
                "complete",
                "linked_files",
                "unlinked_files",
                "unlinked_paths",
                "covered_criteria",
                "uncovered_criteria",
            ):
                assert key in trace

            # Review gate verdict carries the traceability check, and any
            # unlinked helper files surfaced only as advisory findings
            review = json.loads(
                (signalos / "product" / "REVIEW_RESULT.json").read_text(
                    encoding="utf-8",
                )
            )
            assert "acceptance_traceability" in review["checks"]
            if trace["uncovered_criteria"]:
                # strict default: uncovered criteria are blocking findings
                assert review["status"] in ("blocked", "warn")
            for path in trace["unlinked_paths"]:
                matching = [f for f in review["findings"] if path in f]
                assert matching, f"unlinked path {path} not recorded"
                assert all("advisory" in f for f in matching)

            # #11: without an LLM the GTM step was skipped and recorded
            # honestly in the persisted closeout
            persisted = json.loads(
                (signalos / "product" / "CLOSEOUT.json").read_text(
                    encoding="utf-8",
                )
            )
            assert persisted["gtm"]["status"] == "skipped"
            assert persisted["gtm"]["files"] == []
            assert any(
                "GTM assets were not generated" in lim
                for lim in persisted["known_limitations"]
            )
            assert closeout["gtm"]["status"] == "skipped"
