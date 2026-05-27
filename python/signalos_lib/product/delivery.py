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
import os
import sys
import time
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
from .design_decisions import (
    build_design_decisions,
    validate_design_decisions,
    write_design_decisions,
)
from .generation import prepare_generation, write_generation_manifest
from .agent_packets import build_agent_packet, write_agent_packet
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
from .reviews import (
    build_arch_review,
    build_review_readiness,
    validate_arch_review,
    validate_review_readiness,
    write_arch_review,
    write_review_readiness,
)
from .scaffold import run_scaffold
from .security_gate import run_security_gate, write_security_result
from .stacks import detect_profile
from .strategy import (
    build_scope_decisions,
    build_strategy_review,
    validate_scope_decisions,
    validate_strategy_review,
    write_scope_decisions,
    write_strategy_review,
)
from .validation import (
    build_validation_plan,
    check_product_closure,
    run_validation,
    write_validation_result,
)


def _emit_progress(
    phase: str,
    substep: str,
    state: str,
    detail: str | None = None,
) -> None:
    req_id = os.environ.get("SIGNALOS_PROGRESS_REQ_ID", "").strip()
    if not req_id:
        return
    payload = {
        "id": req_id,
        "kind": "progress",
        "phase": phase,
        "substep": substep,
        "state": state,
        "detail": detail,
        "ts": int(time.time() * 1000),
    }
    stream = getattr(sys, "__stdout__", sys.stdout)
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


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
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # 1. INTENT phase
    # ------------------------------------------------------------------
    _emit_progress("intent", "extract", "running", "Extracting product intent")
    try:
        intent = extract_product_intent(prompt)
        _emit_progress("intent", "extract", "done", "Intent extracted")
    except Exception as exc:
        errors.append(f"intent extraction failed: {exc}")
        _emit_progress("intent", "extract", "error", str(exc))
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
    _emit_progress("intent", "questions", "running", "Checking required questions")
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
        _emit_progress("intent", "questions", "done", "Questions and assumptions recorded")
    except Exception as exc:
        errors.append(f"HITL questions/assumptions failed: {exc}")
        _emit_progress("intent", "questions", "error", str(exc))

    # ------------------------------------------------------------------
    # 1c. STRATEGY/SCOPE decision artifacts
    # ------------------------------------------------------------------
    _emit_progress("intent", "scope", "running", "Building scope decisions")
    try:
        strategy_review = _build_delivery_strategy_review(
            prompt=prompt,
            intent=intent,
            questions=questions if "questions" in locals() else [],
            assumptions=assumptions if "assumptions" in locals() else [],
        )
        strategy_errors = validate_strategy_review(strategy_review)
        if strategy_errors:
            errors.extend(f"strategy review: {err}" for err in strategy_errors)
        write_strategy_review(strategy_review, signalos_dir)

        scope_decisions = _build_delivery_scope_decisions(strategy_review)
        scope_errors = validate_scope_decisions(scope_decisions)
        if scope_errors:
            errors.extend(f"scope decisions: {err}" for err in scope_errors)
        write_scope_decisions(scope_decisions, signalos_dir)
        _emit_progress("intent", "scope", "done", "Scope decisions written")
    except Exception as exc:
        errors.append(f"strategy/scope artifacts failed: {exc}")
        _emit_progress("intent", "scope", "error", str(exc))

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
    _emit_progress("scaffold", "create", "running", "Creating product workspace and project files")
    try:
        scaffold_result = run_scaffold(
            repo_root=repo_root,
            profile=profile,
            product_name=product_name,
            prompt=prompt,
            blueprint_id=blueprint_id,
            mode=mode,
        )
        _emit_progress("scaffold", "create", "done", "Scaffold completed")
    except Exception as exc:
        errors.append(f"scaffold failed: {exc}")
        _emit_progress("scaffold", "create", "error", str(exc))
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
        _emit_progress("scaffold", "postflight", "done", f"Scaffold status: {scaffold_status}")
    except Exception:
        pass  # state file may not exist if everything failed

    # ------------------------------------------------------------------
    # 2b. DESIGN phase - select UX library, tokens, state management
    # ------------------------------------------------------------------
    design = None
    design_deps: dict[str, str] = {}
    _emit_progress("design", "select_system", "running", "Selecting product design system")
    try:
        design = build_design_system(intent, actual_profile, bp)
        write_design(design, signalos_dir)
        design_deps = get_design_dependencies(design)
        # Scaffold shared UI layer (theme, layouts)
        scaffold_design_system(repo_root, design)
        # Generate visual design preview for client approval
        try:
            from .design_preview import generate_design_preview_html
            preview_html = generate_design_preview_html(design, intent)
            (signalos_dir / "product" / "design-preview.html").write_text(
                preview_html, encoding="utf-8"
            )
        except Exception:
            pass  # Preview is optional
        _emit_progress("design", "select_system", "done", "Design system selected")
    except Exception as exc:
        errors.append(f"design phase failed: {exc}")
        _emit_progress("design", "select_system", "error", str(exc))

    try:
        design_decisions = build_design_decisions(
            intent,
            design,
            wave="1",
            taste_findings=_build_default_taste_findings(intent, actual_profile),
        )
        design_decision_result = validate_design_decisions(
            design_decisions,
            profile=actual_profile,
            intent=intent,
            design_system=design,
        )
        if not design_decision_result.get("valid", False):
            errors.extend(
                f"design decision: {item}"
                for item in design_decision_result.get("blockers", [])
            )
        warnings.extend(
            f"design decision: {item}"
            for item in design_decision_result.get("warnings", [])
        )
        write_design_decisions(design_decisions, signalos_dir, wave="1")
    except Exception as exc:
        errors.append(f"design decision artifact failed: {exc}")

    # ------------------------------------------------------------------
    # 2c. ARCHITECTURE review artifact
    # ------------------------------------------------------------------
    try:
        arch_review = _build_delivery_arch_review(intent, actual_profile, bp)
        arch_result = validate_arch_review(arch_review)
        if not arch_result.get("valid", False):
            errors.extend(f"arch review: {err}" for err in arch_result.get("errors", []))
        if arch_result.get("blocked"):
            errors.extend(
                f"arch blocker: {item}"
                for item in arch_result.get("blockers", [])
            )
        write_arch_review(arch_review, signalos_dir)
    except Exception as exc:
        errors.append(f"architecture review artifact failed: {exc}")

    # ------------------------------------------------------------------
    # 3. ACCEPTANCE phase
    # ------------------------------------------------------------------
    _emit_progress("acceptance", "matrix", "running", "Building acceptance matrix")
    try:
        acceptance = build_acceptance_matrix(intent, bp, actual_profile)
        write_acceptance_matrix(acceptance, signalos_dir)
        update_delivery_phase(repo_root, "acceptance", "complete")
        _emit_progress("acceptance", "matrix", "done", "Acceptance matrix written")
    except Exception as exc:
        errors.append(f"acceptance phase failed: {exc}")
        _emit_progress("acceptance", "matrix", "error", str(exc))
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
    agent_packet = None
    _emit_progress("generation", "manifest", "running", "Preparing generation manifest")
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

        tasks = _build_agent_tasks_from_acceptance(acceptance)
        agent_packet = build_agent_packet(
            repo_root=repo_root,
            intent=intent,
            blueprint=bp,
            acceptance_matrix=acceptance or {},
            profile=actual_profile,
            wave="1",
            tasks=tasks,
            allowed_paths=generation_packet.get("allowed_paths", []),
            forbidden_actions=None,
            generation_packet=generation_packet,
        )
        write_agent_packet(agent_packet, repo_root)

        update_delivery_phase(repo_root, "generated", "complete")
        _emit_progress("generation", "manifest", "done", "Generation packet and manifest written")
    except Exception as exc:
        errors.append(f"generation phase failed: {exc}")
        _emit_progress("generation", "manifest", "error", str(exc))
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
        _emit_progress("generation", "packet", "running", "Dispatching scoped build agent")
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
                packet=agent_packet or generation_packet,
                governance=governance,
            )
            if agent_result.get("status") == "completed":
                update_delivery_phase(repo_root, "generated", "complete")
            elif agent_result.get("status") == "no_api_key":
                warnings.append("No API key — agent not dispatched. Packet written for external execution.")
            else:
                errors.extend(agent_result.get("errors", []))
            _emit_progress("generation", "packet", "done", agent_result.get("status", "agent finished"))
        except Exception as exc:
            errors.append(f"agent dispatch failed: {exc}")
            _emit_progress("generation", "packet", "error", str(exc))

    # ------------------------------------------------------------------
    # 5. VALIDATION phase
    # ------------------------------------------------------------------
    val_result = None
    closure = {"level": "not_started", "closeable": False, "blockers": []}
    _emit_progress("validation", "run_checks", "running", "Running product validation")
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
        _emit_progress("validation", "run_checks", "done", f"Validation level: {closure.get('level', 'partial')}")
    except Exception as exc:
        errors.append(f"validation phase failed: {exc}")
        _emit_progress("validation", "run_checks", "error", str(exc))
        try:
            update_delivery_phase(repo_root, "validated", "partial")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 5b. SECURITY phase - injection scan, threat model, GDPR detection
    # ------------------------------------------------------------------
    _emit_progress("security", "scan", "running", "Running product security gate")
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
        _emit_progress("security", "scan", "done", security_result.get("status", "Security gate complete"))
    except Exception as exc:
        errors.append(f"security gate failed: {exc}")
        _emit_progress("security", "scan", "error", str(exc))

    # ------------------------------------------------------------------
    # 6. PROOF phase
    # ------------------------------------------------------------------
    _emit_progress("proof", "runtime", "running", "Running runtime proof")
    try:
        runtime_proof = run_runtime_proof(repo_root, actual_profile)
        _emit_progress("proof", "runtime", "done", runtime_proof.get("status", "Runtime proof complete"))
    except Exception as exc:
        errors.append(f"runtime proof failed: {exc}")
        _emit_progress("proof", "runtime", "error", str(exc))
        runtime_proof = {"status": "blocked", "errors": [str(exc)]}

    _emit_progress("proof", "ux", "running", "Running UX proof")
    try:
        ux_proof = run_ux_proof(repo_root, actual_profile)
        _emit_progress("proof", "ux", "done", ux_proof.get("status", "UX proof complete"))
    except Exception as exc:
        errors.append(f"ux proof failed: {exc}")
        _emit_progress("proof", "ux", "error", str(exc))
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
    _emit_progress("deploy", "decision", "running", "Recording deploy decision")
    try:
        deploy_decision = make_deploy_decision(deploy, closure, repo_root)
        write_deploy_decision(deploy_decision, signalos_dir)
        _emit_progress("deploy", "decision", "done", deploy_decision.get("status", deploy))
    except Exception as exc:
        errors.append(f"deploy decision failed: {exc}")
        _emit_progress("deploy", "decision", "error", str(exc))
        deploy_decision = None

    if deploy == "prepare":
        _emit_progress("deploy", "package", "running", "Preparing deploy evidence")
        try:
            prepare_deploy_evidence(
                repo_root, deploy_decision or {}, product_name, actual_profile,
            )
            _emit_progress("deploy", "package", "done", "Deploy package prepared")
        except Exception as exc:
            errors.append(f"deploy evidence failed: {exc}")
            _emit_progress("deploy", "package", "error", str(exc))

    # ------------------------------------------------------------------
    # 7b. REVIEW READINESS artifact
    # ------------------------------------------------------------------
    try:
        readiness = _build_delivery_review_readiness(
            strategy_errors=strategy_errors if "strategy_errors" in locals() else [],
            scope_errors=scope_errors if "scope_errors" in locals() else [],
            arch_result=arch_result if "arch_result" in locals() else None,
            design=design,
            validation_result=val_result,
            runtime_proof=runtime_proof if "runtime_proof" in locals() else None,
            ux_proof=ux_proof if "ux_proof" in locals() else None,
            deploy_decision=deploy_decision,
            errors=errors,
        )
        readiness_result = validate_review_readiness(readiness)
        if not readiness_result.get("valid", False):
            errors.extend(
                f"review readiness: {err}"
                for err in readiness_result.get("errors", [])
            )
        write_review_readiness(readiness, signalos_dir)
    except Exception as exc:
        errors.append(f"review readiness artifact failed: {exc}")

    # ------------------------------------------------------------------
    # 8. CLOSEOUT phase
    # ------------------------------------------------------------------
    _emit_progress("closeout", "handoff", "running", "Writing closeout and handoff")
    try:
        closeout = build_closeout(
            repo_root, product_name, actual_profile, blueprint_id,
        )
        if "readiness_result" in locals() and not readiness_result.get("ready", False):
            closeout.setdefault("known_limitations", []).extend(
                readiness_result.get("errors", [])
                + readiness_result.get("blockers", [])
            )
            if closeout.get("closure_level") == "ready":
                closeout["closure_level"] = "partial"
        if errors:
            closeout.setdefault("known_limitations", []).extend(errors)
        write_closeout(closeout, signalos_dir)
        write_handoff_files(closeout, signalos_dir)
        update_delivery_phase(
            repo_root, "closed", closeout.get("closure_level", "partial"),
        )
        _emit_progress("closeout", "handoff", "done", closeout.get("closure_level", "Closeout written"))
    except Exception as exc:
        errors.append(f"closeout phase failed: {exc}")
        _emit_progress("closeout", "handoff", "error", str(exc))
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

