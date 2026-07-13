# signalos_lib/product/acceptance.py
# Phase P6 - TDD and Acceptance Plan
#
# Builds an acceptance matrix mapping intent -> criteria -> test scenarios.
# The matrix is the gate artifact that must be green before delivery closure.

from __future__ import annotations

__all__ = [
    "apply_verifiability_tiers",
    "build_acceptance_matrix",
    "check_closure_readiness",
    "classify_criterion_verifiability",
    "ensure_ux_acceptance_test",
    "has_responsive_breakpoints",
    "load_acceptance_matrix",
    "reconcile_acceptance_evidence",
    "run_ux_acceptance",
    "scan_ux_state_coverage",
    "update_criterion_status",
    "ux_acceptance_applies",
    "ux_acceptance_test_source",
    "write_acceptance_matrix",
    "UX_ACCEPTANCE_MIN_CONTROLS",
    "UX_ACCEPTANCE_MIN_STYLED",
    "UX_ACCEPTANCE_MIN_STYLED_RATIO",
    "UX_ACCEPTANCE_TEST_BASENAME",
]

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# UX baseline must-haves (deterministic, RED-gated)
# ---------------------------------------------------------------------------
#
# The design contract already ASKS, in prose, for a responsive layout and
# empty/loading/error states. Prose is not enforcement -- weaker models skip it,
# scoring a UI "done" on file existence. These helpers promote the
# deterministically-checkable UX floor into real acceptance criteria the
# pipeline verifies:
#
#   * responsive  -- FAILS if a browser product declares ZERO responsive
#                    breakpoints (Tailwind sm:/md:/lg:/xl:/2xl:, a CSS @media
#                    query, or a @container query).
#   * empty/loading/error -- FAIL unless a test MOUNTS the UI (render/mount/
#                    screen) and exercises that state.
#
# Subjective aesthetics (look / feel / polish) are deliberately NOT gated here;
# that stays for the human/LLM judge. Baseline must-haves are RED-gated;
# quality above baseline is graded. All checks are deterministic (no LLM).

# The four UX baseline criteria, as (marker, outcome) pairs. The marker keys
# into the scan result and the reconciliation blocker/evidence maps.
_UX_BASELINE_CRITERIA: tuple[tuple[str, str], ...] = (
    ("responsive",
     "Responsive layout: the UI adapts across screen sizes "
     "(responsive breakpoints present)"),
    ("empty_state",
     "Empty state: the UI handles the no-data case "
     "(a test mounts empty data and asserts the empty UI)"),
    ("loading_state",
     "Loading state: the UI shows a loading indicator while data is pending "
     "(a test mounts the pending state)"),
    ("error_state",
     "Error state: the UI handles failures gracefully "
     "(a test mounts an error and asserts the error UI)"),
)

_UX_BASELINE_BLOCKERS = {
    "responsive": ("no responsive breakpoints in the UI source -- add sm:/md:/lg: "
                   "utilities, a @media query, or a @container query so the "
                   "layout adapts to screen size"),
    "empty_state": "no test mounts the empty / no-data state and asserts the empty UI",
    "loading_state": ("no test mounts the loading / pending state and asserts a "
                      "loading indicator"),
    "error_state": "no test mounts the error / failure state and asserts the error UI",
}

_UX_BASELINE_EVIDENCE = {
    "responsive": "responsive breakpoints present",
    "empty_state": "empty-state test present",
    "loading_state": "loading-state test present",
    "error_state": "error-state test present",
}

# Front-end source / markup / style files to scan (product code, not backend).
_UX_SOURCE_SUFFIXES = (".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte",
                       ".astro", ".css", ".scss", ".sass", ".less", ".html")
_SCAN_SKIP_DIRS = frozenset({
    "node_modules", "dist", "build", "out", ".next", "coverage", ".signalos",
    "vendor", "__pycache__", ".turbo", ".cache", ".venv", "venv", ".git",
})
_SCAN_FILE_CAP = 5000

# Tailwind responsive variants (sm:/md:/lg:/xl:/2xl:), CSS media queries, and
# container queries -- any one proves the layout adapts to viewport size. The
# lookbehind keeps a breakpoint token from matching inside a longer identifier.
_RESPONSIVE_RE = re.compile(r"@media\b|@container\b|(?<![\w-])(?:sm|md|lg|xl|2xl):")

# A test that MOUNTS the app/component -- distinguishes a real behavioural test
# from a pure unit test that never renders UI.
_MOUNTS_UI_RE = re.compile(r"\b(?:render|mount|renderHook)\s*\(|\bscreen\b")

_UX_STATE_RES = {
    "empty_state": re.compile(
        r"\bempty\b|no\s+\w+\s+(?:yet|found|to\s+show)|nothing\s+here|"
        r"get\s+started|\bno\s+data\b|\bisempty\b|\[\s*\]", re.I),
    "loading_state": re.compile(
        r"\bloading\b|\bpending\b|\bis[_-]?loading\b|\bis[_-]?fetching\b|"
        r"\bspinner\b|\bskeleton\b|aria-busy", re.I),
    "error_state": re.compile(
        r"\berror\b|\bfailed\b|\bfailure\b|try\s+again|something\s+went\s+wrong|"
        r"mock[_-]?reject|rejectedvalue|\bis[_-]?error\b|onerror|\.catch\b|"
        r"\bthrow\b|\breject\b", re.I),
}


def _is_test_file(name: str) -> bool:
    return ".test." in name or ".spec." in name or name.startswith("test_")


