# test_product_generation_manifest.py
# Phase P7 - Tests for Generic Product Generation
#
# Covers manifest construction, file generation, overwrite rules,
# blueprint-specific and intent-driven paths, and round-trip persistence.

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from signalos_lib.product.generation import (
    build_generation_manifest,
    check_file_ownership,
    compute_sha256_lf,
    generate_file_content,
    generate_product,
    load_generation_manifest,
    write_generation_manifest,
)
from signalos_lib.product.blueprints.registry import load_blueprint


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_repo(tmp_path):
    """Create a minimal repo root with src/ directory."""
    (tmp_path / "src" / "components").mkdir(parents=True)
    (tmp_path / ".signalos").mkdir()
    return tmp_path


@pytest.fixture
def task_intent():
    return {
        "product_name": "TaskApp",
        "product_type": "task-management",
        "entities": ["tasks", "projects"],
        "primary_workflows": ["create task", "complete task"],
        "ux_surfaces": ["list", "kanban"],
    }


@pytest.fixture
def finance_intent():
    return {
        "product_name": "FinDash",
        "product_type": "financial-dashboard",
        "entities": ["revenue", "churn"],
        "primary_workflows": ["record revenue"],
        "ux_surfaces": ["chart", "dashboard"],
    }


@pytest.fixture
def empty_intent():
    return {
        "product_name": "",
        "product_type": "",
        "entities": [],
        "primary_workflows": [],
        "ux_surfaces": [],
    }


@pytest.fixture
def task_blueprint():
    return load_blueprint("task-management")


@pytest.fixture
def finance_blueprint():
    return load_blueprint("financial-dashboard")


# ---------------------------------------------------------------------------
# Task-management + react-vite generates expected component files
# ---------------------------------------------------------------------------

