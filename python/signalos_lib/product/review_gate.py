"""#21: the Review agent gate — Build → Test → REVIEW.

The product architecture specifies **Build → Test → Review** before closeout.
Build runs (agent dispatch) and Test runs (validation: tsc + vite + vitest,
fail-closed via #23), but the REVIEW step — a spec-drift / correctness /
test-evidence verdict that GATES closeout — was never dispatched. `test.md`/
`review.md` were handed to the Build agent as reference skills, never run to
check the output. So generated code could ship unbuilt-against-spec and
untested. This module is that gate.

Design principles (matching "fully wired & enforced, never advisory"):
  * DETERMINISTIC — the verdict needs no LLM, so the gate is always enforceable
    (an optional LLM correctness pass can layer on top, but never gates alone).
  * GOVERNED by the `gate-compliance` enforcement rule (a core invariant — it can
    be `warn` but never `off`). `strict` => a failing verdict BLOCKS closure;
    `warn` => the verdict is recorded as a limitation but does not fail closed.
    Relaxation is only possible through the audited enforcement config, never
    silently.
  * ADDITIVE & HONEST — blocks only on CONCRETE evidence of a gap (an entity with
    no component at all, a component with no/empty test, or a build reported
    `failed`), never on absent evidence (a dry run that gathered none).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "run_review_gate",
    "write_review_result",
    "load_review_result",
]

# A test file counts as real evidence when it renders the unit under test AND
# asserts something. Render-only smoke is accepted (the deterministic local
# path legitimately ships it); an empty file or one with no assertion is not.
_RENDER_RE = re.compile(r"\brender\s*\(")
_ASSERT_RE = re.compile(r"\bexpect\s*\(")


def _resolve_gate_mode(repo_root: Path, mode: str | None) -> str:
    """The mode of the `gate-compliance` rule (strict|warn). Explicit `mode`
    wins (tests); otherwise read the Rust-persisted snapshot. Any failure
    defaults to the SAFEST mode (strict) — absence never weakens the gate."""
    if mode:
        return mode
    try:
        from .enforcement_state import FileEnforcementProvider

        state = FileEnforcementProvider().get_enforcement_state(repo_root)
        resolved = state.rule_mode("gate-compliance")
        # gate-compliance is a core invariant: it can never read "off".
        return resolved if resolved in ("strict", "warn") else "strict"
    except Exception:
        return "strict"


def _entity_names(intent: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for e in intent.get("entities", []) or []:
        name = e.get("name") if isinstance(e, dict) else e
        if name:
            names.append(str(name))
    return names


def _component_files(repo_root: Path) -> list[Path]:
    comp_dir = repo_root / "src" / "components"
    if not comp_dir.is_dir():
        return []
    return sorted(
        p for p in comp_dir.glob("*.tsx") if not p.name.endswith(".test.tsx")
    )


def _check_spec_coverage(
    intent: dict[str, Any], components: list[Path],
) -> tuple[bool, list[str]]:
    """Every intent entity should be represented by a generated component.
    Blocks only on the gross failure (entities exist but ZERO components);
    per-entity gaps are recorded as findings (warn) to avoid false blocks on
    entities that are legitimately embedded in another component."""
    entities = _entity_names(intent)
    if not entities:
        return True, []
    if not components:
        return False, [
            f"spec-drift: {len(entities)} entit(y/ies) in the spec "
            f"({', '.join(entities)}) but NO components were generated"
        ]
    comp_blob = " ".join(p.stem.lower() for p in components)
    missing = [e for e in entities if e.lower() not in comp_blob]
    findings = [
        f"spec-coverage: entity '{e}' has no dedicated component (may be "
        f"embedded elsewhere)"
        for e in missing
    ]
    # Non-blocking per-entity gaps; the gross-failure case above is the blocker.
    return True, findings


def _check_test_evidence(components: list[Path]) -> tuple[bool, list[str]]:
    """Every generated component must have a sibling test that renders it and
    asserts something. A missing/empty/assert-less test is a real gap (block)."""
    findings: list[str] = []
    ok = True
    for comp in components:
        test = comp.with_name(comp.stem + ".test.tsx")
        rel = f"src/components/{comp.name}"
        if not test.is_file():
            findings.append(f"test-evidence: {rel} has no test file")
            ok = False
            continue
        try:
            text = test.read_text(encoding="utf-8")
        except OSError:
            findings.append(f"test-evidence: {rel} test is unreadable")
            ok = False
            continue
        if not _ASSERT_RE.search(text) or not _RENDER_RE.search(text):
            findings.append(
                f"test-evidence: {rel} test has no real assertion "
                f"(needs render() + expect())"
            )
            ok = False
    return ok, findings


def _check_build_correctness(
    validation_result: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    """A build the Test phase reported `failed` is a Review block; `not_run`
    (dry run / no evidence gathered) is not held against the verdict."""
    if not validation_result:
        return True, []
    results = validation_result.get("results", {}) or {}
    findings: list[str] = []
    ok = True
    for key in ("build", "test"):
        status = (results.get(key, {}) or {}).get("status", "not_run")
        if status == "failed":
            findings.append(f"correctness: {key} reported failed")
            ok = False
    return ok, findings


def run_review_gate(
    repo_root: Path,
    intent: dict[str, Any],
    manifest: dict[str, Any] | None,
    validation_result: dict[str, Any] | None,
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    """Run the deterministic Review gate. Returns a verdict dict:

        {
          "schema_version": "signalos.review_gate.v1",
          "status": "pass" | "blocked" | "warn",
          "mode": "strict" | "warn",
          "blocking": bool,          # True only when status==blocked & strict
          "checks": {spec_coverage, test_evidence, build_correctness: bool},
          "findings": [...],
        }

    `blocking` is the single field the closeout consults: when True, closure is
    fail-closed (a Review verdict that found real gaps, under strict mode).
    """
    resolved_mode = _resolve_gate_mode(repo_root, mode)
    components = _component_files(repo_root)

    spec_ok, spec_findings = _check_spec_coverage(intent, components)
    test_ok, test_findings = _check_test_evidence(components)
    build_ok, build_findings = _check_build_correctness(validation_result)

    checks = {
        "spec_coverage": spec_ok,
        "test_evidence": test_ok,
        "build_correctness": build_ok,
    }
    findings = spec_findings + test_findings + build_findings
    hard_fail = not (spec_ok and test_ok and build_ok)

    if hard_fail:
        status = "blocked" if resolved_mode == "strict" else "warn"
    else:
        status = "pass"
    blocking = hard_fail and resolved_mode == "strict"

    return {
        "schema_version": "signalos.review_gate.v1",
        "status": status,
        "mode": resolved_mode,
        "blocking": blocking,
        "components_reviewed": [f"src/components/{p.name}" for p in components],
        "checks": checks,
        "findings": findings,
    }


def write_review_result(result: dict[str, Any], signalos_dir: Path) -> Path:
    """Write to .signalos/product/REVIEW_RESULT.json."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "REVIEW_RESULT.json"
    path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def load_review_result(signalos_dir: Path) -> dict[str, Any] | None:
    path = signalos_dir / "product" / "REVIEW_RESULT.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
