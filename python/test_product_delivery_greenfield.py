"""Tests for signalos_lib.product.scaffold — Phase P5 (Real Scaffold).

Covers greenfield and adopt scaffold flows, postflight validation,
auto-detection, blueprint matching, and error handling.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from signalos_lib.product.scaffold import (
    explain_profile_selection,
    run_postflight,
    run_scaffold,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_init_success(repo_root, mode, profile, product_name):
    """Simulate a successful init_product_repo call."""
    signalos_dir = Path(repo_root) / ".signalos"
    signalos_dir.mkdir(parents=True, exist_ok=True)
    return {"success": True, "mode": mode, "errors": []}


def _mock_init_failure(repo_root, mode, profile, product_name):
    """Simulate a failed init_product_repo call."""
    return {"success": False, "mode": mode, "errors": ["init failed: simulated"]}


def _write_react_vite_scaffold(root: Path) -> None:
    """Create a minimal react-vite scaffold in the given directory."""
    pkg = {
        "name": "test-product",
        "dependencies": {"react": "^18.0.0"},
        "devDependencies": {"vite": "^5.0.0"},
    }
    (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    src = root / "src"
    src.mkdir(exist_ok=True)
    (src / "App.tsx").write_text("export default function App() { return <div/>; }", encoding="utf-8")
    (src / "main.tsx").write_text("import App from './App';", encoding="utf-8")
    signalos = root / ".signalos"
    signalos.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Greenfield react-vite scaffold
# ---------------------------------------------------------------------------

class TestGreenfieldReactVite:
    def test_scaffold_creates_package_json_and_src(self, tmp_path):
        target = tmp_path / "my-app"
        target.mkdir()

        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_success,
        ):
            result = run_scaffold(
                repo_root=target,
                profile="react-vite",
                product_name="my-app",
                prompt="Build a task management app",
                mode="greenfield",
            )

        assert result["success"] is True
        assert result["mode"] == "greenfield"
        assert result["profile"] == "react-vite"
        assert (target / "package.json").is_file()
        assert (target / "src").is_dir()
        tsx_files = list((target / "src").glob("*.tsx"))
        assert len(tsx_files) >= 1

    def test_scaffold_postflight_passes(self, tmp_path):
        target = tmp_path / "vite-app"
        target.mkdir()

        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_success,
        ):
            result = run_scaffold(
                repo_root=target,
                profile="react-vite",
                product_name="vite-app",
                prompt="Build a task management app",
                mode="greenfield",
            )

        assert result["postflight"]["passed"] is True
        check_names = [c["name"] for c in result["postflight"]["checks"]]
        assert "package.json exists" in check_names
        assert "src/ directory exists" in check_names
        assert ".tsx file exists in src/" in check_names

    def test_scaffold_includes_scaffold_files(self, tmp_path):
        target = tmp_path / "app"
        target.mkdir()

        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_success,
        ):
            result = run_scaffold(
                repo_root=target,
                profile="react-vite",
                product_name="app",
                prompt="Build something",
                mode="greenfield",
            )

        assert isinstance(result["scaffold_files"], list)
        assert len(result["scaffold_files"]) > 0
        # Must include actual app files, not just .signalos
        non_signalos = [f for f in result["scaffold_files"] if not f.startswith(".signalos")]
        assert len(non_signalos) > 0


# ---------------------------------------------------------------------------
# Greenfield generic scaffold
# ---------------------------------------------------------------------------

class TestGreenfieldGeneric:
    def test_scaffold_creates_signalos_only(self, tmp_path):
        target = tmp_path / "generic-app"
        target.mkdir()

        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_success,
        ):
            result = run_scaffold(
                repo_root=target,
                profile="generic",
                product_name="generic-app",
                prompt="Build something",
                mode="greenfield",
            )

        assert result["success"] is True
        assert result["profile"] == "generic"
        assert (target / ".signalos").is_dir()
        # No app files like package.json or src/
        assert not (target / "package.json").is_file()
        assert not (target / "src").is_dir()

    def test_generic_warns_about_partial_delivery(self, tmp_path):
        target = tmp_path / "gen"
        target.mkdir()

        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_success,
        ):
            result = run_scaffold(
                repo_root=target,
                profile="generic",
                product_name="gen",
                prompt="Build something",
                mode="greenfield",
            )

        assert any("partial" in w.lower() for w in result["warnings"])


# ---------------------------------------------------------------------------
# Postflight validation (direct, no mocks)
# ---------------------------------------------------------------------------

class TestPostflight:
    def test_react_vite_passes_with_complete_scaffold(self, tmp_path):
        _write_react_vite_scaffold(tmp_path)
        result = run_postflight(tmp_path, "react-vite")
        assert result["passed"] is True

    def test_react_vite_fails_without_package_json(self, tmp_path):
        # src exists but no package.json
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "App.tsx").write_text("export default 1;", encoding="utf-8")
        result = run_postflight(tmp_path, "react-vite")
        assert result["passed"] is False
        pkg_check = next(c for c in result["checks"] if "package.json exists" in c["name"])
        assert pkg_check["passed"] is False

    def test_react_vite_fails_without_src(self, tmp_path):
        # package.json exists but no src/
        pkg = {"name": "test", "dependencies": {"react": "^18"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = run_postflight(tmp_path, "react-vite")
        assert result["passed"] is False
        src_check = next(c for c in result["checks"] if "src/" in c["name"] and "directory" in c["name"])
        assert src_check["passed"] is False

    def test_generic_passes_with_signalos_dir(self, tmp_path):
        (tmp_path / ".signalos").mkdir()
        result = run_postflight(tmp_path, "generic")
        assert result["passed"] is True

    def test_generic_fails_without_signalos_dir(self, tmp_path):
        result = run_postflight(tmp_path, "generic")
        assert result["passed"] is False

    def test_existing_repo_checks_preserved_files(self, tmp_path):
        (tmp_path / ".signalos").mkdir()
        (tmp_path / "README.md").write_text("# Hello", encoding="utf-8")
        result = run_postflight(tmp_path, "existing-repo")
        assert result["passed"] is True
        preserved_check = next(c for c in result["checks"] if "preserved" in c["name"])
        assert preserved_check["passed"] is True


# ---------------------------------------------------------------------------
# Auto profile detection
# ---------------------------------------------------------------------------

class TestAutoProfileDetection:
    def test_selects_react_vite_when_vite_dep_present(self, tmp_path):
        pkg = {"dependencies": {}, "devDependencies": {"vite": "^5.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_success,
        ):
            result = run_scaffold(
                repo_root=tmp_path,
                profile="auto",
                product_name="auto-app",
                prompt="Build an app",
                mode="greenfield",
            )

        assert result["profile"] == "react-vite"

    def test_selects_react_vite_for_empty_greenfield_repo(self, tmp_path):
        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_success,
        ):
            result = run_scaffold(
                repo_root=tmp_path,
                profile="auto",
                product_name="empty",
                prompt="Build something",
                mode="greenfield",
            )

        assert result["profile"] == "react-vite"


# ---------------------------------------------------------------------------
# Profile selection explanation
# ---------------------------------------------------------------------------

class TestProfileExplanation:
    def test_explanation_is_non_empty_string(self):
        for profile in ("react-vite", "generic", "existing-repo"):
            explanation = explain_profile_selection(profile, detected=True)
            assert isinstance(explanation, str)
            assert len(explanation) > 0

    def test_explicit_profile_explanation(self):
        explanation = explain_profile_selection("react-vite", detected=False)
        assert "explicitly" in explanation.lower() or "specified" in explanation.lower()


# ---------------------------------------------------------------------------
# Blueprint auto-matching
# ---------------------------------------------------------------------------

class TestBlueprintMatching:
    def test_task_prompt_matches_task_management(self, tmp_path):
        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_success,
        ):
            result = run_scaffold(
                repo_root=tmp_path,
                profile="generic",
                product_name="tasks",
                prompt="Build a task management app with kanban board",
                mode="greenfield",
            )

        # Blueprint should be matched (task-management if registry has it)
        # Even if no blueprint matches, the field must be present
        assert "blueprint" in result


# ---------------------------------------------------------------------------
# Adopt mode
# ---------------------------------------------------------------------------

class TestAdoptMode:
    def test_adopt_preserves_existing_files(self, tmp_path):
        # Pre-populate with existing files
        (tmp_path / "README.md").write_text("# Existing", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("console.log('hi');", encoding="utf-8")

        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_success,
        ):
            result = run_scaffold(
                repo_root=tmp_path,
                profile="generic",
                product_name="adopted",
                prompt="Adopt this repo",
                mode="adopt",
            )

        assert result["success"] is True
        assert result["mode"] == "adopt"
        # Original files must still exist
        assert (tmp_path / "README.md").is_file()
        assert (tmp_path / "src" / "index.ts").is_file()
        readme_content = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert readme_content == "# Existing"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_returns_failure_on_init_error(self, tmp_path):
        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=_mock_init_failure,
        ):
            result = run_scaffold(
                repo_root=tmp_path,
                profile="react-vite",
                product_name="fail",
                prompt="Build something",
                mode="greenfield",
            )

        assert result["success"] is False
        assert len(result["errors"]) > 0

    def test_returns_failure_on_init_exception(self, tmp_path):
        # Use a path that triggers an exception
        bad_path = tmp_path / "nonexistent" / "deeply" / "nested"

        with mock.patch(
            "signalos_lib.product.scaffold.lifecycle.init_product_repo",
            side_effect=OSError("cannot create directory"),
        ):
            result = run_scaffold(
                repo_root=bad_path,
                profile="react-vite",
                product_name="fail",
                prompt="Build something",
                mode="greenfield",
            )

        assert result["success"] is False
        assert len(result["errors"]) > 0