def _build_delivery_strategy_review(
    *,
    prompt: str,
    intent: dict,
    questions: list[dict],
    assumptions: list[dict],
) -> dict:
    product_name = intent.get("product_name") or "the product"
    product_type = intent.get("product_type") or "custom product"
    users = intent.get("target_users") or ["primary users"]
    workflows = intent.get("primary_workflows") or ["complete the core workflow"]

    return build_strategy_review(
        product_thesis=(
            f"{product_name} should solve a focused {product_type} job before "
            "scope expands beyond proven user value."
        ),
        target_user=", ".join(str(user) for user in users),
        job_to_be_done=", ".join(str(workflow) for workflow in workflows),
        literal_request_risk=(
            "Building the prompt literally can miss product tradeoffs, UX "
            "quality, test scope, security boundaries, and handoff evidence."
        ),
        ten_star_options=[
            {
                "id": "TSO-001",
                "title": "Raise the product from literal request to best usable workflow",
                "user_value": "Forces the agent to look for the highest-value product shape.",
                "implementation_cost": "Requires explicit scope decision before adoption.",
                "risk": "Can expand scope if accepted without trace.",
                "recommendation": "Evaluate before backlog finalization.",
                "disposition": "deferred",
            }
        ],
        scope_reduction_options=[
            {
                "id": "SRO-001",
                "title": "Keep delivery to the first provable product slice",
                "tradeoff": "Reduces wow factor but improves build/test/UX proof reliability.",
                "disposition": "deferred",
            }
        ],
        required_questions=[
            q.get("question", str(q))
            for q in questions
            if isinstance(q, dict)
        ],
        assumptions=assumptions,
    )


