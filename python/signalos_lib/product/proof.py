"""Runtime and UX proof for the SignalOS delivery bridge (Phase P10).

Starts the preview/dev server, checks health, captures proof artifacts,
and verifies that UX surfaces are present (no blank page).  Gracefully
handles missing toolchains by returning ``status="blocked"`` rather
than crashing.
"""

from __future__ import annotations

__all__ = [
    "check_proof_completeness",
    "run_runtime_proof",
    "run_ux_proof",
    "write_proof_artifacts",
]

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .stacks import get_adapter


def _proof_timeout_s(default_s: int) -> int:
    raw = os.environ.get("SIGNALOS_PROOF_TIMEOUT_S", "").strip()
    if not raw:
        return default_s
    try:
        parsed = int(raw)
    except ValueError:
        return default_s
    return parsed if parsed > 0 else default_s


# ------------------------------------------------------------------
# Runtime proof
# ------------------------------------------------------------------

def _start_server(command: str, repo_root: Path) -> subprocess.Popen:
    """Start a dev/preview server as a subprocess."""
    return subprocess.Popen(
        command,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=True,
    )


def _poll_health(
    url: str,
    timeout_s: float,
    poll_interval: float = 0.5,
) -> dict[str, Any]:
    """Poll *url* until it responds or *timeout_s* elapses.

    Returns a health-check result dict.
    """
    deadline = time.monotonic() + timeout_s
    last_error: str | None = None

    while time.monotonic() < deadline:
        try:
            start = time.perf_counter()
            resp = urlopen(url, timeout=5)  # noqa: S310
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "url": url,
                "status_code": resp.status,
                "responded": True,
                "response_time_ms": round(elapsed_ms, 1),
            }
        except (URLError, OSError, ConnectionError) as exc:
            last_error = str(exc)
            time.sleep(poll_interval)

    return {
        "url": url,
        "status_code": None,
        "responded": False,
        "response_time_ms": None,
        "last_error": last_error,
    }


def _stop_server(proc: subprocess.Popen, *, grace_s: float = 3.0) -> str:
    """Terminate the server process and return captured output."""
    try:
        proc.terminate()
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)

    stdout = ""
    if proc.stdout is not None:
        try:
            stdout = proc.stdout.read() or ""
        except (OSError, ValueError):
            pass
    return stdout


def run_runtime_proof(
    repo_root: Path,
    profile: str,
    timeout_s: int = 30,
    *,
    _start_fn: Any = None,
) -> dict[str, Any]:
    """Start the preview/dev server, check health, capture proof.

    Parameters
    ----------
    repo_root:
        Root of the product repository.
    profile:
        Stack adapter profile id (e.g. ``"react-vite"``, ``"generic"``).
    timeout_s:
        Max seconds to wait for the health endpoint.
    _start_fn:
        Internal hook for testing -- replaces ``_start_server``.

    Returns
    -------
    dict with keys: status, profile, preview_command, port,
    health_check, html_snapshot, server_log, duration_s, errors.
    """
    start_wall = time.perf_counter()
    errors: list[str] = []

    adapter = get_adapter(profile)
    preview = adapter.preview_plan(repo_root)
    command = preview.get("command")
    port = preview.get("port")
    health_path = preview.get("health_path") or "/"
    plan_timeout = _proof_timeout_s(int(preview.get("timeout_s") or timeout_s))

    # No preview command means the profile cannot produce a runtime
    if command is None:
        return {
            "status": "skipped",
            "profile": profile,
            "preview_command": None,
            "port": None,
            "health_check": {
                "url": None,
                "status_code": None,
                "responded": False,
                "response_time_ms": None,
            },
            "server_log": "",
            "duration_s": round(time.perf_counter() - start_wall, 3),
            "errors": ["No preview command for this profile"],
        }

    # Start server
    starter = _start_fn or _start_server
    try:
        proc = starter(command, repo_root)
    except (OSError, FileNotFoundError) as exc:
        return {
            "status": "blocked",
            "profile": profile,
            "preview_command": command,
            "port": port,
            "health_check": {
                "url": None,
                "status_code": None,
                "responded": False,
                "response_time_ms": None,
            },
            "server_log": "",
            "duration_s": round(time.perf_counter() - start_wall, 3),
            "errors": [f"Could not start server: {exc}"],
        }

    # Poll health endpoint
    url = f"http://localhost:{port}{health_path}"
    health = _poll_health(url, plan_timeout)
    html_snapshot = ""
    if health["responded"]:
        try:
            resp = urlopen(url, timeout=5)  # noqa: S310
            html_snapshot = resp.read().decode("utf-8", errors="replace")
        except (URLError, OSError, ConnectionError):
            html_snapshot = ""

    # Capture server log and stop
    server_log = _stop_server(proc)

    status = "passed" if health["responded"] else "failed"
    if not health["responded"]:
        errors.append(
            f"Health check did not respond within {plan_timeout}s"
        )

    return {
        "status": status,
        "profile": profile,
        "preview_command": command,
        "port": port,
        "health_check": health,
        "html_snapshot": html_snapshot[:200_000],
        "server_log": server_log,
        "duration_s": round(time.perf_counter() - start_wall, 3),
        "errors": errors,
    }


