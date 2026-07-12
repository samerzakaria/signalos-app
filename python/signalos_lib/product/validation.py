"""Profile-aware product validation for the SignalOS delivery bridge.

Composes stack adapter validation plans with execution, evidence
capture, and delivery closure logic.  A dry-run validates wiring
but cannot close delivery.  Missing toolchains are infra blockers,
not successes.  The ``generic`` profile validates non-UI Python products.
"""

from __future__ import annotations

__all__ = [
    "build_validation_plan",
    "check_product_closure",
    "load_validation_result",
    "parse_build_diagnostics",
    "parse_test_diagnostics",
    "run_validation",
    "verify_frozen_tests_collected",
    "write_validation_plan",
    "write_validation_result",
]

import json
import os
import re
import signal
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .stacks import get_adapter

SCHEMA_VERSION = "signalos.validation_plan.v1"
RESULT_SCHEMA_VERSION = "signalos.validation_result.v1"

_CATEGORIES = (
    "install",
    "build",
    "test",
    "lint",
    "qa",
    "e2e",
    "runtime_smoke",
    "ux_smoke",
    "security",
)

# Categories whose failure blocks delivery closure when the profile
# declares that it *can* validate them.
_CRITICAL_CATEGORIES = {"build", "test"}
_REQUIRED_CLOSE_CATEGORIES = {"build", "test"}

# Profiles whose build (`npm run build` -> `tsc && vite build`) and test
# (`vitest run`) output we parse into structured, per-file diagnostics so
# the repair loop can target the failing file(s). Non-JS profiles keep
# their existing aggregated-output validation unchanged.
_JS_DIAGNOSTIC_PROFILES = {"react-vite", "nextjs-app", "vue-vite", "angular"}

# tsc emits: `path(line,col): error TSxxxx: message`
_TSC_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>[^\s(].*?)\((?P<line>\d+),(?P<col>\d+)\):\s+"
    r"error\s+(?P<code>TS\d+):\s+(?P<message>.*)$"
)

# vite/esbuild emits (rollup): `[vite]:` / `ERROR:` with `file:line:col`.
_VITE_DIAGNOSTIC_RE = re.compile(
    r"(?P<file>(?:\.{0,2}/)?[\w./\\-]+\.[cm]?[jt]sx?):(?P<line>\d+):(?P<col>\d+)"
)

# vitest emits (ANSI-stripped): ` FAIL src/foo.test.ts > test name`
_VITEST_FAIL_RE = re.compile(
    r"^\s*FAIL\s+(?P<file>[\w./\\-]+\.[cm]?[jt]sx?)(?:\s*>\s*(?P<name>.*))?$"
)