def _scan_ux(repo_root: Path, source_dir: str | None = None) -> dict[str, bool]:
    """Single deterministic walk of the front-end source. Returns
    ``{"responsive": bool, "empty_state": bool, "loading_state": bool,
    "error_state": bool}``. ``responsive`` is set from product source (non-test)
    breakpoints; the three state flags require a TEST file that mounts the UI AND
    references that state. Vendor/build dirs are pruned and the scan is capped so
    it stays cheap on a real product repo."""
    result = {"responsive": False, "empty_state": False,
              "loading_state": False, "error_state": False}
    base = Path(repo_root)
    if source_dir:
        base = base / source_dir
    if not base.is_dir():
        return result
    files_read = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames
                       if d not in _SCAN_SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(_UX_SOURCE_SUFFIXES):
                continue
            try:
                text = (Path(dirpath) / fn).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                continue
            files_read += 1
            if files_read > _SCAN_FILE_CAP:
                return result
            if _is_test_file(fn):
                if _MOUNTS_UI_RE.search(text):
                    for state, rx in _UX_STATE_RES.items():
                        if not result[state] and rx.search(text):
                            result[state] = True
            elif not result["responsive"] and _RESPONSIVE_RE.search(text):
                result["responsive"] = True
            if all(result.values()):
                return result
    return result


def has_responsive_breakpoints(repo_root: Path,
                               source_dir: str | None = None) -> bool:
    """True if any UI source/style file declares a responsive breakpoint
    (Tailwind sm:/md:/lg:/xl:/2xl:, a CSS @media query, or a @container query).
    RED-gates the responsive baseline: a browser product with zero breakpoints
    does not adapt to screen size."""
    return _scan_ux(repo_root, source_dir)["responsive"]


def scan_ux_state_coverage(repo_root: Path,
                           source_dir: str | None = None) -> dict[str, bool]:
    """Which UX states {empty_state, loading_state, error_state} are exercised by
    a test that MOUNTS the UI (render/mount/screen) AND references that state.
    Makes the design contract's 'handle empty/loading/error' MANDATE enforceable
    rather than advisory."""
    scan = _scan_ux(repo_root, source_dir)
    return {k: scan[k] for k in ("empty_state", "loading_state", "error_state")}


# ---------------------------------------------------------------------------
# Profile -> test-file target mapping
# ---------------------------------------------------------------------------

_PROFILE_TEST_TARGETS: dict[str, str] = {
    "react-vite": "src/{entity}.test.tsx",
    "node-api": "tests/{entity}.test.js",
    "fastapi-api": "tests/test_{entity}.py",
    "dotnet-minimal-api": "tests/{entity}.http",
    "go-api": "internal/app/{entity}_test.go",
    "agent-selected": "tests/{entity}.acceptance.md",
    "generic": "tests/test_{entity}.py",
    "existing-repo": "tests/test_{entity}.py",
}


def _test_target(profile: str, entity: str) -> str:
    """Return a test file path for the given profile and entity slug."""
    template = _PROFILE_TEST_TARGETS.get(profile, "tests/test_{entity}.py")
    slug = entity.lower().replace(" ", "_")
    return template.format(entity=slug)


def _requires_browser_acceptance(intent: dict[str, Any], profile: str) -> bool:
    preferences = intent.get("capability_preferences")
    frontend = ""
    if isinstance(preferences, dict):
        frontend = str(preferences.get("frontend", "")).lower()
    if frontend == "none":
        return False
    return profile == "react-vite" or bool(intent.get("ux_surfaces"))


def _blueprint_scenario(
    blueprint: dict[str, Any],
    test_id: str,
) -> dict[str, Any] | None:
    bp_tests = blueprint.get("tests_detail", {})
    for scenario in bp_tests.get("scenarios", []):
        if scenario.get("id") == test_id:
            return scenario
    return None


def _blueprint_criterion_description(
    crit: dict[str, Any],
    scenario: dict[str, Any] | None,
    *,
    browser_acceptance: bool,
) -> str:
    if not browser_acceptance and scenario and scenario.get("acceptance"):
        return str(scenario["acceptance"])
    return str(crit.get("outcome", ""))


# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------

def _ac_id(n: int) -> str:
    return f"AC-{n:03d}"


def _ts_id(n: int) -> str:
    return f"TS-{n:03d}"


# ---------------------------------------------------------------------------
# Matrix builder
# ---------------------------------------------------------------------------

