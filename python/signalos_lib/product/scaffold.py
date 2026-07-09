"""Scaffold orchestrator for SignalOS product delivery.

Composes existing modules (lifecycle, intent, blueprints, stacks) to
run the full scaffold phase: detect mode, init repo, extract intent,
match blueprint, run adapter scaffold, validate results.
"""

from __future__ import annotations

__all__ = [
    "explain_profile_selection",
    "run_postflight",
    "run_scaffold",
]

import re
from pathlib import Path
from typing import Any

from . import stacks
from .blueprints import registry as blueprint_registry
from . import intent as intent_mod
from . import lifecycle


# ---------------------------------------------------------------------------
# Profile explanation
# ---------------------------------------------------------------------------

_PROFILE_EXPLANATIONS: dict[str, str] = {
    "react-vite": (
        "React + Vite detected: the repository contains a Vite dependency "
        "in package.json, indicating a modern React single-page application."
    ),
    "node-api": (
        "Node.js API selected: the product intent or repository markers "
        "indicate an API/backend service that can be delivered without a UI "
        "stack or .NET lock-in."
    ),
    "fastapi-api": (
        "FastAPI API selected: the product intent or repository markers "
        "indicate a Python API/backend service that can be delivered without "
        "a UI stack or .NET lock-in."
    ),
    "go-api": (
        "Go API selected: the user or repository explicitly requested Go API "
        "delivery. SignalOS will use Go as the product technology without "
        "making it the default."
    ),
    "dotnet-minimal-api": (
        ".NET Minimal API selected: the user or repository explicitly requested "
        ".NET/C# API delivery. SignalOS will use .NET as the product technology "
        "without forcing ABP or making .NET the default."
    ),
    "agent-selected": (
        "Agent-selected technology selected: the user or agent requested a "
        "technology without a native greenfield shell. SignalOS will keep the "
        "delivery governed and require the produced repo's own build/test "
        "commands before ready closeout."
    ),
    "existing-repo": (
        "Existing repository detected: the directory contains recognised "
        "project markers but does not match a more specific profile."
    ),
    "generic": (
        "Generic profile selected: no recognised project markers were found. "
        "SignalOS will create a runnable stdlib Python product scaffold unless "
        "the product intent requires a dedicated UI adapter."
    ),
}


def explain_profile_selection(profile: str, detected: bool) -> str:
    """Return a human-readable explanation of why this profile was selected.

    Used when ``--profile auto`` is specified.
    """
    if not detected:
        return f"Profile '{profile}' was explicitly specified by the user."
    return _PROFILE_EXPLANATIONS.get(
        profile,
        f"Profile '{profile}' was auto-detected from repository contents.",
    )


# ---------------------------------------------------------------------------
# Postflight validation
# ---------------------------------------------------------------------------

