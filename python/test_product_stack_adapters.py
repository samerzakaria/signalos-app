"""Tests for the product stack adapter contract and implementations."""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from signalos_lib.product.stacks import (
    AgentSelectedAdapter,
    AngularAdapter,
    DjangoApiAdapter,
    DotNetMinimalApiAdapter,
    ExistingRepoAdapter,
    ExpoReactNativeAdapter,
    FastApiAdapter,
    FlaskApiAdapter,
    FlutterAppAdapter,
    GenericAdapter,
    GoApiAdapter,
    JavaApiAdapter,
    NestJsApiAdapter,
    NextJsAdapter,
    NodeApiAdapter,
    ReactViteAdapter,
    RustApiAdapter,
    SpringBootApiAdapter,
    StackAdapter,
    VueViteAdapter,
    adapter_has_greenfield_shell,
    detect_profile,
    get_adapter,
    list_adapters,
    stack_shell_present,
)


# ---------------------------------------------------------------------------
# ReactViteAdapter
# ---------------------------------------------------------------------------


class TestReactViteScaffold:
    def test_scaffold_creates_only_governance(self, tmp_path: Path) -> None:
        adapter = ReactViteAdapter()
        result = adapter.scaffold(tmp_path, {})

        # Governance + delivery infrastructure created
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / ".signalos" / "profile.json").is_file()
        assert "package.json" in result["created"]
        # EROFS fix: the shipped Vite config MUST be `.cjs` (CommonJS, loaded via
        # require) -- a `.ts`/`.js`/`.mjs` config makes Vite esbuild-bundle it and
        # write a `*.timestamp-*.mjs` sidecar at the workspace root, which throws
        # EROFS on the funded read-only mount so the graded `npm test` can't load
        # the config for any model. Never regress to a sidecar-triggering config.
        assert "vite.config.cjs" in result["created"]
        assert "vite.config.ts" not in result["created"]

    def test_scaffold_creates_infrastructure_files(self, tmp_path: Path) -> None:
        result = ReactViteAdapter().scaffold(tmp_path, {})

        # Delivery infrastructure files written to disk
        assert (tmp_path / "package.json").is_file()
        # EROFS fix: `.cjs` config (no esbuild timestamp sidecar) loads under the
        # read-only funded workspace mount; a `.ts` config does not.
        cfg = tmp_path / "vite.config.cjs"
        assert cfg.is_file()
        assert not (tmp_path / "vite.config.ts").is_file()
        cfg_text = cfg.read_text(encoding="utf-8")
        assert "module.exports" in cfg_text  # CommonJS, require-loaded, no sidecar
        assert "export default" not in cfg_text
        assert (tmp_path / "index.html").is_file()
        assert (tmp_path / "src" / "main.tsx").is_file()
        assert (tmp_path / "src" / "App.tsx").is_file()
        assert (tmp_path / "src" / "App.test.tsx").is_file()
        assert (tmp_path / "tsconfig.json").is_file()

    def test_scaffold_package_json_has_deps(self, tmp_path: Path) -> None:
        ReactViteAdapter().scaffold(tmp_path, {})
        pkg = json.loads((tmp_path / "package.json").read_text())

        assert "react" in pkg["dependencies"]
        assert "react-dom" in pkg["dependencies"]
        assert "react-router-dom" in pkg["dependencies"]

        assert "vite" in pkg["devDependencies"]
        assert "vitest" in pkg["devDependencies"]
        assert "typescript" in pkg["devDependencies"]
        assert "@testing-library/react" in pkg["devDependencies"]
        assert "@testing-library/dom" in pkg["devDependencies"]
        assert "@testing-library/jest-dom" in pkg["devDependencies"]
        assert "jsdom" in pkg["devDependencies"]

    def test_scaffold_package_json_has_scripts(self, tmp_path: Path) -> None:
        ReactViteAdapter().scaffold(tmp_path, {})
        pkg = json.loads((tmp_path / "package.json").read_text())

        assert "dev" in pkg["scripts"]
        assert "build" in pkg["scripts"]
        assert "test" in pkg["scripts"]

    def test_scaffold_accepts_extra_dependencies(self, tmp_path: Path) -> None:
        extra = {"recharts": "^2.12.0"}
        ReactViteAdapter().scaffold(tmp_path, {}, dependencies=extra)
        pkg = json.loads((tmp_path / "package.json").read_text())
        assert "recharts" in pkg["dependencies"]
        assert pkg["dependencies"]["recharts"] == "^2.12.0"
        # Baseline deps still present
        assert "react" in pkg["dependencies"]

    def test_scaffold_reports_runnable(self, tmp_path: Path) -> None:
        result = ReactViteAdapter().scaffold(tmp_path, {})
        assert result["can_deliver_ui"] is True
        assert result["can_deliver_runnable"] is True


