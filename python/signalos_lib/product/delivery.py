"""Product delivery state machine for the SignalOS delivery bridge.

Orchestrates all bridge phases (intent -> scaffold -> acceptance ->
generation -> validation -> proof -> deploy -> closeout) into a single
pipeline.  Each phase updates DELIVERY_STATE.json.  Errors in one phase
are captured and do not crash the pipeline -- subsequent phases still run
where possible, and the final closure_level reflects failures honestly.
"""

from __future__ import annotations

__all__ = ["run_delivery"]

import json
import sys
from pathlib import Path
from typing import Any

from .acceptance import build_acceptance_matrix, write_acceptance_matrix
from .blueprints.registry import load_blueprint, match_blueprint
from .closeout import build_closeout, write_closeout, write_handoff_files
from .deploy import (
    make_deploy_decision,
    prepare_deploy_evidence,
    write_deploy_decision,
)
from .generation import generate_product, write_generation_manifest
from .intent import extract_product_intent, write_intent
from .lifecycle import (
    create_delivery_state,
    load_delivery_state,
    update_delivery_phase,
)
from .proof import run_runtime_proof, run_ux_proof, write_proof_artifacts
from .scaffold import run_scaffold
from .stacks import detect_profile
from .validation import (
    build_validation_plan,
    check_product_closure,
    run_validation,
    write_validation_result,
)


