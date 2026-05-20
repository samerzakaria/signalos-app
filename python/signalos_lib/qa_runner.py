# SignalOS Core — W7 Sprint QA.
# cli/signalos_lib/qa_runner.py
#
# QA scenario runner — loads YAML-defined scenarios, executes them
# against SBrowser, and emits a structured evidence JSON file.
#
# Called by /signal-qa (gating) and /signal-qa-only (non-gating).
# The only difference between the two call sites is the `gating` flag
# passed to run_scenario_suite() — the runner itself is identical.
#
# Scenario YAML schema (core/governance/QA/scenarios/*.yaml):
#
#   id: qa-001
#   name: "Login happy path"
#   url: "https://staging.example.com/login"
#   steps:
#     - action: fill
#       selector: "#email"
#       value: "test@example.com"
#     - action: fill
#       selector: "#password"
#       value: "hunter2"
#     - action: click
#       selector: "#submit"
#     - action: wait_for
#       url: "/dashboard"
#   assertions:
#     - type: console_errors     # zero console errors captured
#     - type: element_visible
#       selector: "#welcome-banner"
#     - type: url_contains
#       value: "/dashboard"
#     - type: js_value
#       expression: "document.title"
#       expected: "Dashboard"
#   evidence:
#     screenshot: true
#     vitals: true
#
# Supported step actions: navigate, click, fill, wait_for, screenshot,
# evaluate, scroll_to (selector or url arg as relevant to action).
#
# Supported assertion types:
#   console_errors  — asserts zero console.error messages captured
#   element_visible — asserts selector is visible on page
#   url_contains    — asserts current URL contains `value`
#   js_value        — evaluates `expression`, asserts == `expected`

from __future__ import annotations

__all__ = [
    "ScenarioResult",
    "EvidencePack",
    "run_scenario_suite",
    "load_scenarios",
    "SCENARIO_PASS",
    "SCENARIO_FAIL",
    "SCENARIO_SKIP",
]

