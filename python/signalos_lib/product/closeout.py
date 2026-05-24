"""Product handoff closeout for the SignalOS delivery bridge (Phase P12).

Collects evidence from all bridge phases, builds a closeout payload,
writes JSON and human-readable markdown summaries, and generates
handoff files.  The closeout summarises actual evidence -- it never
claims readiness that evidence does not support.
"""

from __future__ import annotations

__all__ = [
    "build_closeout",
    "check_closeout_honesty",
    "generate_closeout_markdown",
    "load_closeout",
    "write_closeout",
    "write_handoff_files",
]

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .acceptance import load_acceptance_matrix, check_closure_readiness
from .deploy import load_deploy_decision
from .generation import load_generation_manifest
from .lifecycle import capture_git_state, load_delivery_state
from .proof import check_proof_completeness
from .security_gate import load_security_result
from .validation import load_validation_result, check_product_closure

SCHEMA_VERSION = "signalos.product_closeout.v1"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# ------------------------------------------------------------------
# Build closeout
# ------------------------------------------------------------------

def build_closeout(
    repo_root: Path,
    product_name: str,
    profile: str,
    blueprint_id: str | None,
) -> dict[str, Any]:
    """Build the product closeout by collecting evidence from all bridge phases.

    Reads evidence artifacts from ``.signalos/`` and git state.  Missing
    artifacts produce degraded (partial) closeouts rather than crashes.
    """
    signalos_dir = repo_root / ".signalos"

    # --- Delivery state ---
    delivery_state = load_delivery_state(repo_root)
    source_prompt_sha256: str | None = None
    if delivery_state is not None:
        source_prompt_sha256 = delivery_state.get("prompt_sha256")

    # --- Git state ---
    git_state = capture_git_state(repo_root)
    repo_git_head = git_state.get("head_sha")

    # --- Generation manifest ---
    manifest = load_generation_manifest(signalos_dir)
    generated_files: list[str] = []
    if manifest is not None:
        generated_files = [
            f.get("path", "") for f in manifest.get("files", [])
        ]

    # --- Validation result ---
    validation_result = load_validation_result(signalos_dir)
    closure = check_product_closure(validation_result)
    closure_level = closure.get("level", "not_started")

    tests_executed: list[dict[str, Any]] = []
    build_status = "not_run"
    security_status = "not_run"
    if validation_result is not None:
        results = validation_result.get("results", {})
        # Collect test-category entries
        for cat_name, cat_result in results.items():
            tests_executed.append({
                "category": cat_name,
                "status": cat_result.get("status", "skipped"),
                "duration_s": cat_result.get("duration_s", 0.0),
            })
        build_status = results.get("build", {}).get("status", "not_run")
        security_status = results.get("security", {}).get("status", "not_run")

    # --- Security gate result (richer than validation-only security) ---
    security_gate = load_security_result(signalos_dir)
    if security_gate is not None:
        security_status = security_gate.get("status", security_status)

    # --- Proof completeness ---
    proof = check_proof_completeness(repo_root, profile)
    runtime_status = proof.get("runtime_status") or "not_run"
    ux_status = proof.get("ux_status") or "not_run"

    # --- Deploy decision ---
    deploy_decision = load_deploy_decision(signalos_dir)
    deploy_status = "not_run"
    if deploy_decision is not None:
        if deploy_decision.get("deploy_allowed"):
            deploy_status = "authorized"
        else:
            deploy_status = deploy_decision.get("mode", "none")

    # --- Acceptance matrix ---
    acceptance_matrix = load_acceptance_matrix(signalos_dir)
    acceptance_summary: dict[str, int] = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
    }
    if acceptance_matrix is not None:
        readiness = check_closure_readiness(acceptance_matrix)
        acceptance_summary = {
            "total": (
                readiness.get("passed", 0)
                + readiness.get("failed", 0)
                + readiness.get("pending", 0)
                + readiness.get("skipped", 0)
            ),
            "passed": readiness.get("passed", 0),
            "failed": readiness.get("failed", 0),
            "pending": readiness.get("pending", 0),
            "skipped": readiness.get("skipped", 0),
        }

    # --- Known limitations ---
    known_limitations = _collect_limitations(
        closure, proof, acceptance_matrix, deploy_decision, profile,
    )

    # --- How to run ---
    how_to_run = _build_how_to_run(profile, str(repo_root))

    # --- What next ---
    what_next = _build_what_next(closure_level)

    return {
        "schema_version": SCHEMA_VERSION,
        "product_name": product_name,
        "repo_path": str(repo_root),
        "repo_git_head": repo_git_head,
        "source_prompt_sha256": source_prompt_sha256,
        "blueprint": blueprint_id,
        "profile": profile,
        "generated_files": generated_files,
        "tests_executed": tests_executed,
        "build_status": build_status,
        "runtime_status": runtime_status,
        "ux_status": ux_status,
        "security_status": security_status,
        "deploy_status": deploy_status,
        "acceptance_summary": acceptance_summary,
        "known_limitations": known_limitations,
        "how_to_run": how_to_run,
        "what_next": what_next,
        "closed_at": _utc_now(),
        "closure_level": closure_level,
    }