def run_delivery(
    prompt: str,
    name: str | None = None,
    repo_root: Path | None = None,
    target_root: Path | None = None,
    mode: str = "auto",
    profile: str = "auto",
    blueprint: str = "auto",
    deploy: str = "none",
    yes: bool = False,
    dry_run: bool = False,
    max_repair_cycles: int = 3,
    agent_mode: str = "none",
    json_output: bool = False,
) -> dict:
    """Run the full product delivery pipeline.

    Phase sequence:
    1. INTENT   -- extract product intent from prompt
    2. SCAFFOLD -- init repo, run profile scaffold
    3. ACCEPTANCE -- build acceptance matrix from intent + blueprint
    4. GENERATION -- generate product files with trace linkage
    5. VALIDATION -- run profile-aware validation
    6. PROOF    -- runtime and UX proof (if profile supports it)
    7. DEPLOY   -- make deploy decision
    8. CLOSEOUT -- build and write closeout + handoff

    Each phase updates DELIVERY_STATE.json.
    If any phase fails critically, subsequent phases still run where
    possible but the final closure_level reflects the failure.

    Returns the closeout dict.
    """

    # ------------------------------------------------------------------
    # 0. Resolve repo root
    # ------------------------------------------------------------------
    if repo_root is None and target_root is not None and name is not None:
        repo_root = Path(target_root) / name
    elif repo_root is None:
        repo_root = Path.cwd()
    else:
        repo_root = Path(repo_root)

    signalos_dir = repo_root / ".signalos"
    errors: list[str] = []

    # ------------------------------------------------------------------
    # 1. INTENT phase
    # ------------------------------------------------------------------
    try:
        intent = extract_product_intent(prompt)
    except Exception as exc:
        errors.append(f"intent extraction failed: {exc}")
        intent = {"product_name": "", "entities": [], "primary_workflows": [],
                  "ux_surfaces": [], "product_type": "custom"}

    if name:
        intent["product_name"] = name
    product_name = intent.get("product_name") or name or repo_root.name

    # Auto-detect blueprint
    if blueprint == "auto":
        blueprint_id = match_blueprint(intent)
    elif blueprint == "none":
        blueprint_id = None
    else:
        blueprint_id = blueprint

    bp = load_blueprint(blueprint_id) if blueprint_id else None

    # ------------------------------------------------------------------
    # 2. SCAFFOLD phase
    # ------------------------------------------------------------------
    try:
        scaffold_result = run_scaffold(
            repo_root=repo_root,
            profile=profile,
            product_name=product_name,
            prompt=prompt,
            blueprint_id=blueprint_id,
            mode=mode,
        )
    except Exception as exc:
        errors.append(f"scaffold failed: {exc}")
        scaffold_result = {"success": False, "profile": profile, "mode": mode}

    actual_profile = scaffold_result.get("profile", profile)
    if actual_profile == "auto":
        actual_profile = detect_profile(repo_root)

    # Ensure signalos dirs exist
    signalos_dir.mkdir(parents=True, exist_ok=True)
    (signalos_dir / "product").mkdir(parents=True, exist_ok=True)

    # Write intent
    try:
        write_intent(intent, signalos_dir)
    except Exception as exc:
        errors.append(f"intent write failed: {exc}")

    # Create/update delivery state
    # run_scaffold already creates delivery state, but we update if
    # scaffold failed and no state was created.
    state = load_delivery_state(repo_root)
    if state is None:
        try:
            create_delivery_state(
                repo_root, mode, prompt, actual_profile, blueprint_id or "",
            )
        except Exception as exc:
            errors.append(f"delivery state creation failed: {exc}")

    scaffold_status = "complete" if scaffold_result.get("success") else "partial"
    try:
        update_delivery_phase(repo_root, "scaffolded", scaffold_status)
    except Exception:
        pass  # state file may not exist if everything failed

    # ------------------------------------------------------------------
    # 3. ACCEPTANCE phase
    # ------------------------------------------------------------------
    try:
        acceptance = build_acceptance_matrix(intent, bp, actual_profile)
        write_acceptance_matrix(acceptance, signalos_dir)
        update_delivery_phase(repo_root, "acceptance", "complete")
    except Exception as exc:
        errors.append(f"acceptance phase failed: {exc}")
        acceptance = None
        try:
            update_delivery_phase(repo_root, "acceptance", "partial")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 4. GENERATION phase
    # ------------------------------------------------------------------
    manifest = None
    try:
        manifest = generate_product(
            repo_root=repo_root,
            intent=intent,
            blueprint=bp,
            profile=actual_profile,
            acceptance_matrix=acceptance,
        )
        update_delivery_phase(repo_root, "generated", "complete")
    except Exception as exc:
        errors.append(f"generation phase failed: {exc}")
        try:
            update_delivery_phase(repo_root, "generated", "partial")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 5. VALIDATION phase
    # ------------------------------------------------------------------
    val_result = None
    closure = {"level": "not_started", "closeable": False, "blockers": []}
    try:
        val_plan = build_validation_plan(repo_root, actual_profile)
        val_result = run_validation(repo_root, val_plan, dry_run=dry_run)
        write_validation_result(val_result, signalos_dir)
        closure = check_product_closure(val_result)
        update_delivery_phase(
            repo_root, "validated", closure.get("level", "partial"),
        )
    except Exception as exc:
        errors.append(f"validation phase failed: {exc}")
        try:
            update_delivery_phase(repo_root, "validated", "partial")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 6. PROOF phase
    # ------------------------------------------------------------------
    try:
        runtime_proof = run_runtime_proof(repo_root, actual_profile)
    except Exception as exc:
        errors.append(f"runtime proof failed: {exc}")
        runtime_proof = {"status": "blocked", "errors": [str(exc)]}

    try:
        ux_proof = run_ux_proof(repo_root, actual_profile)
    except Exception as exc:
        errors.append(f"ux proof failed: {exc}")
        ux_proof = {"status": "blocked", "errors": [str(exc)]}

    try:
        write_proof_artifacts(runtime_proof, ux_proof, repo_root)
    except Exception as exc:
        errors.append(f"proof artifact write failed: {exc}")

    proof_status = (
        "complete"
        if runtime_proof.get("status") in ("passed", "skipped")
        else "partial"
    )
    try:
        update_delivery_phase(repo_root, "proved", proof_status)
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 7. DEPLOY phase
    # ------------------------------------------------------------------
    try:
        deploy_decision = make_deploy_decision(deploy, closure, repo_root)
        write_deploy_decision(deploy_decision, signalos_dir)
    except Exception as exc:
        errors.append(f"deploy decision failed: {exc}")
        deploy_decision = None

    if deploy == "prepare":
        try:
            prepare_deploy_evidence(
                repo_root, deploy_decision or {}, product_name, actual_profile,
            )
        except Exception as exc:
            errors.append(f"deploy evidence failed: {exc}")

    # ------------------------------------------------------------------
    # 8. CLOSEOUT phase
    # ------------------------------------------------------------------
    try:
        closeout = build_closeout(
            repo_root, product_name, actual_profile, blueprint_id,
        )
        write_closeout(closeout, signalos_dir)
        write_handoff_files(closeout, signalos_dir)
        update_delivery_phase(
            repo_root, "closed", closeout.get("closure_level", "partial"),
        )
    except Exception as exc:
        errors.append(f"closeout phase failed: {exc}")
        closeout = {
            "product_name": product_name,
            "profile": actual_profile,
            "repo_path": str(repo_root),
            "closure_level": "partial",
            "generated_files": [],
            "deploy_status": None,
            "how_to_run": [],
            "known_limitations": errors,
        }
        try:
            update_delivery_phase(repo_root, "closed", "partial")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 9. Output
    # ------------------------------------------------------------------
    if json_output:
        json.dump(closeout, sys.stdout, indent=2)
        print()

    return closeout
