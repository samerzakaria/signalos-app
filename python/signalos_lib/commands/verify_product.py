"""Product verification command for SignalOS-governed product repos.

This command composes existing verification surfaces instead of replacing
them: profile-declared build/test/lint commands, QA scenario evidence,
E2E smoke checks, and TDD runner detection metadata.
"""

from __future__ import annotations

__all__ = ["main", "verify_product"]

import argparse
import glob
import json
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from signalos_lib.profiles import ProfileError, ProfileNotFoundError, load_profile
from signalos_lib.profiles.loader import CommandSpec, Profile

SCHEMA_VERSION = "signalos.verify_product.v1"
DEFAULT_EVIDENCE_SEGMENT = "product-verification"
DEFAULT_QA_PATTERN = "core/governance/QA/scenarios/*.yaml"
DEFAULT_QA_REGRESSION_PATTERN = "core/governance/QA/regressions/*.yaml"


@dataclass
class CheckResult:
    name: str
    kind: str
    status: str
    required: bool = False
    reason: str = ""
    command: list[str] | None = None
    duration_ms: int = 0
    exit_code: int | None = None
    evidence_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "required": self.required,
            "reason": self.reason,
            "command": self.command,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
            "evidence_path": self.evidence_path,
            "details": self.details,
        }


def verify_product(
    repo_root: Path | str | None = None,
    *,
    wave: str | None = None,
    profile_id: str | None = None,
    profile_dir: Path | None = None,
    timeout_sec: int = 300,
    include_build: bool = True,
    include_test: bool = True,
    include_lint: bool = True,
    include_qa: bool = True,
    include_e2e: bool = True,
    qa_pattern: str = DEFAULT_QA_PATTERN,
    qa_regression_pattern: str | None = DEFAULT_QA_REGRESSION_PATTERN,
) -> dict[str, Any]:
    """Run product verification and return a stable JSON-compatible payload."""

    root = Path(repo_root or Path.cwd()).resolve()
    wave_segment = _safe_segment(wave or DEFAULT_EVIDENCE_SEGMENT)
    evidence_dir = root / ".signalos" / "evidence" / wave_segment
    root_ok = root.exists() and root.is_dir()

    checks: list[CheckResult] = []
    profile: Profile | None = None
    resolved_profile_id = profile_id or _profile_from_repo(root) or "generic"

    if not root_ok:
        checks.append(
            CheckResult(
                name="workspace",
                kind="workspace",
                status="FAIL",
                required=True,
                reason=f"repo root does not exist or is not a directory: {root}",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="workspace",
                kind="workspace",
                status="PASS",
                required=True,
                reason="repo root is readable",
            )
        )
        evidence_dir.mkdir(parents=True, exist_ok=True)

    try:
        profile = load_profile(resolved_profile_id, profile_dir=profile_dir)
        checks.append(
            CheckResult(
                name="profile",
                kind="profile",
                status="PASS",
                required=True,
                reason=f"loaded profile {profile.id}",
                details={"profile": profile.to_dict()},
            )
        )
    except (ProfileError, ProfileNotFoundError) as exc:
        checks.append(
            CheckResult(
                name="profile",
                kind="profile",
                status="FAIL",
                required=True,
                reason=str(exc),
                details={"requested_profile": resolved_profile_id},
            )
        )

    if root_ok and profile is not None:
        if include_build:
            checks.append(_run_profile_command(root, evidence_dir, "build", profile.command("build"), timeout_sec))
        if include_test:
            checks.append(_run_profile_command(root, evidence_dir, "test", profile.command("test"), timeout_sec))
            checks.append(_inspect_tdd_runner(root, profile.command("test") is not None))
        if include_lint:
            checks.append(_run_profile_command(root, evidence_dir, "lint", profile.command("lint"), timeout_sec))
        if include_qa:
            checks.append(_run_qa(root, evidence_dir, wave_segment, qa_pattern, qa_regression_pattern))
        if include_e2e:
            checks.append(_run_e2e(root, evidence_dir, profile))

    payload = _build_payload(root, wave_segment, resolved_profile_id, evidence_dir, checks)
    if root_ok:
        evidence_path = evidence_dir / "verify-product.json"
        evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        payload["evidence_path"] = _display_path(evidence_path, root)
        evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _build_payload(
    root: Path,
    wave: str,
    profile_id: str,
    evidence_dir: Path,
    checks: list[CheckResult],
) -> dict[str, Any]:
    check_dicts = [check.to_dict() for check in checks]
    failed = [check for check in check_dicts if check["status"] == "FAIL"]
    passed = [check for check in check_dicts if check["status"] == "PASS"]
    skipped = [check for check in check_dicts if check["status"] == "SKIP"]
    return {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(root),
        "wave": wave,
        "profile": profile_id,
        "status": "PASS" if not failed else "FAIL",
        "summary": {
            "total": len(check_dicts),
            "passed": len(passed),
            "failed": len(failed),
            "skipped": len(skipped),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "evidence_dir": _display_path(evidence_dir, root),
        "evidence_path": None,
        "checks": check_dicts,
    }


def _run_profile_command(
    root: Path,
    evidence_dir: Path,
    command_name: str,
    spec: CommandSpec | None,
    timeout_sec: int,
) -> CheckResult:
    if spec is None:
        return CheckResult(
            name=command_name,
            kind="profile-command",
            status="SKIP",
            required=False,
            reason=f"profile does not declare a {command_name} command",
        )

    log_path = evidence_dir / f"{command_name}.log"
    argv = _resolve_argv(spec.argv)
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            shell=False,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        output = (proc.stdout or "")
        if proc.stderr:
            output += "\n--- stderr ---\n" + proc.stderr
        log_path.write_text(output, encoding="utf-8", errors="replace")
        return CheckResult(
            name=command_name,
            kind="profile-command",
            status="PASS" if proc.returncode == 0 else "FAIL",
            required=spec.required,
            reason="command passed" if proc.returncode == 0 else "command failed",
            command=list(spec.argv),
            duration_ms=duration_ms,
            exit_code=proc.returncode,
            evidence_path=_display_path(log_path, root),
            details={"display_name": spec.name},
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        output = (exc.stdout or "")
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        if stderr:
            output += "\n--- stderr ---\n" + stderr
        output += f"\nCommand timed out after {timeout_sec}s.\n"
        log_path.write_text(output, encoding="utf-8", errors="replace")
        return CheckResult(
            name=command_name,
            kind="profile-command",
            status="FAIL",
            required=spec.required,
            reason=f"command timed out after {timeout_sec}s",
            command=list(spec.argv),
            duration_ms=duration_ms,
            exit_code=None,
            evidence_path=_display_path(log_path, root),
            details={"display_name": spec.name},
        )
    except OSError as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log_path.write_text(str(exc) + "\n", encoding="utf-8")
        return CheckResult(
            name=command_name,
            kind="profile-command",
            status="FAIL",
            required=spec.required,
            reason=f"command could not start: {exc}",
            command=list(spec.argv),
            duration_ms=duration_ms,
            exit_code=None,
            evidence_path=_display_path(log_path, root),
            details={"display_name": spec.name},
        )


def _inspect_tdd_runner(root: Path, profile_test_declared: bool) -> CheckResult:
    from signalos_lib.tdd_runner import detect_test_runner

    runner = detect_test_runner(root)
    if runner is None:
        return CheckResult(
            name="tdd-runner",
            kind="runner-detection",
            status="SKIP",
            required=False,
            reason="no TDD-compatible test runner detected",
        )
    name, argv = runner
    reason = "detected test runner; profile test command owns execution"
    if not profile_test_declared:
        reason = "detected test runner but profile does not declare product test execution"
    return CheckResult(
        name="tdd-runner",
        kind="runner-detection",
        status="PASS" if profile_test_declared else "SKIP",
        required=False,
        reason=reason,
        command=list(argv),
        details={"runner": name},
    )


def _run_qa(
    root: Path,
    evidence_dir: Path,
    wave: str,
    scenario_pattern: str,
    regression_pattern: str | None,
) -> CheckResult:
    scenarios = _glob_under_root(root, scenario_pattern)
    if not scenarios:
        return CheckResult(
            name="qa",
            kind="qa-runner",
            status="SKIP",
            required=False,
            reason=f"no QA scenarios matched {scenario_pattern}",
        )

    from signalos_lib.qa_runner import run_scenario_suite

    regressions = _glob_under_root(root, regression_pattern) if regression_pattern else []
    output_path = evidence_dir / "qa-evidence.json"
    start = time.perf_counter()
    try:
        pack = run_scenario_suite(
            scenario_pattern=str(root / scenario_pattern),
            regression_pattern=str(root / regression_pattern) if regression_pattern and regressions else None,
            wave=wave,
            output_path=str(output_path),
            screenshot_dir=str(evidence_dir / "qa-screenshots"),
            capture_vitals=True,
            gating=True,
            verbose=False,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        status = "FAIL" if pack.fail_count > 0 else "PASS"
        return CheckResult(
            name="qa",
            kind="qa-runner",
            status=status,
            required=True,
            reason="QA scenarios passed" if status == "PASS" else "QA scenarios failed",
            duration_ms=duration_ms,
            exit_code=0 if status == "PASS" else 1,
            evidence_path=_display_path(output_path, root),
            details=pack.as_dict(),
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        output_path.write_text(str(exc) + "\n", encoding="utf-8")
        return CheckResult(
            name="qa",
            kind="qa-runner",
            status="FAIL",
            required=True,
            reason=f"QA runner failed: {exc}",
            duration_ms=duration_ms,
            evidence_path=_display_path(output_path, root),
        )


def _run_e2e(root: Path, evidence_dir: Path, profile: Profile) -> CheckResult:
    if profile.preview.mode == "none":
        return CheckResult(
            name="e2e",
            kind="e2e-runner",
            status="SKIP",
            required=False,
            reason=profile.preview.disabled_reason or "profile preview is disabled",
        )

    from signalos_lib.e2e_runner import run_e2e_task

    output_path = evidence_dir / "e2e.json"
    start = time.perf_counter()
    result = run_e2e_task(
        {
            "skills": ["e2e-testing"],
            "description": "Product verification smoke check.",
        },
        root,
    )
    duration_ms = int((time.perf_counter() - start) * 1000)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if result.get("skipped"):
        return CheckResult(
            name="e2e",
            kind="e2e-runner",
            status="SKIP",
            required=False,
            reason=str(result.get("log") or result.get("failure") or "e2e skipped"),
            duration_ms=duration_ms,
            evidence_path=_display_path(output_path, root),
            details=result,
        )
    ok = bool(result.get("ok"))
    return CheckResult(
        name="e2e",
        kind="e2e-runner",
        status="PASS" if ok else "FAIL",
        required=True,
        reason="E2E smoke passed" if ok else str(result.get("failure") or "E2E smoke failed"),
        duration_ms=duration_ms,
        exit_code=0 if ok else 1,
        evidence_path=_display_path(output_path, root),
        details=result,
    )


def _glob_under_root(root: Path, pattern: str | None) -> list[Path]:
    if not pattern:
        return []
    return [Path(path) for path in glob.glob(str(root / pattern), recursive=True)]


def _profile_from_repo(root: Path) -> str | None:
    candidates = [
        root / ".signalos" / "product.json",
        root / ".signalos" / "factory.json",
        root / ".signalos" / "profile.json",
    ]
    keys = ("profile", "profile_id", "stack_profile", "stack")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _resolve_argv(argv: tuple[str, ...]) -> list[str]:
    resolved = list(argv)
    exe = shutil.which(resolved[0])
    if exe:
        resolved[0] = exe
    return resolved


def _safe_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return segment or DEFAULT_EVIDENCE_SEGMENT


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos verify-product",
        description="Run product build/test verification and capture normalized evidence.",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Product repo root. Defaults to cwd.")
    parser.add_argument("--wave", default=None, help="Evidence wave folder. Defaults to product-verification.")
    parser.add_argument("--profile", default=None, help="Profile id. Defaults to repo metadata or generic.")
    parser.add_argument("--timeout-sec", type=int, default=300, help="Timeout for profile commands.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON.")
    parser.add_argument("--no-build", action="store_true", help="Skip profile build command.")
    parser.add_argument("--no-test", action="store_true", help="Skip profile test command.")
    parser.add_argument("--no-lint", action="store_true", help="Skip profile lint command.")
    parser.add_argument("--no-qa", action="store_true", help="Skip QA scenarios.")
    parser.add_argument("--no-e2e", action="store_true", help="Skip E2E smoke.")
    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    payload = verify_product(
        repo_root=args.repo_root,
        wave=args.wave,
        profile_id=args.profile,
        timeout_sec=args.timeout_sec,
        include_build=not args.no_build,
        include_test=not args.no_test,
        include_lint=not args.no_lint,
        include_qa=not args.no_qa,
        include_e2e=not args.no_e2e,
    )
    if args.as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        _render_summary(payload)
    return 0 if payload["status"] == "PASS" else 1


def _render_summary(payload: dict[str, Any]) -> None:
    sys.stdout.write(f"SignalOS product verification: {payload['status']}\n")
    sys.stdout.write(f"Evidence: {payload.get('evidence_path')}\n")
    for check in payload["checks"]:
        sys.stdout.write(f"- {check['name']}: {check['status']} {check['reason']}\n")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
