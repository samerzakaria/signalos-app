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
    "run_validation",
    "write_validation_plan",
    "write_validation_result",
]

import json
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

def run_validation(
    repo_root: Path,
    plan: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute the validation plan.

    For each command category, run the commands and capture results.
    If *dry_run* is ``True``, check that commands exist but do not
    execute them.
    """
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
        "profile": plan.get("profile", "unknown"),
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
    }


def _run_commands(repo_root: Path, cmds: list[str]) -> dict[str, Any]:
    """Run a list of shell commands, returning aggregated result."""
    outputs: list[str] = []
    start = time.perf_counter()
    for cmd in cmds:
        argv = cmd.split()
        exe = shutil.which(argv[0])
        if exe is None:
            elapsed = time.perf_counter() - start
            return {
                "status": "blocked",
                "output": f"command not found: {argv[0]}",
                "duration_s": round(elapsed, 3),
            }
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=300,
                shell=True,
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
                "status": "failed",
                "output": f"command timed out: {cmd}",
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