# vitest file location line (ANSI-stripped): ` ❯ src/foo.test.ts:2:48`
_VITEST_LOC_RE = re.compile(
    r"(?P<file>[\w./\\-]+\.[cm]?[jt]sx?):(?P<line>\d+):(?P<col>\d+)\s*$"
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def parse_build_diagnostics(output: str) -> list[dict[str, Any]]:
    """Parse tsc / vite build output into structured per-file failures.

    Returns a list of ``{file, line, col, code, message}`` dicts, one per
    distinct diagnostic. tsc diagnostics are the primary, richly-coded
    source; vite/rollup ``file:line:col`` references are a fallback so a
    bundler-only failure still names a target file. Clean output yields
    an empty list.
    """
    failures: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    cleaned = _strip_ansi(output or "")
    for raw in cleaned.splitlines():
        line = raw.rstrip()
        m = _TSC_DIAGNOSTIC_RE.match(line.strip())
        if m:
            key = (m.group("file"), int(m.group("line")), m.group("code"))
            if key in seen:
                continue
            seen.add(key)
            failures.append(
                {
                    "file": _normalize_diag_path(m.group("file")),
                    "line": int(m.group("line")),
                    "col": int(m.group("col")),
                    "code": m.group("code"),
                    "message": m.group("message").strip(),
                    "source": "tsc",
                }
            )
    if failures:
        return failures

    # No tsc diagnostics — fall back to vite/rollup file references so a
    # bundler failure still names a target file for the repair loop.
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if "error" not in lowered and "failed" not in lowered:
            continue
        m = _VITE_DIAGNOSTIC_RE.search(line)
        if not m:
            continue
        key = (m.group("file"), int(m.group("line")), "VITE")
        if key in seen:
            continue
        seen.add(key)
        failures.append(
            {
                "file": _normalize_diag_path(m.group("file")),
                "line": int(m.group("line")),
                "col": int(m.group("col")),
                "code": "VITE_BUILD",
                "message": line,
                "source": "vite",
            }
        )
    return failures


def parse_test_diagnostics(output: str) -> list[dict[str, Any]]:
    """Parse vitest run output into structured per-file test failures.

    Returns ``{file, line, col, code, message}`` dicts for each ``FAIL``
    block, enriched with the first ``file:line:col`` location that follows
    it when vitest prints one. Clean output yields an empty list.
    """
    failures: list[dict[str, Any]] = []
    lines = _strip_ansi(output or "").splitlines()
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        m = _VITEST_FAIL_RE.match(stripped)
        if not m:
            continue
        file = _normalize_diag_path(m.group("file"))
        name = (m.group("name") or "").strip()
        line_no: int | None = None
        col_no: int | None = None
        message = name or f"test failed in {file}"
        # Look ahead a bounded window for the location + assertion message.
        for follow in lines[idx + 1 : idx + 12]:
            fs = follow.strip()
            loc = _VITEST_LOC_RE.search(fs)
            if loc and _normalize_diag_path(loc.group("file")) == file:
                line_no = int(loc.group("line"))
                col_no = int(loc.group("col"))
                break
        failures.append(
            {
                "file": file,
                "line": line_no,
                "col": col_no,
                "code": "TEST_FAIL",
                "message": message,
                "source": "vitest",
            }
        )
    return failures


def _normalize_diag_path(path: str) -> str:
    """Normalize a diagnostic file path to a forward-slash relative form."""
    cleaned = path.strip().strip('"').strip("'").replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


# tsc syntax-class codes: a raw parse error where a delimiter is very often the
# real cause, and tsc's point-of-detection column is frequently downstream of
# the actual imbalance ("',' expected" many lines after a missing ')'). When a
# failing file carries one of these, a deterministic delimiter-balance pass on
# the file gives the repair loop a crisp, correctly-localized hint tsc can't.
_SYNTAX_ERROR_CODES = frozenset({
    "TS1005",  # 'x' expected
    "TS1003",  # identifier expected
    "TS1109",  # expression expected
    "TS1128",  # declaration or statement expected
    "TS1136",  # property assignment expected
    "TS1381",  # unexpected token
    "TS1382",  # unexpected token (JSX)
    "TS17002",  # expected corresponding JSX closing tag
})


def analyze_delimiter_balance(text: str) -> dict[str, Any]:
    """Heuristically scan TS/TSX/JS source for unbalanced ``()``/``{}``/``[]``.

    Ignores string literals (``'`` / ``"``), template literals (with nested
    ``${ }`` interpolation), and ``//`` / ``/* */`` comments. Returns::

        {balanced: bool, paren: int, brace: int, bracket: int, hint: str}

    Each count is (opens - closes): positive == unclosed openers, negative ==
    extra closers. This is a heuristic -- it does not fully parse JSX or regex
    literals -- so it is only ever used to ENRICH a diagnostic tsc already
    raised (see the repair loop's corroboration gate), never as a standalone
    gate. For the common "closed a ``describe``/``it`` call with ``}`` instead
    of ``});``" class it localizes precisely where tsc's column misleads."""
    paren = brace = bracket = 0
    i = 0
    n = len(text or "")
    in_line_comment = False
    in_block_comment = False
    string_ch = ""            # "'"/'"' normal string, "`" template literal
    templ_stack: list[int] = []  # brace baseline at each open ${
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if c == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if c == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if string_ch in ("'", '"'):
            if c == "\\":
                i += 2
                continue
            if c == string_ch:
                string_ch = ""
            i += 1
            continue
        if string_ch == "`":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                string_ch = ""
                i += 1
                continue
            if c == "$" and nxt == "{":
                templ_stack.append(brace)  # baseline before the ${ brace
                brace += 1
                string_ch = ""             # now scanning the interpolation code
                i += 2
                continue
            i += 1
            continue
        # normal code context
        if c == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if c in ("'", '"', "`"):
            string_ch = c
            i += 1
            continue
        if c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif c == "[":
            bracket += 1
        elif c == "]":
            bracket -= 1
        elif c == "{":
            brace += 1
        elif c == "}":
            brace -= 1
            if templ_stack and brace == templ_stack[-1]:
                templ_stack.pop()
                string_ch = "`"           # the ${...} closed -> back to template
        i += 1

    balanced = (
        paren == 0 and brace == 0 and bracket == 0
        and not templ_stack and not in_block_comment and string_ch == ""
    )
    problems: list[str] = []
    if paren > 0:
        problems.append(
            f"{paren} unclosed '(' -- a call is likely closed with the wrong "
            f"delimiter (e.g. a `describe(...)`/`it(...)`/`expect(...)` closed "
            f"with `}}` instead of `}});`)"
        )
    elif paren < 0:
        problems.append(f"{-paren} extra ')' with no matching '('")
    if brace > 0:
        problems.append(f"{brace} unclosed '{{'")
    elif brace < 0:
        problems.append(f"{-brace} extra '}}' with no matching '{{'")
    if bracket > 0:
        problems.append(f"{bracket} unclosed '['")
    elif bracket < 0:
        problems.append(f"{-bracket} extra ']' with no matching '['")
    if string_ch:
        problems.append("an unterminated string/template literal")
    if in_block_comment:
        problems.append("an unterminated /* block comment")
    hint = "; ".join(problems)
    return {
        "balanced": balanced,
        "paren": paren,
        "brace": brace,
        "bracket": bracket,
        "hint": hint,
    }

_SKIP_OWNERS = {
    "install": (
        "stack-adapter",
        "No install command is required for this stack.",
    ),
    "lint": (
        "stack-adapter",
        "No lint command is declared for this stack.",
    ),
    "qa": (
        "acceptance-proof",
        "No stack-level QA command is declared; acceptance and proof evidence own QA.",
    ),
    "e2e": (
        "proof-phase",
        "Browser E2E is owned by the runtime and UX proof phase.",
    ),
    "runtime_smoke": (
        "proof-phase",
        "Runtime smoke is owned by the proof phase.",
    ),
    "ux_smoke": (
        "proof-phase",
        "UX smoke is owned by the proof phase.",
    ),
    "security": (
        "security-gate",
        "Security validation is owned by the product security gate.",
    ),
}


def _validation_command_timeout_s() -> int:
    """Return the per-command validation timeout.

    Real deliveries keep the historical 300s default.  Tests and CI can lower
    this with SIGNALOS_VALIDATION_COMMAND_TIMEOUT_S so unavailable package
    registries become explicit blockers instead of hanging the suite.
    """
    raw = os.environ.get("SIGNALOS_VALIDATION_COMMAND_TIMEOUT_S", "").strip()
    if not raw:
        return 300
    try:
        parsed = int(raw)
    except ValueError:
        return 300
    return parsed if parsed > 0 else 300


# Dependency install is network-bound and can dwarf the compile/test steps: the
# funded e2e's `npm install` of Mantine's tree exceeded the 300s per-command cap,
# which cascaded into a FALSE "build/test failed" (tsc/vitest not yet on PATH)
# and a repair loop that got "tsc not recognized" instead of the real nit. Give
# install commands their own, larger budget so a slow-but-fine install is not
# misread as a code failure.
_INSTALL_COMMAND_MARKERS = (
    "npm install", "npm ci", "npm i ", "pnpm install", "pnpm i ",
    "yarn install", "yarn --", "pip install", "pip3 install", "uv pip install",
    "uv sync", "poetry install", "bundle install", "go mod download",
    "cargo fetch", "dotnet restore",
)


def _is_install_command(cmd: str) -> bool:
    low = " " + cmd.lower().strip() + " "
    return any(marker in low for marker in _INSTALL_COMMAND_MARKERS)


def _validation_install_timeout_s() -> int:
    """Per-command timeout for dependency-install commands (larger than the
    compile/test default). Override with SIGNALOS_VALIDATION_INSTALL_TIMEOUT_S;
    never shorter than the base command timeout."""
    base = _validation_command_timeout_s()
    raw = os.environ.get("SIGNALOS_VALIDATION_INSTALL_TIMEOUT_S", "").strip()
    default = 900
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return max(parsed, base)
        except ValueError:
            pass
    return max(default, base)


# ------------------------------------------------------------------
# Plan construction
# ------------------------------------------------------------------

def build_validation_plan(
    repo_root: Path,
    profile: str,
) -> dict[str, Any]:
    """Build a validation plan for *profile*.

    Delegates to the stack adapter's ``validation_plan()`` and
    ``preview_plan()`` methods, then structures the result.
    """
    adapter = get_adapter(profile)
    commands = adapter.validation_plan(repo_root)
    preview = adapter.preview_plan(repo_root)
    detection = adapter.detect(repo_root)

    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
    }
    for cat in _CATEGORIES:
        plan[cat] = list(commands.get(cat, []))

    plan["preview"] = {
        "command": preview.get("command"),
        "port": preview.get("port"),
        "health_path": preview.get("health_path"),
        "timeout_s": preview.get("timeout_s"),
    }
    plan["can_validate_build"] = bool(plan["build"])
    plan["can_validate_tests"] = bool(plan["test"])
    plan["can_validate_runtime"] = preview.get("command") is not None
    plan["can_deliver_ui"] = detection.get("can_deliver_ui", False)

    return plan


