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
from datetime import datetime, timezone
from .deploy import (
    make_deploy_decision,
    prepare_deploy_evidence,
    write_deploy_decision,
)
from .design import (
    build_design_system,
    get_design_dependencies,
    get_design_instructions,
    scaffold_design_system,
    write_design,
)
from .generation import prepare_generation, write_generation_manifest
from .assumptions import record_assumptions, write_assumptions
from .intent import extract_product_intent, refine_intent_with_llm, write_intent
from .lifecycle import (
    create_delivery_state,
    load_delivery_state,
    update_delivery_phase,
)
from .proof import run_runtime_proof, run_ux_proof, write_proof_artifacts
from .questions import generate_questions
from .gate_review import classify_review, handle_request_changes, handle_rejection
from .repair_loop import run_repair_loop
from .scaffold import run_scaffold
from .security_gate import run_security_gate, write_security_result
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

    # Optional LLM refinement -- cleans up entities/roles/qualifiers
    # when an API key is available. Falls back silently if not.
    import os
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("SIGNALOS_LLM_PROVIDER"):
        try:
            intent = refine_intent_with_llm(intent, prompt)
        except Exception:
            pass  # deterministic extraction is the fallback

    # ------------------------------------------------------------------
    # 1b. HITL: Check if intent needs clarification
    # ------------------------------------------------------------------
    try:
        questions = generate_questions(intent)
        blocking = [q for q in questions if q.get("blocking")]

        # Write QUESTIONS.json for desktop app to consume
        questions_path = signalos_dir / "product" / "QUESTIONS.json"
        questions_path.parent.mkdir(parents=True, exist_ok=True)
        questions_payload = {
            "questions": questions,
            "blocking": blocking,
            "answered": False,
            "assumptions_used": True,
        }
        questions_path.write_text(
            json.dumps(questions_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Always record assumptions for unfilled fields
        assumptions = record_assumptions(intent)
        if assumptions:
            write_assumptions(assumptions, signalos_dir)
    except Exception as exc:
        errors.append(f"HITL questions/assumptions failed: {exc}")

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
    # 2b. DESIGN phase - select UX library, tokens, state management
    # ------------------------------------------------------------------
    design = None
    design_deps: dict[str, str] = {}
    try:
        design = build_design_system(intent, actual_profile, bp)
        write_design(design, signalos_dir)
        design_deps = get_design_dependencies(design)
        # Scaffold shared UI layer (theme, layouts)
        scaffold_design_system(repo_root, design)
    except Exception as exc:
        errors.append(f"design phase failed: {exc}")

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
    # 4. GENERATION phase (builds packet -- does NOT write app code)
    # ------------------------------------------------------------------
    manifest = None
    generation_packet = None
    try:
        # Extract task IDs from acceptance criteria
        task_ids = (
            [f"T-{i+1:03d}" for i in range(len(acceptance.get("criteria", [])))]
            if acceptance
            else []
        )

        generation_packet = prepare_generation(
            repo_root=repo_root,
            intent=intent,
            blueprint=bp,
            profile=actual_profile,
            wave="1",
            task_ids=task_ids,
            acceptance_matrix=acceptance,
            design=design,
        )

        # Load the manifest that prepare_generation wrote
        from .generation import load_generation_manifest, collect_governance_instructions
        manifest = load_generation_manifest(repo_root / ".signalos")

        update_delivery_phase(repo_root, "generated", "complete")
    except Exception as exc:
        errors.append(f"generation phase failed: {exc}")
        try:
            update_delivery_phase(repo_root, "generated", "partial")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 4a. REVIEW GATE — write review state for UI to consume
    # ------------------------------------------------------------------
    if generation_packet and not yes:
        try:
            review_state = {
                "gate": "generation",
                "status": "awaiting_review",
                "artifact_summary": {
                    "profile": actual_profile,
                    "task_count": len(task_ids) if task_ids else 0,
                    "blueprint": blueprint_id,
                },
                "cycle": 0,
                "max_cycles": 3,
            }
            review_state_path = signalos_dir / "product" / "REVIEW_STATE.json"
            review_state_path.write_text(
                json.dumps(review_state, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            errors.append(f"review state write failed: {exc}")

    # ------------------------------------------------------------------
    # 4b. AGENT DISPATCH (invoke LLM to write product code)
    # ------------------------------------------------------------------
    agent_result = None
    if generation_packet and agent_mode != "none":
        try:
            from .agent_dispatch import dispatch_build_agent
            governance = collect_governance_instructions(
                agent_role="build",
                extra_contexts=(
                    ["security"] if intent.get("security_constraints") else None
                ),
            )
            agent_result = dispatch_build_agent(
                repo_root=repo_root,
                packet=generation_packet,
                governance=governance,
            )
            if agent_result.get("status") == "completed":
                update_delivery_phase(repo_root, "generated", "complete")
            elif agent_result.get("status") == "no_api_key":
                warnings.append("No API key — agent not dispatched. Packet written for external execution.")
            else:
                errors.extend(agent_result.get("errors", []))
        except Exception as exc:
            errors.append(f"agent dispatch failed: {exc}")

    # ------------------------------------------------------------------
    # 5. VALIDATION phase
    # ------------------------------------------------------------------
    val_result = None
    closure = {"level": "not_started", "closeable": False, "blockers": []}
    try:
        val_plan = build_validation_plan(repo_root, actual_profile)
        val_result = run_validation(repo_root, val_plan, dry_run=dry_run)
        write_validation_result(val_result, signalos_dir)

        # Repair loop: if validation fails and agent_mode is active
        if not val_result.get("can_close_delivery", False) and agent_mode != "none":
            repair_result = run_repair_loop(
                repo_root=repo_root,
                validation_result=val_result,
                profile=actual_profile,
                max_cycles=max_repair_cycles,
                agent_mode=agent_mode,
            )
            # If repaired, re-run validation
            if repair_result.get("status") == "repaired":
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
    # 5b. SECURITY phase - injection scan, threat model, GDPR detection
    # ------------------------------------------------------------------
    try:
        gen_files = (
            [f["path"] for f in manifest.get("files", [])]
            if isinstance(manifest, dict)
            else []
        )
        security_result = run_security_gate(
            repo_root=repo_root,
            intent=intent,
            generated_files=gen_files,
            profile=actual_profile,
        )
        write_security_result(security_result, signalos_dir)
    except Exception as exc:
        errors.append(f"security gate failed: {exc}")

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
    # 9. Workspace switch metadata
    # ------------------------------------------------------------------
    closeout["workspace"] = {
        "repo_root": str(repo_root),
        "product_name": product_name,
        "profile": actual_profile,
        "switch_recommended": True,
    }

    # Write WORKSPACE.json for Tauri app to discover on next launch
    workspace_info = {
        "repo_root": str(repo_root),
        "product_name": product_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": actual_profile,
    }
    try:
        workspace_path = signalos_dir / "product" / "WORKSPACE.json"
        workspace_path.write_text(
            json.dumps(workspace_info, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        errors.append(f"workspace file write failed: {exc}")

    # ------------------------------------------------------------------
    # 10. Output
    # ------------------------------------------------------------------
    if json_output:
        json.dump(closeout, sys.stdout, indent=2)
        print()

    return closeout


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _merge_design_deps(repo_root: Path, deps: dict[str, str]) -> None:
    """Merge design-system dependencies into package.json."""
    pkg_path = repo_root / "package.json"
    if not pkg_path.is_file() or not deps:
        return
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    pkg.setdefault("dependencies", {}).update(deps)
    pkg_path.write_text(
        json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
