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
        "status": status,
        "checks": [
            {"name": "http_status_200", "passed": True, "detail": "HTTP 200"},
            {"name": "body_not_blank", "passed": True, "detail": "Body length: 120 chars"},
        ],
        "errors": [],
    }
    base.update(overrides)
    return base


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

    def test_runtime_html_snapshot_can_prove_ux_without_live_port(self, tmp_path: Path) -> None:
        result = run_ux_proof(
            tmp_path,
            "react-vite",
            port=None,
            html='<!doctype html><html><body><div id="root">Ready</div></body></html>',
        )
        assert result["status"] == "passed"
        assert all(check["passed"] for check in result["checks"])

    def test_blank_runtime_html_snapshot_fails_ux(self, tmp_path: Path) -> None:
        result = run_ux_proof(tmp_path, "react-vite", port=None, html="")
        assert result["status"] == "failed"
        assert any("blank" in err.lower() for err in result["errors"])

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
                tmp_path, "react-vite", timeout_s=10, _start_fn=_fake_start,
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
        result = run_ux_proof(tmp_path, "react-vite", port=port)
        assert result["status"] == "passed"
        assert all(c["passed"] for c in result["checks"])

    def test_ux_proof_detects_root_element(
        self, tmp_path: Path, live_server: int,
    ) -> None:
        port = live_server
        result = run_ux_proof(tmp_path, "react-vite", port=port)
        root_check = next(
            c for c in result["checks"] if c["name"] == "has_root_element"
        )
        assert root_check["passed"] is True