class TestReactViteValidation:
    def test_validation_plan_has_install_build_test(self, tmp_path: Path) -> None:
        plan = ReactViteAdapter().validation_plan(tmp_path)
        assert plan["install"] == ["npm install --legacy-peer-deps"]
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
    def test_scaffold_creates_runnable_python_package(self, tmp_path: Path) -> None:
        result = GenericAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert "pyproject.toml" in result["created"]
        assert (tmp_path / "pyproject.toml").is_file()
        assert (tmp_path / "src").is_dir()
        assert (tmp_path / "tests" / "__init__.py").is_file()
        assert not (tmp_path / "package.json").exists()

    def test_claims_runnable_non_ui(self, tmp_path: Path) -> None:
        info = GenericAdapter().detect(tmp_path)
        assert info["can_deliver_ui"] is False
        assert info["can_deliver_runnable"] is True

    def test_scaffold_reports_runnable(self, tmp_path: Path) -> None:
        result = GenericAdapter().scaffold(tmp_path, {})
        assert result["can_deliver_ui"] is False
        assert result["can_deliver_runnable"] is True

    def test_validation_plan_runs_python_build_and_test(self, tmp_path: Path) -> None:
        plan = GenericAdapter().validation_plan(tmp_path)
        assert any("compileall" in command for command in plan["build"])
        assert any("unittest discover" in command for command in plan["test"])

    def test_preview_plan_is_null(self, tmp_path: Path) -> None:
        plan = GenericAdapter().preview_plan(tmp_path)
        assert plan["command"] is None


# ---------------------------------------------------------------------------
# NodeApiAdapter
# ---------------------------------------------------------------------------


class TestNodeApiAdapter:
    def test_scaffold_creates_runnable_node_api(self, tmp_path: Path) -> None:
        result = NodeApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert "package.json" in result["created"]
        assert (tmp_path / "src" / "app.js").is_file()
        assert (tmp_path / "src" / "server.js").is_file()
        assert (tmp_path / "tests" / "health.test.js").is_file()

    def test_validation_plan_has_install_build_test(self, tmp_path: Path) -> None:
        plan = NodeApiAdapter().validation_plan(tmp_path)
        assert plan["install"] == ["npm install --legacy-peer-deps"]
        assert "node --check src/app.js" in plan["build"]
        assert plan["test"] == ["npm test"]

    def test_preview_plan_uses_health_endpoint(self, tmp_path: Path) -> None:
        plan = NodeApiAdapter().preview_plan(tmp_path)
        assert plan["command"] == "npm start"
        assert plan["port"] == 3000
        assert plan["health_path"] == "/health"


# ---------------------------------------------------------------------------
# FastApiAdapter
# ---------------------------------------------------------------------------


