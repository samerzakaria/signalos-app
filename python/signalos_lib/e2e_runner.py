"""e2e_runner.py - Headless-browser E2E enforcement.

The test-generation and test-driven-development skills enforce that
*unit* tests pass. Unit tests don't catch:
  - components that throw at render / hydrate time
  - CSS that overlaps controls or hides interactive elements
  - routes that 404 against the dev server
  - the dev server failing to start at all
  - console errors from third-party libraries

This module adds the missing layer: when a task is tagged `e2e-testing`,
the orchestrator spawns the project's dev server (vite/next/preact),
launches Playwright headless against the resulting URL, runs a smoke
script, captures any console errors, and fails the task if the page
didn't render cleanly.

The "smart way" pattern from skill_validators.py holds: we don't grade
visual quality, we observe whether the page LOADED + had no console
errors + the named selectors were findable. That's enough to catch the
real regressions (hydration crashes, missing imports, broken CSS that
hides the main button).

Detection:
  - Playwright: `npx playwright --version` succeeds OR @playwright/test in deps
  - Fallback: TDD-runner style "no runner" -> emit warning, treat as advisory

Dev server detection: re-uses tdd_runner.detect_test_runner semantics
adapted for dev (we look for scripts.dev / vite / next).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

__all__ = [
    "is_e2e_task",
    "run_e2e_task",
    "detect_dev_server_command",
    "playwright_available",
]


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------

def playwright_available(root: Path) -> bool:
    """True if Playwright can be invoked in *root*.

    Two paths: the project declares @playwright/test in deps (npx will
    resolve it), OR `playwright` is on PATH (system install).
    """
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        deps = {
            **(data.get("dependencies") or {}),
            **(data.get("devDependencies") or {}),
        }
        if "@playwright/test" in deps or "playwright" in deps:
            return True
    return shutil.which("playwright") is not None


def detect_dev_server_command(root: Path) -> list[str] | None:
    """Return the argv list to spawn the dev server, or None.

    Prefers `npm run dev` if package.json declares it. Falls back to
    `npm run start` or `npm run serve`.
    """
    pkg = root / "package.json"
    if not pkg.is_file():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts") or {}
    for script_name in ("dev", "start", "serve"):
        if script_name in scripts:
            return ["npm", "run", script_name]
    return None


# ---------------------------------------------------------------------------
# Dev server lifecycle
# ---------------------------------------------------------------------------

_PORT_LINE_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)[:](\d{2,5})",
    re.IGNORECASE,
)


def _wait_for_port(host: str, port: int, timeout_sec: float) -> bool:
    """Return True once *host:port* accepts TCP within *timeout_sec*."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _spawn_dev_server(
    root: Path,
    cmd: list[str],
    startup_timeout_sec: float,
) -> tuple[subprocess.Popen | None, str | None, str]:
    """Spawn *cmd* in *root*, read stdout until a localhost URL appears.

    Returns (process, url, log_tail). url is None on failure;
    log_tail is the last ~2KB of stdout/stderr for diagnostics.
    """
    # On Windows, `npm` is a .cmd shim; subprocess needs shell=True OR
    # we resolve to npm.cmd explicitly. shell=True with a list is safe
    # here because the list is fully constructed by us, not user input.
    is_windows = os.name == "nt"
    popen_kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }
    if is_windows:
        popen_kwargs["shell"] = True
        popen_argv: str | list[str] = " ".join(cmd)
    else:
        popen_argv = cmd

    try:
        proc = subprocess.Popen(popen_argv, **popen_kwargs)  # type: ignore[arg-type]
    except (OSError, FileNotFoundError) as exc:
        return (None, None, f"failed to spawn dev server: {exc}")

    captured: list[str] = []
    url: str | None = None
    deadline = time.monotonic() + startup_timeout_sec
    while time.monotonic() < deadline:
        if proc.stdout is None:
            break
        # Non-blocking-ish read: use a short readline timeout via select?
        # Cross-platform fallback: poll proc, sleep a hair, peek.
        if proc.poll() is not None:
            # Server exited before announcing a port.
            remaining = proc.stdout.read() if proc.stdout else ""
            captured.append(remaining or "")
            break
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        captured.append(line)
        m = _PORT_LINE_RE.search(line)
        if m:
            port = int(m.group(1))
            # Wait a tick for the server to actually accept connections.
            if _wait_for_port("127.0.0.1", port, timeout_sec=10.0):
                url = f"http://127.0.0.1:{port}/"
                break

    log_tail = "".join(captured)
    if len(log_tail) > 2000:
        log_tail = "...\n" + log_tail[-2000:]
    return (proc, url, log_tail)


def _stop_dev_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Playwright smoke script
# ---------------------------------------------------------------------------

# A minimal Playwright smoke: load the URL, wait for network idle, capture
# console errors. Optional selectors come from the task description.
_PLAYWRIGHT_SMOKE_TEMPLATE = """\
// Auto-generated by SignalOS e2e_runner. Do not edit by hand.
const { chromium } = require('playwright');

const URL = process.env.SIGNALOS_E2E_URL || 'http://127.0.0.1:5173/';
const SELECTORS = (process.env.SIGNALOS_E2E_SELECTORS || '').split(',').filter(Boolean);
const TIMEOUT_MS = parseInt(process.env.SIGNALOS_E2E_TIMEOUT_MS || '15000', 10);

(async () => {
  const errors = [];
  const consoleErrors = [];
  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
    page.on('requestfailed', (req) => {
      const f = req.failure();
      if (f) errors.push(`request failed ${req.url()}: ${f.errorText}`);
    });

    await page.goto(URL, { waitUntil: 'networkidle', timeout: TIMEOUT_MS });

    for (const sel of SELECTORS) {
      try {
        await page.waitForSelector(sel, { timeout: 5000, state: 'visible' });
      } catch (e) {
        errors.push(`selector not found / not visible: ${sel}`);
      }
    }

    const result = {
      ok: errors.length === 0 && consoleErrors.length === 0,
      errors,
      consoleErrors,
      url: URL,
      checkedSelectors: SELECTORS,
    };
    process.stdout.write(JSON.stringify(result));
    process.exit(result.ok ? 0 : 1);
  } finally {
    await browser.close();
  }
})().catch((err) => {
  process.stdout.write(JSON.stringify({
    ok: false,
    errors: [`runner crash: ${err && err.message ? err.message : String(err)}`],
    consoleErrors: [],
    url: URL,
    checkedSelectors: [],
  }));
  process.exit(2);
});
"""