# ------------------------------------------------------------------
# UX proof
# ------------------------------------------------------------------

_ERROR_INDICATORS = [
    "Cannot GET",
    "Internal Server Error",
    "500 Internal Server Error",
    "404 Not Found",
    "ENOENT",
]


def run_ux_proof(
    repo_root: Path,
    profile: str,
    port: int | None = None,
    *,
    html: str | None = None,
    status_code: int = 200,
) -> dict[str, Any]:
    """Check UX surfaces are present (no blank page).

    For profiles with a preview server, fetches the main page HTML and
    verifies it is not empty and contains expected elements.

    Parameters
    ----------
    repo_root:
        Root of the product repository.
    profile:
        Stack adapter profile id.
    port:
        Port to check.  If ``None``, returns skipped.
    html:
        Optional runtime-captured page HTML.  When provided, UX proof uses this
        snapshot instead of making another network call after the preview
        process has been stopped.
    """
    if port is None and html is None:
        return {
            "status": "skipped",
            "checks": [],
            "errors": ["No port provided; runtime proof was skipped or unavailable"],
        }

    url = f"http://localhost:{port}/" if port is not None else "runtime-html-snapshot"
    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    # Fetch the page unless runtime proof already captured the HTML before
    # shutting down the preview process.
    if html is None:
        try:
            resp = urlopen(url, timeout=10)  # noqa: S310
            html = resp.read().decode("utf-8", errors="replace")
            status_code = resp.status
        except (URLError, OSError, ConnectionError) as exc:
            return {
                "status": "failed",
                "checks": [
                    {
                        "name": "page_fetch",
                        "passed": False,
                        "detail": f"Could not fetch {url}: {exc}",
                    },
                ],
                "errors": [str(exc)],
            }

    # Check 1: HTTP status is 200
    ok_status = status_code == 200
    checks.append({
        "name": "http_status_200",
        "passed": ok_status,
        "detail": f"HTTP {status_code}",
    })
    if not ok_status:
        errors.append(f"HTTP status {status_code}, expected 200")

    # Check 2: Body is not empty
    body_len = len(html.strip())
    not_blank = body_len > 0
    checks.append({
        "name": "body_not_blank",
        "passed": not_blank,
        "detail": f"Body length: {body_len} chars",
    })
    if not not_blank:
        errors.append("Page body is blank")

    # Check 3: Contains expected root element
    has_root = 'id="root"' in html or 'id="app"' in html or "<body" in html
    checks.append({
        "name": "has_root_element",
        "passed": has_root,
        "detail": "Found root/app element" if has_root else "No root element detected",
    })

    # Check 4: No error indicators
    found_errors = [ind for ind in _ERROR_INDICATORS if ind in html]
    no_errors = len(found_errors) == 0
    checks.append({
        "name": "no_error_indicators",
        "passed": no_errors,
        "detail": f"Error indicators found: {found_errors}" if found_errors else "No error indicators",
    })
    if not no_errors:
        errors.append(f"Error indicators found in page: {found_errors}")

    all_passed = all(c["passed"] for c in checks)
    return {
        "status": "passed" if all_passed else "failed",
        "checks": checks,
        "errors": errors,
    }