def _build_delivery_scope_decisions(strategy_review: dict) -> dict:
    decisions: list[dict[str, Any]] = []
    for source, items in (
        ("strategy", strategy_review.get("ten_star_options", [])),
        ("scope", strategy_review.get("scope_reduction_options", [])),
    ):
        for item in items:
            if not isinstance(item, dict):
                continue
            decisions.append({
                "id": str(item.get("id", f"SD-{len(decisions) + 1:03d}")),
                "source": source,
                "proposal": item.get("title") or item.get("proposal") or "",
                "impact": item.get("user_value") or item.get("tradeoff") or "",
                "disposition": item.get("disposition", "deferred"),
                "tickets": item.get("tickets", []),
                "acceptance_criteria": item.get("acceptance_criteria", []),
            })
    return build_scope_decisions(decisions)


def _build_delivery_arch_review(
    intent: dict,
    profile: str,
    blueprint: dict | None,
) -> dict:
    entities = intent.get("entities") or ["product entity"]
    workflows = intent.get("primary_workflows") or ["core workflow"]
    surfaces = intent.get("ux_surfaces") or []
    integrations = intent.get("integrations") or []

    return build_arch_review(
        system_boundaries=[
            f"profile: {profile}",
            f"blueprint: {(blueprint or {}).get('id', 'custom')}",
            f"entities: {', '.join(str(e) for e in entities)}",
        ],
        data_flow=[
            "User workflow input is captured by the product surface, validated, "
            "stored or represented by the selected stack, and surfaced back in "
            "runtime/UX proof."
        ],
        state_transitions=[
            f"{workflow}: requested -> validated -> persisted/rendered -> proved"
            for workflow in workflows
        ],
        failure_modes=[
            "missing toolchain",
            "invalid generated route/component registration",
            "validation or UX proof unavailable",
        ],
        trust_boundaries=[
            "user input to generated application",
            "application code to .signalos governance evidence",
            "local secrets and environment files remain forbidden",
        ],
        edge_cases=[
            "empty data set",
            "invalid input",
            "first-run product with no existing records",
        ],
        test_strategy=[
            "unit/build validation must run",
            "UI products require UX proof",
            "accepted scope decisions must trace to acceptance criteria",
        ],
        open_risks=[
            f"integration: {integration}" for integration in integrations
        ] + ([f"ux surfaces: {', '.join(str(s) for s in surfaces)}"] if surfaces else []),
        blocking_findings=[],
    )