class TestFastApiAdapter:
    def test_scaffold_creates_runnable_fastapi_api(self, tmp_path: Path) -> None:
        result = FastApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert "pyproject.toml" in result["created"]
        assert (tmp_path / "src" / "signalos_product_fastapi" / "app.py").is_file()
        assert (tmp_path / "src" / "signalos_product_fastapi" / "main.py").is_file()
        assert (tmp_path / "tests" / "test_health.py").is_file()

    def test_scaffold_writes_parseable_pyproject(self, tmp_path: Path) -> None:
        FastApiAdapter().scaffold(tmp_path, {})
        pyproject = tomllib.loads(
            (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        )
        assert pyproject["project"]["name"] == "signalos-fastapi-product"
        assert pyproject["tool"]["setuptools"]["package-dir"][""] == "src"

    def test_validation_plan_has_install_build_test(self, tmp_path: Path) -> None:
        plan = FastApiAdapter().validation_plan(tmp_path)
        assert 'python -m pip install -e ".[dev]"' in plan["install"]
        assert "python -m compileall src tests" in plan["build"]
        assert plan["test"] == ["python -m pytest"]

    def test_preview_plan_uses_health_endpoint(self, tmp_path: Path) -> None:
        plan = FastApiAdapter().preview_plan(tmp_path)
        assert "uvicorn signalos_product_fastapi.app:app" in plan["command"]
        assert plan["port"] == 8000
        assert plan["health_path"] == "/health"

    def test_detect_finds_pyproject_fastapi(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["fastapi", "uvicorn"]\n',
            encoding="utf-8",
        )
        info = FastApiAdapter().detect(tmp_path)
        assert "pyproject.toml" in info["signals"]
        assert "fastapi-dep" in info["signals"]
        assert info["can_deliver_runnable"] is True


# ---------------------------------------------------------------------------
# Popular/common adapters
# ---------------------------------------------------------------------------


class TestAngularAdapter:
    def test_scaffold_creates_runnable_angular_shell(self, tmp_path: Path) -> None:
        result = AngularAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "package.json").is_file()
        assert (tmp_path / "angular.json").is_file()
        assert (tmp_path / "src" / "app" / "app.component.ts").is_file()
        assert (tmp_path / "src" / "app" / "app.component.spec.ts").is_file()
        assert result["can_deliver_ui"] is True

    def test_validation_plan_has_angular_commands(self, tmp_path: Path) -> None:
        plan = AngularAdapter().validation_plan(tmp_path)
        assert plan["install"] == ["npm install --legacy-peer-deps"]
        assert plan["build"] == ["npm run build"]
        assert plan["test"] == ["npm test"]

    def test_detect_finds_angular_project(self, tmp_path: Path) -> None:
        AngularAdapter().scaffold(tmp_path, {})
        info = AngularAdapter().detect(tmp_path)
        assert "angular-core-dep" in info["signals"]
        assert "angular.json" in info["signals"]
        assert detect_profile(tmp_path) == "angular"


class TestNextJsAdapter:
    def test_scaffold_creates_runnable_nextjs_shell(self, tmp_path: Path) -> None:
        result = NextJsAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "package.json").is_file()
        assert (tmp_path / "app" / "page.tsx").is_file()
        assert (tmp_path / "app" / "page.test.tsx").is_file()
        assert result["can_deliver_ui"] is True

    def test_detect_finds_next_project_before_react(self, tmp_path: Path) -> None:
        NextJsAdapter().scaffold(tmp_path, {})
        info = NextJsAdapter().detect(tmp_path)
        assert "next-dep" in info["signals"]
        assert detect_profile(tmp_path) == "nextjs-app"


class TestVueViteAdapter:
    def test_scaffold_creates_runnable_vue_shell(self, tmp_path: Path) -> None:
        result = VueViteAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "package.json").is_file()
        assert (tmp_path / "src" / "App.vue").is_file()
        assert (tmp_path / "src" / "App.test.ts").is_file()
        assert result["can_deliver_ui"] is True

    def test_detect_finds_vue_project_before_plain_vite(self, tmp_path: Path) -> None:
        VueViteAdapter().scaffold(tmp_path, {})
        info = VueViteAdapter().detect(tmp_path)
        assert "vue-dep" in info["signals"]
        assert detect_profile(tmp_path) == "vue-vite"