def run_postflight(repo_root: Path, profile: str) -> dict[str, Any]:
    """Validate scaffold completeness for the given profile.

    For react-vite: check package.json exists, has name field, has
    dependencies, src/ exists, at least one .tsx file exists.
    For generic: check runnable Python package scaffold exists.
    For existing-repo: check original files preserved.

    Returns ``{"passed": bool, "checks": [...]}``.
    """
    checks: list[dict[str, Any]] = []

    if profile == "react-vite":
        checks.extend(_postflight_react_vite(repo_root))
    elif profile == "nextjs-app":
        checks.extend(_postflight_package_stack(repo_root, "next", [
            "app/layout.tsx",
            "app/page.tsx",
            "app/page.test.tsx",
            "next.config.mjs",
            ".signalos/profile.json",
        ]))
    elif profile == "vue-vite":
        checks.extend(_postflight_package_stack(repo_root, "vue", [
            "src/App.vue",
            "src/App.test.ts",
            "src/main.ts",
            "vite.config.ts",
            ".signalos/profile.json",
        ]))
    elif profile == "flutter-app":
        checks.extend(_postflight_text_stack(repo_root, "pubspec.yaml", "flutter", [
            "pubspec.yaml",
            "lib/main.dart",
            "test/widget_test.dart",
            ".signalos/profile.json",
        ]))
    elif profile == "expo-react-native":
        checks.extend(_postflight_package_stack(repo_root, "expo", [
            "app.json",
            "App.js",
            "src/productState.js",
            "tests/productState.test.js",
            ".signalos/profile.json",
        ]))
    elif profile == "angular":
        checks.extend(_postflight_package_stack(repo_root, "@angular/core", [
            "angular.json",
            "src/app/app.component.ts",
            "src/app/app.component.spec.ts",
            ".signalos/profile.json",
        ]))
    elif profile == "node-api":
        checks.extend(_postflight_node_api(repo_root))
    elif profile == "nestjs-api":
        checks.extend(_postflight_package_stack(repo_root, "@nestjs/core", [
            "src/main.ts",
            "src/app.module.ts",
            "src/app.controller.ts",
            "src/app.controller.spec.ts",
            ".signalos/profile.json",
        ]))
    elif profile == "fastapi-api":
        checks.extend(_postflight_fastapi_api(repo_root))
    elif profile == "django-api":
        checks.extend(_postflight_python_stack(repo_root, "django", [
            "manage.py",
            "src/signalos_product_django/settings.py",
            "src/signalos_product_django/urls.py",
            "tests/test_health.py",
            ".signalos/profile.json",
        ]))
    elif profile == "flask-api":
        checks.extend(_postflight_python_stack(repo_root, "flask", [
            "src/signalos_product_flask/app.py",
            "src/signalos_product_flask/main.py",
            "tests/test_health.py",
            ".signalos/profile.json",
        ]))
    elif profile == "go-api":
        checks.extend(_postflight_go_api(repo_root))
    elif profile == "spring-boot-api":
        checks.extend(_postflight_text_stack(repo_root, "pom.xml", "spring-boot", [
            "pom.xml",
            "src/main/java/com/signalos/product/ProductApplication.java",
            "src/main/java/com/signalos/product/HealthController.java",
            "src/test/java/com/signalos/product/HealthControllerTest.java",
            ".signalos/profile.json",
        ]))
    elif profile == "java-api":
        checks.extend(_postflight_expected_files(repo_root, [
            "src/main/java/com/signalos/product/ProductServer.java",
            "src/test/java/com/signalos/product/ProductServerTest.java",
            ".signalos/profile.json",
        ]))
    elif profile == "rust-api":
        checks.extend(_postflight_expected_files(repo_root, [
            "Cargo.toml",
            "src/lib.rs",
            "src/main.rs",
            ".signalos/profile.json",
        ]))
    elif profile == "dotnet-minimal-api":
        checks.extend(_postflight_dotnet_minimal_api(repo_root))
    elif profile == "agent-selected":
        checks.extend(_postflight_agent_selected(repo_root))
    elif profile == "existing-repo":
        checks.extend(_postflight_existing_repo(repo_root))
    else:
        # generic / unknown
        checks.extend(_postflight_generic(repo_root))

    passed = all(c["passed"] for c in checks)
    return {"passed": passed, "checks": checks}


