"""Phase specifications and shared helpers for SignalOS test automation.

This module owns the technology-neutral ``PhaseSpec`` catalog plus the
low-level evidence helpers shared by the per-phase runners.  Keeping the
helpers here (rather than in ``__init__``) avoids an import cycle between the
orchestration layer and ``runners``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_INTERNAL_ERROR = 2
EXIT_THRESHOLD_VIOLATION = 8

SCHEMA_VERSION = "signalos.test_automation.v1"
DEFAULT_PROFILE = "generic"

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_BLOCKED = "blocked"
STATUS_PENDING = "pending"
STATUS_DRY_RUN = "dry-run"
STATUS_NOT_APPLICABLE = "not-applicable"
STATUS_PASS_WITH_NOT_APPLICABLE = "pass-with-not-applicable"

# Profiles that genuinely have no UI surface. A generic/undeclared product is
# NOT on this list: SignalOS enforces, never advises, so an undeclared product
# must RUN e2e + visual and block (exit 8) when it lacks the required evidence.
NO_UI_PROFILES = {"node-api", "fastapi-api", "dotnet-minimal-api", "go-api"}

SOURCE_SUFFIXES = {
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".py",
    ".rs",
    ".ts",
    ".tsx",
}
SCAN_DIRS = ("src", "app", "server", "api", "pages", "python")
SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "bin",
    "build",
    "dist",
    "node_modules",
    "obj",
    "target",
    "venv",
}


@dataclass(frozen=True)
class PhaseSpec:
    verb: str
    phase_name: str
    carryover_id: str
    scenario_id: int
    audit_action: str
    runner: str
    description: str
    evidence_globs: tuple[str, ...] = ()
    validation_categories: tuple[str, ...] = ()
    optional_when_no_ui: bool = False
    constitution_flag: str | None = None


PHASES: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        "unit",
        "P1 Unit + Component",
        "CO-008",
        180,
        "test.unit.executed",
        "unit",
        "Unit/component verification through the selected product profile.",
        validation_categories=("test",),
    ),
    PhaseSpec(
        "integration",
        "P2 API + Integration",
        "CO-009",
        181,
        "test.integration.executed",
        "evidence",
        "Integration/API evidence, including container-backed or service tests.",
        (
            ".signalos/quality/p02-api-integration/**",
            "core/quality/test-automation/p02-api-integration/**",
            "tests/integration/**",
            "test/integration/**",
            "**/*IntegrationTests*",
            "docker-compose*.yml",
            "docker-compose*.yaml",
            "compose*.yml",
            "compose*.yaml",
        ),
        ("qa", "e2e", "test"),
    ),
    PhaseSpec(
        "contract",
        "P3 Contract",
        "CO-010",
        182,
        "test.contract.verified",
        "contract",
        "Consumer/provider contract or OpenAPI compatibility evidence.",
        (
            ".signalos/contracts/**",
            ".signalos/quality/p03-contract/**",
            "contracts/**",
            "pacts/**",
            "pact/**",
            "**/openapi*.json",
            "**/openapi*.yaml",
            "**/openapi*.yml",
        ),
    ),
    PhaseSpec(
        "e2e",
        "P4 E2E UI",
        "CO-011",
        183,
        "test.e2e.executed",
        "e2e",
        "End-to-end runtime or browser smoke proof.",
        (
            "core/governance/QA/scenarios/*.yaml",
            "tests/e2e/**",
            "test/e2e/**",
            "playwright.config.*",
            "cypress.config.*",
        ),
        ("e2e", "runtime_smoke", "ux_smoke"),
        optional_when_no_ui=True,
    ),
    PhaseSpec(
        "visual",
        "P5 Visual Regression",
        "CO-012",
        184,
        "test.visual.executed",
        "visual",
        "Visual regression evidence such as screenshots or snapshot reports.",
        (
            ".signalos/quality/reports/visual/**",
            ".signalos/quality/p05-visual/**",
            "**/__screenshots__/**",
            "**/screenshots/**",
            "**/visual-regression/**",
            "playwright.config.*",
        ),
        ("ux_smoke",),
        optional_when_no_ui=True,
    ),
    PhaseSpec(
        "performance",
        "P6 Performance",
        "CO-013",
        185,
        "test.performance.executed",
        "evidence",
        "Performance benchmark and Core Web Vitals evidence.",
        (
            ".signalos/deploy/benchmarks.jsonl",
            ".signalos/performance/**",
            ".signalos/quality/p06-performance/**",
            "**/lighthouse*.json",
            "**/k6*.json",
            "**/performance*.json",
        ),
    ),
    PhaseSpec(
        "security",
        "P7 Security",
        "CO-014",
        186,
        "test.security.executed",
        "security",
        "Injection scan, threat-model, canary, and posture evidence.",
        (
            ".signalos/product/SECURITY_RESULT.json",
            ".signalos/security/**",
            "core/governance/SecurityAudit/**",
        ),
    ),
    PhaseSpec(
        "chaos",
        "P8 Chaos + Resilience",
        "CO-015",
        187,
        "test.chaos.executed",
        "chaos",
        "Resilience, fault-injection, or chaos-test proof.",
        (
            ".signalos/chaos/**",
            ".signalos/quality/p08-chaos/**",
            "chaos/**",
            "resilience/**",
            "**/toxiproxy*.json",
        ),
        constitution_flag="chaos_testing_required",
    ),
    PhaseSpec(
        "production-monitor",
        "P9 Production Monitoring",
        "CO-016",
        188,
        "test.production_monitor.executed",
        "production_monitor",
        "Production signal, listening-window, and deployment telemetry evidence.",
        (
            ".signalos/observability/**",
            ".signalos/deploy/**",
            ".signalos/quality/p09-production-monitor/**",
        ),
        constitution_flag="production_metrics_required",
    ),
    PhaseSpec(
        "data",
        "P10 Test Data Management",
        "CO-017",
        189,
        "test.data.executed",
        "evidence",
        "Test data management and data-protection evidence.",
        (
            ".signalos/privacy/**",
            ".signalos/data/**",
            ".signalos/quality/p10-data/**",
            "core/governance/**/DATA_PROCESSING_RECORD.md",
            "**/DATA_PROCESSING_RECORD.md",
            "tests/fixtures/**",
        ),
    ),
    PhaseSpec(
        "pipeline",
        "P11 Pipeline Integration",
        "CO-018",
        190,
        "test.pipeline.executed",
        "pipeline",
        "CI, release-readiness, and pipeline wiring evidence.",
        (
            ".github/workflows/*.yml",
            ".github/workflows/*.yaml",
            ".gitlab-ci.yml",
            "azure-pipelines.yml",
            ".signalos/pipeline/**",
            ".signalos/evidence/**/release-readiness.json",
        ),
    ),
    PhaseSpec(
        "governance",
        "P12 Metrics Governance",
        "CO-019",
        191,
        "test.governance.executed",
        "governance",
        "Governance artifacts, audit trail, and traceability evidence.",
        (
            ".signalos/AUDIT_TRAIL.jsonl",
            ".signalos/PRD_TRACEABILITY.md",
            ".signalos/TRACEABILITY_MATRIX.md",
            "core/governance/Governance/CONSTITUTION.md",
            "core/governance/Governance/SOUL-DOCUMENT.md",
            "core/strategy/BELIEF.md",
        ),
    ),
)

PHASE_BY_VERB = {phase.verb: phase for phase in PHASES}


def _check(
    check_id: str,
    status: str,
    message: str,
    *,
    evidence: str | list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "message": message,
        "evidence": [] if evidence is None else ([evidence] if isinstance(evidence, str) else evidence),
        "details": details or {},
    }


def _read_audit_rows(root: Path) -> list[dict[str, Any]]:
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not audit.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in audit.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _invalid_jsonl_lines(path: Path) -> list[int]:
    invalid: list[int] = []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            invalid.append(index)
    return invalid


def _match_evidence_globs(root: Path, patterns: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if _is_skipped_path(path):
                continue
            rel = _display_path(path, root)
            if rel not in seen:
                seen.add(rel)
                matches.append(rel)
    return matches


def _discover_source_files(root: Path, *, limit: int) -> list[str]:
    files: list[str] = []
    for rel_dir in SCAN_DIRS:
        base = root / rel_dir
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if len(files) >= limit:
                return files
            if _is_skipped_path(path) or not path.is_file():
                continue
            if path.suffix.lower() in SOURCE_SUFFIXES:
                files.append(_display_path(path, root))
    return files


def _is_skipped_path(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _load_intent(root: Path) -> dict[str, Any]:
    for path in (
        root / ".signalos" / "product" / "INTENT.json",
        root / ".signalos" / "sources" / "initial-intent.json",
        root / ".signalos" / "product.json",
    ):
        data = _load_json_file(path)
        if isinstance(data, dict):
            return data
    return {}


def _load_json_file(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _profile_from_repo(root: Path) -> str:
    for path in (
        root / ".signalos" / "product.json",
        root / ".signalos" / "factory.json",
        root / ".signalos" / "profile.json",
    ):
        data = _load_json_file(path)
        if not isinstance(data, dict):
            continue
        for key in ("profile", "profile_id", "stack_profile", "stack"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return DEFAULT_PROFILE


def _profile_is_no_ui(root: Path, profile: str) -> bool:
    data = _load_json_file(root / ".signalos" / "product.json")
    if isinstance(data, dict):
        frontend = data.get("frontend")
        if isinstance(frontend, str) and frontend.lower() == "none":
            return True
    return profile in NO_UI_PROFILES


def _constitution_flag_enabled(root: Path, flag: str | None) -> bool:
    if not flag:
        return False
    needle = flag.lower()
    for path in (
        root / "core" / "governance" / "Governance" / "CONSTITUTION.md",
        root / ".signalos" / "CONSTITUTION.md",
        root / ".signalos" / "constitution.md",
    ):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if re.search(rf"{re.escape(needle)}\s*[:=]\s*(true|yes|required|on)", text):
            return True
    return False


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