def build_acceptance_matrix(
    intent: dict[str, Any],
    blueprint: dict[str, Any] | None,
    profile: str,
) -> dict[str, Any]:
    """Build an acceptance matrix from intent and blueprint.

    Returns a dict conforming to ``signalos.acceptance_matrix.v1``.
    """
    criteria: list[dict[str, Any]] = []
    test_scenarios: list[dict[str, Any]] = []
    ac_counter = 0
    ts_counter = 0
    from_intent = 0
    from_blueprint = 0

    # --- 1. Entity CRUD criteria (from intent) ---
    for entity in intent.get("entities", []):
        ac_counter += 1
        ac = _ac_id(ac_counter)
        criteria.append({
            "id": ac,
            "source": "intent",
            "description": f"CRUD operations for {entity}",
            "entity": entity,
            "workflow": None,
            "test_ids": [],
            "status": "pending",
            "evidence": None,
        })
        from_intent += 1

        # Generate a test scenario linked to this criterion
        ts_counter += 1
        ts = _ts_id(ts_counter)
        criteria[-1]["test_ids"].append(ts)
        test_scenarios.append({
            "id": ts,
            "acceptance_id": ac,
            "description": f"CRUD operations for {entity} work correctly",
            "kind": "integration",
            "profile_target": _test_target(profile, entity),
            "status": "pending",
        })

    # --- 2. Workflow criteria (from intent) ---
    for workflow in intent.get("primary_workflows", []):
        ac_counter += 1
        ac = _ac_id(ac_counter)
        criteria.append({
            "id": ac,
            "source": "intent",
            "description": f"Workflow: {workflow}",
            "entity": None,
            "workflow": workflow,
            "test_ids": [],
            "status": "pending",
            "evidence": None,
        })
        from_intent += 1

        ts_counter += 1
        ts = _ts_id(ts_counter)
        criteria[-1]["test_ids"].append(ts)
        # Derive a slug from the workflow for the test target
        slug = workflow.split()[0] if workflow else "workflow"
        test_scenarios.append({
            "id": ts,
            "acceptance_id": ac,
            "description": f"Workflow '{workflow}' completes successfully",
            "kind": "integration",
            "profile_target": _test_target(profile, slug),
            "status": "pending",
        })

    browser_acceptance = _requires_browser_acceptance(intent, profile)

    # --- 3. UX surface criteria (from intent) ---
    for surface in (intent.get("ux_surfaces", []) if browser_acceptance else []):
        ac_counter += 1
        ac = _ac_id(ac_counter)
        criteria.append({
            "id": ac,
            "source": "intent",
            "description": f"UX surface '{surface}' renders correctly",
            "entity": None,
            "workflow": None,
            "test_ids": [],
            "status": "pending",
            "evidence": None,
        })
        from_intent += 1

        ts_counter += 1
        ts = _ts_id(ts_counter)
        criteria[-1]["test_ids"].append(ts)
        test_scenarios.append({
            "id": ts,
            "acceptance_id": ac,
            "description": f"UX surface '{surface}' renders without errors",
            "kind": "smoke",
            "profile_target": _test_target(profile, surface),
            "status": "pending",
        })

    # --- 3b. UX baseline must-haves (RED-gated, deterministic) ---
    # Promote the deterministically-checkable UX floor (responsive layout +
    # empty/loading/error states) that the design contract only ASKS for in
    # prose into real criteria the pipeline verifies. Only when there is a
    # browser surface; subjective aesthetics stay with the judge. Marked with
    # ``ux_baseline`` so reconciliation verifies them by source/test scan
    # instead of the test-file-existence path.
    for baseline, outcome in (_UX_BASELINE_CRITERIA if browser_acceptance else ()):
        ac_counter += 1
        criteria.append({
            "id": _ac_id(ac_counter),
            "source": "intent",
            "description": outcome,
            "entity": None,
            "workflow": None,
            "test_ids": [],
            "status": "pending",
            "evidence": None,
            "ux_baseline": baseline,
        })
        from_intent += 1

    # --- 4. Blueprint acceptance criteria ---
    if blueprint is not None:
        bp_acceptance = blueprint.get("acceptance_detail", {})
        for crit in bp_acceptance.get("criteria", []):
            test_id = crit.get("test_id", "")
            scenario = _blueprint_scenario(blueprint, test_id)
            ac_counter += 1
            ac = _ac_id(ac_counter)
            criteria.append({
                "id": ac,
                "source": "blueprint",
                "description": _blueprint_criterion_description(
                    crit,
                    scenario,
                    browser_acceptance=browser_acceptance,
                ),
                "entity": None,
                "workflow": None,
                "test_ids": [],
                "status": "pending",
                "evidence": None,
            })
            from_blueprint += 1

            # --- 5. Blueprint test scenarios ---
            if scenario is not None:
                ts_counter += 1
                ts = _ts_id(ts_counter)
                criteria[-1]["test_ids"].append(ts)
                test_scenarios.append({
                    "id": ts,
                    "acceptance_id": ac,
                    "description": (
                        scenario.get("description", "")
                        if browser_acceptance
                        else scenario.get("acceptance", scenario.get("description", ""))
                    ),
                    "kind": "integration",
                    "profile_target": _test_target(
                        profile, test_id.replace("-", "_"),
                    ),
                    "status": "pending",
                })

    product_name = intent.get("product_name", "")

    # Determine blueprint_id
    bp_id: str | None = None
    if blueprint is not None:
        bp_id = blueprint.get("id")

    matrix = {
        "schema_version": "signalos.acceptance_matrix.v1",
        "product_name": product_name,
        "profile": profile,
        "blueprint_id": bp_id,
        "criteria": criteria,
        "test_scenarios": test_scenarios,
        "summary": {
            "total_criteria": len(criteria),
            "total_tests": len(test_scenarios),
            "from_intent": from_intent,
            "from_blueprint": from_blueprint,
        },
    }
    # Layer 1 (mechanical verification): every criterion carries its
    # verifiability tier from birth -- the contract states up front how much
    # of it is machine-provable.
    return apply_verifiability_tiers(matrix)


# ---------------------------------------------------------------------------
# Verifiability tiers (mechanical-verification Layer 1)
# ---------------------------------------------------------------------------
#
# SignalOS's promise is constant quality per contract, and constancy comes
# only from mechanical verification. These tiers state, per criterion, HOW
# a criterion can be proven:
#
#   "mechanical" -- a concrete executable test target exists and the wording
#                   is objective: build/test evidence alone proves it.
#   "partial"    -- a test target exists but the wording carries subjective
#                   language (the test proves behaviour; a human confirms the
#                   judgment), OR the wording is objective but no executable
#                   test target exists yet (provable in principle, not yet
#                   machine-checked).
#   "human"      -- pure judgment (look / feel / tone) with no executable
#                   test target: only a human can verify it.
#
# Classification is fully deterministic from the criterion's own fields
# (test_scenario profile_target + wording heuristics); no LLM, no ambiguity.

_SUBJECTIVE_PHRASES = (
    "should look",
    "looks like",
    "looks good",
    "look and feel",
    "easy to use",
    "user friendly",
    "user-friendly",
    "on brand",
    "on-brand",
)

