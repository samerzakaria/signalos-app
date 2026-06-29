"""Tests for Deliver command profile selection helpers."""

from __future__ import annotations

import argparse
import json
from contextlib import redirect_stdout
from io import StringIO

from signalos_lib.commands.deliver import cmd_deliver_design


def _run_design(prompt: str, repo_root, profile: str = "auto") -> dict:
    args = argparse.Namespace(
        prompt=prompt,
        name=None,
        repo_root=str(repo_root),
        profile=profile,
        technologies=[],
        frontend="auto",
        database="auto",
        cache="auto",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)
    assert rc == 0
    return json.loads(out.getvalue())


def test_deliver_design_auto_profile_can_choose_generic(tmp_path):
    payload = _run_design(
        "Build a Python checksum library for validating uploaded files",
        tmp_path,
    )

    assert payload["profile"] == "generic"


def test_deliver_design_auto_profile_can_choose_ui_product(tmp_path):
    payload = _run_design(
        "Build a dashboard to manage team tasks, utilization, workload, and KPIs",
        tmp_path,
    )

    assert payload["profile"] == "react-vite"


def test_deliver_design_auto_profile_can_choose_node_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a REST API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["node"],
        frontend="none",
        database="postgresql",
        cache="redis",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "node-api"
    assert payload["capability_profile"]["infrastructure"]["databases"] == ["postgresql"]
    assert payload["capability_profile"]["infrastructure"]["caches"] == ["redis"]


def test_deliver_design_auto_profile_can_choose_fastapi_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a FastAPI REST API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["fastapi"],
        frontend="none",
        database="postgresql",
        cache="redis",
        language="python",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "fastapi-api"
    assert "fastapi" in payload["capability_profile"]["technology_preferences"]
    assert payload["capability_profile"]["infrastructure"]["databases"] == ["postgresql"]
    assert payload["capability_profile"]["infrastructure"]["caches"] == ["redis"]
    assert payload["capability_profile"]["language"] == "python"
    assert "python" in payload["capability_profile"]["application_layers"]["backend"]


def test_deliver_design_auto_profile_can_choose_dotnet_minimal_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a Minimal API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["dotnet"],
        frontend="none",
        database="postgresql",
        cache="redis",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "dotnet-minimal-api"
    assert ".net" in payload["capability_profile"]["technology_preferences"]
    assert payload["capability_profile"]["infrastructure"]["databases"] == ["postgresql"]
    assert payload["capability_profile"]["infrastructure"]["caches"] == ["redis"]


def test_deliver_design_language_csharp_can_choose_dotnet_minimal_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a REST API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=[],
        frontend="none",
        database="auto",
        cache="auto",
        language="c#",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "dotnet-minimal-api"
    assert "csharp" in payload["capability_profile"]["technology_preferences"]
    assert payload["capability_profile"]["language"] == "csharp"


def test_deliver_design_auto_profile_can_choose_go_api_when_explicit(tmp_path):
    args = argparse.Namespace(
        prompt="Build a REST API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["go"],
        frontend="none",
        database="postgresql",
        cache="redis",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "go-api"
    assert "go" in payload["capability_profile"]["technology_preferences"]
    assert payload["capability_profile"]["infrastructure"]["databases"] == ["postgresql"]
    assert payload["capability_profile"]["infrastructure"]["caches"] == ["redis"]


def test_deliver_design_auto_profile_stays_unbiased_without_stack_choice(tmp_path):
    payload = _run_design(
        "Build a REST API for checksum validation with PostgreSQL and Redis",
        tmp_path,
    )

    assert payload["profile"] == "node-api"
    assert payload["profile"] not in {
        "angular",
        "django-api",
        "dotnet-minimal-api",
        "flask-api",
        "flutter-app",
        "go-api",
        "java-api",
        "nestjs-api",
        "nextjs-app",
        "rust-api",
        "spring-boot-api",
        "vue-vite",
        "expo-react-native",
    }


def test_deliver_design_explicit_angular_uses_angular_adapter(tmp_path):
    args = argparse.Namespace(
        prompt="Build an Angular operations dashboard",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=[],
        frontend="angular",
        database="auto",
        cache="auto",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "angular"
    assert payload["capability_profile"]["application_layers"]["frontend"] == ["angular"]


def test_deliver_design_explicit_next_uses_nextjs_adapter(tmp_path):
    args = argparse.Namespace(
        prompt="Build a Next.js operations dashboard",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=[],
        frontend="next",
        database="auto",
        cache="auto",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "nextjs-app"
    assert payload["capability_profile"]["application_layers"]["frontend"] == ["nextjs-app"]


def test_deliver_design_explicit_vue_uses_vue_vite_adapter(tmp_path):
    args = argparse.Namespace(
        prompt="Build a Vue operations dashboard",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=[],
        frontend="vue",
        database="auto",
        cache="auto",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "vue-vite"
    assert payload["capability_profile"]["application_layers"]["frontend"] == ["vue-vite"]


def test_deliver_design_explicit_flutter_uses_flutter_adapter(tmp_path):
    args = argparse.Namespace(
        prompt="Build a Flutter mobile app for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["flutter"],
        frontend="auto",
        database="auto",
        cache="auto",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "flutter-app"
    assert payload["capability_profile"]["application_layers"]["mobile"] == ["flutter-app"]


def test_deliver_design_explicit_expo_uses_react_native_adapter(tmp_path):
    args = argparse.Namespace(
        prompt="Build an Expo React Native mobile app for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["expo"],
        frontend="auto",
        database="auto",
        cache="auto",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "expo-react-native"
    assert payload["capability_profile"]["application_layers"]["mobile"] == ["expo-react-native"]


def test_deliver_design_explicit_django_uses_django_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a Django API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["django"],
        frontend="none",
        database="postgresql",
        cache="redis",
        language="python",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "django-api"
    assert "django" in payload["capability_profile"]["technology_preferences"]


def test_deliver_design_explicit_flask_uses_flask_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a Flask API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["flask"],
        frontend="none",
        database="postgresql",
        cache="redis",
        language="python",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "flask-api"
    assert "flask-api" in payload["capability_profile"]["technology_preferences"]


def test_deliver_design_explicit_nestjs_uses_nestjs_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a NestJS API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["nestjs"],
        frontend="none",
        database="postgresql",
        cache="redis",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "nestjs-api"
    assert "nestjs-api" in payload["capability_profile"]["technology_preferences"]


def test_deliver_design_explicit_spring_boot_uses_spring_boot_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a Spring-style Java API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["spring-boot"],
        frontend="none",
        database="postgresql",
        cache="redis",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "spring-boot-api"
    assert "spring-boot-api" in payload["capability_profile"]["technology_preferences"]


def test_deliver_design_explicit_java_uses_java_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a Java API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["java"],
        frontend="none",
        database="postgresql",
        cache="redis",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "java-api"
    assert "java" in payload["capability_profile"]["technology_preferences"]


def test_deliver_design_explicit_rust_uses_rust_api(tmp_path):
    args = argparse.Namespace(
        prompt="Build a Rust API for task management",
        name=None,
        repo_root=str(tmp_path),
        profile="auto",
        technologies=["rust"],
        frontend="none",
        database="auto",
        cache="auto",
        language="auto",
        deploy_target="auto",
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["profile"] == "rust-api"
    assert "rust" in payload["capability_profile"]["technology_preferences"]
