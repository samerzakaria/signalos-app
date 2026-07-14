"""Tests for P10 runtime and UX proof (product.proof module)."""

from __future__ import annotations

import json
import http.server
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import signalos_lib.product.proof as proof_mod
from signalos_lib.product.proof import (
    check_proof_completeness,
    requires_browser_ux_proof,
    run_runtime_proof,
    run_ux_proof,
    write_proof_artifacts,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _write_profile(repo: Path, profile: str) -> None:
    """Write a .signalos/profile.json for detection."""
    d = repo / ".signalos"
    d.mkdir(parents=True, exist_ok=True)
    (d / "profile.json").write_text(
        json.dumps({"profile": profile}) + "\n",
        encoding="utf-8",
    )


def _make_runtime_result(
    status: str = "passed",
    profile: str = "react-vite",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": status,
        "profile": profile,
        "preview_command": "npm run dev",
        "port": 5173,
        "health_check": {
            "url": "http://localhost:5173/",
            "status_code": 200,
            "responded": True,
            "response_time_ms": 42.0,
        },
        "server_log": "ready in 200ms",
        "duration_s": 1.5,
        "errors": [],
    }
    base.update(overrides)
    return base


def _make_ux_result(
    status: str = "passed",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "signalos.ux-browser-proof.v1",
        "status": status,
        "ux_required": True,
        "executed": status == "passed",
        "runner": "playwright",
        "checks": [
            {"name": name, "passed": True, "detail": "ok"}
            for name in (
                "browser_navigation", "document_complete", "app_root_found",
                "app_root_mounted", "app_root_visible", "no_page_errors",
                "no_console_errors",
            )
        ],
        "errors": [],
    }
    base.update(overrides)
    return base


class TestPackagedPlaywrightTooling:
    def test_preview_server_does_not_inherit_provider_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-reach-preview")
        monkeypatch.setenv("SIGNALOS_ENV_FILE", "C:/private/provider.env")
        captured: dict[str, Any] = {}
        sentinel = object()

        def fake_popen(command: str, **kwargs: Any) -> Any:
            captured["command"] = command
            captured.update(kwargs)
            return sentinel

        monkeypatch.setattr(proof_mod.subprocess, "Popen", fake_popen)

        result = proof_mod._start_server("npm run dev", tmp_path)

        assert result is sentinel
        assert "OPENROUTER_API_KEY" not in captured["env"]
        assert "SIGNALOS_ENV_FILE" not in captured["env"]

    def test_existing_product_or_source_playwright_wins_without_bootstrap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        entry = tmp_path / "node_modules" / "playwright" / "index.js"
        entry.parent.mkdir(parents=True)
        entry.write_text("module.exports = {}\n", encoding="utf-8")
        monkeypatch.setattr(proof_mod, "_playwright_entry", lambda _root: entry)

        def forbidden(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("bootstrap must not run when direct tooling exists")

        monkeypatch.setattr(proof_mod, "_bootstrap_playwright_runtime", forbidden)

        runtime = proof_mod._resolve_playwright_runtime(tmp_path, "node")

        assert runtime["entry"] == entry
        assert runtime["source"] == "product-or-source"
        assert runtime["browser_cache"] is None

    def test_missing_direct_tooling_uses_bootstrap_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        entry = tmp_path / "user-cache" / "node_modules" / "playwright" / "index.js"
        expected = {
            "entry": entry,
            "browser_cache": tmp_path / "user-cache" / "browsers",
            "source": "user-tooling-cache",
            "version": proof_mod._PLAYWRIGHT_VERSION,
        }
        monkeypatch.setattr(proof_mod, "_playwright_entry", lambda _root: None)
        monkeypatch.setattr(
            proof_mod, "_bootstrap_playwright_runtime",
            lambda _root, _node: expected,
        )

        assert proof_mod._resolve_playwright_runtime(tmp_path, "node") == expected

    def test_bootstrap_pins_package_and_installs_matching_chromium_without_network(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        product = tmp_path / "product"
        product.mkdir()
        cache = tmp_path / "user-tooling-cache"
        monkeypatch.setattr(proof_mod, "_playwright_tooling_cache", lambda _root: cache)
        monkeypatch.setattr(
            proof_mod.shutil, "which", lambda name: "npm.cmd" if name == "npm" else None,
        )
        ready_calls = iter((False, False, True))
        monkeypatch.setattr(
            proof_mod, "_cached_playwright_ready",
            lambda *_args, **_kwargs: next(ready_calls),
        )
        calls: list[dict[str, Any]] = []

        def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
            calls.append({"argv": list(argv), **kwargs})
            if argv[0] == "npm.cmd":
                package_dir = cache / "node_modules" / "playwright"
                package_dir.mkdir(parents=True)
                (package_dir / "index.js").write_text("module.exports = {}\n", encoding="utf-8")
                (package_dir / "cli.js").write_text("// cli\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(proof_mod.subprocess, "run", fake_run)

        result = proof_mod._bootstrap_playwright_runtime(product, "node.exe")

        assert result["source"] == "user-tooling-cache"
        assert result["version"] == proof_mod._PLAYWRIGHT_VERSION
        assert len(calls) == 2
        assert calls[0]["argv"][0] == "npm.cmd"
        assert f"playwright@{proof_mod._PLAYWRIGHT_VERSION}" in calls[0]["argv"]
        assert calls[0]["timeout"] == proof_mod._PLAYWRIGHT_PACKAGE_TIMEOUT_S
        assert calls[1]["argv"][-2:] == ["install", "chromium"]
        assert calls[1]["timeout"] == proof_mod._PLAYWRIGHT_BROWSER_TIMEOUT_S
        assert calls[1]["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(cache / "browsers")
        manifest = json.loads((cache / "package.json").read_text(encoding="utf-8"))
        assert manifest["dependencies"]["playwright"] == proof_mod._PLAYWRIGHT_VERSION

    def test_ready_user_cache_never_invokes_npm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        product = tmp_path / "product"
        product.mkdir()
        cache = tmp_path / "user-tooling-cache"
        monkeypatch.setattr(proof_mod, "_playwright_tooling_cache", lambda _root: cache)
        monkeypatch.setattr(proof_mod, "_cached_playwright_ready", lambda *_args: True)

        def forbidden(_name: str) -> str | None:
            raise AssertionError("npm discovery must not run for a verified cache")

        monkeypatch.setattr(proof_mod.shutil, "which", forbidden)

        result = proof_mod._bootstrap_playwright_runtime(product, "node.exe")

        assert result["source"] == "user-tooling-cache"

    def test_bootstrap_errors_are_redacted_in_fail_closed_receipt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        secret = "sk-test-super-secret-value"
        monkeypatch.setenv("OPENROUTER_API_KEY", secret)
        monkeypatch.setattr(proof_mod.shutil, "which", lambda _name: "node.exe")
        monkeypatch.setattr(
            proof_mod, "_resolve_playwright_runtime",
            lambda *_args: {"error": f"registry rejected credential {secret}"},
        )

        result = proof_mod._run_browser_page(tmp_path, "http://localhost:4173/")

        assert result["status"] == "unmeasurable"
        assert result["executed"] is False
        assert secret not in json.dumps(result)
        assert "[REDACTED]" in json.dumps(result)

    def test_runner_uses_cache_and_declares_stable_browser_fallbacks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-reach-browser")
        monkeypatch.setenv("SIGNALOS_ENV_FILE", "C:/private/provider.env")
        entry = tmp_path / "cache" / "node_modules" / "playwright" / "index.js"
        browser_cache = tmp_path / "cache" / "browsers"
        monkeypatch.setattr(proof_mod.shutil, "which", lambda _name: "node.exe")
        monkeypatch.setattr(
            proof_mod, "_resolve_playwright_runtime",
            lambda *_args: {
                "entry": entry,
                "browser_cache": browser_cache,
                "source": "user-tooling-cache",
                "version": proof_mod._PLAYWRIGHT_VERSION,
            },
        )
        captured: dict[str, Any] = {}

        def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
            captured["argv"] = list(argv)
            captured.update(kwargs)
            return subprocess.CompletedProcess(
                argv, 0, json.dumps(_make_ux_result()) + "\n", "",
            )

        monkeypatch.setattr(proof_mod.subprocess, "run", fake_run)

        result = proof_mod._run_browser_page(tmp_path, "http://localhost:4173/")

        script = captured["argv"][2]
        assert "channel: 'chrome'" in script
        assert "channel: 'msedge'" in script
        assert captured["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_cache)
        assert "OPENROUTER_API_KEY" not in captured["env"]
        assert "SIGNALOS_ENV_FILE" not in captured["env"]
        assert result["status"] == "passed"
        assert result["tooling_source"] == "user-tooling-cache"


# ------------------------------------------------------------------
# run_runtime_proof — generic profile returns skipped
# ------------------------------------------------------------------

class TestRuntimeProofGenericSkipped:
    def test_generic_profile_returns_skipped(self, tmp_path: Path) -> None:
        result = run_runtime_proof(tmp_path, "generic")
        assert result["status"] == "skipped"
        assert result["preview_command"] is None
        assert result["port"] is None

    def test_generic_profile_has_all_required_fields(self, tmp_path: Path) -> None:
        result = run_runtime_proof(tmp_path, "generic")
        required = {
            "status", "profile", "preview_command", "port",
            "health_check", "server_log", "duration_s", "errors",
        }
        assert required.issubset(result.keys())


# ------------------------------------------------------------------
# run_runtime_proof — result schema
# ------------------------------------------------------------------

class TestRuntimeProofSchema:
    def test_result_has_all_required_fields(self, tmp_path: Path) -> None:
        result = run_runtime_proof(tmp_path, "generic")
        required = {
            "status", "profile", "preview_command", "port",
            "health_check", "server_log", "duration_s", "errors",
        }
        assert required.issubset(result.keys())

    def test_health_check_has_required_fields(self, tmp_path: Path) -> None:
        result = run_runtime_proof(tmp_path, "generic")
        hc = result["health_check"]
        required = {"url", "status_code", "responded", "response_time_ms"}
        assert required.issubset(hc.keys())


# ------------------------------------------------------------------
# run_ux_proof — no port returns skipped
# ------------------------------------------------------------------

class TestUxProofNoPort:
    def test_no_port_returns_skipped(self, tmp_path: Path) -> None:
        result = run_ux_proof(tmp_path, "generic", port=None)
        assert result["status"] == "skipped"

    def test_result_has_all_required_fields(self, tmp_path: Path) -> None:
        result = run_ux_proof(tmp_path, "generic", port=None)
        required = {"status", "checks", "errors"}
        assert required.issubset(result.keys())

    def test_runtime_html_snapshot_cannot_replace_executed_browser_proof(self, tmp_path: Path) -> None:
        result = run_ux_proof(
            tmp_path,
            "react-vite",
            port=None,
            html='<!doctype html><html><body><div id="root">Ready</div></body></html>',
        )
        assert result["status"] == "unmeasurable"
        assert result["executed"] is False
        assert "raw HTML" in " ".join(result["errors"])

    def test_blank_runtime_html_snapshot_fails_ux(self, tmp_path: Path) -> None:
        result = run_ux_proof(tmp_path, "react-vite", port=None, html="")
        assert result["status"] == "unmeasurable"
        assert result["executed"] is False

    def test_node_api_skips_browser_ux_even_with_runtime_port(self, tmp_path: Path) -> None:
        result = run_ux_proof(tmp_path, "node-api", port=3000, html='{"status":"ok"}')

        assert result["status"] == "skipped"
        assert result["skip_reason"]

    def test_fastapi_api_skips_browser_ux_even_with_runtime_port(self, tmp_path: Path) -> None:
        result = run_ux_proof(tmp_path, "fastapi-api", port=8000, html='{"status":"ok"}')

        assert result["status"] == "skipped"
        assert result["skip_reason"]

    def test_dotnet_minimal_api_skips_browser_ux_even_with_runtime_port(self, tmp_path: Path) -> None:
        result = run_ux_proof(tmp_path, "dotnet-minimal-api", port=5050, html='{"status":"ok"}')

        assert result["status"] == "skipped"
        assert result["skip_reason"]

    def test_go_api_skips_browser_ux_even_with_runtime_port(self, tmp_path: Path) -> None:
        result = run_ux_proof(tmp_path, "go-api", port=8080, html='{"status":"ok"}')

        assert result["status"] == "skipped"
        assert result["skip_reason"]

    def test_profile_ux_requirement_distinguishes_ui_and_api(self, tmp_path: Path) -> None:
        assert requires_browser_ux_proof(tmp_path, "react-vite") is True
        assert requires_browser_ux_proof(tmp_path, "node-api") is False
        assert requires_browser_ux_proof(tmp_path, "fastapi-api") is False
        assert requires_browser_ux_proof(tmp_path, "dotnet-minimal-api") is False
        assert requires_browser_ux_proof(tmp_path, "go-api") is False


# ------------------------------------------------------------------
# write_proof_artifacts
# ------------------------------------------------------------------

class TestWriteProofArtifacts:
    def test_creates_smoke_json(self, tmp_path: Path) -> None:
        rt = _make_runtime_result()
        ux = _make_ux_result()
        proof_dir = write_proof_artifacts(rt, ux, tmp_path)
        assert (proof_dir / "smoke.json").is_file()

    def test_creates_ux_smoke_json(self, tmp_path: Path) -> None:
        rt = _make_runtime_result()
        ux = _make_ux_result()
        proof_dir = write_proof_artifacts(rt, ux, tmp_path)
        assert (proof_dir / "ux-smoke.json").is_file()

    def test_creates_preview_log(self, tmp_path: Path) -> None:
        rt = _make_runtime_result()
        ux = _make_ux_result()
        proof_dir = write_proof_artifacts(rt, ux, tmp_path)
        assert (proof_dir / "preview.log").is_file()

    def test_correct_directory(self, tmp_path: Path) -> None:
        rt = _make_runtime_result()
        ux = _make_ux_result()
        proof_dir = write_proof_artifacts(rt, ux, tmp_path)
        expected = tmp_path / ".signalos" / "product" / "proof" / "runtime"
        assert proof_dir == expected

    def test_smoke_json_is_valid_json(self, tmp_path: Path) -> None:
        rt = _make_runtime_result()
        ux = _make_ux_result()
        proof_dir = write_proof_artifacts(rt, ux, tmp_path)
        data = json.loads((proof_dir / "smoke.json").read_text(encoding="utf-8"))
        assert data["status"] == "passed"

    def test_ux_smoke_json_is_valid_json(self, tmp_path: Path) -> None:
        rt = _make_runtime_result()
        ux = _make_ux_result()
        proof_dir = write_proof_artifacts(rt, ux, tmp_path)
        data = json.loads((proof_dir / "ux-smoke.json").read_text(encoding="utf-8"))
        assert data["status"] == "passed"

    def test_preview_log_contains_server_log(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(server_log="vite ready in 200ms")
        ux = _make_ux_result()
        proof_dir = write_proof_artifacts(rt, ux, tmp_path)
        log = (proof_dir / "preview.log").read_text(encoding="utf-8")
        assert "vite ready in 200ms" in log


# ------------------------------------------------------------------
# check_proof_completeness
# ------------------------------------------------------------------

class TestCheckProofCompleteness:
    def test_no_proof_files_returns_incomplete(self, tmp_path: Path) -> None:
        result = check_proof_completeness(tmp_path, "react-vite")
        assert result["complete"] is False
        assert result["runtime_proof_exists"] is False
        assert result["ux_proof_exists"] is False
        assert len(result["blockers"]) > 0

    def test_passed_proofs_return_complete(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(status="passed")
        ux = _make_ux_result(status="passed")
        write_proof_artifacts(rt, ux, tmp_path)
        result = check_proof_completeness(tmp_path, "react-vite")
        assert result["complete"] is True
        assert result["runtime_status"] == "passed"
        assert result["ux_status"] == "passed"
        assert result["blockers"] == []

    def test_browser_profile_rejects_unexecuted_pass_claim(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(status="passed")
        ux = _make_ux_result(status="passed", executed=False)
        write_proof_artifacts(rt, ux, tmp_path)

        result = check_proof_completeness(tmp_path, "react-vite")

        assert result["complete"] is False
        assert any("execute" in blocker.lower() for blocker in result["blockers"])

    def test_generic_profile_skipped_is_complete(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(
            status="skipped",
            profile="generic",
            preview_command=None,
            port=None,
        )
        ux = _make_ux_result(status="skipped")
        write_proof_artifacts(rt, ux, tmp_path)
        result = check_proof_completeness(tmp_path, "generic")
        assert result["complete"] is True
        assert result["runtime_status"] == "skipped"
        assert result["ux_status"] == "skipped"

    def test_node_api_runtime_passed_with_skipped_ux_is_complete(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(
            status="passed",
            profile="node-api",
            preview_command="npm start",
            port=3000,
        )
        ux = _make_ux_result(status="skipped", skip_reason="Profile does not require browser UX proof")
        write_proof_artifacts(rt, ux, tmp_path)

        result = check_proof_completeness(tmp_path, "node-api")

        assert result["complete"] is True
        assert result["runtime_status"] == "passed"
        assert result["ux_status"] == "skipped"
        assert result["blockers"] == []

    def test_fastapi_api_runtime_passed_with_skipped_ux_is_complete(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(
            status="passed",
            profile="fastapi-api",
            preview_command="python -m uvicorn signalos_product_fastapi.app:app",
            port=8000,
        )
        ux = _make_ux_result(status="skipped", skip_reason="Profile does not require browser UX proof")
        write_proof_artifacts(rt, ux, tmp_path)

        result = check_proof_completeness(tmp_path, "fastapi-api")

        assert result["complete"] is True
        assert result["runtime_status"] == "passed"
        assert result["ux_status"] == "skipped"
        assert result["blockers"] == []

    def test_dotnet_minimal_api_runtime_passed_with_skipped_ux_is_complete(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(
            status="passed",
            profile="dotnet-minimal-api",
            preview_command="dotnet run --project SignalOSProduct.Api/SignalOSProduct.Api.csproj",
            port=5050,
        )
        ux = _make_ux_result(status="skipped", skip_reason="Profile does not require browser UX proof")
        write_proof_artifacts(rt, ux, tmp_path)

        result = check_proof_completeness(tmp_path, "dotnet-minimal-api")

        assert result["complete"] is True
        assert result["runtime_status"] == "passed"
        assert result["ux_status"] == "skipped"
        assert result["blockers"] == []

    def test_go_api_runtime_passed_with_skipped_ux_is_complete(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(
            status="passed",
            profile="go-api",
            preview_command="go run ./cmd/server",
            port=8080,
        )
        ux = _make_ux_result(status="skipped", skip_reason="Profile does not require browser UX proof")
        write_proof_artifacts(rt, ux, tmp_path)

        result = check_proof_completeness(tmp_path, "go-api")

        assert result["complete"] is True
        assert result["runtime_status"] == "passed"
        assert result["ux_status"] == "skipped"
        assert result["blockers"] == []

    def test_round_trip_write_then_check(self, tmp_path: Path) -> None:
        """Write artifacts then check completeness -- round-trip."""
        rt = _make_runtime_result(status="passed")
        ux = _make_ux_result(status="passed")
        write_proof_artifacts(rt, ux, tmp_path)
        completeness = check_proof_completeness(tmp_path, "react-vite")
        assert completeness["complete"] is True
        assert completeness["runtime_proof_exists"] is True
        assert completeness["ux_proof_exists"] is True

    def test_blocked_runtime_records_blocker(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(status="blocked")
        ux = _make_ux_result(status="passed")
        write_proof_artifacts(rt, ux, tmp_path)
        result = check_proof_completeness(tmp_path, "react-vite")
        assert result["complete"] is False
        blocker_text = " ".join(result["blockers"])
        assert "blocked" in blocker_text.lower()

    def test_failed_runtime_records_blocker(self, tmp_path: Path) -> None:
        rt = _make_runtime_result(status="failed")
        ux = _make_ux_result(status="passed")
        write_proof_artifacts(rt, ux, tmp_path)
        result = check_proof_completeness(tmp_path, "react-vite")
        assert result["complete"] is False
        blocker_text = " ".join(result["blockers"])
        assert "failed" in blocker_text.lower()


# ------------------------------------------------------------------
# React-vite profile has expected preview_command
# ------------------------------------------------------------------

class TestReactVitePreviewCommand:
    def test_react_vite_has_preview_command(self, tmp_path: Path) -> None:
        """Even if blocked/skipped, result records the preview command."""
        # Use a mock starter that immediately raises to simulate missing npm
        def _blocked_start(cmd: str, cwd: Path) -> None:
            raise FileNotFoundError("npm not found")

        result = run_runtime_proof(
            tmp_path, "react-vite", _start_fn=_blocked_start,
        )
        assert result["preview_command"] == "npm run dev"
        assert result["status"] == "blocked"

    def test_runtime_proof_timeout_env_bounds_polling(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SIGNALOS_PROOF_TIMEOUT_S caps runtime proof polling."""
        from signalos_lib.product.stacks import ReactViteAdapter

        port = _find_free_port()
        orig_preview = ReactViteAdapter.preview_plan

        class _FakeProc:
            stdout = None

            def terminate(self) -> None:
                pass

            def wait(self, timeout: float = 5) -> None:
                pass

            def kill(self) -> None:
                pass

        def _fake_start(cmd: str, cwd: Path) -> _FakeProc:
            return _FakeProc()

        def _patched_preview(self: Any, repo_root: Path) -> dict[str, Any]:
            return {
                "command": "python -m http.server",
                "port": port,
                "health_path": "/",
                "timeout_s": 30,
            }

        monkeypatch.setenv("SIGNALOS_PROOF_TIMEOUT_S", "1")
        ReactViteAdapter.preview_plan = _patched_preview  # type: ignore[assignment]
        try:
            result = run_runtime_proof(
                tmp_path, "react-vite", _start_fn=_fake_start,
            )
        finally:
            ReactViteAdapter.preview_plan = orig_preview  # type: ignore[assignment]

        assert result["status"] == "failed"
        assert "within 1s" in " ".join(result["errors"])
        assert result["duration_s"] < 5


# ------------------------------------------------------------------
# Live server integration test
# ------------------------------------------------------------------

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class TestLiveServerProof:
    """Start a real Python HTTP server and run proof against it."""

    @pytest.fixture()
    def live_server(self, tmp_path: Path):
        """Start a simple HTTP server in tmp_path with an index.html."""
        # Write an index.html
        index = tmp_path / "index.html"
        index.write_text(
            '<!DOCTYPE html><html><head><title>Test</title></head>'
            '<body><div id="root">Hello</div></body></html>',
            encoding="utf-8",
        )

        port = _find_free_port()

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, directory=str(tmp_path), **kwargs)

            def log_message(self, *args: Any) -> None:
                pass  # suppress output

        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield port
        server.shutdown()

    def test_runtime_proof_with_live_server(
        self, tmp_path: Path, live_server: int,
    ) -> None:
        port = live_server

        # Mock start_fn that returns a dummy process-like object
        class _FakeProc:
            stdout = None
            def terminate(self) -> None:
                pass
            def wait(self, timeout: float = 5) -> None:
                pass
            def kill(self) -> None:
                pass

        def _fake_start(cmd: str, cwd: Path) -> _FakeProc:
            return _FakeProc()

        # Patch the adapter to use our port
        from signalos_lib.product.stacks import ReactViteAdapter

        orig_preview = ReactViteAdapter.preview_plan

        def _patched_preview(self: Any, repo_root: Path) -> dict[str, Any]:
            return {
                "command": "python -m http.server",
                "port": port,
                "health_path": "/",
                "timeout_s": 10,
            }

        ReactViteAdapter.preview_plan = _patched_preview  # type: ignore[assignment]
        try:
            result = run_runtime_proof(
                tmp_path,
                "react-vite",
                timeout_s=10,
                _start_fn=_fake_start,
                _browser_fn=lambda *_args, **_kwargs: _make_ux_result(),
            )
            assert result["status"] == "passed"
            assert result["health_check"]["responded"] is True
            assert result["health_check"]["status_code"] == 200
        finally:
            ReactViteAdapter.preview_plan = orig_preview  # type: ignore[assignment]

    def test_ux_proof_with_live_server(
        self, tmp_path: Path, live_server: int,
    ) -> None:
        port = live_server
        result = run_ux_proof(
            tmp_path,
            "react-vite",
            port=port,
            _browser_fn=lambda *_args, **_kwargs: _make_ux_result(),
        )
        assert result["status"] == "passed"
        assert all(c["passed"] for c in result["checks"])

    def test_ux_proof_detects_root_element(
        self, tmp_path: Path, live_server: int,
    ) -> None:
        port = live_server
        result = run_ux_proof(
            tmp_path,
            "react-vite",
            port=port,
            _browser_fn=lambda *_args, **_kwargs: _make_ux_result(),
        )
        root_check = next(
            c for c in result["checks"] if c["name"] == "app_root_found"
        )
        assert root_check["passed"] is True

    def test_executed_browser_rejects_vite_shell_whose_js_throws(
        self, tmp_path: Path,
    ) -> None:
        """A 200/root shell is not UX proof when JS throws before mounting."""
        from signalos_lib.product.proof import _run_browser_page

        if proof_mod._playwright_entry(tmp_path) is None:
            pytest.skip("source Playwright is absent; bootstrap is unit-tested separately")

        (tmp_path / "index.html").write_text(
            "<!doctype html><html><body><div id='root'></div>"
            "<script>throw new Error('mount exploded')</script></body></html>",
            encoding="utf-8",
        )
        port = _find_free_port()

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, directory=str(tmp_path), **kwargs)

            def log_message(self, *args: Any) -> None:
                pass

        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = _run_browser_page(
                tmp_path, f"http://127.0.0.1:{port}/", timeout_s=10,
            )
        finally:
            server.shutdown()

        if result["status"] == "unmeasurable":
            pytest.skip("Playwright/Chromium is not installed in this test environment")
        assert result["executed"] is True
        assert result["status"] == "failed"
        checks = {check["name"]: check for check in result["checks"]}
        assert checks["app_root_found"]["passed"] is True
        assert checks["app_root_mounted"]["passed"] is False
        assert checks["no_page_errors"]["passed"] is False
