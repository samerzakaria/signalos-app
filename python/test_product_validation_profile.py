"""Tests for profile-aware product validation (Phase P9).

Covers plan construction, execution, persistence round-trips,
and closure assessment logic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the local package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.validation import (
    build_validation_plan,
    check_product_closure,
    load_validation_result,
    run_validation,
    write_validation_plan,
    write_validation_result,
)


# ------------------------------------------------------------------
# build_validation_plan
# ------------------------------------------------------------------


class TestBuildValidationPlan:
    def test_react_vite_has_install_build_test(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "react-vite")
        assert plan["install"], "react-vite should declare install commands"
        assert plan["build"], "react-vite should declare build commands"
        assert plan["test"], "react-vite should declare test commands"

    def test_generic_returns_python_build_and_test_commands(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "generic")
        assert plan["build"], "generic should validate Python source"
        assert plan["test"], "generic should run generated tests"
        for cat in ("install", "lint", "qa", "e2e",
                    "runtime_smoke", "ux_smoke", "security"):
            assert plan[cat] == [], f"generic should have empty {cat}"

    def test_react_vite_can_validate_build(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "react-vite")
        assert plan["can_validate_build"] is True

    def test_generic_can_validate_build(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "generic")
        assert plan["can_validate_build"] is True

    def test_react_vite_can_validate_tests(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "react-vite")
        assert plan["can_validate_tests"] is True

    def test_generic_can_validate_tests(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "generic")
        assert plan["can_validate_tests"] is True

    def test_schema_version_present(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "react-vite")
        assert plan["schema_version"] == "signalos.validation_plan.v1"

    def test_preview_block_present(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "react-vite")
        assert "command" in plan["preview"]
        assert "port" in plan["preview"]

    def test_can_deliver_ui_flag(self, tmp_path: Path):
        plan_rv = build_validation_plan(tmp_path, "react-vite")
        plan_gen = build_validation_plan(tmp_path, "generic")
        assert plan_rv["can_deliver_ui"] is True
        assert plan_gen["can_deliver_ui"] is False


# ------------------------------------------------------------------
# run_validation — dry-run
# ------------------------------------------------------------------


class TestRunValidationDryRun:
    def test_dry_run_all_skipped(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "react-vite")
        result = run_validation(tmp_path, plan, dry_run=True)
        for cat_result in result["results"].values():
            assert cat_result["status"] == "skipped"

    def test_dry_run_cannot_close_delivery(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "react-vite")
        result = run_validation(tmp_path, plan, dry_run=True)
        assert result["can_close_delivery"] is False

    def test_dry_run_has_blocker(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "react-vite")
        result = run_validation(tmp_path, plan, dry_run=True)
        assert any("dry-run" in b.lower() or "Dry-run" in b for b in result["blockers"])


# ------------------------------------------------------------------
# run_validation — empty plan
# ------------------------------------------------------------------


class TestRunValidationGenericMissingScaffold:
    def test_missing_scaffold_fails_build_and_test(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "generic")
        result = run_validation(tmp_path, plan)
        assert result["results"]["build"]["status"] == "failed"
        assert result["results"]["test"]["status"] == "failed"

    def test_missing_scaffold_cannot_close(self, tmp_path: Path):
        plan = build_validation_plan(tmp_path, "generic")
        result = run_validation(tmp_path, plan)
        assert result["can_close_delivery"] is False


# ------------------------------------------------------------------
# run_validation — real commands
# ------------------------------------------------------------------


class TestRunValidationRealCommands:
    def test_passing_command(self, tmp_path: Path):
        plan = {
            "profile": "test",
            "install": [],
            "build": [],
            "test": [f"{sys.executable} -c \"print('ok')\""],
            "lint": [],
            "qa": [],
            "e2e": [],
            "runtime_smoke": [],
            "ux_smoke": [],
            "security": [],
            "can_validate_build": False,
            "can_validate_tests": True,
            "can_validate_runtime": False,
            "can_deliver_ui": False,
        }
        result = run_validation(tmp_path, plan)
        assert result["results"]["test"]["status"] == "passed"

    def test_failing_command(self, tmp_path: Path):
        plan = {
            "profile": "test",
            "install": [],
            "build": [f"{sys.executable} -c \"import sys; sys.exit(1)\""],
            "test": [],
            "lint": [],
            "qa": [],
            "e2e": [],
            "runtime_smoke": [],
            "ux_smoke": [],
            "security": [],
            "can_validate_build": True,
            "can_validate_tests": False,
            "can_validate_runtime": False,
            "can_deliver_ui": False,
        }
        result = run_validation(tmp_path, plan)
        assert result["results"]["build"]["status"] == "failed"

    def test_can_close_when_build_fails(self, tmp_path: Path):
        plan = {
            "profile": "test",
            "install": [],
            "build": [f"{sys.executable} -c \"import sys; sys.exit(1)\""],
            "test": [],
            "lint": [],
            "qa": [],
            "e2e": [],
            "runtime_smoke": [],
            "ux_smoke": [],
            "security": [],
            "can_validate_build": True,
            "can_validate_tests": False,
            "can_validate_runtime": False,
            "can_deliver_ui": False,
        }
        result = run_validation(tmp_path, plan)
        assert result["can_close_delivery"] is False

    def test_can_close_all_skipped_is_false(self, tmp_path: Path):
        plan = {
            "profile": "test",
            "install": [],
            "build": [],
            "test": [],
            "lint": [],
            "qa": [],
            "e2e": [],
            "runtime_smoke": [],
            "ux_smoke": [],
            "security": [],
            "can_validate_build": False,
            "can_validate_tests": False,
            "can_validate_runtime": False,
            "can_deliver_ui": False,
        }
        result = run_validation(tmp_path, plan)
        assert result["can_close_delivery"] is False

    def test_passing_command_can_close(self, tmp_path: Path):
        plan = {
            "profile": "test",
            "install": [],
            "build": [f"{sys.executable} -c \"print('built')\""],
            "test": [f"{sys.executable} -c \"print('tested')\""],
            "lint": [],
            "qa": [],
            "e2e": [],
            "runtime_smoke": [],
            "ux_smoke": [],
            "security": [],
            "can_validate_build": True,
            "can_validate_tests": True,
            "can_validate_runtime": False,
            "can_deliver_ui": False,
        }
        result = run_validation(tmp_path, plan)
        assert result["can_close_delivery"] is True

    def test_summary_counts(self, tmp_path: Path):
        plan = {
            "profile": "test",
            "install": [],
            "build": [f"{sys.executable} -c \"print('ok')\""],
            "test": [],
            "lint": [],
            "qa": [],
            "e2e": [],
            "runtime_smoke": [],
            "ux_smoke": [],
            "security": [],
            "can_validate_build": True,
            "can_validate_tests": False,
            "can_validate_runtime": False,
            "can_deliver_ui": False,
        }
        result = run_validation(tmp_path, plan)
        s = result["summary"]
        assert s["total_checks"] == 9
        assert s["passed"] == 1
        assert s["skipped"] == 8


# ------------------------------------------------------------------
# Persistence round-trip
# ------------------------------------------------------------------


class TestPersistence:
    def test_plan_round_trip(self, tmp_path: Path):
        signalos_dir = tmp_path / ".signalos"
        plan = build_validation_plan(tmp_path, "react-vite")
        path = write_validation_plan(plan, signalos_dir)
        assert path.is_file()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["profile"] == "react-vite"
        assert loaded["schema_version"] == plan["schema_version"]

    def test_result_round_trip(self, tmp_path: Path):
        signalos_dir = tmp_path / ".signalos"
        plan = build_validation_plan(tmp_path, "generic")
        result = run_validation(tmp_path, plan)
        path = write_validation_result(result, signalos_dir)
        assert path.is_file()
        loaded = load_validation_result(signalos_dir)
        assert loaded is not None
        assert loaded["profile"] == "generic"
        assert loaded["summary"] == result["summary"]

    def test_load_missing_returns_none(self, tmp_path: Path):
        signalos_dir = tmp_path / ".signalos"
        assert load_validation_result(signalos_dir) is None


# ------------------------------------------------------------------
# check_product_closure
# ------------------------------------------------------------------


class TestCheckProductClosure:
    def test_not_started_for_none(self):
        closure = check_product_closure(None)
        assert closure["level"] == "not_started"
        assert closure["closeable"] is False

    def test_partial_for_mixed_results(self):
        result = {
            "dry_run": False,
            "results": {
                "build": {"status": "passed"},
                "test": {"status": "failed"},
                "lint": {"status": "skipped"},
            },
            "blockers": ["test check failed"],
        }
        closure = check_product_closure(result)
        assert closure["level"] == "partial"
        assert closure["closeable"] is False

    def test_verified_for_all_passed_dry_run(self):
        result = {
            "dry_run": True,
            "results": {
                "build": {"status": "passed"},
                "test": {"status": "passed"},
            },
            "blockers": ["Dry-run mode: validation was not executed"],
        }
        closure = check_product_closure(result)
        assert closure["level"] == "verified"
        assert closure["closeable"] is False

    def test_ready_for_all_passed_non_dry_run(self):
        result = {
            "dry_run": False,
            "results": {
                "build": {"status": "passed"},
                "test": {"status": "passed"},
            },
            "blockers": [],
        }
        closure = check_product_closure(result)
        assert closure["level"] == "ready"
        assert closure["closeable"] is True

    def test_blocked_when_commands_not_found(self):
        result = {
            "dry_run": False,
            "results": {
                "build": {"status": "blocked", "output": "command not found: npm"},
                "test": {"status": "skipped"},
            },
            "blockers": ["build check blocked: command not found: npm"],
        }
        closure = check_product_closure(result)
        assert closure["level"] == "blocked"
        assert closure["closeable"] is False

    def test_generic_missing_scaffold_cannot_close(self, tmp_path: Path):
        """Generic profile cannot close without real source and tests."""
        plan = build_validation_plan(tmp_path, "generic")
        result = run_validation(tmp_path, plan)
        closure = check_product_closure(result)
        assert closure["closeable"] is False
        assert closure["level"] == "partial"
        assert any("build" in b.lower() for b in closure["blockers"])

    def test_blockers_are_human_readable(self):
        result = {
            "dry_run": False,
            "results": {
                "build": {"status": "failed"},
                "test": {"status": "blocked", "output": "command not found: npm"},
            },
            "blockers": [
                "build check failed",
                "test check blocked: command not found: npm",
            ],
        }
        closure = check_product_closure(result)
        for blocker in closure["blockers"]:
            assert isinstance(blocker, str)
            assert len(blocker) > 5, "Blocker should be human-readable, not a code"

    def test_all_skipped_is_partial(self):
        result = {
            "dry_run": False,
            "results": {
                "build": {"status": "skipped"},
                "test": {"status": "skipped"},
            },
            "blockers": ["All checks were skipped; at least one must pass"],
        }
        closure = check_product_closure(result)
        assert closure["level"] == "partial"
        assert closure["closeable"] is False