# ------------------------------------------------------------------
# Artifact persistence
# ------------------------------------------------------------------

_PROOF_DIR = "product/proof/runtime"


def write_proof_artifacts(
    runtime_result: dict[str, Any],
    ux_result: dict[str, Any],
    repo_root: Path,
) -> Path:
    """Write proof artifacts to ``.signalos/product/proof/runtime/``.

    Creates:
    - ``smoke.json``    -- runtime proof result
    - ``ux-smoke.json`` -- UX proof result
    - ``preview.log``   -- server log capture

    Returns the proof directory path.
    """
    proof_dir = repo_root / ".signalos" / _PROOF_DIR
    proof_dir.mkdir(parents=True, exist_ok=True)

    (proof_dir / "smoke.json").write_text(
        json.dumps(runtime_result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (proof_dir / "ux-smoke.json").write_text(
        json.dumps(ux_result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (proof_dir / "preview.log").write_text(
        runtime_result.get("server_log", ""),
        encoding="utf-8",
    )

    return proof_dir


# ------------------------------------------------------------------
# Completeness check
# ------------------------------------------------------------------

def check_proof_completeness(
    repo_root: Path,
    profile: str,
) -> dict[str, Any]:
    """Check if runtime/UX proof is complete for *profile*.

    Returns a dict with completeness status, individual proof
    existence, statuses, and blockers.
    """
    proof_dir = repo_root / ".signalos" / _PROOF_DIR

    smoke_path = proof_dir / "smoke.json"
    ux_path = proof_dir / "ux-smoke.json"

    runtime_exists = smoke_path.is_file()
    ux_exists = ux_path.is_file()

    runtime_status: str | None = None
    ux_status: str | None = None
    blockers: list[str] = []

    if runtime_exists:
        try:
            data = json.loads(smoke_path.read_text(encoding="utf-8"))
            runtime_status = data.get("status")
        except (json.JSONDecodeError, OSError):
            runtime_status = None
    if ux_exists:
        try:
            data = json.loads(ux_path.read_text(encoding="utf-8"))
            ux_status = data.get("status")
        except (json.JSONDecodeError, OSError):
            ux_status = None

    # Determine completeness
    adapter = get_adapter(profile)
    preview = adapter.preview_plan(repo_root)
    has_preview = preview.get("command") is not None

    if has_preview:
        # Profile expects runtime proof
        if not runtime_exists:
            blockers.append("Runtime proof artifact missing")
        elif runtime_status == "blocked":
            blockers.append("Runtime proof blocked by infrastructure")
        elif runtime_status == "failed":
            blockers.append("Runtime proof failed")
        elif runtime_status is None:
            blockers.append("Runtime proof artifact is corrupt")

        if not ux_exists:
            blockers.append("UX proof artifact missing")
        elif ux_status == "failed":
            blockers.append("UX proof failed")
        elif ux_status is None:
            blockers.append("UX proof artifact is corrupt")

        complete = (
            runtime_exists
            and ux_exists
            and runtime_status in ("passed",)
            and ux_status in ("passed",)
        )
    else:
        # Generic / no-preview profile: both skipped is acceptable
        if runtime_exists and runtime_status == "skipped":
            pass  # expected
        elif not runtime_exists:
            blockers.append("Runtime proof artifact missing")

        if ux_exists and ux_status == "skipped":
            pass  # expected
        elif not ux_exists:
            blockers.append("UX proof artifact missing")

        complete = (
            runtime_exists
            and ux_exists
            and runtime_status == "skipped"
            and ux_status == "skipped"
        )

    return {
        "complete": complete,
        "runtime_proof_exists": runtime_exists,
        "ux_proof_exists": ux_exists,
        "runtime_status": runtime_status,
        "ux_status": ux_status,
        "blockers": blockers,
    }