class TestTaskManagementReactVite:
    def test_generates_expected_files(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite", wave="1",
        )
        paths = [f["path"] for f in manifest["files"]]
        assert "src/components/TaskList.tsx" in paths
        assert "src/components/TaskList.test.tsx" in paths
        assert "src/components/TaskForm.tsx" in paths
        assert "src/components/TaskForm.test.tsx" in paths
        assert "src/components/ProjectBoard.tsx" in paths
        assert "src/components/ProjectBoard.test.tsx" in paths

    def test_generates_types(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        paths = [f["path"] for f in manifest["files"]]
        assert "src/types.ts" in paths

    def test_generates_app_registration(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        paths = [f["path"] for f in manifest["files"]]
        assert "src/App.tsx" in paths

    def test_files_land_in_src(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in manifest["files"]:
            assert f["path"].startswith("src/"), f"File outside src/: {f['path']}"

    def test_component_files_exist_on_disk(self, tmp_repo, task_intent, task_blueprint):
        generate_product(tmp_repo, task_intent, task_blueprint, "react-vite")
        assert (tmp_repo / "src/components/TaskList.tsx").is_file()
        assert (tmp_repo / "src/components/TaskList.test.tsx").is_file()
        assert (tmp_repo / "src/components/TaskForm.tsx").is_file()
        assert (tmp_repo / "src/components/TaskForm.test.tsx").is_file()
        assert (tmp_repo / "src/components/ProjectBoard.tsx").is_file()
        assert (tmp_repo / "src/components/ProjectBoard.test.tsx").is_file()


# ---------------------------------------------------------------------------
# Financial-dashboard + react-vite generates chart/gauge files
# ---------------------------------------------------------------------------

class TestFinancialDashboardReactVite:
    def test_generates_expected_files(self, tmp_repo, finance_intent, finance_blueprint):
        manifest = generate_product(
            tmp_repo, finance_intent, finance_blueprint, "react-vite",
        )
        paths = [f["path"] for f in manifest["files"]]
        assert "src/components/RevenueChart.tsx" in paths
        assert "src/components/RevenueChart.test.tsx" in paths
        assert "src/components/ChurnChart.tsx" in paths
        assert "src/components/ChurnChart.test.tsx" in paths
        assert "src/components/RunwayGauge.tsx" in paths
        assert "src/components/RunwayGauge.test.tsx" in paths

    def test_generates_types(self, tmp_repo, finance_intent, finance_blueprint):
        manifest = generate_product(
            tmp_repo, finance_intent, finance_blueprint, "react-vite",
        )
        paths = [f["path"] for f in manifest["files"]]
        assert "src/types.ts" in paths

    def test_files_land_in_src(self, tmp_repo, finance_intent, finance_blueprint):
        manifest = generate_product(
            tmp_repo, finance_intent, finance_blueprint, "react-vite",
        )
        for f in manifest["files"]:
            assert f["path"].startswith("src/"), f"File outside src/: {f['path']}"


# ---------------------------------------------------------------------------
# TDD order: tests before source in manifest
# ---------------------------------------------------------------------------

class TestTDDOrder:
    def test_test_files_before_source_files(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        files = manifest["files"]
        # For each component pair, the test should come first
        for i, f in enumerate(files):
            if f["kind"] == "source" and f["path"].endswith(".tsx"):
                # Find the matching test
                test_path = f["path"].replace(".tsx", ".test.tsx")
                test_indices = [
                    j for j, t in enumerate(files) if t["path"] == test_path
                ]
                if test_indices:
                    assert test_indices[0] < i, (
                        f"Test {test_path} should come before {f['path']}"
                    )


# ---------------------------------------------------------------------------
# Manifest includes every generated file with sha256_lf
# ---------------------------------------------------------------------------

class TestManifestIntegrity:
    def test_every_file_has_sha256(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in manifest["files"]:
            assert "sha256_lf" in f, f"Missing sha256_lf: {f['path']}"
            assert len(f["sha256_lf"]) == 64, f"Bad sha256_lf length: {f['path']}"

    def test_sha256_is_correct(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in manifest["files"]:
            if f["overwrite_mode"] == "skip":
                continue
            file_path = tmp_repo / f["path"]
            content = file_path.read_text(encoding="utf-8")
            expected = compute_sha256_lf(content)
            assert f["sha256_lf"] == expected, (
                f"SHA-256 mismatch for {f['path']}"
            )

    def test_no_orphan_files(self, tmp_repo, task_intent, task_blueprint):
        """Every file in src/components/ should be in the manifest."""
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        manifest_paths = {f["path"] for f in manifest["files"]}
        components_dir = tmp_repo / "src" / "components"
        if components_dir.is_dir():
            for disk_file in components_dir.iterdir():
                if disk_file.is_file():
                    rel = disk_file.relative_to(tmp_repo).as_posix()
                    assert rel in manifest_paths, f"Orphan file: {rel}"

    def test_manifest_schema_version(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        assert manifest["schema_version"] == "signalos.generation_manifest.v1"

    def test_manifest_has_generated_at(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        assert "generated_at" in manifest
        assert len(manifest["generated_at"]) > 0


# ---------------------------------------------------------------------------
# check_file_ownership
# ---------------------------------------------------------------------------

class TestFileOwnership:
    def test_owned_file_returns_true(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        assert check_file_ownership("src/components/TaskList.tsx", manifest) is True

    def test_unowned_file_returns_false(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        assert check_file_ownership("src/random/Other.tsx", manifest) is False

    def test_backslash_normalisation(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        assert check_file_ownership(
            "src\\components\\TaskList.tsx", manifest
        ) is True


# ---------------------------------------------------------------------------
# Overwrite mode "create" skips existing files
# ---------------------------------------------------------------------------

class TestOverwriteRules:
    def test_create_skips_existing(self, tmp_repo, task_intent, task_blueprint):
        # Pre-create a file
        existing = tmp_repo / "src" / "components" / "TaskList.tsx"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("// pre-existing\n", encoding="utf-8")

        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        # The pre-existing file should not be overwritten
        assert existing.read_text(encoding="utf-8") == "// pre-existing\n"

        # The manifest should record it as "skip"
        tl = [f for f in manifest["files"]
              if f["path"] == "src/components/TaskList.tsx"]
        assert len(tl) == 1
        assert tl[0]["overwrite_mode"] == "skip"

    def test_patch_overwrites(self, tmp_repo, task_intent, task_blueprint):
        # Pre-create App.tsx
        app = tmp_repo / "src" / "App.tsx"
        app.parent.mkdir(parents=True, exist_ok=True)
        app.write_text("// old\n", encoding="utf-8")

        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        # App.tsx should be overwritten (patch mode)
        content = app.read_text(encoding="utf-8")
        assert content != "// old\n"
        assert "import" in content

        app_rec = [f for f in manifest["files"]
                   if f["path"] == "src/App.tsx"]
        assert len(app_rec) == 1
        assert app_rec[0]["overwrite_mode"] == "patch"


# ---------------------------------------------------------------------------
# Custom/no blueprint generates from intent entities
# ---------------------------------------------------------------------------

class TestCustomGeneration:
    def test_no_blueprint_uses_intent_entities(self, tmp_repo, task_intent):
        manifest = generate_product(
            tmp_repo, task_intent, None, "react-vite",
        )
        paths = [f["path"] for f in manifest["files"]]
        # Entities from intent: tasks, projects -> Tasks, Projects components
        assert any("Tasks" in p for p in paths)
        assert any("Projects" in p for p in paths)

    def test_generic_profile_generates_python(self, tmp_repo, task_intent):
        manifest = generate_product(
            tmp_repo, task_intent, None, "generic",
        )
        paths = [f["path"] for f in manifest["files"]]
        assert any(p.endswith(".py") for p in paths)

    def test_generic_with_blueprint(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "generic",
        )
        paths = [f["path"] for f in manifest["files"]]
        # Should generate Python files for blueprint entities
        assert any("task" in p.lower() for p in paths)
        assert any("project" in p.lower() for p in paths)


# ---------------------------------------------------------------------------
# Empty intent generates no files (no crash)
# ---------------------------------------------------------------------------

class TestEmptyIntent:
    def test_empty_intent_no_crash(self, tmp_repo, empty_intent):
        manifest = generate_product(
            tmp_repo, empty_intent, None, "react-vite",
        )
        # No component files, but manifest should still be valid
        assert manifest["schema_version"] == "signalos.generation_manifest.v1"
        # No source or test files (possibly just empty list)
        source_files = [
            f for f in manifest["files"] if f["kind"] in ("source", "test")
        ]
        assert len(source_files) == 0

    def test_empty_intent_generic_no_crash(self, tmp_repo, empty_intent):
        manifest = generate_product(
            tmp_repo, empty_intent, None, "generic",
        )
        assert manifest["schema_version"] == "signalos.generation_manifest.v1"
        assert len(manifest["files"]) == 0


# ---------------------------------------------------------------------------
# Manifest round-trips through write/load
# ---------------------------------------------------------------------------

class TestManifestPersistence:
    def test_round_trip(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        signalos_dir = tmp_repo / ".signalos"
        loaded = load_generation_manifest(signalos_dir)
        assert loaded is not None
        assert loaded["schema_version"] == manifest["schema_version"]
        assert loaded["product"] == manifest["product"]
        assert loaded["blueprint"] == manifest["blueprint"]
        assert loaded["profile"] == manifest["profile"]
        assert loaded["wave"] == manifest["wave"]
        assert len(loaded["files"]) == len(manifest["files"])

    def test_explicit_write_load(self, tmp_path):
        signalos_dir = tmp_path / ".signalos"
        signalos_dir.mkdir()
        manifest = build_generation_manifest(
            product_name="TestProd",
            blueprint_id="test-bp",
            profile="react-vite",
            wave="1",
            task_ids=["T-001"],
            files=[{
                "path": "src/Foo.tsx",
                "kind": "source",
                "task_id": None,
                "acceptance_id": None,
                "sha256_lf": "abc123",
                "overwrite_mode": "create",
            }],
            validation_commands=["npm test"],
        )
        path = write_generation_manifest(manifest, signalos_dir)
        assert path.is_file()
        loaded = load_generation_manifest(signalos_dir)
        assert loaded is not None
        assert loaded["product"] == "TestProd"
        assert loaded["files"][0]["path"] == "src/Foo.tsx"

    def test_load_missing_returns_none(self, tmp_path):
        assert load_generation_manifest(tmp_path / ".signalos") is None


# ---------------------------------------------------------------------------
# Generated content is non-empty and syntactically plausible
# ---------------------------------------------------------------------------

class TestGeneratedContent:
    def test_react_source_contains_keywords(self, tmp_repo, task_intent, task_blueprint):
        generate_product(tmp_repo, task_intent, task_blueprint, "react-vite")
        content = (tmp_repo / "src/components/TaskList.tsx").read_text(encoding="utf-8")
        assert "import React" in content
        assert "function TaskList" in content
        assert "export default TaskList" in content
        assert len(content) > 50

    def test_react_test_contains_keywords(self, tmp_repo, task_intent, task_blueprint):
        generate_product(tmp_repo, task_intent, task_blueprint, "react-vite")
        content = (tmp_repo / "src/components/TaskList.test.tsx").read_text(encoding="utf-8")
        assert "describe" in content
        assert "it(" in content or "it('" in content
        assert "render" in content
        assert "expect" in content
        assert len(content) > 50

    def test_types_file_contains_interfaces(self, tmp_repo, task_intent, task_blueprint):
        generate_product(tmp_repo, task_intent, task_blueprint, "react-vite")
        content = (tmp_repo / "src/types.ts").read_text(encoding="utf-8")
        assert "export interface Task" in content
        assert "export interface Project" in content
        assert "export interface User" in content

    def test_app_tsx_imports_components(self, tmp_repo, task_intent, task_blueprint):
        generate_product(tmp_repo, task_intent, task_blueprint, "react-vite")
        content = (tmp_repo / "src/App.tsx").read_text(encoding="utf-8")
        assert "import TaskList" in content
        assert "import TaskForm" in content
        assert "import ProjectBoard" in content

    def test_generic_python_source(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "generic",
        )
        # Find a source file
        source_files = [f for f in manifest["files"] if f["kind"] == "source"]
        assert len(source_files) > 0
        path = tmp_repo / source_files[0]["path"]
        content = path.read_text(encoding="utf-8")
        assert "class " in content
        assert "def " in content

    def test_generic_python_test(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "generic",
        )
        test_files = [f for f in manifest["files"] if f["kind"] == "test"]
        assert len(test_files) > 0
        path = tmp_repo / test_files[0]["path"]
        content = path.read_text(encoding="utf-8")
        assert "unittest" in content
        assert "def test_" in content


# ---------------------------------------------------------------------------
# Reserved path rejection
# ---------------------------------------------------------------------------

class TestReservedPaths:
    def test_no_files_in_signalos(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in manifest["files"]:
            normed = f["path"].replace("\\", "/")
            assert not normed.startswith(".signalos/"), (
                f"File in reserved .signalos/: {f['path']}"
            )

    def test_no_files_in_node_modules(self, tmp_repo, task_intent, task_blueprint):
        manifest = generate_product(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in manifest["files"]:
            normed = f["path"].replace("\\", "/")
            assert not normed.startswith("node_modules/"), (
                f"File in reserved node_modules/: {f['path']}"
            )


# ---------------------------------------------------------------------------
# compute_sha256_lf unit tests
# ---------------------------------------------------------------------------

class TestSHA256:
    def test_basic_hash(self):
        h = compute_sha256_lf("hello\n")
        assert len(h) == 64
        assert h == compute_sha256_lf("hello\n")

    def test_crlf_normalised(self):
        assert compute_sha256_lf("a\r\nb\r\n") == compute_sha256_lf("a\nb\n")

    def test_cr_normalised(self):
        assert compute_sha256_lf("a\rb\r") == compute_sha256_lf("a\nb\n")

    def test_different_content_different_hash(self):
        assert compute_sha256_lf("hello") != compute_sha256_lf("world")


# ---------------------------------------------------------------------------
# generate_file_content standalone tests
# ---------------------------------------------------------------------------

class TestGenerateFileContent:
    def test_react_source(self):
        content = generate_file_content(
            entity={"name": "Foo", "fields": ["id", "name"]},
            workflow=None, surface=None,
            kind="source", profile="react-vite", blueprint=None,
        )
        assert "function Foo" in content
        assert "import React" in content

    def test_react_test(self):
        content = generate_file_content(
            entity={"name": "Foo"}, workflow=None, surface=None,
            kind="test", profile="react-vite", blueprint=None,
        )
        assert "describe" in content
        assert "Foo" in content

    def test_generic_source(self):
        content = generate_file_content(
            entity={"name": "Bar", "fields": ["id"]},
            workflow=None, surface=None,
            kind="source", profile="generic", blueprint=None,
        )
        assert "class Bar" in content

    def test_generic_test(self):
        content = generate_file_content(
            entity={"name": "Bar"}, workflow=None, surface=None,
            kind="test", profile="generic", blueprint=None,
        )
        assert "unittest" in content
        assert "TestBar" in content

    def test_react_config_with_entities(self):
        bp = {"entities": [{"name": "X", "fields": ["id", "name"]}]}
        content = generate_file_content(
            entity=None, workflow=None, surface=None,
            kind="config", profile="react-vite", blueprint=bp,
        )
        assert "export interface X" in content