class TestFlutterAppAdapter:
    def test_scaffold_creates_runnable_flutter_shell(self, tmp_path: Path) -> None:
        result = FlutterAppAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "pubspec.yaml").is_file()
        assert (tmp_path / "lib" / "main.dart").is_file()
        assert (tmp_path / "test" / "widget_test.dart").is_file()
        assert result["can_deliver_ui"] is True

    def test_detect_finds_flutter_project(self, tmp_path: Path) -> None:
        FlutterAppAdapter().scaffold(tmp_path, {})
        info = FlutterAppAdapter().detect(tmp_path)
        assert "flutter-dep" in info["signals"]
        assert detect_profile(tmp_path) == "flutter-app"


class TestExpoReactNativeAdapter:
    def test_scaffold_creates_runnable_expo_shell(self, tmp_path: Path) -> None:
        result = ExpoReactNativeAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "package.json").is_file()
        assert (tmp_path / "app.json").is_file()
        assert (tmp_path / "App.js").is_file()
        assert (tmp_path / "tests" / "productState.test.js").is_file()
        assert result["can_deliver_ui"] is True

    def test_detect_finds_expo_project(self, tmp_path: Path) -> None:
        ExpoReactNativeAdapter().scaffold(tmp_path, {})
        info = ExpoReactNativeAdapter().detect(tmp_path)
        assert "expo-dep" in info["signals"]
        assert "react-native-dep" in info["signals"]
        assert detect_profile(tmp_path) == "expo-react-native"


class TestDjangoApiAdapter:
    def test_scaffold_creates_runnable_django_api_shell(self, tmp_path: Path) -> None:
        result = DjangoApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "manage.py").is_file()
        assert (tmp_path / "src" / "signalos_product_django" / "settings.py").is_file()
        assert (tmp_path / "src" / "signalos_product_django" / "urls.py").is_file()
        assert (tmp_path / "tests" / "test_health.py").is_file()

    def test_detect_finds_django_pyproject(self, tmp_path: Path) -> None:
        DjangoApiAdapter().scaffold(tmp_path, {})
        info = DjangoApiAdapter().detect(tmp_path)
        assert "django-dep" in info["signals"]
        assert "django-settings" in info["signals"]
        assert detect_profile(tmp_path) == "django-api"


class TestFlaskApiAdapter:
    def test_scaffold_creates_runnable_flask_api_shell(self, tmp_path: Path) -> None:
        result = FlaskApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "pyproject.toml").is_file()
        assert (tmp_path / "src" / "signalos_product_flask" / "app.py").is_file()
        assert (tmp_path / "tests" / "test_health.py").is_file()

    def test_detect_finds_flask_pyproject(self, tmp_path: Path) -> None:
        FlaskApiAdapter().scaffold(tmp_path, {})
        info = FlaskApiAdapter().detect(tmp_path)
        assert "flask-dep" in info["signals"]
        assert detect_profile(tmp_path) == "flask-api"


class TestNestJsApiAdapter:
    def test_scaffold_creates_runnable_nestjs_api_shell(self, tmp_path: Path) -> None:
        result = NestJsApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "package.json").is_file()
        assert (tmp_path / "src" / "app.module.ts").is_file()
        assert (tmp_path / "src" / "app.controller.spec.ts").is_file()

    def test_detect_finds_nestjs_project(self, tmp_path: Path) -> None:
        NestJsApiAdapter().scaffold(tmp_path, {})
        info = NestJsApiAdapter().detect(tmp_path)
        assert "nestjs-core-dep" in info["signals"]
        assert detect_profile(tmp_path) == "nestjs-api"


