"""Runtime and UX proof for the SignalOS delivery bridge (Phase P10).

Starts the preview/dev server, checks health, captures proof artifacts,
and verifies that UX surfaces are present (no blank page).  Gracefully
handles missing toolchains by returning ``status="blocked"`` rather
than crashing.
"""

from __future__ import annotations

__all__ = [
    "check_proof_completeness",
    "requires_browser_ux_proof",
    "run_runtime_proof",
    "run_ux_proof",
    "write_proof_artifacts",
]

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .stacks import get_adapter


_BROWSER_PROOF_SCHEMA = "signalos.ux-browser-proof.v1"
_PLAYWRIGHT_VERSION = "1.61.1"
_PLAYWRIGHT_PACKAGE_TIMEOUT_S = 180
_PLAYWRIGHT_BROWSER_TIMEOUT_S = 300
_PLAYWRIGHT_LOCK_TIMEOUT_S = 60
_SENSITIVE_ENV_MARKERS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH",
)


def _redact_proof_error(value: object) -> str:
    """Return diagnostic text with credentials and secret env values removed."""
    text = str(value or "")
    for name, secret in os.environ.items():
        upper = name.upper()
        if (
            secret
            and len(secret) >= 6
            and any(marker in upper for marker in _SENSITIVE_ENV_MARKERS)
        ):
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"(?i)(https?://[^\s/:@]+:)[^\s/@]+@",
        r"\1[REDACTED]@",
        text,
    )
    text = re.sub(
        r"(?i)(access_token|api[_-]?key|token|secret|password)=([^&\s]+)",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,})\b",
        "[REDACTED]",
        text,
    )
    return text[:2000]


def _unmeasurable_browser_proof(reason: str) -> dict[str, Any]:
    return {
        "schema_version": _BROWSER_PROOF_SCHEMA,
        "status": "unmeasurable",
        "executed": False,
        "runner": "playwright",
        "checks": [],
        "errors": [_redact_proof_error(reason)],
    }


def _playwright_entry(repo_root: Path) -> Path | None:
    """Find a real installed Playwright runner without downloading anything."""
    candidates = [Path(repo_root).resolve()]
    try:
        candidates.append(Path(__file__).resolve().parents[3])
    except IndexError:
        pass
    seen: set[Path] = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        entry = base / "node_modules" / "playwright" / "index.js"
        if entry.is_file():
            return entry
    return None


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _playwright_tooling_cache(repo_root: Path) -> Path:
    """Return a user-scoped cache that can never be the product workspace."""
    if os.name == "nt":
        base = Path(
            os.environ.get("LOCALAPPDATA")
            or (Path.home() / "AppData" / "Local")
        )
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    cache = base.expanduser() / "SignalOS" / "tooling" / "playwright" / _PLAYWRIGHT_VERSION
    if not _path_is_within(cache, base.expanduser()):
        raise ValueError("per-user Playwright tooling cache is redirected")
    if _path_is_within(cache, Path(repo_root)):
        raise ValueError("per-user Playwright tooling cache resolves inside the product workspace")
    return cache


def _browser_cache_env(browser_cache: Path | None) -> dict[str, str]:
    # Browser/tooling subprocesses are a separate trust domain from the
    # provider adapter.  They need ordinary host configuration (PATH, proxy,
    # certificates), never model/provider credentials or env-file pointers.
    from .sandbox import _child_process_env

    env = _child_process_env()
    if browser_cache is not None:
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
    env["npm_config_audit"] = "false"
    env["npm_config_fund"] = "false"
    env["npm_config_update_notifier"] = "false"
    return env


