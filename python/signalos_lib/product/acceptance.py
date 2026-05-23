# signalos_lib/product/acceptance.py
# Phase P6 — TDD and Acceptance Plan
#
# Builds an acceptance matrix mapping intent → criteria → test scenarios.
# The matrix is the gate artifact that must be green before delivery closure.

from __future__ import annotations

__all__ = [
    "build_acceptance_matrix",
    "check_closure_readiness",
    "load_acceptance_matrix",
    "update_criterion_status",
    "write_acceptance_matrix",
]

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Profile → test-file target mapping
# ---------------------------------------------------------------------------

_PROFILE_TEST_TARGETS: dict[str, str] = {
    "react-vite": "src/{entity}.test.tsx",
    "generic": "tests/test_{entity}.py",
    "existing-repo": "tests/test_{entity}.py",
}


def _test_target(profile: str, entity: str) -> str:
    """Return a test file path for the given profile and entity slug."""
    template = _PROFILE_TEST_TARGETS.get(profile, "tests/test_{entity}.py")
    slug = entity.lower().replace(" ", "_")
    return template.format(entity=slug)


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

    # --- 3. UX surface criteria (from intent) ---
    for surface in intent.get("ux_surfaces", []):
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

    # --- 4. Blueprint acceptance criteria ---
    if blueprint is not None:
        bp_acceptance = blueprint.get("acceptance_detail", {})
        for crit in bp_acceptance.get("criteria", []):
            ac_counter += 1
            ac = _ac_id(ac_counter)
            test_id = crit.get("test_id", "")
            criteria.append({
                "id": ac,
                "source": "blueprint",
                "description": crit.get("outcome", ""),
                "entity": None,
                "workflow": None,
                "test_ids": [],
                "status": "pending",
                "evidence": None,
            })
            from_blueprint += 1

            # --- 5. Blueprint test scenarios ---
            bp_tests = blueprint.get("tests_detail", {})
            for scenario in bp_tests.get("scenarios", []):
                if scenario.get("id") == test_id:
                    ts_counter += 1
                    ts = _ts_id(ts_counter)
                    criteria[-1]["test_ids"].append(ts)
                    test_scenarios.append({
                        "id": ts,
                        "acceptance_id": ac,
                        "description": scenario.get("description", ""),
                        "kind": "integration",
                        "profile_target": _test_target(
                            profile, test_id.replace("-", "_"),
                        ),
                        "status": "pending",
                    })
                    break

    product_name = intent.get("product_name", "")

    # Determine blueprint_id
    bp_id: str | None = None
    if blueprint is not None:
        bp_id = blueprint.get("id")

    return {
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
            blockers.append(f"{crit['id']}: {crit.get('description', '')} — failed")
        elif st == "pending":
            pending += 1
            blockers.append(f"{crit['id']}: {crit.get('description', '')} — pending")
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