class TestSpringBootApiAdapter:
    def test_scaffold_creates_spring_boot_api_shell(self, tmp_path: Path) -> None:
        result = SpringBootApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "pom.xml").is_file()
        assert (tmp_path / "src" / "main" / "java" / "com" / "signalos" / "product" / "ProductApplication.java").is_file()
        assert (tmp_path / "src" / "test" / "java" / "com" / "signalos" / "product" / "HealthControllerTest.java").is_file()

    def test_detect_finds_spring_boot_before_plain_java(self, tmp_path: Path) -> None:
        SpringBootApiAdapter().scaffold(tmp_path, {})
        info = SpringBootApiAdapter().detect(tmp_path)
        assert "spring-boot-pom" in info["signals"]
        assert detect_profile(tmp_path) == "spring-boot-api"

    def test_returns_flutter_for_pubspec(self, tmp_path: Path) -> None:
        (tmp_path / "pubspec.yaml").write_text(
            "dependencies:\n  flutter:\n    sdk: flutter\n",
            encoding="utf-8",
        )
        assert detect_profile(tmp_path) == "flutter-app"

    def test_returns_expo_for_react_native_package(self, tmp_path: Path) -> None:
        pkg = {"dependencies": {"expo": "^52", "react-native": "^0.76"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        assert detect_profile(tmp_path) == "expo-react-native"


class TestJavaApiAdapter:
    def test_scaffold_creates_java_api_shell(self, tmp_path: Path) -> None:
        result = JavaApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "src" / "main" / "java" / "com" / "signalos" / "product" / "ProductServer.java").is_file()
        assert (tmp_path / "src" / "test" / "java" / "com" / "signalos" / "product" / "ProductServerTest.java").is_file()
        assert result["can_deliver_runnable"] is True

    def test_validation_plan_uses_javac_without_hidden_generator(self, tmp_path: Path) -> None:
        plan = JavaApiAdapter().validation_plan(tmp_path)
        assert any(command.startswith("javac -d build/classes") for command in plan["build"])
        assert "java -cp build/classes com.signalos.product.ProductServerTest" in plan["test"]


class TestRustApiAdapter:
    def test_scaffold_creates_rust_api_shell(self, tmp_path: Path) -> None:
        result = RustApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "Cargo.toml").is_file()
        assert (tmp_path / "src" / "lib.rs").is_file()
        assert (tmp_path / "src" / "main.rs").is_file()
        assert result["can_deliver_runnable"] is True

    def test_detect_finds_rust_api_project(self, tmp_path: Path) -> None:
        RustApiAdapter().scaffold(tmp_path, {})
        info = RustApiAdapter().detect(tmp_path)
        assert "Cargo.toml" in info["signals"]
        assert "rust-main" in info["signals"]
        assert detect_profile(tmp_path) == "rust-api"


# ---------------------------------------------------------------------------
# GoApiAdapter
# ---------------------------------------------------------------------------


class TestGoApiAdapter:
    def test_scaffold_creates_runnable_go_api(self, tmp_path: Path) -> None:
        result = GoApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert "go.mod" in result["created"]
        assert (tmp_path / "cmd" / "server" / "main.go").is_file()
        assert (tmp_path / "internal" / "app" / "app.go").is_file()
        assert (tmp_path / "internal" / "app" / "app_test.go").is_file()
        assert (tmp_path / "tests" / "acceptance-map.md").is_file()

        profile = json.loads((tmp_path / ".signalos" / "profile.json").read_text())
        assert profile["profile"] == "go-api"
        assert "not the default" in profile["technology_policy"]

    def test_validation_plan_has_go_test(self, tmp_path: Path) -> None:
        plan = GoApiAdapter().validation_plan(tmp_path)
        assert plan["build"] == ["go test ./..."]
        assert plan["test"] == ["go test ./..."]

    def test_preview_plan_uses_health_endpoint(self, tmp_path: Path) -> None:
        plan = GoApiAdapter().preview_plan(tmp_path)
        assert plan["command"] == "go run ./cmd/server"
        assert plan["port"] == 8080
        assert plan["health_path"] == "/health"

    def test_detect_finds_go_api_signals(self, tmp_path: Path) -> None:
        GoApiAdapter().scaffold(tmp_path, {})
        info = GoApiAdapter().detect(tmp_path)
        assert "go.mod" in info["signals"]
        assert "server-entry" in info["signals"]
        assert "app-handler" in info["signals"]
        assert "go-tests" in info["signals"]
        assert info["can_deliver_runnable"] is True


# ---------------------------------------------------------------------------
# DotNetMinimalApiAdapter
# ---------------------------------------------------------------------------