# ------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------

def verify_frozen_tests_collected(
    output: str,
    frozen_tests: list[str],
) -> list[str]:
    """Return the frozen tests that were NOT collected/executed by the test run.

    The G4 verification contract is the FROZEN plan-authored acceptance tests.
    A model can "pass" without running the exam by neutering the test command
    (``"test": "exit 0"``) or excluding the frozen tests from discovery
    (vitest ``exclude``). Both leave the frozen test's path ABSENT from the test
    runner's output. A frozen test counts as collected when its path — or its
    bare basename — appears in the (ANSI-stripped) output; anything missing is
    returned so the caller can FAIL verification. Empty result == all collected.
    """
    if not frozen_tests:
        return []
    haystack = _strip_ansi(output or "").replace("\\", "/")
    missing: list[str] = []
    for entry in frozen_tests:
        norm = str(entry).replace("\\", "/").strip()
        if not norm:
            continue
        base = norm.rsplit("/", 1)[-1]
        if norm in haystack or (base and base in haystack):
            continue
        missing.append(str(entry))
    return missing


def run_validation(
    repo_root: Path,
    plan: dict[str, Any],
    dry_run: bool = False,
    frozen_tests: list[str] | None = None,
) -> dict[str, Any]:
    """Execute the validation plan.

    For each command category, run the commands and capture results.
    If *dry_run* is ``True``, check that commands exist but do not
    execute them.

    *frozen_tests* is the G4 verification contract: the plan-authored acceptance
    tests that MUST actually run. When provided (real run), after the test
    command executes we confirm each frozen test was collected/executed; if any
    is missing (the command was neutered or the tests were excluded), the test
    category is FAILED so a neutered exam can never close delivery. Default None
    keeps the historical behavior byte-identical.

    INTEGRATE: the G4 caller (gate_orchestrator._verify_g4_build /
    subagent_build, owned elsewhere) should pass the frozen plan-test paths as
    ``frozen_tests`` so the collection check runs on the real gate verification.
    """
    profile = plan.get("profile", "unknown")
    parse_diagnostics = profile in _JS_DIAGNOSTIC_PROFILES and not dry_run

    results: dict[str, dict[str, Any]] = {}
    for cat in _CATEGORIES:
        cmds = plan.get(cat, [])
        if not cmds:
            results[cat] = _skipped_result(cat, dry_run=False)
            continue
        if dry_run:
            results[cat] = _dry_run_skipped_result(cat)
            continue
        results[cat] = _run_commands(repo_root, cmds)
        if parse_diagnostics and results[cat].get("status") == "failed":
            _attach_diagnostics(cat, results[cat])

    # Frozen-test collection gate (G4 verification contract). Only meaningful for
    # a real run where the test command actually executed (passed or failed).
    frozen_uncollected: list[str] = []
    if not dry_run and frozen_tests:
        test_result = results.get("test", {})
        if test_result.get("status") in ("passed", "failed"):
            output = test_result.get("output", "") or ""
            frozen_uncollected = verify_frozen_tests_collected(output, frozen_tests)
            if frozen_uncollected:
                results["test"] = {
                    **test_result,
                    "status": "failed",
                    "output": (
                        output
                        + "\n\nFROZEN TEST VERIFICATION FAILED: these frozen "
                        "acceptance tests were NOT collected/executed by the test "
                        "command (excluded from discovery, or the command was "
                        "neutered): "
                        + ", ".join(frozen_uncollected)
                        + ". The verification contract is immutable during the "
                        "build -- run the frozen tests; do not exclude them or "
                        "replace the test command."
                    ),
                    "frozen_tests_uncollected": list(frozen_uncollected),
                }

    violations = _collect_violations(results)

    # Summary
    total = len(_CATEGORIES)
    passed = sum(1 for r in results.values() if r["status"] == "passed")
    failed = sum(1 for r in results.values() if r["status"] == "failed")
    skipped = sum(1 for r in results.values() if r["status"] == "skipped")
    blocked = sum(1 for r in results.values() if r["status"] == "blocked")

    blockers = _compute_blockers(plan, results, dry_run)
    can_close = _can_close_delivery(plan, results, dry_run)

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "profile": profile,
        "dry_run": dry_run,
        "results": results,
        "summary": {
            "total_checks": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "blocked": blocked,
        },
        "can_close_delivery": can_close,
        "blockers": blockers,
        # Structured, per-file failures for the repair loop (empty for
        # profiles/runs without diagnostic parsing).
        "violations": violations,
        # Frozen plan tests the test run did NOT collect/execute (empty when the
        # contract held or no frozen tests were supplied). A non-empty list means
        # the exam was neutered -- the test category is failed above.
        "frozen_tests_uncollected": frozen_uncollected,
    }