def _build_default_taste_findings(intent: dict, profile: str) -> list[dict[str, str]]:
    if profile != "react-vite" and not intent.get("ux_surfaces"):
        return []
    return [
        {
            "finding": "The UI must prioritize the primary workflow over a generic landing-page composition.",
            "disposition": "deferred",
        },
        {
            "finding": "The selected variant requires external approval before it can authorize delivery scope.",
            "disposition": "deferred",
        },
    ]


def _build_agent_tasks_from_acceptance(acceptance: dict | None) -> list[dict]:
    criteria = (acceptance or {}).get("criteria", [])
    tasks: list[dict] = []
    for index, criterion in enumerate(criteria, start=1):
        tasks.append({
            "id": f"T-{index:03d}",
            "title": criterion.get("description", f"Implement criterion {index}"),
            "description": criterion.get("description", ""),
            "acceptance_id": criterion.get("id"),
            "skills": ["test-driven-development", "test-generation"],
        })
    return tasks


def _build_delivery_review_readiness(
    *,
    strategy_errors: list[str],
    scope_errors: list[str],
    arch_result: dict | None,
    design: dict | None,
    validation_result: dict | None,
    runtime_proof: dict | None,
    ux_proof: dict | None,
    deploy_decision: dict | None,
    errors: list[str],
) -> dict:
    blocking_items: list[str] = []
    blocking_items.extend(strategy_errors)
    blocking_items.extend(scope_errors)
    if arch_result:
        blocking_items.extend(arch_result.get("errors", []))
        blocking_items.extend(arch_result.get("blockers", []))
    blocking_items.extend(errors)

    validation_closeable = bool(
        validation_result and validation_result.get("can_close_delivery")
    )
    runtime_status = (runtime_proof or {}).get("status", "not_run")
    ux_status = (ux_proof or {}).get("status", "not_run")
    deploy_status = (
        (deploy_decision or {}).get("mode")
        if deploy_decision is not None
        else "not_run"
    )

    ready = (
        not blocking_items
        and validation_closeable
        and runtime_status in {"passed", "skipped"}
        and ux_status in {"passed", "skipped"}
    )

    return build_review_readiness(
        strategy_status="blocked" if strategy_errors else "complete",
        scope_status="blocked" if scope_errors else "complete",
        architecture_status=(
            "blocked"
            if arch_result and arch_result.get("blocked")
            else "complete" if arch_result and arch_result.get("valid") else "missing"
        ),
        design_status="complete" if design else "not_applicable",
        build_status=_validation_status(validation_result, "build"),
        test_status=_validation_status(validation_result, "test"),
        browser_qa_status=ux_status,
        security_status=_validation_status(validation_result, "security"),
        docs_status="complete",
        handoff_status="pending",
        blocking_items=blocking_items,
        ready=ready,
    )


def _validation_status(validation_result: dict | None, key: str) -> str:
    if not validation_result:
        return "not_run"
    return (
        validation_result.get("results", {})
        .get(key, {})
        .get("status", "not_run")
    )

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