class TestDotNetMinimalApiAdapter:
    def test_scaffold_creates_runnable_dotnet_minimal_api(self, tmp_path: Path) -> None:
        result = DotNetMinimalApiAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert "SignalOSProduct.Api/SignalOSProduct.Api.csproj" in result["created"]
        assert (tmp_path / "SignalOSProduct.Api" / "Program.cs").is_file()
        assert (tmp_path / "SignalOSProduct.Api" / "ProductRoutes.cs").is_file()
        assert (tmp_path / "tests" / "acceptance-map.md").is_file()

        profile = json.loads((tmp_path / ".signalos" / "profile.json").read_text())
        assert profile["profile"] == "dotnet-minimal-api"
        assert "not ABP-locked" in profile["technology_policy"]

    def test_validation_plan_has_restore_build_self_test(self, tmp_path: Path) -> None:
        plan = DotNetMinimalApiAdapter().validation_plan(tmp_path)
        project = "SignalOSProduct.Api/SignalOSProduct.Api.csproj"
        assert plan["install"] == [f"dotnet restore {project}"]
        assert plan["build"] == [f"dotnet build {project} --no-restore"]
        assert plan["test"] == [f"dotnet run --project {project} --no-build -- --self-test"]

    def test_preview_plan_uses_health_endpoint(self, tmp_path: Path) -> None:
        plan = DotNetMinimalApiAdapter().preview_plan(tmp_path)
        assert plan["command"].startswith("dotnet run --project")
        assert plan["port"] == 5050
        assert plan["health_path"] == "/health"

    def test_detect_finds_csproj(self, tmp_path: Path) -> None:
        DotNetMinimalApiAdapter().scaffold(tmp_path, {})
        info = DotNetMinimalApiAdapter().detect(tmp_path)
        assert "csproj" in info["signals"]
        assert "minimal-api-program" in info["signals"]
        assert info["can_deliver_runnable"] is True

    def test_scaffold_builds_and_self_tests_with_installed_dotnet(self, tmp_path: Path) -> None:
        if shutil.which("dotnet") is None:
            pytest.skip("dotnet CLI is not installed")

        DotNetMinimalApiAdapter().scaffold(tmp_path, {})
        project = "SignalOSProduct.Api/SignalOSProduct.Api.csproj"
        commands = [
            ["dotnet", "restore", project],
            ["dotnet", "build", project, "--no-restore"],
            ["dotnet", "run", "--project", project, "--no-build", "--", "--self-test"],
        ]

        for command in commands:
            result = subprocess.run(
                command,
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# AgentSelectedAdapter
# ---------------------------------------------------------------------------


class TestAgentSelectedAdapter:
    def test_scaffold_creates_stack_decision_stub(self, tmp_path: Path) -> None:
        result = AgentSelectedAdapter().scaffold(tmp_path, {})
        assert ".signalos/profile.json" in result["created"]
        assert (tmp_path / "PRODUCT_STACK.md").is_file()
        assert (tmp_path / "src").is_dir()
        assert (tmp_path / "tests").is_dir()

    def test_validation_delegates_to_detected_repo(self, tmp_path: Path) -> None:
        pkg = {"scripts": {"build": "echo ok", "test": "echo ok"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        plan = AgentSelectedAdapter().validation_plan(tmp_path)
        assert plan["build"] == ["npm run build"]
        assert plan["test"] == ["npm test"]


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
        assert "rust-api" in info["detected_stacks"]

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
        assert plan["install"] == ["npm install --legacy-peer-deps"]
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
        assert detect_profile(tmp_path) == "node-api"

    def test_returns_fastapi_for_fastapi_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["fastapi"]\n',
            encoding="utf-8",
        )
        assert detect_profile(tmp_path) == "fastapi-api"

    def test_returns_dotnet_for_csproj(self, tmp_path: Path) -> None:
        (tmp_path / "SignalOSProduct.Api").mkdir()
        (tmp_path / "SignalOSProduct.Api" / "SignalOSProduct.Api.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk.Web" />',
            encoding="utf-8",
        )
        assert detect_profile(tmp_path) == "dotnet-minimal-api"

    def test_returns_go_api_for_go_server_repo(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/product\n", encoding="utf-8")
        (tmp_path / "cmd" / "server").mkdir(parents=True)
        (tmp_path / "cmd" / "server" / "main.go").write_text("package main\n", encoding="utf-8")
        assert detect_profile(tmp_path) == "go-api"

    def test_returns_java_api_for_java_source_repo(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").write_text("<project />", encoding="utf-8")
        (tmp_path / "src" / "main" / "java").mkdir(parents=True)
        assert detect_profile(tmp_path) == "java-api"

    def test_returns_spring_boot_for_spring_boot_pom(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").write_text(
            "<project><artifactId>spring-boot-starter-web</artifactId></project>",
            encoding="utf-8",
        )
        (tmp_path / "src" / "main" / "java").mkdir(parents=True)
        assert detect_profile(tmp_path) == "spring-boot-api"


class TestDetectProfileHonorsSelection:
    """FIX 1: a founder's EXPLICIT stack selection in ``.signalos/profile.json``
    wins over on-disk inference, so a greenfield repo whose shell has not been
    materialized yet is still built with the chosen stack (not 'generic')."""

    def _write_profile_json(self, root: Path, data: dict) -> None:
        (root / ".signalos").mkdir(parents=True, exist_ok=True)
        (root / ".signalos" / "profile.json").write_text(
            json.dumps(data), encoding="utf-8")

    def test_profile_key_honored_without_markers(self, tmp_path: Path) -> None:
        # {"profile": "react-vite"} + NO package.json -> react-vite (not generic)
        self._write_profile_json(tmp_path, {"profile": "react-vite"})
        assert detect_profile(tmp_path) == "react-vite"

    def test_profile_id_key_honored_without_markers(self, tmp_path: Path) -> None:
        # init.py schema {"profile_id": "react-vite"} -> same
        self._write_profile_json(tmp_path, {"profile_id": "react-vite"})
        assert detect_profile(tmp_path) == "react-vite"

    def test_selection_wins_over_conflicting_markers(self, tmp_path: Path) -> None:
        self._write_profile_json(tmp_path, {"profile": "vue-vite"})
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"vite": "^5"}}), encoding="utf-8")
        assert detect_profile(tmp_path) == "vue-vite"

    def test_marker_fallback_intact_when_no_profile_json(self, tmp_path: Path) -> None:
        # No profile.json + package.json with vite -> react-vite (unchanged fallback)
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"vite": "^5"}}), encoding="utf-8")
        assert detect_profile(tmp_path) == "react-vite"

    def test_garbage_profile_falls_back_to_markers(self, tmp_path: Path) -> None:
        self._write_profile_json(tmp_path, {"profile": "not-a-real-stack"})
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"express": "^4"}}), encoding="utf-8")
        assert detect_profile(tmp_path) == "node-api"

    def test_garbage_profile_and_no_markers_is_generic(self, tmp_path: Path) -> None:
        self._write_profile_json(tmp_path, {"profile": "not-a-real-stack"})
        assert detect_profile(tmp_path) == "generic"

    def test_malformed_profile_json_falls_back(self, tmp_path: Path) -> None:
        (tmp_path / ".signalos").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".signalos" / "profile.json").write_text("{ not json", encoding="utf-8")
        assert detect_profile(tmp_path) == "generic"

    def test_non_dict_profile_json_falls_back(self, tmp_path: Path) -> None:
        (tmp_path / ".signalos").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".signalos" / "profile.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert detect_profile(tmp_path) == "generic"


