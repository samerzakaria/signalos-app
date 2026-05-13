# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/validate_cmd.py
# W3.5 — Operator validator runner (AMD-CORE-018)
#
# Runs all governance validators with severity-labelled output,
# mirroring deliver.sh's VALIDATOR_SEVERITY map.

from __future__ import annotations

__all__ = [
    "VALIDATOR_SEVERITY",
    "DEFAULT_SEVERITY",
    "ValidatorResult",
    "run_validators",
    "get_severity",
]

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Mirror of deliver.sh's VALIDATOR_SEVERITY map
VALIDATOR_SEVERITY: dict[str, str] = {
    "gate-signature-guard":         "HALT",
    "constitution-amendment-guard": "HALT",
    "ownership-guard":              "HALT",
    "trust-tier-guard":             "BLOCK_MERGE",
    "tier-sheet-guard":             "BLOCK_MERGE",
    "artifact-shape-guard":         "BLOCK_MERGE",
    "path-consistency-guard":       "BLOCK_MERGE",
    "expectation-redline-guard":    "BLOCK_MERGE",
    "decision-dna-guard":           "WARN",
    "client-signal-verbatim-guard": "WARN",
    "metrics-config-validator":     "WARN",
}

DEFAULT_SEVERITY = "BLOCK_MERGE"


def get_severity(name: str) -> str:
    """Return the severity for *name*, falling back to DEFAULT_SEVERITY."""
    return VALIDATOR_SEVERITY.get(name, DEFAULT_SEVERITY)


@dataclass
class ValidatorResult:
    """Result of running one validator script."""
    name: str
    severity: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    skipped: bool = False
    skip_reason: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 or self.skipped

    @property
    def status_label(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.exit_code == 0 else "FAIL"


def run_validators(
    repo_root: Optional[Path] = None,
    validator_name: Optional[str] = None,
) -> list[ValidatorResult]:
    """Run all (or one named) validator scripts under core/governance/Validators/.

    Returns a list of :class:`ValidatorResult` in alphabetical order by name.

    Exit-code semantics preserved from deliver.sh: exit 0 = pass, anything
    else = fail.  Scripts that do not exist are reported as ``skipped``.
    """
    if repo_root is None:
        repo_root = Path.cwd()
    repo_root = Path(repo_root)

    validators_dir = repo_root / "core" / "governance" / "Validators"

    if validator_name:
        names = [validator_name]
    else:
        if validators_dir.exists():
            names = sorted(
                p.stem for p in validators_dir.glob("*.sh")
                if p.stem != "wiring-guard"  # wiring-guard is run by health
            )
        else:
            names = list(VALIDATOR_SEVERITY.keys())

    results: list[ValidatorResult] = []
    for name in names:
        script = validators_dir / f"{name}.sh"
        severity = get_severity(name)

        if not script.exists():
            results.append(ValidatorResult(
                name=name, severity=severity, exit_code=0,
                skipped=True, skip_reason=f"script not found: {script}"
            ))
            continue

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                ["bash", str(script), "--repo-root", str(repo_root)],
                capture_output=True, text=True, timeout=120,
                cwd=str(repo_root),
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            results.append(ValidatorResult(
                name=name,
                severity=severity,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
            ))
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - t0) * 1000)
            results.append(ValidatorResult(
                name=name, severity=severity, exit_code=1,
                stderr="timed out after 120s", duration_ms=duration_ms,
            ))
        except Exception as exc:
            results.append(ValidatorResult(
                name=name, severity=severity, exit_code=1,
                stderr=str(exc),
            ))

    return results


def overall_exit_code(results: list[ValidatorResult]) -> int:
    """Return 0/1/2 exit code from a list of results.

    0 = all pass, 1 = any HALT failure, 2 = any BLOCK_MERGE failure, else 0
    (WARN failures don't change exit code — they're informational).
    """
    has_halt = any(not r.passed and r.severity == "HALT" for r in results)
    has_block = any(not r.passed and r.severity == "BLOCK_MERGE" for r in results)
    if has_halt:
        return 1
    if has_block:
        return 2
    return 0
