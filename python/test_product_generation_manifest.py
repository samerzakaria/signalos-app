# test_product_generation_manifest.py
# Phase P7 - Tests for Product Generation Packet Builder
#
# Covers packet construction, file spec derivation, trace linkage,
# validation of agent output, and round-trip manifest persistence.
# SignalOS builds packets (WHAT to create), not code (HOW to create it).

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from signalos_lib.product.generation import (
    _to_pascal_case,
    build_generation_manifest,
    build_generation_packet,
    check_file_ownership,
    compute_sha256_lf,
    get_blueprint_dependencies,
    link_generation_to_acceptance,
    load_generation_manifest,
    prepare_generation,
    validate_generation_output,
    verify_trace_completeness,
    write_generation_manifest,
)
from signalos_lib.product.acceptance import build_acceptance_matrix
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
# Task-management + react-vite generates expected file specs
# ---------------------------------------------------------------------------

class TestTaskManagementReactVite:
    def test_generates_expected_file_specs(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite", wave="1",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/components/TaskList.tsx" in paths
        assert "src/components/TaskList.test.tsx" in paths
        assert "src/components/TaskForm.tsx" in paths
        assert "src/components/TaskForm.test.tsx" in paths
        assert "src/components/ProjectBoard.tsx" in paths
        assert "src/components/ProjectBoard.test.tsx" in paths

    def test_generates_types_spec(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/types.ts" in paths

    def test_generates_app_registration_spec(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/App.tsx" in paths

    def test_file_specs_target_src(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in packet["file_specs"]:
            assert f["path"].startswith("src/"), f"File outside src/: {f['path']}"

    def test_no_code_files_written_to_disk(self, tmp_repo, task_intent, task_blueprint):
        """prepare_generation does NOT write application code."""
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        # No component files should exist on disk
        assert not (tmp_repo / "src/components/TaskList.tsx").is_file()
        assert not (tmp_repo / "src/components/TaskList.test.tsx").is_file()

    def test_packet_written_to_disk(self, tmp_repo, task_intent, task_blueprint):
        """GENERATION_PACKET.json is written."""
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        packet_path = tmp_repo / ".signalos" / "product" / "GENERATION_PACKET.json"
        assert packet_path.is_file()
        data = json.loads(packet_path.read_text(encoding="utf-8"))
        assert data["schema_version"] == "signalos.generation_packet.v1"


# ---------------------------------------------------------------------------
# Financial-dashboard + react-vite generates chart/gauge file specs
# ---------------------------------------------------------------------------

class TestFinancialDashboardReactVite:
    def test_generates_expected_file_specs(self, tmp_repo, finance_intent, finance_blueprint):
        packet = prepare_generation(
            tmp_repo, finance_intent, finance_blueprint, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/components/RevenueChart.tsx" in paths
        assert "src/components/RevenueChart.test.tsx" in paths
        assert "src/components/ChurnChart.tsx" in paths
        assert "src/components/ChurnChart.test.tsx" in paths
        assert "src/components/RunwayGauge.tsx" in paths
        assert "src/components/RunwayGauge.test.tsx" in paths

    def test_generates_types_spec(self, tmp_repo, finance_intent, finance_blueprint):
        packet = prepare_generation(
            tmp_repo, finance_intent, finance_blueprint, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/types.ts" in paths

    def test_file_specs_target_src(self, tmp_repo, finance_intent, finance_blueprint):
        packet = prepare_generation(
            tmp_repo, finance_intent, finance_blueprint, "react-vite",
        )
        for f in packet["file_specs"]:
            assert f["path"].startswith("src/"), f"File outside src/: {f['path']}"


# ---------------------------------------------------------------------------
# Node API and agent-selected generation profiles
# ---------------------------------------------------------------------------


class TestTechnologyNeutralProfiles:
    def test_node_api_generates_js_route_and_test_specs(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "node-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/routes/task.js" in paths
        assert "tests/task.test.js" in paths
        assert "src/app.js" in paths
        assert packet["capability_profile"]["adapter_profile"] == "node-api"

    def test_agent_selected_does_not_force_react_python_or_dotnet(self, tmp_repo, task_intent):
        task_intent["stack_preferences"] = ["angular", "postgresql", "redis"]
        packet = prepare_generation(
            tmp_repo, task_intent, None, "agent-selected",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "PRODUCT_STACK.md" in paths
        assert "README.md" in paths
        assert "src/App.tsx" not in paths
        assert not any(path.endswith(".py") for path in paths)
        assert packet["capability_profile"]["application_layers"]["frontend"] == ["angular"]

    def test_fastapi_api_generates_python_routes_models_and_tests(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "fastapi-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert "src/signalos_product_fastapi/routes/task.py" in paths
        assert "src/signalos_product_fastapi/models/task.py" in paths
        assert "tests/test_task.py" in paths
        assert "src/signalos_product_fastapi/app.py" in paths
        assert "src/App.tsx" not in paths
        assert "src/app.js" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "fastapi-api"

    def test_dotnet_minimal_api_generates_csharp_routes_models_and_http_tests(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "dotnet-minimal-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert "SignalOSProduct.Api/Models/Task.cs" in paths
        assert "SignalOSProduct.Api/Routes/TaskRoutes.cs" in paths
        assert "tests/task.http" in paths
        assert "SignalOSProduct.Api/Stores/InMemoryStore.cs" in paths
        assert "SignalOSProduct.Api/ProductRoutes.cs" in paths
        assert "src/App.tsx" not in paths
        assert "src/app.js" not in paths
        assert not any(path.endswith(".py") for path in paths)
        assert packet["capability_profile"]["adapter_profile"] == "dotnet-minimal-api"

    def test_go_api_generates_go_handlers_store_and_tests(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "go-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert "internal/app/task.go" in paths
        assert "internal/app/task_test.go" in paths
        assert "internal/app/store.go" in paths
        assert "internal/app/app.go" in paths
        assert "src/App.tsx" not in paths
        assert "src/app.js" not in paths
        assert not any(path.endswith(".py") for path in paths)
        assert packet["capability_profile"]["adapter_profile"] == "go-api"

    def test_angular_generates_component_specs_without_react_fallback(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "angular",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert any(path.endswith(".component.spec.ts") for path in paths)
        assert any(path.endswith(".component.ts") for path in paths)
        assert "src/App.tsx" not in paths
        assert "src/app.js" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "angular"

    def test_nextjs_generates_app_router_specs_without_react_vite_fallback(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "nextjs-app",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert any(path.startswith("app/components/") and path.endswith(".tsx") for path in paths)
        assert any(path.startswith("app/components/") and path.endswith(".test.tsx") for path in paths)
        assert "app/page.tsx" in paths
        assert "src/App.tsx" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "nextjs-app"

    def test_vue_vite_generates_vue_specs_without_react_fallback(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "vue-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert any(path.startswith("src/components/") and path.endswith(".vue") for path in paths)
        assert any(path.startswith("src/components/") and path.endswith(".spec.ts") for path in paths)
        assert "src/App.vue" in paths
        assert "src/App.tsx" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "vue-vite"

    def test_flutter_generates_mobile_screen_specs_without_web_fallback(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "flutter-app",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert any(path.startswith("lib/screens/") and path.endswith(".dart") for path in paths)
        assert any(path.startswith("test/") and path.endswith("_test.dart") for path in paths)
        assert "lib/main.dart" in paths
        assert "src/App.tsx" not in paths
        assert "src/app.js" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "flutter-app"

    def test_expo_react_native_generates_mobile_screen_specs_without_web_fallback(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "expo-react-native",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert any(path.startswith("src/screens/") and path.endswith(".js") for path in paths)
        assert any(path.startswith("tests/") and path.endswith(".test.js") for path in paths)
        assert "App.js" in paths
        assert "src/App.tsx" not in paths
        assert "src/app.js" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "expo-react-native"

    def test_django_api_generates_django_views_and_tests(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "django-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert "src/signalos_product_django/product/task_views.py" in paths
        assert "src/signalos_product_django/product/task_schemas.py" in paths
        assert "tests/test_task.py" in paths
        assert "src/signalos_product_django/urls.py" in paths
        assert "src/signalos_product_fastapi/app.py" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "django-api"

    def test_flask_api_generates_flask_blueprints_and_tests(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "flask-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert "src/signalos_product_flask/routes/task.py" in paths
        assert "src/signalos_product_flask/routes/task_schemas.py" in paths
        assert "tests/test_task.py" in paths
        assert "src/signalos_product_flask/app.py" in paths
        assert "src/signalos_product_fastapi/app.py" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "flask-api"

    def test_nestjs_api_generates_controller_service_and_tests(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "nestjs-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert "src/task/task.controller.ts" in paths
        assert "src/task/task.service.ts" in paths
        assert "src/task/task.controller.spec.ts" in paths
        assert "src/app.module.ts" in paths
        assert "src/app.js" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "nestjs-api"

    def test_java_api_generates_java_resources_and_tests(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "java-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert "src/main/java/com/signalos/product/TaskResource.java" in paths
        assert "src/test/java/com/signalos/product/TaskResourceTest.java" in paths
        assert "src/main/java/com/signalos/product/ProductServer.java" in paths
        assert "src/App.tsx" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "java-api"

    def test_spring_boot_api_generates_spring_controllers_and_tests(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "spring-boot-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert "src/main/java/com/signalos/product/task/TaskController.java" in paths
        assert "src/main/java/com/signalos/product/task/TaskService.java" in paths
        assert "src/test/java/com/signalos/product/task/TaskControllerTest.java" in paths
        assert "src/main/java/com/signalos/product/ProductApplication.java" in paths
        assert "src/main/java/com/signalos/product/ProductServer.java" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "spring-boot-api"

    def test_rust_api_generates_rust_modules_and_tests(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "rust-api",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        assert "src/task.rs" in paths
        assert "tests/task_api.rs" in paths
        assert "src/lib.rs" in paths
        assert "src/App.tsx" not in paths
        assert packet["capability_profile"]["adapter_profile"] == "rust-api"


# ---------------------------------------------------------------------------
# TDD order: tests before source in file specs
# ---------------------------------------------------------------------------

class TestTDDOrder:
    def test_test_specs_before_source_specs(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        specs = packet["file_specs"]
        # For each component pair, the test should come first
        for i, f in enumerate(specs):
            if f["kind"] == "source" and f["path"].endswith(".tsx"):
                # Find the matching test
                test_path = f["path"].replace(".tsx", ".test.tsx")
                test_indices = [
                    j for j, t in enumerate(specs) if t["path"] == test_path
                ]
                if test_indices:
                    assert test_indices[0] < i, (
                        f"Test {test_path} should come before {f['path']}"
                    )


# ---------------------------------------------------------------------------
# Packet and manifest integrity
# ---------------------------------------------------------------------------

class TestManifestIntegrity:
    def test_every_file_spec_has_kind(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in packet["file_specs"]:
            assert "kind" in f, f"Missing kind: {f['path']}"
            assert f["kind"] in ("test", "source", "config", "registration")

    def test_every_file_spec_has_description(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in packet["file_specs"]:
            assert "description" in f, f"Missing description: {f['path']}"
            assert len(f["description"]) > 10, f"Description too short: {f['path']}"

    def test_every_file_spec_has_constraints(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in packet["file_specs"]:
            assert "constraints" in f, f"Missing constraints: {f['path']}"
            assert isinstance(f["constraints"], list)

    def test_manifest_schema_version(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        assert packet["schema_version"] == "signalos.generation_packet.v1"

    def test_manifest_has_created_at(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        assert "created_at" in packet
        assert len(packet["created_at"]) > 0

    def test_packet_has_design_constraints(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        dc = packet["design_constraints"]
        assert "ui_library" in dc
        assert "state_management" in dc
        assert "conventions" in dc

    def test_packet_consumes_signed_generation_contracts(
        self, tmp_repo, task_intent, task_blueprint
    ):
        arch_review = {
            "schema_version": "signalos.arch_review.v1",
            "system_boundaries": ["profile: react-vite", "entities: Task"],
            "data_flow": ["form input -> state -> rendered list"],
            "trust_boundaries": ["user input to generated application"],
            "test_strategy": ["unit/build validation must run"],
        }
        design_decisions = {
            "schema_version": "signalos.design_decisions.v1",
            "selected_variant": "variant-focused",
            "selection_reason": "Dense task workflow wins",
            "taste_findings": [
                {
                    "id": "TF-001",
                    "finding": "Use compact task rows",
                    "disposition": "accepted",
                }
            ],
        }
        scope_decisions = {
            "schema_version": "signalos.scope_decisions.v1",
            "decisions": [
                {
                    "id": "SD-001",
                    "proposal": "Build task CRUD",
                    "disposition": "accepted",
                    "acceptance_criteria": ["AC-001"],
                },
                {
                    "id": "SD-002",
                    "proposal": "Add Slack sync",
                    "disposition": "deferred",
                },
            ],
        }

        packet = prepare_generation(
            tmp_repo,
            task_intent,
            task_blueprint,
            "react-vite",
            arch_review=arch_review,
            design_decisions=design_decisions,
            scope_decisions=scope_decisions,
        )

        contracts = packet["generation_contracts"]
        assert contracts["source_artifacts"] == {
            "architecture": "ARCH_REVIEW.yaml",
            "design": "DESIGN_DECISIONS.yaml",
            "scope": "SCOPE_DECISIONS.yaml",
        }
        assert "architecture" in contracts
        assert contracts["design_decisions"]["selected_variant"] == "variant-focused"
        assert contracts["scope_decisions"]["decisions"][0]["proposal"] == "Build task CRUD"
        assert packet["design_constraints"]["selected_variant"] == "variant-focused"
        assert "Use compact task rows" in packet["design_constraints"]["accepted_taste_findings"]

        joined_constraints = "\n".join(
            "\n".join(spec.get("constraints", []))
            for spec in packet["file_specs"]
        )
        assert "ARCH_REVIEW.yaml" in joined_constraints
        assert "DESIGN_DECISIONS.yaml" in joined_constraints
        assert "SCOPE_DECISIONS.yaml" in joined_constraints

    def test_packet_has_allowed_forbidden_paths(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        assert len(packet["allowed_paths"]) > 0
        assert len(packet["forbidden_paths"]) > 0
        assert ".signalos/" in packet["forbidden_paths"]

    def test_packet_has_validation_commands(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        assert len(packet["validation_commands"]) > 0
        assert "npm test" in packet["validation_commands"]


# ---------------------------------------------------------------------------
# check_file_ownership (works on manifest)
# ---------------------------------------------------------------------------

class TestFileOwnership:
    def test_owned_file_returns_true(self, tmp_repo, task_intent, task_blueprint):
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        manifest = load_generation_manifest(tmp_repo / ".signalos")
        assert check_file_ownership("src/components/TaskList.tsx", manifest) is True

    def test_unowned_file_returns_false(self, tmp_repo, task_intent, task_blueprint):
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        manifest = load_generation_manifest(tmp_repo / ".signalos")
        assert check_file_ownership("src/random/Other.tsx", manifest) is False

    def test_backslash_normalisation(self, tmp_repo, task_intent, task_blueprint):
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        manifest = load_generation_manifest(tmp_repo / ".signalos")
        assert check_file_ownership(
            "src\\components\\TaskList.tsx", manifest
        ) is True


# ---------------------------------------------------------------------------
# Custom/no blueprint generates from intent entities
# ---------------------------------------------------------------------------

class TestCustomGeneration:
    def test_no_blueprint_uses_intent_entities(self, tmp_repo, task_intent):
        packet = prepare_generation(
            tmp_repo, task_intent, None, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        # Entities from intent: tasks, projects -> Tasks, Projects components
        assert any("Tasks" in p for p in paths)
        assert any("Projects" in p for p in paths)

    def test_generic_profile_generates_python_specs(self, tmp_repo, task_intent):
        packet = prepare_generation(
            tmp_repo, task_intent, None, "generic",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert any(p.endswith(".py") for p in paths)

    def test_generic_with_blueprint(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "generic",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        # Should generate Python file specs for blueprint entities
        assert any("task" in p.lower() for p in paths)
        assert any("project" in p.lower() for p in paths)


# ---------------------------------------------------------------------------
# Empty intent generates no file specs (no crash)
# ---------------------------------------------------------------------------

class TestEmptyIntent:
    def test_empty_intent_no_crash(self, tmp_repo, empty_intent):
        packet = prepare_generation(
            tmp_repo, empty_intent, None, "react-vite",
        )
        # No component file specs, but packet should still be valid
        assert packet["schema_version"] == "signalos.generation_packet.v1"
        # No source or test specs
        source_specs = [
            f for f in packet["file_specs"] if f["kind"] in ("source", "test")
        ]
        assert len(source_specs) == 0

    def test_empty_intent_generic_no_crash(self, tmp_repo, empty_intent):
        packet = prepare_generation(
            tmp_repo, empty_intent, None, "generic",
        )
        assert packet["schema_version"] == "signalos.generation_packet.v1"
        assert len(packet["file_specs"]) == 0


# ---------------------------------------------------------------------------
# Manifest round-trips through write/load
# ---------------------------------------------------------------------------

class TestManifestPersistence:
    def test_round_trip(self, tmp_repo, task_intent, task_blueprint):
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        signalos_dir = tmp_repo / ".signalos"
        loaded = load_generation_manifest(signalos_dir)
        assert loaded is not None
        assert loaded["schema_version"] == "signalos.generation_manifest.v1"
        assert loaded["product"] == "TaskApp"
        assert loaded["profile"] == "react-vite"
        assert loaded["wave"] == "1"
        assert len(loaded["files"]) > 0

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
# File spec descriptions are meaningful (not code)
# ---------------------------------------------------------------------------

class TestFileSpecDescriptions:
    def test_source_specs_describe_what_not_how(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        source_specs = [f for f in packet["file_specs"] if f["kind"] == "source"]
        for spec in source_specs:
            # Descriptions should be natural language, not code
            assert "function" not in spec["description"]
            assert "import" not in spec["description"]
            assert len(spec["description"]) > 20

    def test_test_specs_describe_what_to_test(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        test_specs = [f for f in packet["file_specs"] if f["kind"] == "test"]
        for spec in test_specs:
            assert "test" in spec["description"].lower() or "vitest" in spec["description"].lower()

    def test_generic_source_specs(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(tmp_repo, task_intent, task_blueprint, "generic")
        source_specs = [f for f in packet["file_specs"] if f["kind"] == "source"]
        assert len(source_specs) > 0
        for spec in source_specs:
            assert "entity" in spec["description"].lower() or "module" in spec["description"].lower()

    def test_generic_test_specs(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(tmp_repo, task_intent, task_blueprint, "generic")
        test_specs = [f for f in packet["file_specs"] if f["kind"] == "test"]
        assert len(test_specs) > 0
        for spec in test_specs:
            assert "test" in spec["description"].lower()


# ---------------------------------------------------------------------------
# Reserved path rejection
# ---------------------------------------------------------------------------

class TestReservedPaths:
    def test_no_specs_in_signalos(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in packet["file_specs"]:
            normed = f["path"].replace("\\", "/")
            assert not normed.startswith(".signalos/"), (
                f"Spec in reserved .signalos/: {f['path']}"
            )

    def test_no_specs_in_node_modules(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in packet["file_specs"]:
            normed = f["path"].replace("\\", "/")
            assert not normed.startswith("node_modules/"), (
                f"Spec in reserved node_modules/: {f['path']}"
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
# validate_generation_output
# ---------------------------------------------------------------------------

class TestValidateGenerationOutput:
    def test_all_files_present_is_valid(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        # Simulate agent writing files
        for spec in packet["file_specs"]:
            path = tmp_repo / spec["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"// {spec['description']}\nexport default null;\n", encoding="utf-8")
        result = validate_generation_output(tmp_repo, packet)
        assert result["valid"] is True
        assert result["files_missing"] == []
        assert result["files_expected"] == len(packet["file_specs"])
        assert result["files_found"] == len(packet["file_specs"])

    def test_missing_files_reported(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        # Don't write any files -- all should be missing
        result = validate_generation_output(tmp_repo, packet)
        assert result["valid"] is False
        assert len(result["files_missing"]) == len(packet["file_specs"])
        assert result["files_found"] == 0

    def test_empty_file_is_violation(self, tmp_repo, task_intent, task_blueprint):
        packet = prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        # Write one empty file
        spec = packet["file_specs"][0]
        path = tmp_repo / spec["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        result = validate_generation_output(tmp_repo, packet)
        assert any("empty" in v.lower() for v in result["violations"])


# ---------------------------------------------------------------------------
# W4 trace linkage: acceptance matrix integration
# ---------------------------------------------------------------------------

class TestAcceptanceLinkage:
    """Tests for linking file specs to acceptance criteria."""

    @pytest.fixture
    def acceptance_matrix(self, task_intent, task_blueprint):
        return build_acceptance_matrix(task_intent, task_blueprint, "react-vite")

    def test_generation_with_acceptance_links_specs(
        self, tmp_repo, task_intent, task_blueprint, acceptance_matrix,
    ):
        """Generate with acceptance matrix -> specs have non-None acceptance_ids."""
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
            acceptance_matrix=acceptance_matrix,
        )
        linked = [f for f in packet["file_specs"] if f["acceptance_id"] is not None]
        assert len(linked) > 0, "No specs were linked to acceptance criteria"

    def test_link_generation_to_acceptance_matches_entities(
        self, tmp_repo, task_intent, task_blueprint, acceptance_matrix,
    ):
        """Entity-based matching links Task files to Task criteria."""
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        manifest = load_generation_manifest(tmp_repo / ".signalos")
        manifest = link_generation_to_acceptance(manifest, acceptance_matrix)
        # TaskList and TaskForm should be linked to the task entity criterion
        task_files = [
            f for f in manifest["files"]
            if "task" in f["path"].lower()
            and f["path"].endswith((".tsx", ".test.tsx"))
        ]
        assert len(task_files) > 0
        for f in task_files:
            assert f["acceptance_id"] is not None, (
                f"Task file {f['path']} not linked"
            )

    def test_link_generation_to_acceptance_matches_workflows(
        self, tmp_repo, task_intent, task_blueprint, acceptance_matrix,
    ):
        """Workflow-based matching works for files related to workflows."""
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        manifest = load_generation_manifest(tmp_repo / ".signalos")
        manifest = link_generation_to_acceptance(manifest, acceptance_matrix)
        # At minimum, task-related files should be linked
        linked = [f for f in manifest["files"] if f["acceptance_id"] is not None]
        assert len(linked) > 0

    def test_verify_trace_completeness_all_linked(
        self, tmp_repo, task_intent, task_blueprint,
    ):
        """When all files are linked and all criteria covered, complete=True."""
        small_matrix = {
            "criteria": [
                {"id": "AC-001", "entity": "tasks", "workflow": None},
                {"id": "AC-002", "entity": "projects", "workflow": None},
            ],
            "test_scenarios": [],
        }
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        manifest = load_generation_manifest(tmp_repo / ".signalos")
        # Assign every file to one of the two criteria (round-robin)
        cids = [c["id"] for c in small_matrix["criteria"]]
        for i, f in enumerate(manifest["files"]):
            f["acceptance_id"] = cids[i % len(cids)]
        result = verify_trace_completeness(manifest, small_matrix)
        assert result["complete"] is True
        assert result["unlinked_files"] == 0
        assert result["unlinked_paths"] == []

    def test_verify_trace_completeness_with_unlinked(
        self, tmp_repo, task_intent, task_blueprint, acceptance_matrix,
    ):
        """Some unlinked files returns complete=False with unlinked_paths."""
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        manifest = load_generation_manifest(tmp_repo / ".signalos")
        # Don't link anything - all files should be unlinked
        result = verify_trace_completeness(manifest, acceptance_matrix)
        assert result["complete"] is False
        assert result["unlinked_files"] > 0
        assert len(result["unlinked_paths"]) == result["unlinked_files"]

    def test_trace_with_task_ids(
        self, tmp_repo, task_intent, task_blueprint, acceptance_matrix,
    ):
        """When task_ids provided, specs get task_id assignments."""
        task_ids = ["T-001", "T-002"]
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
            task_ids=task_ids,
            acceptance_matrix=acceptance_matrix,
        )
        for f in packet["file_specs"]:
            assert f["task_id"] in task_ids, (
                f"Spec {f['path']} has unexpected task_id: {f['task_id']}"
            )

    def test_backward_compatible_no_matrix(
        self, tmp_repo, task_intent, task_blueprint,
    ):
        """Without acceptance_matrix, task_id and acceptance_id remain None."""
        packet = prepare_generation(
            tmp_repo, task_intent, task_blueprint, "react-vite",
        )
        for f in packet["file_specs"]:
            assert f["task_id"] is None
            assert f["acceptance_id"] is None

    def test_link_idempotent(
        self, tmp_repo, task_intent, task_blueprint, acceptance_matrix,
    ):
        """Calling link_generation_to_acceptance twice doesn't change results."""
        prepare_generation(tmp_repo, task_intent, task_blueprint, "react-vite")
        manifest = load_generation_manifest(tmp_repo / ".signalos")
        manifest = link_generation_to_acceptance(manifest, acceptance_matrix)
        first_pass = [f.get("acceptance_id") for f in manifest["files"]]
        manifest = link_generation_to_acceptance(manifest, acceptance_matrix)
        second_pass = [f.get("acceptance_id") for f in manifest["files"]]
        assert first_pass == second_pass


# ---------------------------------------------------------------------------
# PascalCase file name generation
# ---------------------------------------------------------------------------

class TestPascalCaseHelper:
    def test_spaces(self):
        assert _to_pascal_case("patient intake") == "PatientIntake"

    def test_hyphens(self):
        assert _to_pascal_case("revenue-chart") == "RevenueChart"

    def test_underscores(self):
        assert _to_pascal_case("lab_results") == "LabResults"

    def test_already_pascal(self):
        assert _to_pascal_case("Task") == "Task"

    def test_mixed_separators(self):
        assert _to_pascal_case("clinical notes") == "ClinicalNotes"

    def test_multiple_words(self):
        assert _to_pascal_case("my cool widget") == "MyCoolWidget"

    def test_illegal_path_characters_are_removed(self):
        assert (
            _to_pascal_case("Category;See Running Total")
            == "CategorySeeRunningTotal"
        )
        assert _to_pascal_case("9:bad/path?name*") == "BadPathName"


class TestPascalCaseFileNames:
    def test_entity_with_spaces_generates_pascal_paths(self, tmp_repo):
        intent = {
            "product_name": "Clinic",
            "product_type": "custom",
            "entities": ["patient intake", "clinical notes"],
            "primary_workflows": [],
            "ux_surfaces": [],
        }
        packet = prepare_generation(
            tmp_repo, intent, None, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/components/PatientIntake.tsx" in paths
        assert "src/components/PatientIntake.test.tsx" in paths
        assert "src/components/ClinicalNotes.tsx" in paths
        assert "src/components/ClinicalNotes.test.tsx" in paths
        # No spaces in any path
        for p in paths:
            assert " " not in p, f"Space found in generated path: {p}"

    def test_entity_patient_intake_not_spaced(self, tmp_repo):
        intent = {
            "product_name": "Clinic",
            "product_type": "custom",
            "entities": ["patient intake"],
            "primary_workflows": [],
            "ux_surfaces": [],
        }
        packet = prepare_generation(
            tmp_repo, intent, None, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/components/PatientIntake.tsx" in paths
        assert "src/components/Patient intake.tsx" not in paths

    def test_entity_with_illegal_path_chars_generates_safe_paths(self, tmp_repo):
        intent = {
            "product_name": "Expense Tracker",
            "product_type": "custom",
            "entities": ["Category;SeeRunningTotal"],
            "primary_workflows": [],
            "ux_surfaces": [],
        }
        packet = prepare_generation(
            tmp_repo, intent, None, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/components/CategorySeeRunningTotal.tsx" in paths
        assert "src/components/Category;SeeRunningTotal.tsx" not in paths
        for path in paths:
            assert ";" not in path
            assert ":" not in path
            assert "?" not in path
            assert "*" not in path


# ---------------------------------------------------------------------------
# Blueprint dependencies
# ---------------------------------------------------------------------------

class TestBlueprintDependencies:
    def test_financial_dashboard_includes_recharts(self):
        bp = {"id": "financial-dashboard", "entities": [],
              "ui": ["revenue-chart", "churn-chart", "runway-gauge"]}
        deps = get_blueprint_dependencies(bp, "react-vite")
        assert "recharts" in deps["dependencies"]
        assert deps["dependencies"]["recharts"] == "^2.12.0"

    def test_task_management_no_extra_deps(self):
        bp = {"id": "task-management", "entities": []}
        deps = get_blueprint_dependencies(bp, "react-vite")
        assert deps["dependencies"] == {}

    def test_generic_profile_returns_empty(self):
        bp = {"id": "financial-dashboard", "entities": []}
        deps = get_blueprint_dependencies(bp, "generic")
        assert deps["dependencies"] == {}
        assert deps["devDependencies"] == {}

    def test_none_blueprint_returns_empty(self):
        deps = get_blueprint_dependencies(None, "react-vite")
        assert deps["dependencies"] == {}
        assert deps["devDependencies"] == {}


# ---------------------------------------------------------------------------
# Level 8: data-driven generation (no hardcoded product types)
# ---------------------------------------------------------------------------

class TestDataDrivenGeneration:
    """Verify generation is purely blueprint-driven, not hardcoded."""

    def test_generation_is_blueprint_driven(self, tmp_repo):
        """A custom blueprint with unique components generates those exact
        component specs without any code change to generation.py."""
        custom_blueprint = {
            "id": "custom-crm",
            "display_name": "Custom CRM",
            "entities": [
                {"name": "Contact", "fields": ["id", "name", "email"]},
                {"name": "Deal", "fields": ["id", "title", "value"]},
            ],
            "workflows": [],
            "ui": [],
            "ui_detail": {
                "surfaces": [
                    {
                        "id": "contact-list",
                        "component": "ContactList",
                        "description": "List of contacts",
                        "data_bindings": ["GET /contacts"],
                        "layout": "main-content",
                    },
                    {
                        "id": "deal-pipeline",
                        "component": "DealPipeline",
                        "description": "Pipeline view for deals",
                        "data_bindings": ["GET /deals"],
                        "layout": "full-width",
                    },
                ]
            },
        }
        intent = {
            "product_name": "MyCRM",
            "product_type": "custom-crm",
            "entities": ["contact", "deal"],
            "primary_workflows": [],
            "ux_surfaces": [],
        }
        packet = prepare_generation(
            tmp_repo, intent, custom_blueprint, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/components/ContactList.tsx" in paths
        assert "src/components/ContactList.test.tsx" in paths
        assert "src/components/DealPipeline.tsx" in paths
        assert "src/components/DealPipeline.test.tsx" in paths
        # No files on disk (packet only)
        assert not (tmp_repo / "src/components/ContactList.tsx").is_file()

    def test_new_blueprint_without_code_change(self, tmp_repo):
        """A third blueprint (recipe-manager) generates correctly just from
        data, proving no product-type-specific code is needed."""
        recipe_blueprint = {
            "id": "recipe-manager",
            "display_name": "Recipe Manager",
            "entities": [
                {"name": "Recipe", "fields": ["id", "title", "servings"]},
                {"name": "Ingredient", "fields": ["id", "name", "unit"]},
            ],
            "workflows": [
                {"name": "create_recipe", "description": "Create a new recipe"},
            ],
            "ui": [],
            "ui_detail": {
                "surfaces": [
                    {
                        "id": "recipe-list",
                        "component": "RecipeList",
                        "description": "Browse all recipes",
                        "data_bindings": ["GET /recipes"],
                        "layout": "main-content",
                    },
                    {
                        "id": "recipe-detail",
                        "component": "RecipeDetail",
                        "description": "View a single recipe",
                        "data_bindings": ["GET /recipes/:id"],
                        "layout": "detail-panel",
                    },
                    {
                        "id": "ingredient-picker",
                        "component": "IngredientPicker",
                        "description": "Search and pick ingredients",
                        "data_bindings": ["GET /ingredients"],
                        "layout": "sidebar",
                    },
                ]
            },
        }
        intent = {
            "product_name": "RecipeApp",
            "product_type": "recipe-manager",
            "entities": ["recipe", "ingredient"],
            "primary_workflows": ["create recipe"],
            "ux_surfaces": ["list", "detail"],
        }
        packet = prepare_generation(
            tmp_repo, intent, recipe_blueprint, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]

        # All three components from the blueprint
        assert "src/components/RecipeList.tsx" in paths
        assert "src/components/RecipeList.test.tsx" in paths
        assert "src/components/RecipeDetail.tsx" in paths
        assert "src/components/RecipeDetail.test.tsx" in paths
        assert "src/components/IngredientPicker.tsx" in paths
        assert "src/components/IngredientPicker.test.tsx" in paths

        # Types spec includes blueprint entities
        assert "src/types.ts" in paths
        types_spec = next(
            f for f in packet["file_specs"] if f["path"] == "src/types.ts"
        )
        assert "Recipe" in types_spec["description"]
        assert "Ingredient" in types_spec["description"]

        # App.tsx spec references all components
        app_spec = next(
            f for f in packet["file_specs"] if f["path"] == "src/App.tsx"
        )
        assert "RecipeList" in app_spec["description"]
        assert "RecipeDetail" in app_spec["description"]
        assert "IngredientPicker" in app_spec["description"]

    def test_no_product_type_switch_in_generation(self):
        """generation.py must not contain if/elif checks on specific
        blueprint IDs for component selection."""
        gen_path = Path(__file__).resolve().parent / (
            "signalos_lib/product/generation.py"
        )
        source = gen_path.read_text(encoding="utf-8")

        import re

        hardcoded_patterns = [
            r'if\s+.*["\']task-management["\']',
            r'elif\s+.*["\']task-management["\']',
            r'if\s+.*["\']financial-dashboard["\']',
            r'elif\s+.*["\']financial-dashboard["\']',
        ]
        for pattern in hardcoded_patterns:
            matches = re.findall(pattern, source)
            real_matches = [
                m for m in matches
                if not m.strip().startswith("#")
            ]
            assert len(real_matches) == 0, (
                f"Found hardcoded blueprint ID check in generation.py: {real_matches}"
            )

    def test_blueprint_fallback_to_ui_ids(self, tmp_repo):
        """When ui_detail is absent, generation falls back to the ui list
        of surface IDs and converts them to PascalCase components."""
        minimal_blueprint = {
            "id": "minimal-app",
            "display_name": "Minimal",
            "entities": [
                {"name": "Widget", "fields": ["id", "name"]},
            ],
            "workflows": [],
            "ui": ["widget-list", "widget-form"],
        }
        intent = {
            "product_name": "MinimalApp",
            "product_type": "minimal-app",
            "entities": ["widget"],
            "primary_workflows": [],
            "ux_surfaces": [],
        }
        packet = prepare_generation(
            tmp_repo, intent, minimal_blueprint, "react-vite",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        assert "src/components/WidgetList.tsx" in paths
        assert "src/components/WidgetForm.tsx" in paths

    def test_generic_profile_blueprint_driven(self, tmp_repo):
        """Generic (Python) profile also works with arbitrary blueprints."""
        bp = {
            "id": "inventory-tracker",
            "display_name": "Inventory Tracker",
            "entities": [
                {"name": "Product", "fields": ["id", "sku", "name", "qty"]},
                {"name": "Warehouse", "fields": ["id", "location"]},
            ],
            "workflows": [],
            "ui": [],
        }
        intent = {
            "product_name": "InventoryApp",
            "product_type": "inventory-tracker",
            "entities": ["product", "warehouse"],
            "primary_workflows": [],
            "ux_surfaces": [],
        }
        packet = prepare_generation(
            tmp_repo, intent, bp, "generic",
        )
        paths = [f["path"] for f in packet["file_specs"]]
        # Python file specs for each entity
        assert any("product" in p.lower() and p.endswith(".py") for p in paths)
        assert any("warehouse" in p.lower() and p.endswith(".py") for p in paths)