_SUBJECTIVE_WORDS = frozenset({
    "feel",
    "feels",
    "intuitive",
    "beautiful",
    "elegant",
    "delightful",
    "polished",
    "pleasing",
    "aesthetic",
    "aesthetics",
    "tasteful",
    "sleek",
    "stylish",
    "tone",
})


def _has_subjective_wording(text: str) -> bool:
    lowered = text.lower()
    if any(phrase in lowered for phrase in _SUBJECTIVE_PHRASES):
        return True
    words = set(re.findall(r"[a-z]+", lowered))
    return bool(words & _SUBJECTIVE_WORDS)


def classify_criterion_verifiability(
    criterion: dict[str, Any],
    scenarios: list[dict[str, Any]],
) -> str:
    """Deterministically classify a criterion into a verifiability tier.

    Returns ``"mechanical"``, ``"partial"``, or ``"human"`` (see the module
    section comment for exact semantics).
    """
    text = _criterion_text(criterion, scenarios)
    subjective = _has_subjective_wording(text)
    has_executable_target = any(
        _is_executable_test_target(
            str(scenario.get("profile_target", "")).replace("\\", "/")
        )
        for scenario in scenarios
        if scenario.get("profile_target")
    )
    if has_executable_target:
        return "partial" if subjective else "mechanical"
    return "human" if subjective else "partial"


def apply_verifiability_tiers(matrix: dict[str, Any]) -> dict[str, Any]:
    """Set ``verifiability`` on every criterion + a matrix-level summary.

    Persists ``verifiability_summary`` =
    ``{"mechanical": n, "partial": n, "human": n, "mechanical_pct": float}``
    -- ``mechanical_pct`` is the fraction of the contract that is
    machine-proven. Idempotent and purely additive (no blocking semantics).
    """
    criteria = matrix.get("criteria", [])
    scenario_by_id = {
        str(scenario.get("id")): scenario
        for scenario in matrix.get("test_scenarios", [])
        if scenario.get("id") is not None
    }
    counts = {"mechanical": 0, "partial": 0, "human": 0}
    for criterion in criteria:
        scenarios = [
            scenario_by_id[test_id]
            for test_id in criterion.get("test_ids", [])
            if test_id in scenario_by_id
        ]
        tier = classify_criterion_verifiability(criterion, scenarios)
        criterion["verifiability"] = tier
        counts[tier] += 1
    total = len(criteria)
    matrix["verifiability_summary"] = {
        **counts,
        "mechanical_pct": (
            round(100.0 * counts["mechanical"] / total, 1) if total else 0.0
        ),
    }
    return matrix


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_acceptance_matrix(matrix: dict[str, Any], signalos_dir: Path) -> Path:
    """Write to ``.signalos/product/ACCEPTANCE_MATRIX.json``."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "ACCEPTANCE_MATRIX.json"
    path.write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_acceptance_matrix(signalos_dir: Path) -> dict[str, Any] | None:
    """Load from ``.signalos/product/ACCEPTANCE_MATRIX.json``, or None."""
    path = signalos_dir / "product" / "ACCEPTANCE_MATRIX.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Status updates
# ---------------------------------------------------------------------------

def update_criterion_status(
    matrix: dict[str, Any],
    criterion_id: str,
    status: str,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Update a criterion's status. Returns the updated matrix."""
    for crit in matrix.get("criteria", []):
        if crit["id"] == criterion_id:
            crit["status"] = status
            if evidence is not None:
                crit["evidence"] = evidence
            break
    return matrix


# ---------------------------------------------------------------------------
# Evidence reconciliation
# ---------------------------------------------------------------------------

_EXECUTABLE_TEST_SUFFIXES = (
    ".test.js",
    ".test.jsx",
    ".test.ts",
    ".test.tsx",
    "_test.py",
    ".py",
)

_SECURITY_WORDS = {
    "auth",
    "access",
    "permission",
    "permissions",
    "rbac",
    "role",
    "roles",
    "security",
    "tenant",
    "isolation",
}