class TestGreenfieldShellHelpers:
    """FIX 2 support: the helpers the scaffold-first step relies on to stay a
    strict no-op on an already-scaffolded repo."""

    def test_shell_present_true_for_package_json(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"vite": "^5"}}), encoding="utf-8")
        assert stack_shell_present(tmp_path) is True

    def test_shell_present_false_for_empty_repo(self, tmp_path: Path) -> None:
        assert stack_shell_present(tmp_path) is False

    def test_shell_present_ignores_profile_json(self, tmp_path: Path) -> None:
        # profile.json alone is NOT a materialized shell.
        (tmp_path / ".signalos").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".signalos" / "profile.json").write_text(
            json.dumps({"profile": "react-vite"}), encoding="utf-8")
        assert stack_shell_present(tmp_path) is False

    def test_greenfield_shell_membership(self) -> None:
        assert adapter_has_greenfield_shell("react-vite") is True
        assert adapter_has_greenfield_shell("nextjs-app") is True
        assert adapter_has_greenfield_shell("generic") is False
        assert adapter_has_greenfield_shell("existing-repo") is False
        assert adapter_has_greenfield_shell("agent-selected") is False
        assert adapter_has_greenfield_shell("not-a-real-stack") is False

    def test_scaffold_is_idempotent_and_shell_present_guards_rescaffold(
        self, tmp_path: Path
    ) -> None:
        # FAIRNESS FIX (scaffold-first at the G4 boundary): the stack shell must
        # be safe to materialize even when the files already exist, so the
        # scaffold-first step can GUARANTEE the shell is present idempotently
        # (no first-run-vs-resumed special case). Calling scaffold twice must
        # not raise and must leave a byte-identical shell; and after the first
        # call ``stack_shell_present`` is True, so the scaffold-first guard turns
        # any second materialize into a strict no-op.
        adapter = get_adapter("react-vite")
        first = adapter.scaffold(tmp_path, {})
        pkg_after_first = (tmp_path / "package.json").read_text(encoding="utf-8")
        # the idempotency guard the orchestrator reads now short-circuits a re-scaffold
        assert stack_shell_present(tmp_path) is True
        second = adapter.scaffold(tmp_path, {})  # safe to call again
        assert first["created"] == second["created"]
        assert (tmp_path / "package.json").read_text(encoding="utf-8") == pkg_after_first
        assert (tmp_path / "vite.config.cjs").is_file()