def _postflight_expected_files(repo_root: Path, expected_files: list[str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for rel in expected_files:
        path = repo_root / rel
        checks.append({
            "name": f"{rel} exists",
            "passed": path.is_file(),
            "detail": f"{rel} found" if path.is_file() else f"{rel} not found",
        })
    return checks


def _postflight_package_stack(
    repo_root: Path,
    dependency: str,
    expected_files: list[str],
) -> list[dict[str, Any]]:
    checks = _postflight_expected_files(repo_root, ["package.json", *expected_files])
    pkg_path = repo_root / "package.json"
    has_dependency = False
    if pkg_path.is_file():
        try:
            import json
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            has_dependency = dependency in deps
        except Exception:
            pass
    checks.append({
        "name": f"{dependency} dependency exists",
        "passed": has_dependency,
        "detail": f"{dependency} found" if has_dependency else f"{dependency} dependency missing",
    })
    return checks


def _postflight_python_stack(
    repo_root: Path,
    dependency: str,
    expected_files: list[str],
) -> list[dict[str, Any]]:
    checks = _postflight_expected_files(repo_root, ["pyproject.toml", *expected_files])
    pyproject = repo_root / "pyproject.toml"
    has_dependency = False
    if pyproject.is_file():
        try:
            has_dependency = dependency.lower() in pyproject.read_text(encoding="utf-8").lower()
        except OSError:
            pass
    checks.append({
        "name": f"{dependency} dependency exists",
        "passed": has_dependency,
        "detail": f"{dependency} found" if has_dependency else f"{dependency} dependency missing",
    })
    return checks


def _postflight_text_stack(
    repo_root: Path,
    marker_file: str,
    marker_text: str,
    expected_files: list[str],
) -> list[dict[str, Any]]:
    checks = _postflight_expected_files(repo_root, expected_files)
    marker = repo_root / marker_file
    has_marker = False
    if marker.is_file():
        try:
            has_marker = marker_text.lower() in marker.read_text(encoding="utf-8").lower()
        except OSError:
            pass
    checks.append({
        "name": f"{marker_text} marker exists",
        "passed": has_marker,
        "detail": f"{marker_text} found" if has_marker else f"{marker_text} marker missing",
    })
    return checks


def _postflight_react_vite(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    # 1. package.json exists
    pkg_path = repo_root / "package.json"
    pkg_exists = pkg_path.is_file()
    checks.append({
        "name": "package.json exists",
        "passed": pkg_exists,
        "detail": str(pkg_path) if pkg_exists else "package.json not found",
    })

    # 2. package.json has name field
    has_name = False
    has_deps = False
    if pkg_exists:
        try:
            import json
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            has_name = bool(pkg.get("name"))
            has_deps = bool(pkg.get("dependencies"))
        except Exception:
            pass

    checks.append({
        "name": "package.json has name",
        "passed": has_name,
        "detail": "name field present" if has_name else "name field missing",
    })

    # 3. package.json has dependencies
    checks.append({
        "name": "package.json has dependencies",
        "passed": has_deps,
        "detail": "dependencies present" if has_deps else "dependencies missing",
    })

    # 4. src/ exists
    src_exists = (repo_root / "src").is_dir()
    checks.append({
        "name": "src/ directory exists",
        "passed": src_exists,
        "detail": "src/ found" if src_exists else "src/ not found",
    })

    # 5. at least one .tsx file
    tsx_files = list((repo_root / "src").glob("*.tsx")) if src_exists else []
    has_tsx = len(tsx_files) > 0
    checks.append({
        "name": ".tsx file exists in src/",
        "passed": has_tsx,
        "detail": f"{len(tsx_files)} .tsx file(s) found" if has_tsx else "no .tsx files found",
    })

    return checks


def _postflight_agent_selected(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    signalos_exists = (repo_root / ".signalos").is_dir()
    stack_decision_exists = (repo_root / "PRODUCT_STACK.md").is_file()
    src_exists = (repo_root / "src").is_dir()
    tests_exists = (repo_root / "tests").is_dir()
    checks.extend([
        {
            "name": ".signalos/ directory exists",
            "passed": signalos_exists,
            "detail": ".signalos/ found" if signalos_exists else ".signalos/ not found",
        },
        {
            "name": "PRODUCT_STACK.md exists",
            "passed": stack_decision_exists,
            "detail": (
                "PRODUCT_STACK.md found"
                if stack_decision_exists
                else "PRODUCT_STACK.md not found"
            ),
        },
        {
            "name": "src/ directory exists",
            "passed": src_exists,
            "detail": "src/ found" if src_exists else "src/ not found",
        },
        {
            "name": "tests/ directory exists",
            "passed": tests_exists,
            "detail": "tests/ found" if tests_exists else "tests/ not found",
        },
    ])
    return checks


def _postflight_node_api(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    pkg_path = repo_root / "package.json"
    pkg_exists = pkg_path.is_file()
    checks.append({
        "name": "package.json exists",
        "passed": pkg_exists,
        "detail": str(pkg_path) if pkg_exists else "package.json not found",
    })

    has_express = False
    has_scripts = False
    if pkg_exists:
        try:
            import json
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            has_express = "express" in deps
            scripts = pkg.get("scripts", {})
            has_scripts = "start" in scripts and "test" in scripts
        except Exception:
            pass

    checks.append({
        "name": "Express dependency exists",
        "passed": has_express,
        "detail": "express found" if has_express else "express dependency missing",
    })
    checks.append({
        "name": "Node API scripts exist",
        "passed": has_scripts,
        "detail": "start/test scripts found" if has_scripts else "start/test scripts missing",
    })

    app_exists = (repo_root / "src" / "app.js").is_file()
    server_exists = (repo_root / "src" / "server.js").is_file()
    checks.append({
        "name": "src/app.js exists",
        "passed": app_exists,
        "detail": "src/app.js found" if app_exists else "src/app.js not found",
    })
    checks.append({
        "name": "src/server.js exists",
        "passed": server_exists,
        "detail": "src/server.js found" if server_exists else "src/server.js not found",
    })

    tests_exist = any((repo_root / "tests").glob("*.test.js")) if (repo_root / "tests").is_dir() else False
    checks.append({
        "name": "Node test file exists",
        "passed": tests_exist,
        "detail": "tests/*.test.js found" if tests_exist else "no tests/*.test.js found",
    })

    return checks


def _postflight_fastapi_api(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    pyproject = repo_root / "pyproject.toml"
    pyproject_exists = pyproject.is_file()
    checks.append({
        "name": "pyproject.toml exists",
        "passed": pyproject_exists,
        "detail": str(pyproject) if pyproject_exists else "pyproject.toml not found",
    })

    has_fastapi = False
    has_uvicorn = False
    if pyproject_exists:
        try:
            content = pyproject.read_text(encoding="utf-8").lower()
            has_fastapi = "fastapi" in content
            has_uvicorn = "uvicorn" in content
        except OSError:
            pass
    checks.append({
        "name": "FastAPI dependency exists",
        "passed": has_fastapi,
        "detail": "fastapi found" if has_fastapi else "fastapi dependency missing",
    })
    checks.append({
        "name": "Uvicorn dependency exists",
        "passed": has_uvicorn,
        "detail": "uvicorn found" if has_uvicorn else "uvicorn dependency missing",
    })

    expected_files = [
        "src/signalos_product_fastapi/__init__.py",
        "src/signalos_product_fastapi/app.py",
        "src/signalos_product_fastapi/main.py",
        "src/signalos_product_fastapi/routes/__init__.py",
        "src/signalos_product_fastapi/models/__init__.py",
        "tests/test_health.py",
        ".signalos/profile.json",
    ]
    for rel in expected_files:
        path = repo_root / rel
        checks.append({
            "name": f"{rel} exists",
            "passed": path.is_file(),
            "detail": f"{rel} found" if path.is_file() else f"{rel} not found",
        })

    return checks


def _postflight_go_api(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    expected_files = [
        "go.mod",
        "cmd/server/main.go",
        "internal/app/app.go",
        "internal/app/app_test.go",
        "tests/acceptance-map.md",
        ".signalos/profile.json",
    ]
    for rel in expected_files:
        path = repo_root / rel
        checks.append({
            "name": f"{rel} exists",
            "passed": path.is_file(),
            "detail": f"{rel} found" if path.is_file() else f"{rel} not found",
        })

    go_mod = repo_root / "go.mod"
    has_module = False
    if go_mod.is_file():
        try:
            has_module = go_mod.read_text(encoding="utf-8").startswith("module ")
        except OSError:
            pass
    checks.append({
        "name": "Go module exists",
        "passed": has_module,
        "detail": "module declaration found" if has_module else "module declaration missing",
    })
    return checks


def _postflight_dotnet_minimal_api(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    expected_files = [
        "SignalOSProduct.Api/SignalOSProduct.Api.csproj",
        "SignalOSProduct.Api/Program.cs",
        "SignalOSProduct.Api/ProductRoutes.cs",
        "tests/acceptance-map.md",
        ".signalos/profile.json",
    ]
    for rel in expected_files:
        path = repo_root / rel
        checks.append({
            "name": f"{rel} exists",
            "passed": path.is_file(),
            "detail": f"{rel} found" if path.is_file() else f"{rel} not found",
        })

    csproj = repo_root / "SignalOSProduct.Api" / "SignalOSProduct.Api.csproj"
    has_web_sdk = False
    has_net_target = False
    if csproj.is_file():
        try:
            content = csproj.read_text(encoding="utf-8")
            has_web_sdk = "Microsoft.NET.Sdk.Web" in content
            has_net_target = bool(
                re.search(r"<TargetFramework>net\d+\.0</TargetFramework>", content)
            )
        except OSError:
            pass
    checks.append({
        "name": ".NET Web SDK project exists",
        "passed": has_web_sdk,
        "detail": "Microsoft.NET.Sdk.Web found" if has_web_sdk else "Web SDK not found",
    })
    checks.append({
        "name": ".NET target framework exists",
        "passed": has_net_target,
        "detail": ".NET target framework found" if has_net_target else ".NET target framework not found",
    })
    return checks


def _postflight_existing_repo(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    signalos_exists = (repo_root / ".signalos").is_dir()
    checks.append({
        "name": ".signalos/ directory exists",
        "passed": signalos_exists,
        "detail": ".signalos/ found" if signalos_exists else ".signalos/ not found",
    })

    # Check that non-dot files still exist (preserved)
    non_dot = [c for c in repo_root.iterdir() if not c.name.startswith(".")]
    has_files = len(non_dot) > 0
    checks.append({
        "name": "original files preserved",
        "passed": has_files,
        "detail": f"{len(non_dot)} original item(s) present" if has_files else "no original files found",
    })

    return checks


def _postflight_generic(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    signalos_exists = (repo_root / ".signalos").is_dir()
    checks.append({
        "name": ".signalos/ directory exists",
        "passed": signalos_exists,
        "detail": ".signalos/ found" if signalos_exists else ".signalos/ not found",
    })

    pyproject_exists = (repo_root / "pyproject.toml").is_file()
    checks.append({
        "name": "pyproject.toml exists",
        "passed": pyproject_exists,
        "detail": "pyproject.toml found" if pyproject_exists else "pyproject.toml not found",
    })

    src_exists = (repo_root / "src").is_dir()
    package_dirs = [
        child
        for child in (repo_root / "src").iterdir()
        if src_exists and child.is_dir() and (child / "__init__.py").is_file()
    ] if src_exists else []
    checks.append({
        "name": "Python package exists under src/",
        "passed": bool(package_dirs),
        "detail": (
            f"{len(package_dirs)} package(s) found"
            if package_dirs
            else "no src package with __init__.py found"
        ),
    })

    tests_exists = (repo_root / "tests").is_dir()
    checks.append({
        "name": "tests/ directory exists",
        "passed": tests_exists,
        "detail": "tests/ found" if tests_exists else "tests/ not found",
    })

    return checks


# ---------------------------------------------------------------------------
# Main scaffold orchestrator
# ---------------------------------------------------------------------------

def run_scaffold(
    repo_root: Path,
    profile: str,
    product_name: str,
    prompt: str,
    blueprint_id: str | None = None,
    mode: str = "auto",
    product_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full scaffold phase.

    Steps:
    1. Detect or use provided mode (delegate to lifecycle.detect_mode)
    2. Init product repo (delegate to lifecycle.init_product_repo)
    3. Extract and write product intent (delegate to intent module)
    4. Match or use provided blueprint
    5. Detect or use provided profile
    6. Run adapter scaffold (delegate to stacks.get_adapter().scaffold())
    7. Run scaffold postflight validation
    8. Create delivery state at phase="scaffolded"
    9. Return result dict

    Returns a dict with keys: success, mode, profile, blueprint,
    scaffold_files, postflight, errors, warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []
    scaffold_files: list[str] = []
    resolved_mode = mode
    resolved_profile = profile
    resolved_blueprint: str | None = blueprint_id
    postflight_result: dict[str, Any] = {"passed": False, "checks": []}

    # 1. Detect mode
    if resolved_mode == "auto":
        resolved_mode = lifecycle.detect_mode(repo_root)

    # 3. Extract product intent before stack selection so "auto" can make a
    # product-shaped adapter decision rather than defaulting every greenfield
    # request to one frontend stack.
    if product_intent is None:
        product_intent = intent_mod.extract_product_intent(prompt)
    if product_name and not product_intent.get("product_name"):
        product_intent["product_name"] = product_name

    profile_detected = False
    if resolved_profile == "auto":
        if resolved_mode == "greenfield":
            resolved_profile = select_greenfield_profile(repo_root, product_intent)
        else:
            resolved_profile = stacks.detect_profile(repo_root)
            profile_detected = True

    # 2. Init product repo
    try:
        init_result = lifecycle.init_product_repo(
            repo_root=repo_root,
            mode=resolved_mode,
            profile=resolved_profile,
            product_name=product_name,
        )
        if not init_result["success"]:
            errors.extend(init_result.get("errors", []))
            return _build_result(
                success=False,
                mode=resolved_mode,
                profile=resolved_profile,
                blueprint=resolved_blueprint,
                scaffold_files=scaffold_files,
                postflight=postflight_result,
                errors=errors,
                warnings=warnings,
            )
    except Exception as exc:
        errors.append(f"init failed: {exc}")
        return _build_result(
            success=False,
            mode=resolved_mode,
            profile=resolved_profile,
            blueprint=resolved_blueprint,
            scaffold_files=scaffold_files,
            postflight=postflight_result,
            errors=errors,
            warnings=warnings,
        )

    # 4. Match or use provided blueprint
    if resolved_blueprint is None:
        resolved_blueprint = blueprint_registry.match_blueprint(product_intent)
    bp = (
        blueprint_registry.load_blueprint(resolved_blueprint)
        if resolved_blueprint
        else None
    )
    product_intent = blueprint_registry.apply_blueprint_intent_defaults(
        product_intent, bp,
    )
    if product_name:
        product_intent["product_name"] = product_name

    signalos_dir = repo_root / ".signalos"
    try:
        intent_mod.write_intent(product_intent, signalos_dir)
        scaffold_files.append(".signalos/product/INTENT.json")
    except Exception as exc:
        warnings.append(f"intent write failed: {exc}")

    # 6. Run adapter scaffold (skip for adopt mode - preserve existing files)
    if resolved_mode != "adopt":
        try:
            adapter = stacks.get_adapter(resolved_profile)
            adapter_result = adapter.scaffold(repo_root, product_intent)
            scaffold_files.extend(adapter_result.get("created", []))
        except Exception as exc:
            errors.append(f"adapter scaffold failed: {exc}")
            return _build_result(
                success=False,
                mode=resolved_mode,
                profile=resolved_profile,
                blueprint=resolved_blueprint,
                scaffold_files=scaffold_files,
                postflight=postflight_result,
                errors=errors,
                warnings=warnings,
            )
    else:
        # Adopt mode: only governance metadata via existing-repo adapter
        try:
            adapter = stacks.get_adapter("existing-repo")
            adapter_result = adapter.scaffold(repo_root, product_intent)
            scaffold_files.extend(adapter_result.get("created", []))
        except Exception as exc:
            warnings.append(f"adopt metadata write failed: {exc}")

    # 7. Run postflight
    postflight_result = run_postflight(repo_root, resolved_profile)

    # 8. Create delivery state
    try:
        lifecycle.create_delivery_state(
            repo_root=repo_root,
            mode=resolved_mode,
            prompt=prompt,
            profile=resolved_profile,
            blueprint=resolved_blueprint or "",
        )
        lifecycle.update_delivery_phase(repo_root, phase="scaffolded")
    except Exception as exc:
        warnings.append(f"delivery state write failed: {exc}")

    return _build_result(
        success=True,
        mode=resolved_mode,
        profile=resolved_profile,
        blueprint=resolved_blueprint,
        scaffold_files=scaffold_files,
        postflight=postflight_result,
        errors=errors,
        warnings=warnings,
    )


def _build_result(
    *,
    success: bool,
    mode: str,
    profile: str,
    blueprint: str | None,
    scaffold_files: list[str],
    postflight: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "success": success,
        "mode": mode,
        "profile": profile,
        "blueprint": blueprint,
        "scaffold_files": scaffold_files,
        "postflight": postflight,
        "errors": errors,
        "warnings": warnings,
    }


def select_greenfield_profile(repo_root: Path, intent: dict[str, Any]) -> str:
    """Choose the first supported greenfield stack from product intent.

    This is a stack-adapter decision, not a product-domain hardcode. A product
    that clearly needs user-facing screens gets the currently supported UI
    adapter. Products with no UI surface get the stdlib Python adapter so the
    bridge still produces a real runnable repo.
    """
    detected_profile = stacks.detect_profile(repo_root)
    if detected_profile != "generic":
        return detected_profile

    stack_preferences = {
        str(pref).lower()
        for pref in intent.get("stack_preferences", [])
        if str(pref).strip()
    }
    if stack_preferences & {"flutter", "flutter-app", "dart"}:
        return "flutter-app"
    if stack_preferences & {
        "react-native", "react native", "expo", "expo-react-native",
    }:
        return "expo-react-native"
    if stack_preferences & {"react", "vite", "react-vite", "frontend", "web-ui"}:
        return "react-vite"
    if stack_preferences & {"angular", "ng"}:
        return "angular"
    if stack_preferences & {"next", "nextjs", "next.js", "nextjs-app"}:
        return "nextjs-app"
    if stack_preferences & {"vue", "vuejs", "vue.js", "vue-vite"}:
        return "vue-vite"
    if stack_preferences & {"fastapi", "fastapi-api"}:
        return "fastapi-api"
    if stack_preferences & {"django", "django-api"}:
        return "django-api"
    if stack_preferences & {"flask", "flask-api"}:
        return "flask-api"
    if stack_preferences & {"go", "go-api", "golang"}:
        return "go-api"
    if stack_preferences & {"nestjs", "nestjs-api", "nest", "nest.js"}:
        return "nestjs-api"
    if stack_preferences & {"spring", "spring-boot", "springboot", "spring-boot-api"}:
        return "spring-boot-api"
    if stack_preferences & {"java", "java-api"}:
        return "java-api"
    if stack_preferences & {"rust", "rust-api"}:
        return "rust-api"
    if stack_preferences & {
        "dotnet-minimal-api", ".net", "dotnet", "aspnet", "asp.net",
        "aspnetcore", "asp.net-core", "minimal-api", "csharp", "c#",
    }:
        return "dotnet-minimal-api"
    if stack_preferences & {"node", "node-api", "express", "backend", "api", "rest-api"}:
        return "node-api"
    if stack_preferences & {"python", "library", "cli", "generic"}:
        api_surfaces = {
            str(surface).lower()
            for surface in intent.get("api_surfaces", [])
            if str(surface).strip()
        }
        if api_surfaces & {"rest-api", "webhook", "graphql"}:
            return "fastapi-api"
        return "generic"
    if stack_preferences & {"svelte", "blazor", "mobile", "mobile-app", "android", "ios"}:
        return "agent-selected"

    surfaces = {
        str(surface).lower()
        for surface in intent.get("ux_surfaces", [])
        if str(surface).strip()
    }
    ui_surfaces = {
        "web-ui", "dashboard", "table", "form", "detail", "list", "chart",
        "gauge", "kanban", "calendar", "timeline", "report",
    }
    if surfaces & ui_surfaces:
        return "react-vite"

    product_type = str(intent.get("product_type") or "").lower()
    ui_product_types = {
        "task-management", "financial-dashboard", "e-commerce",
        "social-platform", "crm", "dashboard",
    }
    if product_type in ui_product_types:
        return "react-vite"

    api_surfaces = {
        str(surface).lower()
        for surface in intent.get("api_surfaces", [])
        if str(surface).strip()
    }
    if api_surfaces & {"rest-api", "webhook", "graphql"}:
        return "node-api"

    return "generic"