def _run_playwright_smoke(
    root: Path,
    url: str,
    selectors: list[str],
    timeout_sec: float = 30.0,
) -> tuple[bool, dict[str, Any], str]:
    """Run the Playwright smoke script. Returns (ok, parsed_json, raw_output)."""
    # Drop the smoke script into a temp file so we don't pollute the workspace.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8",
    ) as f:
        f.write(_PLAYWRIGHT_SMOKE_TEMPLATE)
        script_path = f.name

    env = {
        **os.environ,
        "SIGNALOS_E2E_URL": url,
        "SIGNALOS_E2E_SELECTORS": ",".join(selectors),
        "SIGNALOS_E2E_TIMEOUT_MS": "15000",
    }
    cmd = ["node", script_path]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return (False, {"errors": [f"playwright timed out after {timeout_sec}s"]}, "")
    except OSError as exc:
        return (False, {"errors": [f"node not available: {exc}"]}, "")
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

    raw = (proc.stdout or "").strip()
    try:
        # The script's last write is the JSON result.
        parsed = json.loads(raw.splitlines()[-1]) if raw else {}
    except (ValueError, IndexError):
        parsed = {"errors": [f"unparseable runner output: {raw[:300]}"]}

    return (proc.returncode == 0, parsed, raw)


# ---------------------------------------------------------------------------
# Task entrypoint
# ---------------------------------------------------------------------------

def is_e2e_task(task: dict) -> bool:
    skills = task.get("skills") or []
    return "e2e-testing" in skills


def _extract_selectors_from_description(description: str) -> list[str]:
    """Parse "selectors: a, b, c" out of a task description.

    The AI can hint at what to verify by writing:
        Selectors: button[data-test=submit], input[name=email]
    in the task description. Falls back to empty list (just verifies
    the page loads with no console errors).
    """
    if not description:
        return []
    m = re.search(r"(?:^|\n)\s*selectors?\s*:\s*([^\n]+)", description, re.IGNORECASE)
    if not m:
        return []
    return [s.strip() for s in m.group(1).split(",") if s.strip()]


def run_e2e_task(
    task: dict,
    root: Path,
    startup_timeout_sec: float = 60.0,
    smoke_timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """Run the e2e-testing enforcement on *task* in *root*.

    Returns a dict shaped like:
      { ok: bool, failure: str|None, errors: list[str],
        consoleErrors: list[str], url: str|None, log: str }
    Caller uses ok=False to mark the orchestrator task as failed and
    feeds `failure` into previous_failure for smart retry.
    """
    dev_cmd = detect_dev_server_command(root)
    if dev_cmd is None:
        return {
            "ok": True,
            "failure": None,
            "errors": [],
            "consoleErrors": [],
            "url": None,
            "log": "no dev script in package.json; skipping e2e (advisory)",
            "skipped": True,
        }
    if not playwright_available(root):
        return {
            "ok": True,
            "failure": None,
            "errors": [],
            "consoleErrors": [],
            "url": None,
            "log": (
                "playwright not installed; e2e enforcement collapsed to "
                "advisory. Add @playwright/test to devDependencies to "
                "enable headless verification."
            ),
            "skipped": True,
        }

    proc, url, server_log = _spawn_dev_server(root, dev_cmd, startup_timeout_sec)
    if proc is None or url is None:
        if proc is not None:
            _stop_dev_server(proc)
        return {
            "ok": False,
            "failure": (
                "e2e: dev server failed to come up within "
                f"{startup_timeout_sec}s. Server log tail:\n\n{server_log}"
            ),
            "errors": ["dev server did not announce a port"],
            "consoleErrors": [],
            "url": None,
            "log": server_log,
        }

    try:
        selectors = _extract_selectors_from_description(task.get("description") or "")
        ok, parsed, raw_out = _run_playwright_smoke(
            root, url, selectors, timeout_sec=smoke_timeout_sec,
        )
    finally:
        _stop_dev_server(proc)

    errors = list(parsed.get("errors") or [])
    console_errors = list(parsed.get("consoleErrors") or [])

    if not ok:
        # Compose a single human-readable failure message that the
        # orchestrator can stuff into previous_failure.
        parts = []
        if errors:
            parts.append("Errors:\n" + "\n".join(f"  - {e}" for e in errors))
        if console_errors:
            parts.append(
                "Console errors:\n" + "\n".join(f"  - {e}" for e in console_errors)
            )
        if not parts:
            parts.append("Playwright smoke failed without a parseable error list.")
            parts.append(f"Raw output:\n{raw_out[:1000]}")
        return {
            "ok": False,
            "failure": "e2e smoke FAILED:\n" + "\n\n".join(parts),
            "errors": errors,
            "consoleErrors": console_errors,
            "url": url,
            "log": server_log,
        }

    return {
        "ok": True,
        "failure": None,
        "errors": [],
        "consoleErrors": [],
        "url": url,
        "log": server_log,
        "checkedSelectors": selectors,
    }