def _cached_playwright_ready(
    node: str,
    entry: Path,
    browser_cache: Path,
) -> bool:
    """Verify that the pinned package's matching Chromium executable exists."""
    if not entry.is_file() or not browser_cache.is_dir():
        return False
    try:
        package = json.loads(
            (entry.parent / "package.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    if (
        not isinstance(package, dict)
        or package.get("version") != _PLAYWRIGHT_VERSION
        or not _path_is_within(entry, browser_cache.parent)
        or not _path_is_within(browser_cache, browser_cache.parent)
    ):
        return False
    script = (
        "const fs=require('fs');"
        "const {chromium}=require(process.argv[1]);"
        "const p=chromium.executablePath();"
        "if(fs.existsSync(p)){process.stdout.write(p);process.exit(0)}"
        "process.exit(1);"
    )
    try:
        checked = subprocess.run(
            [node, "-e", script, str(entry)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            env=_browser_cache_env(browser_cache),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if checked.returncode != 0 or not (checked.stdout or "").strip():
        return False
    executable = Path((checked.stdout or "").strip())
    return executable.is_file() and _path_is_within(executable, browser_cache)


def _write_tooling_manifest(cache: Path) -> None:
    payload = {
        "name": "signalos-playwright-tooling",
        "private": True,
        "version": "0.0.0",
        "dependencies": {"playwright": _PLAYWRIGHT_VERSION},
    }
    target = cache / "package.json"
    tmp = cache / f".package-{os.getpid()}.tmp"
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _acquire_tooling_lock(path: Path) -> int | None:
    deadline = time.monotonic() + _PLAYWRIGHT_LOCK_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, str(os.getpid()).encode("ascii"))
                return descriptor
            except OSError:
                os.close(descriptor)
                path.unlink(missing_ok=True)
                return None
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > (
                    _PLAYWRIGHT_PACKAGE_TIMEOUT_S + _PLAYWRIGHT_BROWSER_TIMEOUT_S
                )
                if stale:
                    path.unlink()
                    continue
            except OSError:
                pass
            time.sleep(0.2)
        except OSError:
            return None
    return None


def _bootstrap_playwright_runtime(
    repo_root: Path,
    node: str,
) -> dict[str, Any]:
    """Install pinned Playwright + Chromium into the per-user tooling cache."""
    try:
        cache = _playwright_tooling_cache(repo_root)
        cache.mkdir(parents=True, exist_ok=True)
        # Resolve again after creation to detect a redirected/symlinked cache.
        if _path_is_within(cache, Path(repo_root)):
            raise ValueError("tooling cache redirects into the product workspace")
    except (OSError, ValueError):
        return {"error": "Per-user Playwright tooling cache is unavailable"}

    entry = cache / "node_modules" / "playwright" / "index.js"
    browser_cache = cache / "browsers"
    if _cached_playwright_ready(node, entry, browser_cache):
        return {
            "entry": entry,
            "browser_cache": browser_cache,
            "source": "user-tooling-cache",
            "version": _PLAYWRIGHT_VERSION,
        }

    npm = shutil.which("npm")
    if not npm:
        return {"error": "npm is unavailable for pinned Playwright bootstrap"}

    lock_path = cache / ".bootstrap.lock"
    descriptor = _acquire_tooling_lock(lock_path)
    if descriptor is None:
        return {"error": "Pinned Playwright tooling bootstrap lock timed out"}
    try:
        if _cached_playwright_ready(node, entry, browser_cache):
            return {
                "entry": entry,
                "browser_cache": browser_cache,
                "source": "user-tooling-cache",
                "version": _PLAYWRIGHT_VERSION,
            }
        try:
            _write_tooling_manifest(cache)
            installed = subprocess.run(
                [
                    npm, "install", "--prefix", str(cache), "--ignore-scripts",
                    "--no-audit", "--no-fund", "--package-lock=false",
                    "--save-exact", f"playwright@{_PLAYWRIGHT_VERSION}",
                ],
                cwd=str(cache),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PLAYWRIGHT_PACKAGE_TIMEOUT_S,
                check=False,
                env=_browser_cache_env(browser_cache),
            )
        except subprocess.TimeoutExpired:
            return {"error": "Pinned Playwright package bootstrap timed out"}
        except OSError:
            return {"error": "Pinned Playwright package bootstrap could not start"}
        if installed.returncode != 0 or not entry.is_file():
            # Deliberately do not surface npm output: registry URLs and npm
            # credentials may occur there.
            return {"error": "Pinned Playwright package bootstrap failed"}

        cli = entry.parent / "cli.js"
        if not cli.is_file():
            return {"error": "Pinned Playwright browser installer is missing"}
        try:
            browser = subprocess.run(
                [node, str(cli), "install", "chromium"],
                cwd=str(cache),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PLAYWRIGHT_BROWSER_TIMEOUT_S,
                check=False,
                env=_browser_cache_env(browser_cache),
            )
        except subprocess.TimeoutExpired:
            return {"error": "Pinned Chromium tooling bootstrap timed out"}
        except OSError:
            return {"error": "Pinned Chromium tooling bootstrap could not start"}
        if browser.returncode != 0:
            return {"error": "Pinned Chromium tooling bootstrap failed"}
        if not _cached_playwright_ready(node, entry, browser_cache):
            return {"error": "Pinned Chromium tooling verification failed"}
        return {
            "entry": entry,
            "browser_cache": browser_cache,
            "source": "user-tooling-cache",
            "version": _PLAYWRIGHT_VERSION,
        }
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _resolve_playwright_runtime(repo_root: Path, node: str) -> dict[str, Any]:
    """Prefer existing product/source tooling; bootstrap only when absent."""
    entry = _playwright_entry(repo_root)
    if entry is not None:
        return {
            "entry": entry,
            "browser_cache": None,
            "source": "product-or-source",
            "version": None,
        }
    return _bootstrap_playwright_runtime(repo_root, node)


def _run_browser_page(
    repo_root: Path,
    url: str,
    *,
    timeout_s: int = 20,
) -> dict[str, Any]:
    """Execute *url* in Chromium and measure the post-JavaScript DOM.

    HTTP/raw HTML is intentionally insufficient: a Vite shell can return 200
    while its JavaScript throws before mounting.  The emitted receipt therefore
    records whether a browser page actually ran and whether its app root was
    mounted, visible, and free of page/console errors.
    """
    node = shutil.which("node")
    if not node:
        return _unmeasurable_browser_proof("Node.js browser runner is unavailable")
    runtime = _resolve_playwright_runtime(repo_root, node)
    playwright = runtime.get("entry")
    if not isinstance(playwright, Path):
        return _unmeasurable_browser_proof(
            str(runtime.get("error") or "Playwright tooling is unavailable")
        )
    browser_cache = runtime.get("browser_cache")

    timeout_ms = max(1, int(timeout_s)) * 1000
    script = r"""
const { chromium } = require(process.argv[1]);
const target = process.argv[2];
const timeoutMs = Number(process.argv[3]);
(async () => {
  let browser;
  let browserChannel = 'bundled-chromium';
  const pageErrors = [];
  const consoleErrors = [];
  try {
    const launchPlans = [
      { label: 'bundled-chromium', options: { headless: true } },
      { label: 'stable-chrome', options: { headless: true, channel: 'chrome' } },
      { label: 'stable-edge', options: { headless: true, channel: 'msedge' } },
    ];
    for (const plan of launchPlans) {
      try {
        browser = await chromium.launch(plan.options);
        browserChannel = plan.label;
        break;
      } catch (_) {}
    }
    if (!browser) {
      throw new Error('No bundled Chromium, stable Chrome, or stable Edge browser could be launched');
    }
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    page.on('pageerror', error => pageErrors.push(String(error && error.message || error)));
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    const response = await page.goto(target, {
      waitUntil: 'domcontentloaded', timeout: timeoutMs
    });
    await page.waitForTimeout(750);
    const measured = await page.evaluate(() => {
      const root = document.getElementById('root') || document.getElementById('app');
      const rect = root ? root.getBoundingClientRect() : null;
      const style = root ? window.getComputedStyle(root) : null;
      const mounted = Boolean(root && root.innerHTML.trim() && root.childNodes.length);
      const visible = Boolean(rect && rect.width > 0 && rect.height > 0 &&
        style && style.display !== 'none' && style.visibility !== 'hidden');
      return {
        ready_state: document.readyState,
        root_found: Boolean(root),
        root_mounted: mounted,
        root_visible: visible,
        root_html_length: root ? root.innerHTML.trim().length : 0,
        body_text_length: (document.body && document.body.innerText || '').trim().length,
      };
    });
    const statusCode = response ? response.status() : 0;
    const checks = [
      { name: 'browser_navigation', passed: statusCode >= 200 && statusCode < 400,
        detail: `HTTP ${statusCode}` },
      { name: 'document_complete', passed: measured.ready_state === 'complete',
        detail: `readyState=${measured.ready_state}` },
      { name: 'app_root_found', passed: measured.root_found,
        detail: measured.root_found ? 'root/app found' : 'root/app missing' },
      { name: 'app_root_mounted', passed: measured.root_mounted,
        detail: `rendered HTML length=${measured.root_html_length}` },
      { name: 'app_root_visible', passed: measured.root_visible,
        detail: measured.root_visible ? 'mounted root is visible' : 'mounted root is not visible' },
      { name: 'no_page_errors', passed: pageErrors.length === 0,
        detail: pageErrors.length ? pageErrors.join('; ') : 'no page errors' },
      { name: 'no_console_errors', passed: consoleErrors.length === 0,
        detail: consoleErrors.length ? consoleErrors.join('; ') : 'no console errors' },
    ];
    const errors = [...pageErrors, ...consoleErrors];
    for (const check of checks) if (!check.passed) errors.push(check.detail);
    process.stdout.write(JSON.stringify({
      schema_version: 'signalos.ux-browser-proof.v1',
      status: checks.every(check => check.passed) ? 'passed' : 'failed',
      executed: true,
      runner: 'playwright',
      browser_channel: browserChannel,
      url: target,
      checks,
      measurements: measured,
      errors: [...new Set(errors)],
    }) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({
      schema_version: 'signalos.ux-browser-proof.v1',
      status: 'unmeasurable',
      executed: false,
      runner: 'playwright',
      url: target,
      checks: [],
      errors: [String(error && error.message || error)],
    }) + '\n');
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
})().catch(error => {
  process.stdout.write(JSON.stringify({
    schema_version: 'signalos.ux-browser-proof.v1', status: 'unmeasurable',
    executed: false, runner: 'playwright', checks: [],
    errors: [String(error && error.message || error)]
  }) + '\n');
});
"""
    try:
        completed = subprocess.run(
            [node, "-e", script, str(playwright), url, str(timeout_ms)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(timeout_s) + 10),
            check=False,
            env=_browser_cache_env(
                browser_cache if isinstance(browser_cache, Path) else None
            ),
        )
    except subprocess.TimeoutExpired:
        return _unmeasurable_browser_proof("Playwright browser proof timed out")
    except OSError:
        return _unmeasurable_browser_proof(
            "Playwright browser proof could not start"
        )

    result: dict[str, Any] | None = None
    for line in reversed((completed.stdout or "").splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            result = candidate
            break
    if result is None:
        return _unmeasurable_browser_proof(
            "Playwright browser runner returned no valid proof receipt"
        )
    if result.get("schema_version") != _BROWSER_PROOF_SCHEMA:
        return _unmeasurable_browser_proof(
            "Playwright browser runner returned an invalid proof schema"
        )
    result["tooling_source"] = runtime.get("source")
    result["playwright_version"] = runtime.get("version")
    result["errors"] = [
        _redact_proof_error(error) for error in list(result.get("errors") or [])
    ]
    checks = result.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict) and "detail" in check:
                check["detail"] = _redact_proof_error(check.get("detail"))
    return result


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
    from .sandbox import _child_process_env

    popen_kwargs: dict[str, Any] = {
        "cwd": str(repo_root),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": True,
        # The preview executes model-authored product code.  It must not inherit
        # the sidecar's provider credentials or credential-file pointers.
        "env": _child_process_env(),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0,
        )
    else:
        popen_kwargs["start_new_session"] = True

    return subprocess.Popen(
        command,
        **popen_kwargs,
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
            with urlopen(url, timeout=5) as resp:  # noqa: S310
                status_code = resp.status
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "url": url,
                "status_code": status_code,
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
    if os.name == "nt":
        _kill_process_tree(proc)
    else:
        _terminate_process_group(proc)

    try:
        stdout, _ = proc.communicate(timeout=grace_s)
        return stdout or ""
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            stdout, _ = proc.communicate(timeout=2)
            return stdout or ""
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return ""
    except (OSError, ValueError, AttributeError):
        return ""


def _kill_process_tree(proc: subprocess.Popen) -> None:
    pid = getattr(proc, "pid", None)
    if not pid:
        try:
            proc.kill()
        except (AttributeError, OSError):
            pass
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        proc.kill()
    except OSError:
        pass


def _terminate_process_group(proc: subprocess.Popen) -> None:
    pid = getattr(proc, "pid", None)
    if not pid:
        try:
            proc.terminate()
        except (AttributeError, OSError):
            pass
        return

    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            proc.terminate()
        except OSError:
            pass


def run_runtime_proof(
    repo_root: Path,
    profile: str,
    timeout_s: int = 30,
    *,
    _start_fn: Any = None,
    _browser_fn: Any = None,
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
    ux_required = requires_browser_ux_proof(repo_root, profile)

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
            "ux_required": ux_required,
            "browser_ux": None,
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
            "ux_required": ux_required,
            "browser_ux": (
                _unmeasurable_browser_proof("Runtime server could not start")
                if ux_required else None
            ),
        }

    # Poll health endpoint
    url = f"http://localhost:{port}{health_path}"
    health = _poll_health(url, plan_timeout)
    html_snapshot = ""
    if health["responded"]:
        try:
            with urlopen(url, timeout=5) as resp:  # noqa: S310
                html_snapshot = resp.read().decode("utf-8", errors="replace")
        except (URLError, OSError, ConnectionError):
            html_snapshot = ""

    browser_ux: dict[str, Any] | None = None
    if ux_required:
        if health["responded"]:
            browser_runner = _browser_fn or _run_browser_page
            try:
                browser_ux = browser_runner(
                    repo_root, f"http://localhost:{port}/", timeout_s=plan_timeout,
                )
            except Exception as exc:
                browser_ux = _unmeasurable_browser_proof(
                    f"Browser UX runner errored: {type(exc).__name__}: {exc}"
                )
        else:
            browser_ux = _unmeasurable_browser_proof(
                "Runtime server did not respond; browser UX was not executed"
            )

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
        "ux_required": ux_required,
        "browser_ux": browser_ux,
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


def requires_browser_ux_proof(repo_root: Path, profile: str) -> bool:
    """Return whether *profile* must prove browser UX evidence.

    Runtime proof is useful for any runnable product. Browser UX proof is only
    meaningful for profiles that actually deliver an HTML UI. API-only profiles
    still need runtime health proof, but a JSON endpoint must not be judged as a
    failed browser page.
    """
    if profile == "react-vite":
        return True
    if profile in {"generic", "node-api", "fastapi-api", "dotnet-minimal-api", "go-api", "agent-selected"}:
        return False

    try:
        detection = get_adapter(profile).detect(repo_root)
    except (KeyError, OSError):
        return False
    return bool(detection.get("can_deliver_ui"))


def run_ux_proof(
    repo_root: Path,
    profile: str,
    port: int | None = None,
    *,
    html: str | None = None,
    status_code: int = 200,
    browser_result: dict[str, Any] | None = None,
    _browser_fn: Any = None,
) -> dict[str, Any]:
    """Verify UX from an actually executed browser page.

    Raw HTTP HTML is retained as a backward-compatible input but can never
    satisfy a browser-required profile: it cannot prove that application
    JavaScript mounted successfully.  A runtime caller may pass the Playwright
    receipt captured while its preview server was live; direct callers with a
    live *port* execute the same browser runner here.

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
    ux_required = requires_browser_ux_proof(repo_root, profile)
    if not ux_required:
        return {
            "status": "skipped",
            "schema_version": _BROWSER_PROOF_SCHEMA,
            "ux_required": False,
            "executed": False,
            "checks": [],
            "errors": [],
            "skip_reason": "Profile does not require browser UX proof",
        }

    receipt = browser_result
    if receipt is None and port is not None:
        runner = _browser_fn or _run_browser_page
        try:
            receipt = runner(
                repo_root,
                f"http://localhost:{port}/",
                timeout_s=_proof_timeout_s(20),
            )
        except Exception as exc:
            receipt = _unmeasurable_browser_proof(
                f"Browser UX runner errored: {type(exc).__name__}: {exc}"
            )
    if not isinstance(receipt, dict):
        reason = "No executed browser proof is available"
        if html is not None:
            reason += "; a raw HTML snapshot is not executable UX evidence"
        receipt = _unmeasurable_browser_proof(reason)

    result = dict(receipt)
    result["ux_required"] = True
    errors = list(result.get("errors") or [])
    checks = result.get("checks")
    required_checks = {
        "browser_navigation",
        "document_complete",
        "app_root_found",
        "app_root_mounted",
        "app_root_visible",
        "no_page_errors",
        "no_console_errors",
    }
    check_names = {
        str(check.get("name") or "")
        for check in checks
        if isinstance(check, dict)
    } if isinstance(checks, list) else set()
    structurally_valid = (
        result.get("schema_version") == _BROWSER_PROOF_SCHEMA
        and result.get("runner") == "playwright"
        and result.get("executed") is True
        and isinstance(checks, list)
        and required_checks.issubset(check_names)
        and all(
            isinstance(check, dict) and check.get("passed") is True
            for check in checks
        )
    )
    if result.get("status") != "passed" or not structurally_valid:
        if not structurally_valid:
            errors.append("Executed browser proof receipt is incomplete or invalid")
        result["status"] = (
            "failed" if result.get("executed") is True else "unmeasurable"
        )
    result["errors"] = list(dict.fromkeys(str(error) for error in errors if error))
    return result


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
    ux_executed: bool | None = None
    ux_schema: str | None = None
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
            ux_executed = data.get("executed")
            ux_schema = data.get("schema_version")
        except (json.JSONDecodeError, OSError):
            ux_status = None

    # Determine completeness
    adapter = get_adapter(profile)
    preview = adapter.preview_plan(repo_root)
    has_preview = preview.get("command") is not None

    requires_ux = requires_browser_ux_proof(repo_root, profile)

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

        if requires_ux:
            if not ux_exists:
                blockers.append("UX proof artifact missing")
            elif ux_status == "failed":
                blockers.append("UX proof failed")
            elif ux_status in {"skipped", "unmeasurable"}:
                blockers.append(f"UX proof was {ux_status}")
            elif ux_status is None:
                blockers.append("UX proof artifact is corrupt")
            elif ux_executed is not True:
                blockers.append("UX proof did not execute a browser page")
            elif ux_schema != _BROWSER_PROOF_SCHEMA:
                blockers.append("UX proof schema is missing or invalid")
        elif ux_exists and ux_status not in {"skipped", "passed"}:
            blockers.append("Optional UX proof artifact is not skipped")

        complete = (
            runtime_exists
            and runtime_status in ("passed",)
            and (
                ux_status in ("passed",)
                and ux_executed is True
                and ux_schema == _BROWSER_PROOF_SCHEMA
                if requires_ux
                else (not ux_exists or ux_status in {"skipped", "passed"})
            )
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
        "ux_executed": ux_executed,
        "blockers": blockers,
    }
