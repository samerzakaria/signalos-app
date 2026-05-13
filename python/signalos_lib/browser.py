# SignalOS Core — W7 Sprint QA.
# cli/signalos_lib/browser.py
#
# SBrowser — Playwright-native browser engine for SignalOS QA.
# Provides navigate, screenshot, click, fill, wait_for,
# get_console_errors, and measure_vitals. All methods are synchronous
# wrappers around Playwright's sync API so they compose cleanly with
# the rest of the Python-based CLI.
#
# headed=True debug flag: set env SIGNALOS_BROWSER_HEADED=1 or pass
# headed=True to SBrowser() to run with a visible browser window.
#
# Playwright is an optional dep (raise ImportError with install hint
# if missing, matching the AMD-CORE-007 pattern for optional providers).

from __future__ import annotations

__all__ = ["SBrowser", "BrowserError", "VitalsResult", "ConsoleMessage"]

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Lazy Playwright import (optional dep — matches AMD-CORE-007 pattern)
# ---------------------------------------------------------------------------

def _require_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        return sync_playwright
    except ImportError:
        raise ImportError(
            "Playwright is required for SBrowser. "
            "Install it with: pip install playwright && playwright install chromium"
        ) from None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ConsoleMessage:
    type: str        # "log" | "warning" | "error" | "info"
    text: str
    url: str = ""
    line: int = 0

@dataclass
class VitalsResult:
    lcp_ms: float | None = None    # Largest Contentful Paint
    inp_ms: float | None = None    # Interaction to Next Paint (estimated)
    cls: float | None = None       # Cumulative Layout Shift
    ttfb_ms: float | None = None   # Time to First Byte
    total_weight_kb: float | None = None
    resource_count: int | None = None
    measured_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class BrowserError(RuntimeError):
    """Raised when a browser operation fails."""


# ---------------------------------------------------------------------------
# SBrowser
# ---------------------------------------------------------------------------