import glob
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Encoding-safe glyphs (Windows cp1252 stdout can't encode emoji)
#
# On Linux/macOS terminals default to UTF-8 and the emoji icons render fine.
# Windows Python defaults to cp1252 for stdout outside Windows Terminal —
# printing ✅ / ❌ / ⚠ raises UnicodeEncodeError mid-test. _glyph picks an
# ASCII fallback when stdout encoding can't handle the emoji; _safe_print
# wraps print() to swallow the rare UnicodeEncodeError that slips through.
# ---------------------------------------------------------------------------

_UNICODE_GLYPHS: dict[str, str] = {
    "pass":  "✅",
    "fail":  "❌",
    "skip":  "⏭",
    "warn":  "⚠",
    "arrow": "↪",
}
_ASCII_GLYPHS: dict[str, str] = {
    "pass":  "[OK]",
    "fail":  "[X]",
    "skip":  "[-]",
    "warn":  "[!]",
    "arrow": "->",
}


def _stdout_supports_unicode() -> bool:
    enc = getattr(sys.stdout, "encoding", None) or ""
    return enc.lower().replace("-", "") in {"utf8", "utf16", "utf32"}


def _glyph(name: str) -> str:
    if _stdout_supports_unicode():
        return _UNICODE_GLYPHS.get(name, _ASCII_GLYPHS.get(name, ""))
    return _ASCII_GLYPHS.get(name, "")


def _safe_print(*args: Any, **kwargs: Any) -> None:
    """print() that degrades to ASCII when the active encoding can't
    encode one of the chars (Windows cp1252 etc). Last-resort safety net
    so a single Unicode char never aborts the whole QA suite."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        safe_args = [
            (a.encode(enc, errors="replace").decode(enc, errors="replace")
             if isinstance(a, str) else a)
            for a in args
        ]
        print(*safe_args, **kwargs)


# ---------------------------------------------------------------------------
# Lazy YAML import (stdlib has no yaml — require PyYAML, matching AMD-CORE-007
# optional-dep pattern: raise ImportError with install hint if missing)
# ---------------------------------------------------------------------------

def _require_yaml() -> Any:
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required for the QA runner. "
            "Install it with: pip install PyYAML"
        ) from None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCENARIO_PASS = "pass"
SCENARIO_FAIL = "fail"
SCENARIO_SKIP = "skip"

_DEFAULT_SCREENSHOT_DIR = "core/governance/QA/evidence/screenshots"
_DEFAULT_EVIDENCE_DIR = "core/governance/QA/evidence"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AssertionResult:
    type: str
    passed: bool
    detail: str = ""  # human-readable reason on failure


@dataclass
class ScenarioResult:
    id: str
    name: str
    status: str                                 # SCENARIO_PASS | FAIL | SKIP
    duration_ms: float = 0.0
    screenshot: str = ""                        # relative path or ""
    vitals: dict[str, Any] = field(default_factory=dict)
    assertions: list[AssertionResult] = field(default_factory=list)
    error: str | None = None                    # exception message if FAIL


@dataclass
class EvidencePack:
    wave: str
    run_at: str
    browser_engine: str
    scenario_count: int
    regression_count: int
    pass_count: int
    fail_count: int
    skip_count: int
    gating: bool                                # False for signal-qa-only runs
    scenarios: list[ScenarioResult] = field(default_factory=list)
    qa_evidence_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "wave": self.wave,
            "run_at": self.run_at,
            "browser_engine": self.browser_engine,
            "scenario_count": self.scenario_count,
            "regression_count": self.regression_count,
            "pass": self.pass_count,
            "fail": self.fail_count,
            "skip": self.skip_count,
            "gating": self.gating,
            "qa_evidence_path": self.qa_evidence_path,
            "scenarios": [
                {
                    "id": s.id,
                    "name": s.name,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "screenshot": s.screenshot,
                    "vitals": s.vitals,
                    "assertions": [
                        {"type": a.type, "passed": a.passed, "detail": a.detail}
                        for a in s.assertions
                    ],
                    "error": s.error,
                }
                for s in self.scenarios
            ],
        }


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------

def load_scenarios(pattern: str) -> list[dict[str, Any]]:
    """
    Discover and parse all YAML scenario files matching *pattern*.
    Returns a list of raw scenario dicts. Raises ValueError for
    any scenario missing required fields (id, name, url).
    """
    yaml = _require_yaml()
    paths = sorted(glob.glob(pattern, recursive=True))
    scenarios: list[dict[str, Any]] = []
    for p in paths:
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"Scenario file {p!r} must be a YAML mapping")
        for required in ("id", "name", "url"):
            if required not in data:
                raise ValueError(
                    f"Scenario file {p!r} is missing required field '{required}'"
                )
        scenarios.append(data)
    return scenarios


# ---------------------------------------------------------------------------
# Step executor
# ---------------------------------------------------------------------------

def _execute_step(browser: Any, step: dict[str, Any]) -> None:
    """Execute a single scenario step against *browser* (SBrowser instance)."""
    action = step.get("action", "").lower()
    if action == "navigate":
        browser.navigate(step["url"], wait_until=step.get("wait_until", "load"))
    elif action == "click":
        browser.click(step["selector"])
    elif action == "fill":
        browser.fill(step["selector"], step["value"])
    elif action == "wait_for":
        browser.wait_for(
            selector=step.get("selector"),
            url=step.get("url"),
            state=step.get("state", "visible"),
            timeout_ms=step.get("timeout_ms"),
        )
    elif action == "screenshot":
        path = step.get("path", f"/tmp/signalos-step-{time.time_ns()}.png")
        browser.screenshot(path)
    elif action == "evaluate":
        browser.evaluate(step["expression"])
    elif action == "scroll_to":
        browser.evaluate(
            f"document.querySelector({json.dumps(step['selector'])}).scrollIntoView()"
        )
    else:
        raise ValueError(f"Unknown step action: {action!r}")


# ---------------------------------------------------------------------------
# Assertion runner
# ---------------------------------------------------------------------------

def _run_assertions(
    browser: Any,
    assertions: list[dict[str, Any]],
) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    for assertion in assertions:
        atype = assertion.get("type", "").lower()
        try:
            if atype == "console_errors":
                errors = [
                    m for m in browser.get_console_errors() if m.type == "error"
                ]
                if errors:
                    detail = "; ".join(m.text[:120] for m in errors[:3])
                    results.append(AssertionResult("console_errors", False, detail))
                else:
                    results.append(AssertionResult("console_errors", True))

            elif atype == "element_visible":
                selector = assertion["selector"]
                try:
                    browser.wait_for(selector=selector, state="visible", timeout_ms=5_000)
                    results.append(AssertionResult("element_visible", True))
                except Exception as exc:
                    results.append(AssertionResult(
                        "element_visible", False,
                        f"selector {selector!r} not visible: {exc}"
                    ))

            elif atype == "url_contains":
                value = assertion["value"]
                current = browser.current_url()
                if value in current:
                    results.append(AssertionResult("url_contains", True))
                else:
                    results.append(AssertionResult(
                        "url_contains", False,
                        f"expected {value!r} in URL, got {current!r}"
                    ))

            elif atype == "js_value":
                expression = assertion["expression"]
                expected = assertion["expected"]
                actual = browser.evaluate(expression)
                if actual == expected:
                    results.append(AssertionResult("js_value", True))
                else:
                    results.append(AssertionResult(
                        "js_value", False,
                        f"expression {expression!r}: expected {expected!r}, got {actual!r}"
                    ))

            else:
                results.append(AssertionResult(atype, False, f"unknown assertion type: {atype!r}"))

        except Exception as exc:
            results.append(AssertionResult(atype, False, f"assertion raised: {exc}"))

    return results


# ---------------------------------------------------------------------------
# Single scenario runner
# ---------------------------------------------------------------------------

def _run_scenario(
    scenario: dict[str, Any],
    screenshot_dir: Path,
    capture_vitals: bool,
) -> ScenarioResult:
    from signalos_lib.browser import SBrowser, BrowserError  # local import — SBrowser is W7

    sid = scenario["id"]
    name = scenario["name"]
    url = scenario["url"]
    steps = scenario.get("steps", [])
    assertions = scenario.get("assertions", [])
    evidence_opts = scenario.get("evidence", {})
    want_screenshot = evidence_opts.get("screenshot", True)
    want_vitals = capture_vitals or evidence_opts.get("vitals", False)

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    screenshot_path = str(screenshot_dir / f"{sid}-{ts}.png")

    start = time.monotonic()
    try:
        with SBrowser() as browser:
            # Navigate to entry URL first
            browser.navigate(url)

            # Execute steps
            for step in steps:
                _execute_step(browser, step)

            # Run assertions
            assertion_results = _run_assertions(browser, assertions)

            # Screenshot
            if want_screenshot:
                browser.screenshot(screenshot_path)

            # Vitals
            vitals: dict[str, Any] = {}
            if want_vitals:
                v = browser.measure_vitals()
                vitals = {k: val for k, val in v.as_dict().items() if k != "measured_at"}

        duration_ms = (time.monotonic() - start) * 1000

        # Determine pass/fail from assertions
        failed_assertions = [a for a in assertion_results if not a.passed]
        status = SCENARIO_FAIL if failed_assertions else SCENARIO_PASS

        return ScenarioResult(
            id=sid,
            name=name,
            status=status,
            duration_ms=round(duration_ms, 1),
            screenshot=screenshot_path if want_screenshot else "",
            vitals=vitals,
            assertions=assertion_results,
            error=None if status == SCENARIO_PASS else (
                "; ".join(f"{a.type}: {a.detail}" for a in failed_assertions)
            ),
        )

    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        return ScenarioResult(
            id=sid,
            name=name,
            status=SCENARIO_FAIL,
            duration_ms=round(duration_ms, 1),
            screenshot="",
            vitals={},
            assertions=[],
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Regression auto-generation (Milestone 5 / §6.8.4)
# ---------------------------------------------------------------------------

def _autogen_regression_for_failed_scenario(
    scenario: dict[str, Any],
    result: ScenarioResult,
) -> Path | None:
    """
    Auto-generate a regression entry under
    ``core/governance/QA/regressions/`` for a failing scenario.

    Returns the path of the written file, or None if generation was
    skipped (e.g. missing required fields — defensive). Errors raise
    so the caller can decide whether to log or re-raise.
    """
    from signalos_lib.regression import generate_regression_from_dict

    sid = scenario.get("id") or "unknown"
    name = scenario.get("name") or f"Regression for {sid}"
    url = scenario.get("url")
    if not url:
        # Cannot build a regression entry without a repro URL.
        return None

    # Bug-id is derived from the failing scenario id + timestamp so the
    # generator can produce a unique reg-NNN slot and the user can trace
    # the regression back to the originating QA run.
    ts_token = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bug_id = f"{sid}-{ts_token}"

    payload: dict[str, Any] = {
        "bug_id": bug_id,
        "name": name,
        "url": url,
        "steps": scenario.get("steps", []),
        "assertions": scenario.get("assertions", []),
        "capture_vitals": bool(scenario.get("evidence", {}).get("vitals", False)),
        "pr_ref": "",
        "fixed_at": "",
    }

    out_path = generate_regression_from_dict(payload)

    # Optional journal-style stderr breadcrumb so users see which file was
    # produced when a scenario fails. Quiet by default (no print on stdout)
    # to avoid duplicating qa_runner's per-scenario status line.
    print(
        f"  {_glyph('arrow')} auto-generated regression: {out_path} "
        f"(from failing scenario {sid}: {result.error or 'failed assertions'})",
        file=sys.stderr,
    )
    return out_path


# ---------------------------------------------------------------------------
# Public API — run_scenario_suite
# ---------------------------------------------------------------------------

def run_scenario_suite(
    scenario_pattern: str,
    regression_pattern: str | None = None,
    wave: str = "unknown",
    output_path: str | None = None,
    screenshot_dir: str = _DEFAULT_SCREENSHOT_DIR,
    capture_vitals: bool = False,
    gating: bool = True,
    verbose: bool = True,
) -> EvidencePack:
    """
    Run all scenarios matching *scenario_pattern* (and optionally
    *regression_pattern*), collect results, write evidence JSON to
    *output_path*, and return a populated EvidencePack.

    Parameters
    ----------
    scenario_pattern : str
        Glob pattern for scenario YAML files.
    regression_pattern : str or None
        Glob pattern for regression YAML files. If None, regressions
        are skipped.
    wave : str
        Wave ID for evidence front-matter (e.g. "07").
    output_path : str or None
        Where to write the evidence JSON. Defaults to
        ``core/governance/QA/evidence/wave-{wave}-qa-evidence.json``
        (gating) or ``core/governance/QA/evidence/qa-only-{ts}.json``
        (non-gating).
    screenshot_dir : str
        Directory for screenshots.
    capture_vitals : bool
        Force Web Vitals capture for every scenario regardless of
        per-scenario ``evidence.vitals`` setting.
    gating : bool
        True → /signal-qa (Gate 5 entry). False → /signal-qa-only.
    verbose : bool
        Print live per-scenario results to stdout.
    """
    run_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ts = run_at.replace(":", "").replace("-", "")

    # Resolve output path
    if output_path is None:
        evidence_dir = Path(_DEFAULT_EVIDENCE_DIR)
        if gating:
            output_path = str(evidence_dir / f"wave-{wave}-qa-evidence.json")
        else:
            output_path = str(evidence_dir / f"qa-only-{ts}.json")

    scr_dir = Path(screenshot_dir)
    scr_dir.mkdir(parents=True, exist_ok=True)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Load scenarios
    main_scenarios = load_scenarios(scenario_pattern)
    regression_scenarios = load_scenarios(regression_pattern) if regression_pattern else []

    all_scenarios = main_scenarios + regression_scenarios
    regression_count = len(regression_scenarios)

    # Detect browser engine version
    try:
        import importlib.metadata
        pw_ver = importlib.metadata.version("playwright")
    except Exception:
        pw_ver = "unknown"
    browser_engine = f"SBrowser/playwright-{pw_ver}"

    # Execute
    results: list[ScenarioResult] = []
    for i, scenario in enumerate(all_scenarios):
        is_regression = i >= len(main_scenarios)
        label = "[regression]" if is_regression else ""
        if verbose:
            print(f"  running  {scenario['id']}  {scenario['name']} {label}".rstrip())

        result = _run_scenario(scenario, scr_dir, capture_vitals)
        results.append(result)

        # Milestone 5 / §6.8.4: regression entries are system-emitted.
        # When a main-suite scenario fails, auto-generate a regression
        # YAML so the next QA run replays the failure path. We skip
        # already-regression scenarios (no need to regenerate one for
        # a failing replay) and wrap in try/except so a generation
        # failure cannot block the rest of the QA run — it logs to
        # stderr and continues.
        if result.status == SCENARIO_FAIL and not is_regression:
            try:
                _autogen_regression_for_failed_scenario(scenario, result)
            except Exception as exc:  # pragma: no cover — defensive
                # Journal-style stderr write; QA run is non-blocking.
                _safe_print(
                    f"  {_glyph('warn')} regression auto-gen failed for {scenario.get('id', '?')}: {exc}",
                    file=sys.stderr,
                )

        if verbose:
            if result.status == SCENARIO_PASS:
                icon = _glyph("pass")
            elif result.status == SCENARIO_SKIP:
                icon = _glyph("skip")
            else:
                icon = _glyph("fail")
            _safe_print(f"  {icon}  [{result.status.upper()}]  {result.id}  {result.name}  ({result.duration_ms:.0f} ms)")
            if result.error:
                _safe_print(f"         {_glyph('arrow')} {result.error}")

    # Tally
    pass_count = sum(1 for r in results if r.status == SCENARIO_PASS)
    fail_count = sum(1 for r in results if r.status == SCENARIO_FAIL)
    skip_count = sum(1 for r in results if r.status == SCENARIO_SKIP)

    pack = EvidencePack(
        wave=wave,
        run_at=run_at,
        browser_engine=browser_engine,
        scenario_count=len(main_scenarios),
        regression_count=regression_count,
        pass_count=pass_count,
        fail_count=fail_count,
        skip_count=skip_count,
        gating=gating,
        scenarios=results,
        qa_evidence_path=output_path,
    )

    # Write evidence JSON
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(pack.as_dict(), fh, indent=2)

    if verbose:
        gating_label = "QUALITY_CHECK.md will be updated" if gating else "non-gating run — QUALITY_CHECK.md not updated"
        print()
        print(
            f"signal-qa{'  ' if gating else '-only'} complete — "
            f"{len(all_scenarios)} scenarios · "
            f"{pass_count} pass · {fail_count} fail · {skip_count} skip"
        )
        print(f"Evidence: {output_path}")
        print(f"Screenshots: {screenshot_dir}/")
        print(f"({gating_label})")

    return pack