# ------------------------------------------------------------------
# Limitations, how-to-run, what-next helpers
# ------------------------------------------------------------------

def _collect_limitations(
    closure: dict[str, Any],
    proof: dict[str, Any],
    acceptance_matrix: dict[str, Any] | None,
    deploy_decision: dict[str, Any] | None,
    profile: str,
) -> list[str]:
    limitations: list[str] = []

    # From validation blockers
    for b in closure.get("blockers", []):
        limitations.append(b)

    # From proof blockers
    for b in proof.get("blockers", []):
        limitations.append(b)

    # From acceptance pending/failed criteria
    if acceptance_matrix is not None:
        readiness = check_closure_readiness(acceptance_matrix)
        for b in readiness.get("blockers", []):
            limitations.append(b)

    # From deploy decision (if deploy not allowed)
    if deploy_decision is not None and not deploy_decision.get("deploy_allowed"):
        for b in deploy_decision.get("blockers", []):
            limitations.append(b)

    # Generic profile limitations
    if profile == "generic":
        limitations.append(
            "Generic profile: no UI preview or runtime verification available"
        )

    return limitations


def _build_how_to_run(profile: str, repo_path: str) -> list[str]:
    if profile == "react-vite":
        return [
            f"cd {repo_path}",
            "npm install",
            "npm run dev",
            "Open http://localhost:5173",
        ]
    return [
        f"cd {repo_path}",
        "Review generated files in src/",
    ]


def _build_what_next(closure_level: str) -> list[str]:
    if closure_level == "ready":
        return [
            "Review the product",
            "Run full test suite",
            "Consider deployment",
        ]
    if closure_level == "partial":
        return [
            "Address known limitations",
            "Complete pending acceptance criteria",
            "Run validation again",
        ]
    # blocked, not_started, verified, etc.
    return [
        "Install required toolchain",
        "Fix build errors",
        "Re-run validation",
    ]


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------

