"""Tests for the product stack adapter contract and implementations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signalos_lib.product.stacks import (
    ExistingRepoAdapter,
    GenericAdapter,
    ReactViteAdapter,
    StackAdapter,
    detect_profile,
    get_adapter,
    list_adapters,
)


# ---------------------------------------------------------------------------
# ReactViteAdapter
# ---------------------------------------------------------------------------


class TestReactViteScaffold:
    def test_scaffold_creates_required_files(self, tmp_path: Path) -> None:
        adapter = ReactViteAdapter()
        result = adapter.scaffold(tmp_path, {})

        assert "package.json" in result["created"]
        assert "vite.config.ts" in result["created"]
        assert "src/main.tsx" in result["created"]
        assert "src/App.tsx" in result["created"]
        assert "src/App.test.tsx" in result["created"]

        for rel in result["created"]:
            assert (tmp_path / rel).is_file(), f"{rel} was not created on disk"

    def test_scaffold_package_json_is_parseable(self, tmp_path: Path) -> None:
        ReactViteAdapter().scaffold(tmp_path, {})
        pkg = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert isinstance(pkg, dict)
        assert "dependencies" in pkg
        assert "react" in pkg["dependencies"]
        assert "vite" in pkg["devDependencies"]
        assert "scripts" in pkg
        assert "test" in pkg["scripts"]

    def test_scaffold_package_json_has_baseline_deps(self, tmp_path: Path) -> None:
        ReactViteAdapter().scaffold(tmp_path, {})
        pkg = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        # dependencies
        assert "react" in pkg["dependencies"]
        assert "react-dom" in pkg["dependencies"]
        assert "react-router-dom" in pkg["dependencies"]
        # devDependencies
        assert "@types/react" in pkg["devDependencies"]
        assert "@types/react-dom" in pkg["devDependencies"]
        assert "@vitejs/plugin-react" in pkg["devDependencies"]
        assert "typescript" in pkg["devDependencies"]
        assert "vite" in pkg["devDependencies"]
        assert "vitest" in pkg["devDependencies"]
        assert "@testing-library/react" in pkg["devDependencies"]
        assert "@testing-library/jest-dom" in pkg["devDependencies"]
        assert "jsdom" in pkg["devDependencies"]

    def test_scaffold_package_json_has_scripts(self, tmp_path: Path) -> None:
        ReactViteAdapter().scaffold(tmp_path, {})
        pkg = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert "dev" in pkg["scripts"]
        assert "build" in pkg["scripts"]
        assert "test" in pkg["scripts"]

    def test_scaffold_accepts_extra_dependencies(self, tmp_path: Path) -> None:
        extra = {"recharts": "^2.12.0"}
        ReactViteAdapter().scaffold(tmp_path, {}, dependencies=extra)
        pkg = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert "recharts" in pkg["dependencies"]
        assert pkg["dependencies"]["recharts"] == "^2.12.0"
        # Baseline deps still present
        assert "react" in pkg["dependencies"]

    def test_scaffold_files_not_empty(self, tmp_path: Path) -> None:
        ReactViteAdapter().scaffold(tmp_path, {})
        for rel in ("vite.config.ts", "index.html", "src/main.tsx", "src/App.tsx", "src/App.test.tsx"):
            content = (tmp_path / rel).read_text(encoding="utf-8")
            assert len(content.strip()) > 20, f"{rel} looks like an empty stub"

    def test_scaffold_reports_runnable(self, tmp_path: Path) -> None:
        result = ReactViteAdapter().scaffold(tmp_path, {})
        assert result["can_deliver_ui"] is True
        assert result["can_deliver_runnable"] is True


class TestReactViteValidation:
    def test_validation_plan_has_install_build_test(self, tmp_path: Path) -> None:
        plan = ReactViteAdapter().validation_plan(tmp_path)
        assert plan["install"] == ["npm install"]
        assert plan["build"] == ["npm run build"]
        assert plan["test"] == ["npm test"]


class TestReactVitePreview:
    def test_preview_plan_has_required_keys(self, tmp_path: Path) -> None:
        plan = ReactViteAdapter().preview_plan(tmp_path)
        assert plan["command"] == "npm run dev"
        assert plan["port"] == 5173
        assert plan["health_path"] == "/"
        assert isinstance(plan["timeout_s"], int)


class TestReactViteDetect:
    def test_detect_finds_signals(self, tmp_path: Path) -> None:
        # Set up a repo that looks like react-vite
        pkg = {"dependencies": {"react": "^18"}, "devDependencies": {"vite": "^5"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        (tmp_path / "src").mkdir()

        info = ReactViteAdapter().detect(tmp_path)
        assert "package.json" in info["signals"]
        assert "vite-dep" in info["signals"]
        assert "react-dep" in info["signals"]
        assert "src-dir" in info["signals"]
        assert info["can_deliver_ui"] is True


# ---------------------------------------------------------------------------
# GenericAdapter
# ---------------------------------------------------------------------------


class TestGenericAdapter:
    def test_scaffold_only_creates_signalos(self, tmp_path: Path) -> None:
        result = GenericAdapter().scaffold(tmp_path, {})
        # Only .signalos/ governance files should exist
        for created in result["created"]:
            assert created.startswith(".signalos/"), (
                f"generic scaffold created non-governance file: {created}"
            )
        # No app files
        assert not (tmp_path / "package.json").exists()
        assert not (tmp_path / "src").exists()

    def test_cannot_claim_runnable_ui(self, tmp_path: Path) -> None:
        info = GenericAdapter().detect(tmp_path)
        assert info["can_deliver_ui"] is False
        assert info["can_deliver_runnable"] is False

    def test_scaffold_reports_not_runnable(self, tmp_path: Path) -> None:
        result = GenericAdapter().scaffold(tmp_path, {})
        assert result["can_deliver_ui"] is False
        assert result["can_deliver_runnable"] is False

    def test_validation_plan_is_empty(self, tmp_path: Path) -> None:
        plan = GenericAdapter().validation_plan(tmp_path)
        for commands in plan.values():
            assert commands == []

    def test_preview_plan_is_null(self, tmp_path: Path) -> None:
        plan = GenericAdapter().preview_plan(tmp_path)
        assert plan["command"] is None


# ---------------------------------------------------------------------------
# ExistingRepoAdapter
# ---------------------------------------------------------------------------


class TestExistingRepoDetect:
    def test_detect_finds_package_json(self, tmp_path: Path) -> None:
        pkg = {"name": "my-app", "scripts": {"build": "echo ok"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

        info = ExistingRepoAdapter().detect(tmp_path)
        assert "package.json" in info["signals"]
        assert info["can_deliver_runnable"] is True

    def test_detect_finds_cargo_toml(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"', encoding="utf-8")
        info = ExistingRepoAdapter().detect(tmp_path)
        assert "Cargo.toml" in info["signals"]
        assert "rust" in info["detected_stacks"]

    def test_detect_empty_repo(self, tmp_path: Path) -> None:
        info = ExistingRepoAdapter().detect(tmp_path)
        assert info["signals"] == []
        assert info["can_deliver_runnable"] is False


class TestExistingRepoScaffold:
    def test_scaffold_does_not_overwrite_source(self, tmp_path: Path) -> None:
        # Pre-existing source file
        (tmp_path / "src").mkdir()
        original = "console.log('original');\n"
        (tmp_path / "src" / "index.ts").write_text(original, encoding="utf-8")
        (tmp_path / "package.json").write_text('{"name":"x"}', encoding="utf-8")

        ExistingRepoAdapter().scaffold(tmp_path, {})

        # Source file must be untouched
        assert (tmp_path / "src" / "index.ts").read_text(encoding="utf-8") == original
        # package.json must be untouched
        assert json.loads((tmp_path / "package.json").read_text(encoding="utf-8")) == {"name": "x"}

    def test_scaffold_creates_profile_json(self, tmp_path: Path) -> None:
        result = ExistingRepoAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / ".signalos" / "profile.json").is_file()

    def test_scaffold_records_preserved(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        result = ExistingRepoAdapter().scaffold(tmp_path, {})
        assert "package.json" in result["preserved"]


class TestExistingRepoValidation:
    def test_infers_npm_commands(self, tmp_path: Path) -> None:
        pkg = {"scripts": {"build": "tsc", "test": "jest", "lint": "eslint ."}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        plan = ExistingRepoAdapter().validation_plan(tmp_path)
        assert plan["install"] == ["npm install"]
        assert plan["build"] == ["npm run build"]
        assert plan["test"] == ["npm test"]
        assert plan["lint"] == ["npm run lint"]

    def test_infers_cargo_commands(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"', encoding="utf-8")
        plan = ExistingRepoAdapter().validation_plan(tmp_path)
        assert plan["build"] == ["cargo build"]
        assert plan["test"] == ["cargo test"]


# ---------------------------------------------------------------------------
# Registry functions
# ---------------------------------------------------------------------------


class TestDetectProfile:
    def test_returns_react_vite_for_vite_dep(self, tmp_path: Path) -> None:
        pkg = {"devDependencies": {"vite": "^5"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        assert detect_profile(tmp_path) == "react-vite"

    def test_returns_generic_for_empty_repo(self, tmp_path: Path) -> None:
        assert detect_profile(tmp_path) == "generic"

    def test_returns_existing_repo_for_non_vite_package(self, tmp_path: Path) -> None:
        pkg = {"dependencies": {"express": "^4"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        assert detect_profile(tmp_path) == "existing-repo"


class TestGetAdapter:
    def test_returns_correct_adapter_by_id(self) -> None:
        assert isinstance(get_adapter("react-vite"), ReactViteAdapter)
        assert isinstance(get_adapter("generic"), GenericAdapter)
        assert isinstance(get_adapter("existing-repo"), ExistingRepoAdapter)

    def test_raises_for_unknown_id(self) -> None:
        with pytest.raises(KeyError):
            get_adapter("nonexistent")


class TestListAdapters:
    def test_returns_all_three(self) -> None:
        adapters = list_adapters()
        ids = {a["id"] for a in adapters}
        assert ids == {"react-vite", "generic", "existing-repo"}

    def test_entries_have_display_name(self) -> None:
        for entry in list_adapters():
            assert "id" in entry
            assert "display_name" in entry
            assert isinstance(entry["display_name"], str)


# ---------------------------------------------------------------------------
# Profile persistence (profile.json round-trip)
# ---------------------------------------------------------------------------


class TestProfilePersistence:
    def test_write_and_read_profile_json(self, tmp_path: Path) -> None:
        adapter = ReactViteAdapter()
        adapter.scaffold(tmp_path, {})

        profile_path = tmp_path / ".signalos" / "profile.json"
        assert profile_path.is_file()

        data = json.loads(profile_path.read_text(encoding="utf-8"))
        assert data["profile"] == "react-vite"
        assert data["display_name"] == "React + Vite"

        # Resolve back to adapter
        restored = get_adapter(data["profile"])
        assert restored.id == "react-vite"