class TestGetAdapter:
    def test_returns_correct_adapter_by_id(self) -> None:
        assert isinstance(get_adapter("react-vite"), ReactViteAdapter)
        assert isinstance(get_adapter("nextjs-app"), NextJsAdapter)
        assert isinstance(get_adapter("vue-vite"), VueViteAdapter)
        assert isinstance(get_adapter("flutter-app"), FlutterAppAdapter)
        assert isinstance(get_adapter("expo-react-native"), ExpoReactNativeAdapter)
        assert isinstance(get_adapter("node-api"), NodeApiAdapter)
        assert isinstance(get_adapter("nestjs-api"), NestJsApiAdapter)
        assert isinstance(get_adapter("go-api"), GoApiAdapter)
        assert isinstance(get_adapter("dotnet-minimal-api"), DotNetMinimalApiAdapter)
        assert isinstance(get_adapter("fastapi-api"), FastApiAdapter)
        assert isinstance(get_adapter("django-api"), DjangoApiAdapter)
        assert isinstance(get_adapter("flask-api"), FlaskApiAdapter)
        assert isinstance(get_adapter("angular"), AngularAdapter)
        assert isinstance(get_adapter("spring-boot-api"), SpringBootApiAdapter)
        assert isinstance(get_adapter("java-api"), JavaApiAdapter)
        assert isinstance(get_adapter("rust-api"), RustApiAdapter)
        assert isinstance(get_adapter("agent-selected"), AgentSelectedAdapter)
        assert isinstance(get_adapter("generic"), GenericAdapter)
        assert isinstance(get_adapter("existing-repo"), ExistingRepoAdapter)

    def test_raises_for_unknown_id(self) -> None:
        with pytest.raises(KeyError):
            get_adapter("nonexistent")


class TestListAdapters:
    def test_returns_all_three(self) -> None:
        adapters = list_adapters()
        ids = {a["id"] for a in adapters}
        assert ids == {
            "react-vite",
            "nextjs-app",
            "vue-vite",
            "flutter-app",
            "expo-react-native",
            "node-api",
            "nestjs-api",
            "go-api",
            "dotnet-minimal-api",
            "fastapi-api",
            "django-api",
            "flask-api",
            "angular",
            "spring-boot-api",
            "java-api",
            "rust-api",
            "agent-selected",
            "generic",
            "existing-repo",
        }

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