def write_closeout(closeout: dict[str, Any], signalos_dir: Path) -> tuple[Path, Path]:
    """Write both CLOSEOUT.json and CLOSEOUT.md.

    Returns ``(json_path, md_path)``.
    """
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)

    json_path = product_dir / "CLOSEOUT.json"
    json_path.write_text(
        json.dumps(closeout, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    md_content = generate_closeout_markdown(closeout)
    md_path = product_dir / "CLOSEOUT.md"
    md_path.write_text(md_content, encoding="utf-8")

    return json_path, md_path


def load_closeout(signalos_dir: Path) -> dict[str, Any] | None:
    """Load closeout JSON, returning ``None`` if absent or corrupt."""
    path = signalos_dir / "product" / "CLOSEOUT.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------
# Markdown generation
# ------------------------------------------------------------------

def generate_closeout_markdown(closeout: dict[str, Any]) -> str:
    """Generate human-readable markdown from closeout data.

    Includes product name, repo path, what was built, how to run,
    tests/checks that passed, deploy status, known limitations,
    and next actions.  Never claims ready when closure is partial.
    """
    lines: list[str] = []
    product_name = closeout.get("product_name", "Product")
    closure_level = closeout.get("closure_level", "not_started")

    lines.append(f"# {product_name} -- Product Closeout")
    lines.append("")

    # Status line -- honest
    if closure_level == "ready":
        lines.append("**Status:** All checks passed. Product is ready for review.")
    elif closure_level == "partial":
        lines.append(
            "**Status:** Some checks did not pass. Review limitations below."
        )
    elif closure_level == "blocked":
        lines.append(
            "**Status:** Validation is blocked. Infrastructure issues must be resolved."
        )
    else:
        lines.append("**Status:** Validation has not been completed.")
    lines.append("")

    # Repo info
    lines.append("## Repository")
    lines.append("")
    lines.append(f"- **Path:** {closeout.get('repo_path', 'N/A')}")
    if closeout.get("repo_git_head"):
        lines.append(f"- **Git HEAD:** `{closeout['repo_git_head']}`")
    if closeout.get("blueprint"):
        lines.append(f"- **Blueprint:** {closeout['blueprint']}")
    lines.append(f"- **Profile:** {closeout.get('profile', 'N/A')}")
    lines.append("")

    # What was built
    generated = closeout.get("generated_files", [])
    lines.append("## What Was Built")
    lines.append("")
    if generated:
        lines.append(f"{len(generated)} files generated:")
        lines.append("")
        for f in generated:
            lines.append(f"- `{f}`")
    else:
        lines.append("No files were generated.")
    lines.append("")

    # How to run
    how_to_run = closeout.get("how_to_run", [])
    lines.append("## How to Run")
    lines.append("")
    if how_to_run:
        lines.append("```")
        for step in how_to_run:
            lines.append(step)
        lines.append("```")
    else:
        lines.append("No run instructions available.")
    lines.append("")

    # Checks / tests
    tests = closeout.get("tests_executed", [])
    lines.append("## Checks and Tests")
    lines.append("")
    if tests:
        lines.append("| Category | Status | Duration |")
        lines.append("|----------|--------|----------|")
        for t in tests:
            cat = t.get("category", "")
            status = t.get("status", "")
            dur = t.get("duration_s", 0.0)
            lines.append(f"| {cat} | {status} | {dur:.1f}s |")
    else:
        lines.append("No checks were executed.")
    lines.append("")

    # Build / runtime / deploy
    lines.append("## Key Results")
    lines.append("")
    lines.append(f"- **Build:** {closeout.get('build_status', 'not_run')}")
    lines.append(f"- **Runtime:** {closeout.get('runtime_status', 'not_run')}")
    lines.append(f"- **UX:** {closeout.get('ux_status', 'not_run')}")
    lines.append(f"- **Security:** {closeout.get('security_status', 'not_run')}")
    lines.append(f"- **Deploy:** {closeout.get('deploy_status', 'not_run')}")
    lines.append("")

    # Acceptance summary
    acc = closeout.get("acceptance_summary", {})
    if acc.get("total", 0) > 0:
        lines.append("## Acceptance Criteria")
        lines.append("")
        lines.append(
            f"- Total: {acc['total']}, Passed: {acc['passed']}, "
            f"Failed: {acc['failed']}, Pending: {acc['pending']}, "
            f"Skipped: {acc['skipped']}"
        )
        lines.append("")

    # Known limitations
    limitations = closeout.get("known_limitations", [])
    lines.append("## Known Limitations")
    lines.append("")
    if limitations:
        for lim in limitations:
            lines.append(f"- {lim}")
    else:
        lines.append("None identified.")
    lines.append("")

    # Next actions
    what_next = closeout.get("what_next", [])
    lines.append("## Next Actions")
    lines.append("")
    if what_next:
        for action in what_next:
            lines.append(f"- {action}")
    else:
        lines.append("No further actions suggested.")
    lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Handoff files
# ------------------------------------------------------------------

def write_handoff_files(
    closeout: dict[str, Any],
    signalos_dir: Path,
) -> list[Path]:
    """Write handoff files from closeout evidence.

    Creates:
    - ``.signalos/handoffs/product-summary.md``
    - ``.signalos/handoffs/test-evidence.md``
    - ``.signalos/handoffs/operator-runbook.md``

    Returns list of created file paths.
    """
    handoff_dir = signalos_dir / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []

    # 1. Product summary
    summary_path = handoff_dir / "product-summary.md"
    summary_path.write_text(
        _generate_product_summary(closeout), encoding="utf-8",
    )
    paths.append(summary_path)

    # 2. Test evidence
    evidence_path = handoff_dir / "test-evidence.md"
    evidence_path.write_text(
        _generate_test_evidence(closeout), encoding="utf-8",
    )
    paths.append(evidence_path)

    # 3. Operator runbook
    runbook_path = handoff_dir / "operator-runbook.md"
    runbook_path.write_text(
        _generate_operator_runbook(closeout), encoding="utf-8",
    )
    paths.append(runbook_path)

    return paths


def _generate_product_summary(closeout: dict[str, Any]) -> str:
    product_name = closeout.get("product_name", "Product")
    lines = [
        f"# {product_name} -- Product Summary",
        "",
        f"**Profile:** {closeout.get('profile', 'N/A')}",
        f"**Repository:** {closeout.get('repo_path', 'N/A')}",
        f"**Closure level:** {closeout.get('closure_level', 'unknown')}",
        "",
    ]

    # Blueprint
    if closeout.get("blueprint"):
        lines.append(f"**Blueprint:** {closeout['blueprint']}")
        lines.append("")

    # Generated files
    generated = closeout.get("generated_files", [])
    lines.append("## Generated Files")
    lines.append("")
    if generated:
        for f in generated:
            lines.append(f"- `{f}`")
    else:
        lines.append("No files were generated.")
    lines.append("")

    # Acceptance
    acc = closeout.get("acceptance_summary", {})
    if acc.get("total", 0) > 0:
        lines.append("## Acceptance")
        lines.append("")
        lines.append(
            f"Passed {acc['passed']} of {acc['total']} criteria "
            f"({acc['failed']} failed, {acc['pending']} pending, "
            f"{acc['skipped']} skipped)."
        )
        lines.append("")

    # Limitations
    limitations = closeout.get("known_limitations", [])
    if limitations:
        lines.append("## Limitations")
        lines.append("")
        for lim in limitations:
            lines.append(f"- {lim}")
        lines.append("")

    return "\n".join(lines)


def _generate_test_evidence(closeout: dict[str, Any]) -> str:
    product_name = closeout.get("product_name", "Product")
    lines = [
        f"# {product_name} -- Test Evidence",
        "",
    ]

    tests = closeout.get("tests_executed", [])
    if tests:
        lines.append("## Check Results")
        lines.append("")
        lines.append("| Category | Status | Duration |")
        lines.append("|----------|--------|----------|")
        for t in tests:
            cat = t.get("category", "")
            status = t.get("status", "")
            dur = t.get("duration_s", 0.0)
            lines.append(f"| {cat} | {status} | {dur:.1f}s |")
        lines.append("")
    else:
        lines.append("No checks were executed.")
        lines.append("")

    lines.append("## Key Results")
    lines.append("")
    lines.append(f"- Build: {closeout.get('build_status', 'not_run')}")
    lines.append(f"- Runtime: {closeout.get('runtime_status', 'not_run')}")
    lines.append(f"- UX: {closeout.get('ux_status', 'not_run')}")
    lines.append(f"- Security: {closeout.get('security_status', 'not_run')}")
    lines.append("")

    return "\n".join(lines)


def _generate_operator_runbook(closeout: dict[str, Any]) -> str:
    product_name = closeout.get("product_name", "Product")
    lines = [
        f"# {product_name} -- Operator Runbook",
        "",
    ]

    # How to run
    how_to_run = closeout.get("how_to_run", [])
    lines.append("## Running the Product")
    lines.append("")
    if how_to_run:
        lines.append("```")
        for step in how_to_run:
            lines.append(step)
        lines.append("```")
    else:
        lines.append("No run instructions available.")
    lines.append("")

    # Deploy status
    lines.append("## Deploy Status")
    lines.append("")
    lines.append(f"Current deploy status: **{closeout.get('deploy_status', 'not_run')}**")
    lines.append("")

    # Next actions
    what_next = closeout.get("what_next", [])
    lines.append("## Next Actions")
    lines.append("")
    if what_next:
        for action in what_next:
            lines.append(f"- {action}")
    else:
        lines.append("No further actions suggested.")
    lines.append("")

    # Known limitations
    limitations = closeout.get("known_limitations", [])
    if limitations:
        lines.append("## Known Limitations")
        lines.append("")
        for lim in limitations:
            lines.append(f"- {lim}")
        lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Honesty check
# ------------------------------------------------------------------

def check_closeout_honesty(closeout: dict[str, Any]) -> dict[str, Any]:
    """Verify the closeout does not overstate readiness.

    Returns ``{"honest": bool, "issues": [...]}``.
    """
    issues: list[str] = []
    level = closeout.get("closure_level", "")
    build = closeout.get("build_status", "not_run")
    acc = closeout.get("acceptance_summary", {})
    tests = closeout.get("tests_executed", [])
    limitations = closeout.get("known_limitations", [])

    if level == "ready":
        if build == "failed":
            issues.append("Claims ready but build_status is failed")

        if acc.get("failed", 0) > 0:
            issues.append("Claims ready but has failed acceptance criteria")

        # All tests skipped
        if tests and all(t.get("status") == "skipped" for t in tests):
            issues.append("Claims ready but all tests were skipped")

    if level == "partial":
        if not limitations:
            issues.append(
                "Closure is partial but known_limitations is empty"
            )

    # deploy_status must match deploy decision shape
    deploy_status = closeout.get("deploy_status", "not_run")
    if level == "ready" and deploy_status == "not_run":
        # Not necessarily dishonest, but worth flagging
        pass

    return {
        "honest": len(issues) == 0,
        "issues": issues,
    }