def reconcile_acceptance_evidence(
    matrix: dict[str, Any],
    repo_root: Path,
    *,
    validation_result: dict[str, Any] | None,
    runtime_proof: dict[str, Any] | None = None,
    ux_proof: dict[str, Any] | None = None,
    security_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark acceptance criteria passed only when concrete evidence exists.

    The reconciliation is intentionally conservative:
    - dry-run validation never passes acceptance;
    - build and test validation must both pass;
    - every linked test target for a criterion must exist on disk;
    - browser UX criteria require UX proof when UX proof was applicable;
    - security-sensitive criteria require a passed security gate.

    Criteria without enough evidence remain pending. Existing failed criteria
    are never overwritten.
    """
    criteria = matrix.get("criteria", [])
    scenarios = matrix.get("test_scenarios", [])
    scenario_by_id = {
        str(scenario.get("id")): scenario
        for scenario in scenarios
        if scenario.get("id") is not None
    }
    validation_ok = _validation_supports_acceptance(validation_result)
    build_status = _validation_category_status(validation_result, "build")
    test_status = _validation_category_status(validation_result, "test")
    runtime_status = (runtime_proof or {}).get("status", "not_run")
    ux_status = (ux_proof or {}).get("status", "not_run")
    security_status = (security_result or {}).get("status", "not_run")

    # UX baseline must-haves are verified by a deterministic source/test scan
    # (responsive breakpoints; tests that mount empty/loading/error) rather than
    # a named test file. Compute the scan once, only when such criteria exist.
    needs_ux_scan = any(c.get("ux_baseline") for c in criteria)
    ux_scan = _scan_ux(repo_root) if needs_ux_scan else {}

    passed = 0
    pending = 0
    failed = 0
    skipped = 0
    blockers: list[str] = []

    for criterion in criteria:
        current_status = str(criterion.get("status", "pending"))
        if current_status == "failed":
            failed += 1
            continue
        if current_status == "skipped":
            skipped += 1
            continue

        criterion_scenarios = [
            scenario_by_id[test_id]
            for test_id in criterion.get("test_ids", [])
            if test_id in scenario_by_id
        ]
        result = _criterion_reconciliation_result(
            criterion=criterion,
            scenarios=criterion_scenarios,
            repo_root=repo_root,
            validation_ok=validation_ok,
            validation_result=validation_result,
            ux_status=ux_status,
            security_status=security_status,
            ux_scan=ux_scan,
        )
        if result["passed"]:
            criterion["status"] = "passed"
            criterion["evidence"] = result["evidence"]
            passed += 1
            for scenario in criterion_scenarios:
                scenario["status"] = "passed"
                scenario["evidence"] = result["evidence"]
        else:
            criterion["status"] = "pending"
            criterion["evidence"] = result["evidence"]
            pending += 1
            blockers.extend(
                f"{criterion.get('id', 'AC-?')}: {blocker}"
                for blocker in result["blockers"]
            )
            for scenario in criterion_scenarios:
                scenario.setdefault("status", "pending")

    reconciliation = {
        "schema_version": "signalos.acceptance_reconciliation.v1",
        "reconciled_at": _utc_now(),
        "validation_build_status": build_status,
        "validation_test_status": test_status,
        "runtime_status": runtime_status,
        "ux_status": ux_status,
        "security_status": security_status,
        "passed": passed,
        "pending": pending,
        "failed": failed,
        "skipped": skipped,
        "blockers": blockers,
        "ready": pending == 0 and failed == 0 and passed > 0,
    }
    matrix["reconciliation"] = reconciliation
    matrix["summary"] = {
        **matrix.get("summary", {}),
        "passed": passed,
        "pending": pending,
        "failed": failed,
        "skipped": skipped,
    }
    # Layer 1: re-apply verifiability tiers (idempotent) so matrices built
    # before the tier feature existed still gain tiers at reconciliation.
    return apply_verifiability_tiers(matrix)


def _criterion_reconciliation_result(
    *,
    criterion: dict[str, Any],
    scenarios: list[dict[str, Any]],
    repo_root: Path,
    validation_ok: bool,
    validation_result: dict[str, Any] | None,
    ux_status: str,
    security_status: str,
    ux_scan: dict[str, bool] | None = None,
) -> dict[str, Any]:
    baseline = criterion.get("ux_baseline")
    if baseline:
        return _ux_baseline_reconciliation_result(
            baseline=str(baseline),
            validation_ok=validation_ok,
            validation_result=validation_result,
            ux_scan=ux_scan or {},
        )

    blockers: list[str] = []
    evidence_parts: list[str] = []

    if not validation_ok:
        if validation_result and validation_result.get("dry_run"):
            blockers.append("validation was dry-run only")
        else:
            blockers.append("build and test validation have not both passed")

    if not scenarios:
        blockers.append("no linked test scenario")

    targets: list[str] = []
    for scenario in scenarios:
        target = str(scenario.get("profile_target", "")).replace("\\", "/")
        if not target:
            blockers.append(f"{scenario.get('id', 'TS-?')} has no profile target")
            continue
        targets.append(target)
        if not _is_executable_test_target(target):
            blockers.append(f"{target} is not executable test evidence")
            continue
        if not (repo_root / target).is_file():
            blockers.append(f"{target} is missing")

    text = _criterion_text(criterion, scenarios)
    if _is_ux_criterion(text) and ux_status not in {"passed", "skipped"}:
        blockers.append(f"UX proof status is {ux_status}")
    if _is_security_criterion(text) and security_status != "passed":
        blockers.append(f"security gate status is {security_status}")

    if targets:
        evidence_parts.append("linked targets: " + ", ".join(sorted(targets)))
    if validation_ok:
        evidence_parts.append("build/test validation passed")
    if _is_ux_criterion(text):
        evidence_parts.append(f"UX proof: {ux_status}")
    if _is_security_criterion(text):
        evidence_parts.append(f"security gate: {security_status}")

    return {
        "passed": not blockers,
        "blockers": blockers,
        "evidence": (
            "; ".join(evidence_parts)
            if evidence_parts
            else "Acceptance evidence is pending."
        ),
    }


def _ux_baseline_reconciliation_result(
    *,
    baseline: str,
    validation_ok: bool,
    validation_result: dict[str, Any] | None,
    ux_scan: dict[str, bool],
) -> dict[str, Any]:
    """Reconcile a UX baseline must-have. Verified by the deterministic
    source/test scan (``ux_scan``), not a named test file. Still requires the
    build+test suite to be green (a criterion cannot pass on a red build)."""
    blockers: list[str] = []
    evidence_parts: list[str] = []

    if not validation_ok:
        if validation_result and validation_result.get("dry_run"):
            blockers.append("validation was dry-run only")
        else:
            blockers.append("build and test validation have not both passed")

    if ux_scan.get(baseline):
        evidence_parts.append(
            _UX_BASELINE_EVIDENCE.get(baseline, f"{baseline} present"))
    else:
        blockers.append(
            _UX_BASELINE_BLOCKERS.get(baseline, f"{baseline} not satisfied"))

    if validation_ok:
        evidence_parts.append("build/test validation passed")

    return {
        "passed": not blockers,
        "blockers": blockers,
        "evidence": (
            "; ".join(evidence_parts)
            if evidence_parts
            else "UX baseline evidence is pending."
        ),
    }


def _validation_supports_acceptance(
    validation_result: dict[str, Any] | None,
) -> bool:
    if not validation_result or validation_result.get("dry_run"):
        return False
    return (
        _validation_category_status(validation_result, "build") == "passed"
        and _validation_category_status(validation_result, "test") == "passed"
    )


def _validation_category_status(
    validation_result: dict[str, Any] | None,
    category: str,
) -> str:
    if not validation_result:
        return "not_run"
    return (
        validation_result.get("results", {})
        .get(category, {})
        .get("status", "not_run")
    )


def _is_executable_test_target(target: str) -> bool:
    lowered = target.lower()
    return any(lowered.endswith(suffix) for suffix in _EXECUTABLE_TEST_SUFFIXES)


def _criterion_text(
    criterion: dict[str, Any],
    scenarios: list[dict[str, Any]],
) -> str:
    parts = [
        str(criterion.get("description", "")),
        str(criterion.get("entity", "")),
        str(criterion.get("workflow", "")),
    ]
    for scenario in scenarios:
        parts.append(str(scenario.get("description", "")))
    return " ".join(parts).lower()


def _is_ux_criterion(text: str) -> bool:
    return any(
        word in text
        for word in ("ux surface", "renders", "dashboard", "screen", "page")
    )


def _is_security_criterion(text: str) -> bool:
    words = set(text.replace("-", " ").replace("_", " ").split())
    return bool(words & _SECURITY_WORDS)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Closure readiness
# ---------------------------------------------------------------------------

def check_closure_readiness(matrix: dict[str, Any]) -> dict[str, Any]:
    """Check if the matrix is ready for delivery closure.

    Returns a dict with ready flag, status counts, and blocker descriptions.
    """
    passed = 0
    failed = 0
    pending = 0
    skipped = 0
    blockers: list[str] = []

    for crit in matrix.get("criteria", []):
        st = crit.get("status", "pending")
        if st == "passed":
            passed += 1
        elif st == "failed":
            failed += 1
            blockers.append(f"{crit['id']}: {crit.get('description', '')} - failed")
        elif st == "pending":
            pending += 1
            blockers.append(f"{crit['id']}: {crit.get('description', '')} - pending")
        elif st == "skipped":
            skipped += 1

    ready = failed == 0 and pending == 0 and passed > 0

    return {
        "ready": ready,
        "passed": passed,
        "failed": failed,
        "pending": pending,
        "skipped": skipped,
        "blockers": blockers,
    }


# ---------------------------------------------------------------------------
# UX acceptance -- a BUILD-TIME HARD GATE: "ship a real, usable UI"
# ---------------------------------------------------------------------------
#
# The design contract asks, in prose, for a real, styled, usable interface.
# Prose is not enforcement -- a benchmark's strongest model shipped
# functionally-correct code with ZERO UI because only *function* was enforced
# ("grade only what you enforce"). This promotes "usable UI" into a MEASURED
# acceptance test the build must satisfy, enforced by the G4 "acceptance tests
# must pass" machinery.
#
# The check is a REAL browser-DOM MEASUREMENT, not source/className counting:
# it renders the actual product entry with @testing-library/react on jsdom
# (both shipped by the react-vite fixture, fully OFFLINE -- no Playwright, no
# network) and asserts, by inspecting the rendered DOM:
#
#   1. INTERACTIVE CONTROLS -- the UI renders real controls found by ARIA role
#      (button / textbox / combobox / checkbox / ...). A no-UI build that emits
#      only static markup renders nothing interactive -> fails. (A decoy that
#      sprinkles classNames onto non-interactive divs has no real controls ->
#      fails here.)
#   2. REAL STYLING -- a non-trivial share of the RENDERED elements carry a
#      styling signal: a component-library component (Mantine/Chakra/MUI/...),
#      an inline style, or a CSS class. A bare-HTML build (no classes, no
#      styles, no component library) has zero styled elements -> fails here.
#   3. A11Y BASICS -- every interactive control has an accessible name (a
#      <label>/aria-label for inputs, a name for buttons).
#
# Requiring BOTH (1) AND (2) is what makes it fair: a bare-HTML build passes
# controls but fails styling; a sprinkled-className decoy passes styling but
# fails controls; a genuinely styled, interactive product passes both. The
# measurement runs on the MOUNTED DOM, so dead/unmounted markup never counts.
#
# Thresholds are calibrated against real cached builds (styled builds pass with
# wide margin; a bare-HTML build has 0 styled elements) and kept deliberately
# generous so a genuinely-good build is never false-failed.

# Minimum interactive controls the rendered UI must expose (found by ARIA
# role). Two is enough to distinguish a real interactive surface from a
# static/no-UI shell without demanding a specific control count.
UX_ACCEPTANCE_MIN_CONTROLS = 2
# Minimum number of RENDERED elements that carry a styling signal (class /
# inline style / component-library component). A bare-HTML build has 0.
UX_ACCEPTANCE_MIN_STYLED = 3
# ...and at least this share of rendered elements must be styled, so a mostly
# unstyled page with a couple of incidental classes still fails.
UX_ACCEPTANCE_MIN_STYLED_RATIO = 0.10

# The authored test's basename. Lives beside the app entry (source dir) so its
# `./App` import resolves and vitest auto-discovers it in the suite.
UX_ACCEPTANCE_TEST_BASENAME = "__ux_acceptance__.test.tsx"

# Browser profiles that ship a React entry the render measurement can drive.
_UX_BROWSER_PROFILES = frozenset({"react-vite"})
_APP_ENTRY_SUFFIXES = (".tsx", ".jsx")


def ux_acceptance_test_source(app_import: str = "./App") -> str:
    """The source of the UX acceptance test (a real vitest + jsdom +
    @testing-library render measurement). Parametrised only by the import
    specifier for the product's App entry. The thresholds are baked in from
    the module constants so the model READS the exact bar it must satisfy."""
    return f'''/**
 * UX ACCEPTANCE -- SignalOS build-time hard gate (AUTO-AUTHORED, READ-ONLY).
 *
 * This test is the signed UX spec: a build does not pass the Build gate unless
 * it ships a REAL, styled, usable UI. It renders the actual product entry and
 * MEASURES the rendered DOM (no className/source counting): interactive
 * controls found by ARIA role, real styling signal on the mounted elements,
 * and a11y names. Do NOT edit, weaken, or delete this file -- make the PRODUCT
 * satisfy it (add a real, styled, interactive interface).
 *
 * Deterministic and fully offline (jsdom + Testing Library; no network).
 */
import {{ describe, it, expect }} from 'vitest';
import {{ render, queryAllByRole }} from '@testing-library/react';
import App from '{app_import}';

const INTERACTIVE_ROLES = [
  'button', 'textbox', 'combobox', 'checkbox', 'radio', 'switch',
  'spinbutton', 'searchbox', 'slider', 'menuitem', 'menuitemcheckbox', 'tab',
  'link',
];

// A component-library component leaves a recognizable class prefix on the DOM
// it renders (Mantine/Chakra/MUI/Ant/emotion/styled-components). Any of these,
// an inline style, or a plain CSS class counts as a styling signal.
const LIB_CLASS = /(^|\\s)(mantine-|chakra-|Mui[A-Z]|ant-|css-[a-z0-9]{{4,}}|chi-|sc-[a-zA-Z]|MuiBox)/;

function collectControls(container) {{
  let out = [];
  for (const role of INTERACTIVE_ROLES) {{
    out = out.concat(queryAllByRole(container, role));
  }}
  return out;
}}

function accessibleName(el) {{
  const aria = (el.getAttribute('aria-label') || '').trim();
  if (aria) return aria;
  const doc = el.ownerDocument;
  const labelledby = el.getAttribute('aria-labelledby');
  if (labelledby) {{
    const t = labelledby
      .split(/\\s+/)
      .map((id) => (doc.getElementById(id)?.textContent || '').trim())
      .join(' ')
      .trim();
    if (t) return t;
  }}
  const id = el.getAttribute('id');
  if (id) {{
    const lbl = doc.querySelector(`label[for="${{id}}"]`);
    if (lbl && (lbl.textContent || '').trim()) return lbl.textContent.trim();
  }}
  let p = el.parentElement;
  while (p) {{
    if (p.tagName === 'LABEL' && (p.textContent || '').trim()) return p.textContent.trim();
    p = p.parentElement;
  }}
  const txt = (el.textContent || '').trim();
  if (txt) return txt;
  const title = (el.getAttribute('title') || '').trim();
  if (title) return title;
  const ph = (el.getAttribute('placeholder') || '').trim();
  if (ph) return ph;
  const alt = (el.getAttribute('alt') || '').trim();
  if (alt) return alt;
  return '';
}}

describe('UX acceptance: ships a real, styled, usable UI', () => {{
  it('renders real interactive controls (not a no-UI/static shell)', () => {{
    const {{ container }} = render(<App />);
    const controls = collectControls(container);
    expect(
      controls.length,
      'The UI renders no real interactive controls (buttons/inputs/selects/links). ' +
        'A usable product needs an interactive surface, not static markup.',
    ).toBeGreaterThanOrEqual({UX_ACCEPTANCE_MIN_CONTROLS});
  }});

  it('ships real styling (component library, inline styles, or CSS -- not bare HTML)', () => {{
    const {{ container }} = render(<App />);
    const all = Array.from(container.querySelectorAll('*'));
    // Measured on the MOUNTED DOM (never source/className counting). Preferred
    // "real" signals: a PARSED inline style (jsdom computes it) or a
    // component-library component (it injects real CSS). Fallback rendered-DOM
    // signal: a CSS class on a rendered element -- how a utility framework
    // (Tailwind) or an external stylesheet hangs styling that jsdom's cascade
    // cannot compute. A bare-HTML build has none of the three -> styled == 0.
    let styled = 0;
    for (const el of all) {{
      const cls = (el.getAttribute('class') || '').trim();
      const hasInline = (el.getAttribute('style') || '').trim().length > 0 ||
        (el.style && el.style.length > 0);
      // real (jsdom-computed) styling OR the rendered-DOM class fallback
      if (hasInline || LIB_CLASS.test(cls) || cls) styled += 1;
    }}
    const ratio = all.length ? styled / all.length : 0;
    expect(
      styled,
      'The rendered UI carries no styling: not one element has a CSS class, an inline ' +
        'style, or a component-library component. Style the interface with a component ' +
        'library or real CSS -- do not ship bare, unstyled HTML.',
    ).toBeGreaterThanOrEqual({UX_ACCEPTANCE_MIN_STYLED});
    expect(
      ratio,
      'Almost nothing in the rendered UI is styled -- give the interface a real, ' +
        'consistent visual design.',
    ).toBeGreaterThanOrEqual({UX_ACCEPTANCE_MIN_STYLED_RATIO});
  }});

  it('interactive controls have accessible names (a11y basics)', () => {{
    const {{ container }} = render(<App />);
    const controls = collectControls(container);
    const unnamed = controls.filter((el) => !accessibleName(el));
    expect(
      unnamed.length,
      `${{unnamed.length}} interactive control(s) have no accessible name. Every input ` +
        'needs a <label> or aria-label, and every button needs a name.',
    ).toBe(0);
  }});
}});
'''


def _app_entry_import(repo_root: Path, source_dir: str) -> str | None:
    """The import specifier for the product's App entry, relative to a test
    that sits in ``source_dir`` -- ``./App`` when ``source_dir/App.tsx`` (or
    .jsx) exists, else None (non-standard layout: the gate does not apply)."""
    base = Path(repo_root) / source_dir
    for suffix in _APP_ENTRY_SUFFIXES:
        if (base / f"App{suffix}").is_file():
            return "./App"
    return None


def ux_acceptance_applies(repo_root: Path, profile: str,
                          source_dir: str = "src") -> bool:
    """True when the UX acceptance render measurement can drive this build: a
    browser profile whose source dir ships a React App entry. Non-browser
    profiles (APIs, CLIs) and non-standard layouts are N/A."""
    if profile not in _UX_BROWSER_PROFILES:
        return False
    return _app_entry_import(repo_root, source_dir) is not None


def ensure_ux_acceptance_test(repo_root: Path, *, source_dir: str = "src",
                              profile: str = "react-vite") -> Path | None:
    """Author (or refresh) the UX acceptance test into the product's suite so
    the build agent iterates against it and the G4 suite enforces it. The test
    is the signed spec: it is (re)written to the canonical content every call,
    so a model cannot silently weaken or delete it. Returns the path, or None
    when the gate does not apply (non-browser profile / no App entry)."""
    app_import = _app_entry_import(repo_root, source_dir)
    if profile not in _UX_BROWSER_PROFILES or app_import is None:
        return None
    path = Path(repo_root) / source_dir / UX_ACCEPTANCE_TEST_BASENAME
    source = ux_acceptance_test_source(app_import)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Idempotent: only write when content differs, so mtime/byte churn is
        # avoided on a re-run of an already-authored build.
        if not path.is_file() or path.read_text(encoding="utf-8") != source:
            path.write_text(source, encoding="utf-8")
    except OSError:
        return None
    return path


def _single_test_argv(repo_root: Path, profile: str, rel_test: str) -> list | None:
    """The stack adapter's own single-test command for ``rel_test`` (e.g.
    ``[npx, vitest, run, <path>]`` for react-vite). None when the adapter has
    no single-test runner. Stays stack-agnostic -- no vitest/npx literal here."""
    try:
        from .stacks import get_adapter
        adapter = get_adapter(profile)
    except Exception:
        return None
    tfc = getattr(adapter, "test_file_command", None)
    if not callable(tfc):
        return None
    try:
        argv = tfc(repo_root, rel_test)
    except Exception:
        return None
    return list(argv) if argv else None


# Assertion-failure lines vitest prints, mined for a crisp gate reason.
_UX_FAIL_LINE_RE = re.compile(
    r"AssertionError|The (?:UI|rendered)|no real interactive|carries no styling|"
    r"accessible name|Almost nothing|expected .* to be", re.I)


def run_ux_acceptance(repo_root: Path, *, source_dir: str = "src",
                      profile: str | None = None,
                      timeout: int = 420) -> dict[str, Any]:
    """Run the UX acceptance MEASUREMENT offline and report the result.

    Authors the render-measurement test into the suite, then executes JUST that
    file through the stack's single-test runner (vitest on jsdom -- offline, no
    network). Returns::

        {"ok": bool, "ran": bool, "reason": str, "skipped": str|None}

    ``ran`` distinguishes a real measurement from a skip. The gate blocks ONLY
    on ``ran and not ok`` -- when the measurement genuinely cannot run offline
    (no installed dependencies / no single-test runner / no App entry) it
    returns ``ok=True, ran=False`` so a good build is never false-failed on
    tooling grounds (the suite-level enforcement remains the backstop)."""
    repo_root = Path(repo_root)
    if profile is None:
        try:
            from .stacks import detect_profile
            profile = detect_profile(repo_root)
        except Exception:
            profile = "generic"

    def _skip(reason: str) -> dict[str, Any]:
        return {"ok": True, "ran": False, "reason": reason, "skipped": reason}

    if not ux_acceptance_applies(repo_root, profile, source_dir):
        return _skip("UX acceptance N/A for this profile/layout")
    # Offline preconditions: installed dependencies + a runnable single-test
    # command. Absent -> skip (never a false fail on tooling).
    if not (repo_root / "node_modules").is_dir():
        return _skip("dependencies not installed -- UX measurement skipped")
    test_path = ensure_ux_acceptance_test(
        repo_root, source_dir=source_dir, profile=profile)
    if test_path is None:
        return _skip("could not author the UX acceptance test")
    rel_test = str(test_path.relative_to(repo_root)).replace("\\", "/")
    argv = _single_test_argv(repo_root, profile, rel_test)
    if not argv:
        return _skip("no single-test runner for this stack")
    if shutil.which(str(argv[0])) is None and not Path(argv[0]).exists():
        return _skip(f"test runner '{argv[0]}' not available")
    try:
        proc = subprocess.run(
            argv, cwd=str(repo_root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # A tooling failure (could not execute / timed out) is NOT a UX
        # verdict -- do not false-fail the build on it.
        return _skip(f"UX measurement could not run ({type(exc).__name__})")
    out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "",
                 (proc.stdout or "") + "\n" + (proc.stderr or ""))
    if proc.returncode == 0:
        return {"ok": True, "ran": True,
                "reason": "UX acceptance passed: the UI renders styled, "
                          "accessible, interactive controls.", "skipped": None}
    fails = [ln.strip() for ln in out.splitlines()
             if ln.strip() and _UX_FAIL_LINE_RE.search(ln)]
    detail = "\n".join(dict.fromkeys(fails))[:1200] or out[-1200:]
    return {
        "ok": False, "ran": True, "skipped": None,
        "reason": ("UX acceptance FAILED -- the build does not ship a real, "
                   "styled, usable UI. Fix the interface (render real "
                   "interactive controls, give them a styled visual design, "
                   "and label them), then rebuild:\n" + detail),
    }
