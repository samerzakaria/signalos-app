"""Tests for signalos_lib.product.closeout (Phase P12).

Covers build_closeout, write_closeout, generate_closeout_markdown,
write_handoff_files, check_closeout_honesty, and load_closeout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the repo python dir is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.closeout import (
    build_closeout,
    check_closeout_honesty,
    generate_closeout_markdown,
    load_closeout,
    write_closeout,
    write_handoff_files,
)


# ------------------------------------------------------------------
# Fixtures — minimal evidence files
# ------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _make_delivery_state(repo_root: Path) -> None:
    _write_json(
        repo_root / ".signalos" / "product" / "DELIVERY_STATE.json",
        {
            "schema_version": "signalos.delivery_state.v1",
            "phase": "closeout",
            "mode": "greenfield",
            "repo_root": str(repo_root),
            "product_name": "TestProduct",
            "prompt_sha256": "abc123",
            "profile": "react-vite",
            "blueprint": "task-management",
            "wave": "1",
            "status": "running",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "checkpoints": [],
        },
    )


def _make_generation_manifest(repo_root: Path) -> None:
    _write_json(
        repo_root / ".signalos" / "product" / "GENERATION_MANIFEST.json",
        {
            "schema_version": "signalos.generation_manifest.v1",
            "product": "TestProduct",
            "blueprint": "task-management",
            "profile": "react-vite",
            "wave": "1",
            "task_ids": [],
            "files": [
                {"path": "src/components/TaskList.tsx", "kind": "source"},
                {"path": "src/components/TaskList.test.tsx", "kind": "test"},
            ],
            "validation_commands": ["npm run build"],
        },
    )


def _make_validation_result(
    repo_root: Path,
    *,
    build_status: str = "passed",
    test_status: str = "passed",
    security_status: str = "skipped",
    all_skipped: bool = False,
) -> None:
    def _not_applicable(category: str, owner: str) -> dict:
        return {
            "status": "skipped",
            "output": "",
            "duration_s": 0.0,
            "skip_reason": f"{category} is covered by {owner} in this fixture",
            "skip_owner": owner,
            "release_disposition": "not_applicable",
            "category": category,
        }

    if all_skipped:
        results = {
            cat: {"status": "skipped", "output": "", "duration_s": 0.0}
            for cat in (
                "install", "build", "test", "lint", "qa",
                "e2e", "runtime_smoke", "ux_smoke", "security",
            )
        }
        blockers = ["All checks were skipped; at least one must pass"]
    else:
        results = {
            "install": {"status": "passed", "output": "", "duration_s": 0.5},
            "build": {"status": build_status, "output": "", "duration_s": 1.2},
            "test": {"status": test_status, "output": "", "duration_s": 2.0},
            "lint": _not_applicable("lint", "stack-adapter"),
            "qa": _not_applicable("qa", "acceptance-proof"),
            "e2e": _not_applicable("e2e", "proof-phase"),
            "runtime_smoke": _not_applicable("runtime_smoke", "proof-phase"),
            "ux_smoke": _not_applicable("ux_smoke", "proof-phase"),
            "security": {"status": security_status, "output": "", "duration_s": 0.0},
        }
        if security_status == "skipped":
            results["security"] = _not_applicable("security", "security-gate")
        blockers = []
        if build_status == "failed":
            blockers.append("build check failed")
        if test_status == "failed":
            blockers.append("test check failed")

    can_close = (
        build_status == "passed"
        and test_status == "passed"
        and not all_skipped
    )
    _write_json(
        repo_root / ".signalos" / "product" / "VALIDATION_RESULT.json",
        {
            "schema_version": "signalos.validation_result.v1",
            "profile": "react-vite",
            "dry_run": False,
            "results": results,
            "summary": {
                "total_checks": 9,
                "passed": sum(1 for r in results.values() if r["status"] == "passed"),
                "failed": sum(1 for r in results.values() if r["status"] == "failed"),
                "skipped": sum(1 for r in results.values() if r["status"] == "skipped"),
                "blocked": 0,
            },
            "can_close_delivery": can_close,
            "blockers": blockers,
        },
    )


def _make_proof_artifacts(
    repo_root: Path,
    *,
    runtime_status: str = "passed",
    ux_status: str = "passed",
) -> None:
    proof_dir = repo_root / ".signalos" / "product" / "proof" / "runtime"
    _write_json(
        proof_dir / "smoke.json",
        {"status": runtime_status, "profile": "react-vite"},
    )
    _write_json(
        proof_dir / "ux-smoke.json",
        {"status": ux_status, "checks": []},
    )


def _make_deploy_decision(
    repo_root: Path,
    *,
    mode: str = "none",
    deploy_allowed: bool = False,
) -> None:
    blockers = [] if deploy_allowed else ["No deployment requested"]
    _write_json(
        repo_root / ".signalos" / "product" / "DEPLOY_DECISION.json",
        {
            "schema_version": "signalos.deploy_decision.v1",
            "mode": mode,
            "decided_at": "2026-01-01T00:00:00Z",
            "validation_status": "ready",
            "deploy_allowed": deploy_allowed,
            "reason": "test",
            "blockers": blockers,
            "evidence": {
                "validation_closeable": True,
                "validation_level": "ready",
            },
        },
    )


def _make_acceptance_matrix(
    repo_root: Path,
    *,
    passed: int = 2,
    failed: int = 0,
    pending: int = 0,
) -> None:
    criteria = []
    for i in range(passed):
        criteria.append({
            "id": f"AC-{i + 1:03d}",
            "source": "intent",
            "description": f"Criterion {i + 1}",
            "entity": None,
            "workflow": None,
            "test_ids": [],
            "status": "passed",
            "evidence": "test passed",
        })
    for i in range(failed):
        criteria.append({
            "id": f"AC-{passed + i + 1:03d}",
            "source": "intent",
            "description": f"Failed criterion {i + 1}",
            "entity": None,
            "workflow": None,
            "test_ids": [],
            "status": "failed",
            "evidence": None,
        })
    for i in range(pending):
        criteria.append({
            "id": f"AC-{passed + failed + i + 1:03d}",
            "source": "intent",
            "description": f"Pending criterion {i + 1}",
            "entity": None,
            "workflow": None,
            "test_ids": [],
            "status": "pending",
            "evidence": None,
        })
    _write_json(
        repo_root / ".signalos" / "product" / "ACCEPTANCE_MATRIX.json",
        {
            "schema_version": "signalos.acceptance_matrix.v1",
            "product_name": "TestProduct",
            "profile": "react-vite",
            "blueprint_id": "task-management",
            "criteria": criteria,
            "test_scenarios": [],
            "summary": {
                "total_criteria": len(criteria),
                "total_tests": 0,
                "from_intent": len(criteria),
                "from_blueprint": 0,
            },
        },
    )


def _populate_full_evidence(repo_root: Path) -> None:
    """Write all evidence files for a successful closeout."""
    _make_delivery_state(repo_root)
    _make_generation_manifest(repo_root)
    _make_validation_result(repo_root)
    _make_proof_artifacts(repo_root)
    _make_deploy_decision(repo_root)
    _make_acceptance_matrix(repo_root)


# ------------------------------------------------------------------
# build_closeout tests
# ------------------------------------------------------------------

class TestBuildCloseout:
    def test_full_evidence_returns_complete_closeout(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", "task-management")

        assert closeout["schema_version"] == "signalos.product_closeout.v1"
        assert closeout["product_name"] == "TestProduct"
        assert closeout["profile"] == "react-vite"
        assert closeout["blueprint"] == "task-management"
        assert closeout["closure_level"] == "ready"
        assert closeout["closed_at"]
        assert len(closeout["generated_files"]) == 2
        assert len(closeout["tests_executed"]) > 0
        assert closeout["build_status"] == "passed"
        assert closeout["how_to_run"]
        assert closeout["what_next"]

    def test_missing_evidence_returns_partial_no_crash(self, tmp_path: Path):
        # No evidence files at all
        closeout = build_closeout(tmp_path, "EmptyProduct", "generic", None)

        assert closeout["product_name"] == "EmptyProduct"
        assert closeout["closure_level"] == "not_started"
        assert closeout["generated_files"] == []
        assert closeout["build_status"] == "not_run"
        assert closeout["deploy_status"] == "not_run"

    def test_includes_repo_path_and_git_head(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)

        assert closeout["repo_path"] == str(tmp_path)
        # No .git dir in tmp_path, so head is None
        assert closeout["repo_git_head"] is None

    def test_collects_generated_files_from_manifest(self, tmp_path: Path):
        _make_generation_manifest(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)

        assert "src/components/TaskList.tsx" in closeout["generated_files"]
        assert "src/components/TaskList.test.tsx" in closeout["generated_files"]

    def test_collects_build_test_status_from_validation(self, tmp_path: Path):
        _make_validation_result(tmp_path, build_status="failed")
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)

        assert closeout["build_status"] == "failed"
        assert closeout["closure_level"] == "partial"

    def test_source_prompt_sha256_from_delivery_state(self, tmp_path: Path):
        _make_delivery_state(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)

        assert closeout["source_prompt_sha256"] == "abc123"

    def test_acceptance_summary_counts(self, tmp_path: Path):
        _make_acceptance_matrix(tmp_path, passed=3, failed=1, pending=2)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)

        acc = closeout["acceptance_summary"]
        assert acc["total"] == 6
        assert acc["passed"] == 3
        assert acc["failed"] == 1
        assert acc["pending"] == 2

    def test_pending_acceptance_degrades_ready_validation_to_partial(
        self, tmp_path: Path,
    ):
        _make_delivery_state(tmp_path)
        _make_generation_manifest(tmp_path)
        _make_validation_result(tmp_path)
        _make_proof_artifacts(tmp_path)
        _make_acceptance_matrix(tmp_path, passed=1, failed=0, pending=1)

        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)

        assert closeout["build_status"] == "passed"
        assert closeout["closure_level"] == "partial"
        assert closeout["acceptance_summary"]["pending"] == 1
        assert any("pending" in item for item in closeout["known_limitations"])


# ------------------------------------------------------------------
# write_closeout / load_closeout tests
# ------------------------------------------------------------------

class TestWriteLoadCloseout:
    def test_creates_json_and_md(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        signalos_dir = tmp_path / ".signalos"

        json_path, md_path = write_closeout(closeout, signalos_dir)

        assert json_path.is_file()
        assert md_path.is_file()
        assert json_path.name == "CLOSEOUT.json"
        assert md_path.name == "CLOSEOUT.md"

    def test_json_has_all_required_fields(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        signalos_dir = tmp_path / ".signalos"
        json_path, _ = write_closeout(closeout, signalos_dir)

        loaded = json.loads(json_path.read_text(encoding="utf-8"))
        required_fields = [
            "schema_version", "product_name", "repo_path",
            "repo_git_head", "source_prompt_sha256", "blueprint",
            "profile", "generated_files", "tests_executed",
            "build_status", "runtime_status", "ux_status",
            "security_status", "deploy_status", "acceptance_summary",
            "known_limitations", "how_to_run", "what_next",
            "closed_at", "closure_level",
        ]
        for field in required_fields:
            assert field in loaded, f"Missing field: {field}"

    def test_md_contains_product_name_repo_path_how_to_run(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        signalos_dir = tmp_path / ".signalos"
        _, md_path = write_closeout(closeout, signalos_dir)

        md_content = md_path.read_text(encoding="utf-8")
        assert "TestProduct" in md_content
        assert str(tmp_path) in md_content
        assert "npm install" in md_content

    def test_md_does_not_say_ready_when_partial(self, tmp_path: Path):
        _make_validation_result(tmp_path, build_status="failed")
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        assert closeout["closure_level"] == "partial"
        signalos_dir = tmp_path / ".signalos"
        _, md_path = write_closeout(closeout, signalos_dir)

        md_content = md_path.read_text(encoding="utf-8")
        # Should NOT say "ready for review"
        assert "ready for review" not in md_content.lower()
        # Should mention limitations
        assert "limitations" in md_content.lower()

    def test_roundtrip_write_load(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        signalos_dir = tmp_path / ".signalos"
        write_closeout(closeout, signalos_dir)

        loaded = load_closeout(signalos_dir)
        assert loaded is not None
        assert loaded["product_name"] == closeout["product_name"]
        assert loaded["closure_level"] == closeout["closure_level"]
        assert loaded["generated_files"] == closeout["generated_files"]

    def test_load_returns_none_when_absent(self, tmp_path: Path):
        assert load_closeout(tmp_path / ".signalos") is None


# ------------------------------------------------------------------
# generate_closeout_markdown tests
# ------------------------------------------------------------------

class TestGenerateCloseoutMarkdown:
    def test_includes_all_required_sections(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        md = generate_closeout_markdown(closeout)

        assert "# TestProduct" in md
        assert "## Repository" in md
        assert "## What Was Built" in md
        assert "## How to Run" in md
        assert "## Checks and Tests" in md
        assert "## Key Results" in md
        assert "## Known Limitations" in md
        assert "## Next Actions" in md

    def test_does_not_claim_ready_when_partial(self, tmp_path: Path):
        _make_validation_result(tmp_path, build_status="failed")
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        md = generate_closeout_markdown(closeout)

        assert "ready for review" not in md.lower()
        assert "did not pass" in md.lower() or "limitations" in md.lower()


# ------------------------------------------------------------------
# write_handoff_files tests
# ------------------------------------------------------------------

class TestWriteHandoffFiles:
    def test_creates_three_files(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        signalos_dir = tmp_path / ".signalos"

        paths = write_handoff_files(closeout, signalos_dir)

        assert len(paths) == 3
        assert all(p.is_file() for p in paths)
        names = {p.name for p in paths}
        assert "product-summary.md" in names
        assert "test-evidence.md" in names
        assert "operator-runbook.md" in names

    def test_handoff_files_contain_actual_evidence(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        signalos_dir = tmp_path / ".signalos"

        paths = write_handoff_files(closeout, signalos_dir)

        # Product summary should contain generated files
        summary = (signalos_dir / "handoffs" / "product-summary.md").read_text(
            encoding="utf-8"
        )
        assert "TaskList.tsx" in summary
        assert "TestProduct" in summary

        # Test evidence should contain build result
        evidence = (signalos_dir / "handoffs" / "test-evidence.md").read_text(
            encoding="utf-8"
        )
        assert "passed" in evidence

        # Operator runbook should contain how-to-run
        runbook = (signalos_dir / "handoffs" / "operator-runbook.md").read_text(
            encoding="utf-8"
        )
        assert "npm install" in runbook


# ------------------------------------------------------------------
# check_closeout_honesty tests
# ------------------------------------------------------------------

class TestCheckCloseoutHonesty:
    def test_honest_true_for_consistent_closeout(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)

        result = check_closeout_honesty(closeout)
        assert result["honest"] is True
        assert result["issues"] == []

    def test_catches_ready_with_failed_build(self, tmp_path: Path):
        _populate_full_evidence(tmp_path)
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        # Force dishonest state
        closeout["closure_level"] = "ready"
        closeout["build_status"] = "failed"

        result = check_closeout_honesty(closeout)
        assert result["honest"] is False
        assert any("build_status is failed" in i for i in result["issues"])

    def test_catches_ready_with_all_tests_skipped(self, tmp_path: Path):
        closeout = {
            "closure_level": "ready",
            "build_status": "passed",
            "acceptance_summary": {"total": 2, "passed": 2, "failed": 0, "pending": 0, "skipped": 0},
            "tests_executed": [
                {"category": "build", "status": "skipped", "duration_s": 0.0},
                {"category": "test", "status": "skipped", "duration_s": 0.0},
            ],
            "known_limitations": [],
            "deploy_status": "none",
        }

        result = check_closeout_honesty(closeout)
        assert result["honest"] is False
        assert any("all tests were skipped" in i for i in result["issues"])

    def test_catches_ready_with_failed_acceptance(self, tmp_path: Path):
        closeout = {
            "closure_level": "ready",
            "build_status": "passed",
            "acceptance_summary": {"total": 3, "passed": 2, "failed": 1, "pending": 0, "skipped": 0},
            "tests_executed": [
                {"category": "build", "status": "passed", "duration_s": 1.0},
            ],
            "known_limitations": [],
            "deploy_status": "none",
        }

        result = check_closeout_honesty(closeout)
        assert result["honest"] is False
        assert any("failed acceptance criteria" in i for i in result["issues"])

    def test_catches_ready_with_pending_acceptance(self, tmp_path: Path):
        closeout = {
            "closure_level": "ready",
            "build_status": "passed",
            "acceptance_summary": {"total": 3, "passed": 2, "failed": 0, "pending": 1, "skipped": 0},
            "tests_executed": [
                {"category": "build", "status": "passed", "duration_s": 1.0},
            ],
            "known_limitations": [],
            "deploy_status": "none",
        }

        result = check_closeout_honesty(closeout)
        assert result["honest"] is False
        assert any("pending acceptance criteria" in i for i in result["issues"])

    def test_catches_empty_limitations_on_partial(self, tmp_path: Path):
        closeout = {
            "closure_level": "partial",
            "build_status": "failed",
            "acceptance_summary": {"total": 0, "passed": 0, "failed": 0, "pending": 0, "skipped": 0},
            "tests_executed": [],
            "known_limitations": [],
            "deploy_status": "not_run",
        }

        result = check_closeout_honesty(closeout)
        assert result["honest"] is False
        assert any("known_limitations is empty" in i for i in result["issues"])


# ------------------------------------------------------------------
# how_to_run / what_next tests
# ------------------------------------------------------------------

class TestHowToRunAndWhatNext:
    def test_how_to_run_includes_repo_path(self, tmp_path: Path):
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        assert any(str(tmp_path) in step for step in closeout["how_to_run"])

    def test_react_vite_how_to_run_includes_npm(self, tmp_path: Path):
        closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        joined = " ".join(closeout["how_to_run"])
        assert "npm install" in joined
        assert "npm run dev" in joined

    def test_generic_how_to_run_is_simpler(self, tmp_path: Path):
        closeout = build_closeout(tmp_path, "TestProduct", "generic", None)
        joined = " ".join(closeout["how_to_run"])
        assert "npm" not in joined
        assert "Review generated files" in joined

    def test_what_next_differs_by_closure_level(self, tmp_path: Path):
        # ready
        _populate_full_evidence(tmp_path)
        ready_closeout = build_closeout(tmp_path, "TestProduct", "react-vite", None)
        assert ready_closeout["closure_level"] == "ready"
        assert "Review the product" in ready_closeout["what_next"]

        # partial (new tmp_path)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            partial_root = Path(td)
            _make_validation_result(partial_root, build_status="failed")
            partial_closeout = build_closeout(
                partial_root, "TestProduct", "react-vite", None,
            )
            assert partial_closeout["closure_level"] == "partial"
            assert "Address known limitations" in partial_closeout["what_next"]

        # blocked
        with tempfile.TemporaryDirectory() as td:
            blocked_root = Path(td)
            # Write a validation result with blocked status
            results = {
                cat: {"status": "skipped", "output": "", "duration_s": 0.0}
                for cat in (
                    "install", "test", "lint", "qa",
                    "e2e", "runtime_smoke", "ux_smoke", "security",
                )
            }
            results["build"] = {
                "status": "blocked",
                "output": "command not found: npm",
                "duration_s": 0.0,
            }
            _write_json(
                Path(td) / ".signalos" / "product" / "VALIDATION_RESULT.json",
                {
                    "schema_version": "signalos.validation_result.v1",
                    "profile": "react-vite",
                    "dry_run": False,
                    "results": results,
                    "summary": {"total_checks": 9, "passed": 0, "failed": 0, "skipped": 8, "blocked": 1},
                    "can_close_delivery": False,
                    "blockers": ["build check blocked: command not found: npm"],
                },
            )
            blocked_closeout = build_closeout(
                blocked_root, "TestProduct", "react-vite", None,
            )
            assert blocked_closeout["closure_level"] == "blocked"
            assert "Install required toolchain" in blocked_closeout["what_next"]