def _attach_diagnostics(category: str, result: dict[str, Any]) -> None:
    """Parse a failed build/test result's output into per-file failures.

    Mutates *result* in place, adding a ``failures`` list of structured
    ``{file, line, code, message}`` dicts. Never weakens status: a failed
    category stays failed even when no per-file diagnostic could be parsed.
    """
    output = result.get("output", "") or ""
    if category == "build":
        failures = parse_build_diagnostics(output)
    elif category == "test":
        failures = parse_test_diagnostics(output)
    else:
        failures = []
    if failures:
        result["failures"] = failures


def _collect_violations(
    results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate per-category structured failures into one violation list."""
    violations: list[dict[str, Any]] = []
    for cat in ("build", "test"):
        for failure in results.get(cat, {}).get("failures", []) or []:
            entry = dict(failure)
            entry.setdefault("category", cat)
            violations.append(entry)
    return violations


def _run_commands(repo_root: Path, cmds: list[str]) -> dict[str, Any]:
    """Run a list of shell commands, returning aggregated result."""
    outputs: list[str] = []
    start = time.perf_counter()
    timeout_s = _validation_command_timeout_s()
    install_timeout_s = _validation_install_timeout_s()
    for cmd in cmds:
        argv = _split_command(cmd)
        if not argv:
            continue
        # #41-env: dependency install gets its own (larger) budget so a slow
        # install is not misread as a build/test failure.
        cmd_timeout = install_timeout_s if _is_install_command(cmd) else timeout_s
        # Option 2 -- containerized validation. Run the build/test command in its
        # per-stack container when the host LACKS the toolchain (or the workspace
        # forces sandboxed validation) -- this is what lets a Go/.NET/Java/react
        # product build+test with ZERO language toolchain on the operator's
        # machine, only a container runtime (docker|podman). The official base
        # image is pulled on demand (see sandbox.resolve_stack_image). Host
        # toolchain wins when present and not forced (the fast path); neither
        # present -> an honest "need a container runtime" blocker.
        from signalos_lib.sandbox import (
            build_docker_run_argv,
            docker_available,
            is_sandbox_enabled,
        )

        exe = shutil.which(argv[0])
        force_sandbox = (
            os.environ.get("SIGNALOS_VALIDATE_IN_SANDBOX", "").strip() == "1"
            or is_sandbox_enabled(repo_root)
        )
        if exe is not None and not force_sandbox:
            run_argv = [exe, *argv[1:]]
        elif docker_available():
            run_argv = build_docker_run_argv(repo_root, argv)
        elif exe is not None:
            run_argv = [exe, *argv[1:]]  # forced but no runtime -> host fallback
        else:
            elapsed = time.perf_counter() - start
            return {
                "status": "blocked",
                "output": (
                    f"command '{argv[0]}' is not installed on the host, and no "
                    f"container runtime (docker/podman) is available to run it "
                    f"in a per-stack container"
                ),
                "duration_s": round(elapsed, 3),
            }
        try:
            proc = _run_shell_command(
                cmd,
                run_argv,
                repo_root,
                cmd_timeout,
            )
            out = proc.stdout or ""
            if proc.stderr:
                out += "\n" + proc.stderr
            outputs.append(out)
            if proc.returncode != 0:
                elapsed = time.perf_counter() - start
                return {
                    "status": "failed",
                    "output": "\n".join(outputs),
                    "duration_s": round(elapsed, 3),
                }
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - start
            return {
                "status": "blocked",
                "output": f"command timed out after {cmd_timeout}s: {cmd}",
                "duration_s": round(elapsed, 3),
            }
        except OSError as exc:
            elapsed = time.perf_counter() - start
            return {
                "status": "blocked",
                "output": f"command could not start: {exc}",
                "duration_s": round(elapsed, 3),
            }

    elapsed = time.perf_counter() - start
    return {
        "status": "passed",
        "output": "\n".join(outputs),
        "duration_s": round(elapsed, 3),
    }


def _run_shell_command(
    cmd: str,
    argv: list[str],
    repo_root: Path,
    timeout_s: int,
) -> subprocess.CompletedProcess[str]:
    """Run a command and kill its process tree on timeout."""
    popen_kwargs: dict[str, Any] = {
        "cwd": str(repo_root),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": False,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0,
        )
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(argv, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(proc)
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=timeout_s,
            output=exc.output,
            stderr=exc.stderr,
        )

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _split_command(cmd: str) -> list[str]:
    """Split a validation command into argv without invoking a shell."""
    try:
        args = shlex.split(cmd, posix=os.name != "nt")
    except ValueError:
        args = cmd.split()
    cleaned: list[str] = []
    for arg in args:
        if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in {"'", '"'}:
            cleaned.append(arg[1:-1])
        else:
            cleaned.append(arg)
    return cleaned


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            proc.kill()
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        return

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def _dry_run_skipped_result(category: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "output": "dry-run only",
        "duration_s": 0.0,
        "skip_reason": "Dry-run mode did not execute validation commands.",
        "skip_owner": "operator",
        "release_disposition": "blocked",
        "category": category,
    }


def _skipped_result(category: str, *, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return _dry_run_skipped_result(category)
    if category in _REQUIRED_CLOSE_CATEGORIES:
        return {
            "status": "skipped",
            "output": "",
            "duration_s": 0.0,
            "skip_reason": f"{category} validation command is missing.",
            "skip_owner": "stack-adapter",
            "release_disposition": "must_fix",
            "category": category,
        }
    owner, reason = _SKIP_OWNERS.get(
        category,
        ("stack-adapter", "No validation command is declared for this category."),
    )
    return {
        "status": "skipped",
        "output": "",
        "duration_s": 0.0,
        "skip_reason": reason,
        "skip_owner": owner,
        "release_disposition": "not_applicable",
        "category": category,
    }


def _can_close_delivery(
    plan: dict[str, Any],
    results: dict[str, dict[str, Any]],
    dry_run: bool,
) -> bool:
    if dry_run:
        return False

    statuses = [r["status"] for r in results.values()]
    # All skipped -> cannot close
    if all(s == "skipped" for s in statuses):
        return False

    for cat in _REQUIRED_CLOSE_CATEGORIES:
        if results.get(cat, {}).get("status") != "passed":
            return False

    if _unauthorized_skips(results):
        return False

    # Any failure or blocked in critical categories
    for cat in _CRITICAL_CATEGORIES:
        can_key = f"can_validate_{cat}" if cat != "test" else "can_validate_tests"
        if cat == "build":
            can_key = "can_validate_build"
        if plan.get(can_key, False):
            r = results.get(cat, {})
            if r.get("status") in ("failed", "blocked"):
                return False

    # Any failure at all blocks closure
    if any(r["status"] == "failed" for r in results.values()):
        return False

    # Any blocked blocks closure
    if any(r["status"] == "blocked" for r in results.values()):
        return False

    return True


def _compute_blockers(
    plan: dict[str, Any],
    results: dict[str, dict[str, Any]],
    dry_run: bool,
) -> list[str]:
    blockers: list[str] = []
    if dry_run:
        blockers.append("Dry-run mode: validation was not executed")
    for cat in _CATEGORIES:
        r = results.get(cat, {})
        st = r.get("status", "skipped")
        if st == "failed":
            blockers.append(f"{cat} check failed")
        elif st == "blocked":
            out = r.get("output", "")
            blockers.append(f"{cat} check blocked: {out}")
        elif st == "skipped" and r.get("release_disposition") != "not_applicable":
            reason = r.get("skip_reason") or "missing not-applicable evidence"
            blockers.append(f"{cat} check skipped: {reason}")
    for cat in _REQUIRED_CLOSE_CATEGORIES:
        if results.get(cat, {}).get("status") != "passed":
            blockers.append(f"{cat} check must pass before delivery can close")
    if all(r.get("status") == "skipped" for r in results.values()):
        blockers.append("All checks were skipped; at least one must pass")
    return blockers


def _unauthorized_skips(results: dict[str, dict[str, Any]]) -> list[str]:
    return [
        cat
        for cat, result in results.items()
        if result.get("status") == "skipped"
        and result.get("release_disposition") != "not_applicable"
    ]


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------

def write_validation_plan(plan: dict[str, Any], signalos_dir: Path) -> Path:
    """Write to .signalos/product/VALIDATION_PLAN.json."""
    out = signalos_dir / "product" / "VALIDATION_PLAN.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out


def write_validation_result(result: dict[str, Any], signalos_dir: Path) -> Path:
    """Write to .signalos/product/VALIDATION_RESULT.json."""
    out = signalos_dir / "product" / "VALIDATION_RESULT.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out


def load_validation_result(signalos_dir: Path) -> dict[str, Any] | None:
    """Load validation result, returning ``None`` if absent."""
    path = signalos_dir / "product" / "VALIDATION_RESULT.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------
# Closure assessment
# ------------------------------------------------------------------

def check_product_closure(result: dict[str, Any] | None) -> dict[str, Any]:
    """Check if validation supports delivery closure.

    Returns a closure assessment with level, evidence summary,
    and blockers list.
    """
    if result is None:
        return {
            "closeable": False,
            "level": "not_started",
            "evidence_summary": "No validation result exists",
            "blockers": ["No validation has been run"],
        }

    results = result.get("results", {})
    dry_run = result.get("dry_run", False)
    blockers = list(result.get("blockers", []))

    statuses = [r.get("status", "skipped") for r in results.values()]

    has_blocked = any(s == "blocked" for s in statuses)
    has_failed = any(s == "failed" for s in statuses)
    has_passed = any(s == "passed" for s in statuses)
    all_skipped = all(s == "skipped" for s in statuses)
    unauthorized_skips = _unauthorized_skips(results)
    required_missing = [
        cat
        for cat in _REQUIRED_CLOSE_CATEGORIES
        if results.get(cat, {}).get("status") != "passed"
    ]

    if has_blocked:
        return {
            "closeable": False,
            "level": "blocked",
            "evidence_summary": "Infrastructure blockers prevent validation",
            "blockers": blockers,
        }

    if has_failed:
        return {
            "closeable": False,
            "level": "partial",
            "evidence_summary": "Some checks failed",
            "blockers": blockers,
        }

    if all_skipped:
        return {
            "closeable": False,
            "level": "partial",
            "evidence_summary": "All checks were skipped; no evidence of product quality",
            "blockers": blockers or ["All checks were skipped; at least one must pass"],
        }

    if required_missing or unauthorized_skips:
        skip_blockers = [
            f"{cat} check skipped without not-applicable evidence"
            for cat in unauthorized_skips
        ]
        required_blockers = [
            f"{cat} check must pass before delivery can close"
            for cat in required_missing
        ]
        return {
            "closeable": False,
            "level": "partial",
            "evidence_summary": "Mandatory validation evidence is incomplete",
            "blockers": blockers or required_blockers + skip_blockers,
        }

    # has_passed is True, no failures, no blocked
    if dry_run:
        return {
            "closeable": False,
            "level": "verified",
            "evidence_summary": "All enabled checks passed (dry-run)",
            "blockers": blockers,
        }

    # Real run, all enabled checks passed
    return {
        "closeable": True,
        "level": "ready",
        "evidence_summary": "All enabled checks passed",
        "blockers": [],
    }