class SBrowser:
    """
    Playwright-backed browser engine for SignalOS QA.

    Usage
    -----
        browser = SBrowser(headed=True)          # debug mode
        browser = SBrowser()                     # headless (default)

        with browser:
            browser.navigate("https://example.com")
            browser.screenshot("/tmp/shot.png")
            errs = browser.get_console_errors()
            vitals = browser.measure_vitals()

    Or use the context manager — it handles open/close automatically.
    """

    def __init__(
        self,
        headed: bool | None = None,
        timeout_ms: int = 30_000,
        user_agent: str | None = None,
    ) -> None:
        if headed is None:
            headed = os.environ.get("SIGNALOS_BROWSER_HEADED", "0") == "1"
        self._headed = headed
        self._timeout_ms = timeout_ms
        self._user_agent = user_agent

        self._pw: Any = None      # sync_playwright context
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._console_messages: list[ConsoleMessage] = []

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def open(self) -> "SBrowser":
        sync_playwright = _require_playwright()
        self._pw = sync_playwright().start()
        launch_opts: dict[str, Any] = {"headless": not self._headed}
        self._browser = self._pw.chromium.launch(**launch_opts)
        ctx_opts: dict[str, Any] = {}
        if self._user_agent:
            ctx_opts["user_agent"] = self._user_agent
        self._context = self._browser.new_context(**ctx_opts)
        self._page = self._context.new_page()
        self._page.set_default_timeout(self._timeout_ms)
        self._page.on("console", self._on_console)
        return self

    def close(self) -> None:
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._pw:
            self._pw.stop()
            self._pw = None
        self._page = None
        self._context = None

    def __enter__(self) -> "SBrowser":
        return self.open()

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_console(self, msg: Any) -> None:
        self._console_messages.append(
            ConsoleMessage(
                type=msg.type,
                text=msg.text,
                url=msg.location.get("url", "") if msg.location else "",
                line=msg.location.get("lineNumber", 0) if msg.location else 0,
            )
        )

    def _assert_open(self) -> None:
        if self._page is None:
            raise BrowserError(
                "SBrowser is not open. Use 'with SBrowser() as browser:' "
                "or call browser.open() first."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def navigate(self, url: str, wait_until: str = "load") -> None:
        """
        Navigate to *url* and wait for the page to reach *wait_until*
        state (``"load"`` | ``"domcontentloaded"`` | ``"networkidle"``).
        """
        self._assert_open()
        self._console_messages.clear()
        try:
            self._page.goto(url, wait_until=wait_until)
        except Exception as exc:
            raise BrowserError(f"navigate({url!r}) failed: {exc}") from exc

    def screenshot(self, path: str | Path, full_page: bool = True) -> Path:
        """
        Save a screenshot to *path*. Returns the resolved Path.
        Creates parent directories if they don't exist.
        """
        self._assert_open()
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._page.screenshot(path=str(out), full_page=full_page)
        except Exception as exc:
            raise BrowserError(f"screenshot({path!r}) failed: {exc}") from exc
        return out

    def click(self, selector: str) -> None:
        """Click the first element matching *selector*."""
        self._assert_open()
        try:
            self._page.click(selector)
        except Exception as exc:
            raise BrowserError(f"click({selector!r}) failed: {exc}") from exc

    def fill(self, selector: str, value: str) -> None:
        """Fill an input/textarea matching *selector* with *value*."""
        self._assert_open()
        try:
            self._page.fill(selector, value)
        except Exception as exc:
            raise BrowserError(f"fill({selector!r}) failed: {exc}") from exc

    def wait_for(
        self,
        selector: str | None = None,
        *,
        url: str | None = None,
        state: str = "visible",
        timeout_ms: int | None = None,
    ) -> None:
        """
        Wait for *selector* to reach *state* (``"visible"`` | ``"hidden"``
        | ``"attached"`` | ``"detached"``), or for the page URL to match
        *url* (substring match).
        """
        self._assert_open()
        t = timeout_ms or self._timeout_ms
        try:
            if selector is not None:
                self._page.wait_for_selector(selector, state=state, timeout=t)
            elif url is not None:
                self._page.wait_for_url(f"**{url}**", timeout=t)
            else:
                raise BrowserError("wait_for requires selector or url")
        except Exception as exc:
            raise BrowserError(f"wait_for failed: {exc}") from exc

    def get_console_errors(self) -> list[ConsoleMessage]:
        """
        Return all console messages captured since the last navigate().
        Includes errors, warnings, and logs.
        """
        self._assert_open()
        return list(self._console_messages)

    def measure_vitals(self) -> VitalsResult:
        """
        Measure Web Vitals via the Navigation Timing + PerformanceObserver
        APIs. Returns a VitalsResult with whatever the page exposes.
        LCP/CLS require the page to have fully painted; call after
        wait_for(state="networkidle") for best results.
        """
        self._assert_open()
        js = """
        () => {
            const nav = performance.getEntriesByType('navigation')[0] || {};
            const resources = performance.getEntriesByType('resource') || [];
            const totalBytes = resources.reduce((s, r) => s + (r.transferSize || 0), 0);
            const ttfb = nav.responseStart ? nav.responseStart - nav.requestStart : null;

            // LCP via PerformanceObserver buffer (best effort)
            let lcp = null;
            const lcpEntries = performance.getEntriesByType('largest-contentful-paint');
            if (lcpEntries && lcpEntries.length) {
                lcp = lcpEntries[lcpEntries.length - 1].startTime;
            }

            // CLS via layout-shift buffer
            let cls = 0;
            const clsEntries = performance.getEntriesByType('layout-shift');
            clsEntries.forEach(e => { if (!e.hadRecentInput) cls += e.value; });

            return {
                lcp_ms: lcp,
                ttfb_ms: ttfb,
                cls: cls,
                total_weight_kb: totalBytes ? Math.round(totalBytes / 1024) : null,
                resource_count: resources.length,
            };
        }
        """
        try:
            raw = self._page.evaluate(js)
        except Exception as exc:
            raise BrowserError(f"measure_vitals() failed: {exc}") from exc

        return VitalsResult(
            lcp_ms=raw.get("lcp_ms"),
            inp_ms=None,  # INP requires real user interaction — not measurable headlessly
            cls=raw.get("cls"),
            ttfb_ms=raw.get("ttfb_ms"),
            total_weight_kb=raw.get("total_weight_kb"),
            resource_count=raw.get("resource_count"),
            measured_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def current_url(self) -> str:
        """Return the current page URL."""
        self._assert_open()
        return self._page.url

    def get_text(self, selector: str) -> str:
        """Return inner text of the first element matching *selector*."""
        self._assert_open()
        try:
            return self._page.inner_text(selector)
        except Exception as exc:
            raise BrowserError(f"get_text({selector!r}) failed: {exc}") from exc

    def evaluate(self, js: str) -> Any:
        """Evaluate *js* in the page context and return the result."""
        self._assert_open()
        try:
            return self._page.evaluate(js)
        except Exception as exc:
            raise BrowserError(f"evaluate() failed: {exc}") from exc
