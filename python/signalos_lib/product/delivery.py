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

from .acceptance import (
    build_acceptance_matrix,
    reconcile_acceptance_evidence,
    write_acceptance_matrix,
)
from .blueprints.registry import (
    apply_blueprint_intent_defaults,
    load_blueprint,
    match_blueprint,
)
from .closeout import build_closeout, write_closeout, write_handoff_files
from .budgets import build_execution_budget_policy, resolve_repair_cycle_budget
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
from .generation import (
    compute_sha256_lf,
    link_generation_to_acceptance,
    prepare_generation,
    validate_generation_output,
    verify_trace_completeness,
    write_generation_manifest,
)
from .agent_packets import build_agent_packet, write_agent_packet
from .assumptions import record_assumptions, write_assumptions
from .evidence_freshness import (
    snapshot_workspace,
    verify_workspace_snapshot,
    workspace_snapshot_files,
    write_freshness_report,
)
from .test_quality import analyze_test_quality, write_test_quality_report
from .capabilities import apply_capability_choices, build_capability_profile
from .intent import extract_product_intent, refine_intent_with_llm, write_intent
from .lifecycle import (
    create_delivery_state,
    load_delivery_state,
    update_delivery_phase,
)
from .ownership import build_delivery_ownership_map, write_delivery_ownership_map
from .proof import (
    requires_browser_ux_proof,
    run_runtime_proof,
    run_ux_proof,
    write_proof_artifacts,
)
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


# Profiles the deterministic local renderer can build with no API key.
_LOCAL_RENDERABLE_PROFILES = {"react-vite", "generic", "fastapi-api"}

_AGENT_LOOP_ALLOWED_PATHS: dict[str, list[str]] = {
    "react-vite": [
        "src/**",
        "public/**",
        "tests/**",
        "index.html",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "vite.config.*",
        "tsconfig*.json",
        "core/execution/BUILD_EVIDENCE.md",
    ],
    "fastapi-api": [
        "src/**",
        "tests/**",
        "pyproject.toml",
        "requirements*.txt",
        "pytest.ini",
        "README.md",
        "core/execution/BUILD_EVIDENCE.md",
    ],
}

_DEFAULT_AGENT_LOOP_ALLOWED_PATHS = [
    "src/**",
    "app/**",
    "pages/**",
    "components/**",
    "lib/**",
    "server/**",
    "api/**",
    "public/**",
    "tests/**",
    "test/**",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "requirements*.txt",
    "pytest.ini",
    "README.md",
    "core/execution/BUILD_EVIDENCE.md",
]


def _agent_loop_allowed_paths(profile: str) -> list[str]:
    return list(
        _AGENT_LOOP_ALLOWED_PATHS.get(profile, _DEFAULT_AGENT_LOOP_ALLOWED_PATHS)
    )


def _legacy_generation_dispatch_packet(
    agent_packet: dict | None,
    generation_packet: dict | None,
) -> dict:
    """Wrap legacy file-spec generation in the current agent run id.

    The production scope artifact stays acceptance-first, but deterministic
    local fallback and explicit chunked mode still require ``generation``. This
    wrapper keeps RESULT.json in the same agent-run directory instead of
    creating a second run keyed by the raw generation packet.
    """

    if agent_packet:
        packet = dict(agent_packet)
    else:
        packet = {}
    if generation_packet:
        packet["generation"] = generation_packet
    if not packet and generation_packet:
        packet = dict(generation_packet)
    if agent_packet and generation_packet:
        packet["run_id"] = str(
            agent_packet.get("run_id") or generation_packet.get("run_id")
        )
    return packet


def _choose_dispatch_route(
    agent_mode: str,
    actual_profile: str,
    *,
    llm_available: bool,
) -> str:
    """Decide which build-agent path a delivery takes.

    Returns:
      - "agent-loop" for governed LLM-backed production generation.
      - "local-parallel" for deterministic, git-free, no-key fallback.
      - "chunked-llm" only for explicit legacy/dev opt-in.

    - agent_mode == "local": always local (explicit operator choice).
    - agent_mode in ("chunked", "legacy-chunked"): preserve the old per-file
      LLM path for focused regression/debug use.
    - agent_mode in ("auto", "remote"): use AgentLoop when a usable LLM exists.
      With no key, fall back to local for renderable profiles; for unsupported
      profiles return AgentLoop so it can fail closed with a no-key/governance
      handoff instead of silently generating through the legacy file splitter.
    """
    normalized = (agent_mode or "auto").strip().lower()
    if normalized == "local":
        return "local-parallel"
    if normalized in ("chunked", "legacy-chunked"):
        return "chunked-llm"
    if normalized in ("auto", "remote", "orchestrator"):
        if llm_available:
            return "agent-loop"
        if actual_profile in _LOCAL_RENDERABLE_PROFILES:
            return "local-parallel"
        return "agent-loop"
    # Any other/unknown mode: prefer local for a renderable profile.
    return "local-parallel" if actual_profile in _LOCAL_RENDERABLE_PROFILES else "agent-loop"


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
    max_repair_cycles: int | None = None,
    agent_mode: str = "auto",
    json_output: bool = False,
    technologies: list[str] | None = None,
    frontend: str = "auto",
    database: str = "auto",
    cache: str = "auto",
    language: str = "auto",
    deployment_target: str = "auto",
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

    # Resolve "auto" mode from the repo's ORIGINAL state, BEFORE this pipeline
    # writes any .signalos/ artifact. run_scaffold's own detect_mode runs only in
    # the SCAFFOLD phase -- by which point the INTENT phase has already created
    # .signalos/product/QUESTIONS.json, so detect_mode would see a .signalos/ dir
    # and misclassify a FIRST-TIME adoption of an existing codebase as a
    # 'refresh'. Capturing the mode here (and passing it explicitly to
    # run_scaffold) keeps greenfield/adopt/refresh honest, which in turn lets
    # governance provisioning pick the correct provenance tier -- an existing
    # codebase must reconstruct-from-code, never be labelled 'assumed'.
    if mode == "auto":
        try:
            from .lifecycle import detect_mode
            mode = detect_mode(repo_root)
        except Exception:
            pass

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

    # LLM refinement -- cleans up entities/roles/qualifiers
    from .llm_provider import is_llm_available
    if is_llm_available():
        try:
            intent = refine_intent_with_llm(intent, prompt)
            _emit_progress("intent", "refine", "done", "Intent refined by AI")
        except Exception as exc:
            warnings.append(f"LLM intent refinement failed: {exc}")
            _emit_progress("intent", "refine", "error", f"AI refinement failed: {exc}")

    intent = apply_capability_choices(
        intent,
        technologies=technologies,
        frontend=frontend,
        database=database,
        cache=cache,
        language=language,
        deployment_target=deployment_target,
        adapter_profile=profile,
        source="delivery-cli",
    )

    # Match the blueprint before questions so non-technical users get
    # blueprint-owned product defaults instead of technical interrogation.
    if blueprint == "auto":
        blueprint_id = match_blueprint(intent, repo_root=repo_root)
    elif blueprint == "none":
        blueprint_id = None
    else:
        blueprint_id = blueprint

    bp = load_blueprint(blueprint_id, repo_root=repo_root) if blueprint_id else None
    intent = apply_blueprint_intent_defaults(intent, bp)
    intent = apply_capability_choices(
        intent,
        technologies=technologies,
        frontend=frontend,
        database=database,
        cache=cache,
        language=language,
        deployment_target=deployment_target,
        adapter_profile=profile,
        source="delivery-cli",
    )
    if name:
        intent["product_name"] = name
    product_name = intent.get("product_name") or name or repo_root.name

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
            product_intent=intent,
        )
        _emit_progress("scaffold", "create", "done", "Scaffold completed")
    except Exception as exc:
        errors.append(f"scaffold failed: {exc}")
        _emit_progress("scaffold", "create", "error", str(exc))
        scaffold_result = {"success": False, "profile": profile, "mode": mode}

    actual_profile = scaffold_result.get("profile", profile)
    if actual_profile == "auto":
        actual_profile = detect_profile(repo_root)
    capability_profile = build_capability_profile(
        intent,
        adapter_profile=actual_profile,
    )

    # Ensure signalos dirs exist
    signalos_dir.mkdir(parents=True, exist_ok=True)
    (signalos_dir / "product").mkdir(parents=True, exist_ok=True)

    ownership_map = build_delivery_ownership_map(
        prompt=prompt,
        intent=intent,
        blueprint_id=blueprint_id,
        profile=actual_profile,
        deploy_mode=deploy,
        capability_profile=capability_profile,
    )
    try:
        write_delivery_ownership_map(ownership_map, signalos_dir)
    except Exception as exc:
        errors.append(f"delivery ownership map failed: {exc}")

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
        # root threads product-level key availability AND the competitive-
        # context block (.signalos/product/COMPETITORS.json) into the design
        # architect prompt; absent file keeps the prompt byte-identical.
        design = build_design_system(intent, actual_profile, bp, root=repo_root)
        write_design(design, signalos_dir)
        design_deps = get_design_dependencies(design)
        _merge_design_deps(repo_root, design_deps)
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
        arch_review = _build_delivery_arch_review(
            intent,
            actual_profile,
            bp,
            capability_profile=capability_profile,
        )
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

    feature_gate_blocker = ""
    _emit_progress("generation", "feature_gate", "running", "Evaluating active wave scope")
    try:
        feature_gate_run = _run_delivery_feature_gate(
            repo_root=repo_root,
            request=prompt,
        )
        if feature_gate_run.get("blocked"):
            feature_gate_blocker = str(feature_gate_run.get("reason") or "feature gate blocked generation")
            _emit_progress("generation", "feature_gate", "error", feature_gate_blocker)
        else:
            detail = (
                str(feature_gate_run.get("reason"))
                if not feature_gate_run.get("executed")
                else f"Feature Gate verdict: {feature_gate_run.get('verdict')}"
            )
            _emit_progress("generation", "feature_gate", "done", detail)
    except Exception as exc:
        feature_gate_blocker = f"feature gate failed: {exc}"
        _emit_progress("generation", "feature_gate", "error", str(exc))

    # ------------------------------------------------------------------
    # 4. GENERATION phase (builds packet -- does NOT write app code)
    # ------------------------------------------------------------------
    manifest = None
    generation_packet = None
    agent_packet = None
    execution_budget: dict[str, Any] | None = None
    _emit_progress("generation", "manifest", "running", "Preparing generation manifest")
    try:
        if feature_gate_blocker:
            raise RuntimeError(feature_gate_blocker)

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
            arch_review=arch_review if "arch_review" in locals() else None,
            design_decisions=(
                design_decisions if "design_decisions" in locals() else None
            ),
            scope_decisions=(
                scope_decisions if "scope_decisions" in locals() else None
            ),
        )

        # Load the manifest that prepare_generation wrote
        from .generation import load_generation_manifest, collect_governance_instructions
        manifest = load_generation_manifest(repo_root / ".signalos")

        tasks = _build_agent_tasks_from_acceptance(acceptance)
        execution_budget = build_execution_budget_policy(
            repair_cycle_budget=max_repair_cycles,
        )
        agent_packet = build_agent_packet(
            repo_root=repo_root,
            intent=intent,
            blueprint=bp,
            acceptance_matrix=acceptance or {},
            profile=actual_profile,
            wave="1",
            tasks=tasks,
            allowed_paths=_agent_loop_allowed_paths(actual_profile),
            forbidden_actions=None,
            generation_packet=generation_packet,
            ownership_map=ownership_map,
            include_generation=False,
            execution_budget=execution_budget,
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
                "max_cycles": (
                    execution_budget["repair_cycle_budget"]
                    if execution_budget
                    else resolve_repair_cycle_budget(max_repair_cycles)
                ),
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
    # #6: per-file acceptance traceability. Populated after a completed build
    # from what actually landed on disk; consumed at acceptance reconciliation
    # by verify_trace_completeness and folded into the Review gate verdict.
    trace_manifest: dict | None = None
    # #23 fake-green hard block: a generation that produced no real files is a
    # FAILURE, full stop. This flag/blocker force delivery to fail-closed --
    # build_status can never be reported "passed" off the trivially-building
    # scaffold stub when the agent wrote nothing.
    generation_blocked = False
    generation_blocker: str = ""
    governance_tier: dict | None = None
    # UNIFIED GOVERNANCE PROVISIONING -- runs in EVERY mode, including the
    # no-build modes ('none'/'packet-only'), BEFORE any build dispatch. Fill any
    # MISSING prior gate (G0-G3) present-and-signed under an EXPLICIT provenance
    # tier so the repo is NEVER left with a governance-layer weakness: an
    # unsigned gate that a later build -- or a route that does not re-check --
    # could slip through ungoverned. This is what makes run_delivery a COMPLETE
    # governed entrypoint: it never demands governance it did not produce, and it
    # never leaves a half-governed repo behind. Provisioning is mode-shaped --
    # existing code (adopt/refresh) -> reconstructed-from-code (the code is the
    # decision; the founder reviews & corrects); greenfield -> assumed, grounded
    # in the delivery's own generated brief -- and is ALWAYS signed as a SYSTEM
    # identity, NEVER the founder. It is idempotent: an existing founder
    # signature is preserved (provision_gates + sign_gate skip_signed).
    try:
        from .provision_gates import (
            governance_tier_summary,
            provision_gates,
        )
        from .reconstruct_gate_content import (
            code_content_fn,
            evidence_content_fn,
        )
        _pre_signed, _pre_blockers = _signed_prior_gates_for_g4(repo_root)
        if _pre_blockers:
            # RESOLVED mode (run_scaffold turns "auto" into greenfield/adopt/
            # refresh). adopt AND refresh operate on an EXISTING codebase ->
            # reconstructed-from-code; only a true greenfield -> assumed from the
            # generated brief. Never a generic placeholder.
            _resolved_mode = scaffold_result.get("mode", mode)
            if _resolved_mode in ("adopt", "refresh"):
                _tier, _content = "reconstructed", code_content_fn(repo_root)
            else:
                _tier, _content = "assumed", evidence_content_fn(repo_root)
            provisioned = provision_gates(
                repo_root, tier=_tier, content_fn=_content)
            if provisioned:
                _emit_progress(
                    "generation", "governance", "running",
                    f"Provisioned {len(provisioned)} prior gate(s) as "
                    f"'{_tier}' (not founder-reviewed) -- present-and-signed "
                    "under an explicit provenance tier")
        governance_tier = governance_tier_summary(repo_root)
    except Exception as exc:
        errors.append(f"gate provisioning failed: {exc}")

    if generation_packet and agent_mode not in ("none", "packet-only"):
        _emit_progress("generation", "packet", "running", "Dispatching scoped build agent")
        try:
            from .agent_dispatch import dispatch_local_build_agent_parallel
            from .executor import run_worker_pool
            from .secrets_resolver import is_llm_available
            from signalos_lib.task_store import InMemoryTaskStore

            governance = collect_governance_instructions(
                agent_role="build",
                extra_contexts=(
                    ["security"] if intent.get("security_constraints") else None
                ),
            )
            # A founder WITH a key gets the governed AgentLoop path. Without a
            # key we stay on the deterministic, git-free local parallel path
            # for renderable profiles. The legacy per-file splitter is only
            # reachable through an explicit chunked/legacy-chunked agent_mode.
            route = _choose_dispatch_route(
                agent_mode,
                actual_profile,
                llm_available=is_llm_available(repo_root),
            )
            packet_for_agent = (
                agent_packet
                if route == "agent-loop" and agent_packet is not None
                else _legacy_generation_dispatch_packet(agent_packet, generation_packet)
            )

            def _dispatch_once(_task: Any) -> dict:
                # FIX A -- ROUTE-INDEPENDENT GOVERNANCE GATE: every build route
                # (local-parallel, chunked-llm, agent-loop) requires the prior
                # governance gates (G0-G3) to be signed before ANY product code
                # is written. Previously only the agent-loop route checked, so
                # the no-key deterministic local renderer emitted UNGOVERNED
                # product and reported success -- a fail-open masked exactly on
                # the path taken when a provider (hence the governed loop) is
                # unavailable. Now no route can bypass governance.
                _gov_signed, _gov_blockers = _signed_prior_gates_for_g4(repo_root)
                if _gov_blockers:
                    return {
                        "status": "governance_required",
                        "run_id": packet_for_agent.get("run_id"),
                        "files_written": [],
                        "errors": _gov_blockers,
                    }
                if route == "local-parallel":
                    # 1.1: transparently parallelizes across independent
                    # react-vite components; falls back verbatim to the single
                    # synchronous call for every other profile and <2 components.
                    dispatched = dispatch_local_build_agent_parallel(
                        repo_root=repo_root,
                        packet=packet_for_agent,
                    )
                elif route == "chunked-llm":
                    from .agent_dispatch import dispatch_build_agent_chunked

                    dispatched = dispatch_build_agent_chunked(
                        repo_root=repo_root,
                        packet=packet_for_agent,
                        governance=governance,
                    )
                else:
                    dispatched = _dispatch_agent_loop_build(
                        repo_root=repo_root,
                        packet=packet_for_agent,
                        governance=governance,
                        prompt=prompt,
                        profile=actual_profile,
                    )
                # "no_api_key" is a config problem retrying can't fix -- terminal,
                # not a transient failure. "governance_required" is also terminal:
                # the system needs signed prior gates, not another retry.
                # Anything else non-"completed" is worth one bounded retry
                # through the same claim/lease/retry contract the parallel
                # executor uses (executor.py, Wave 1.1).
                if dispatched.get("status") not in (
                    "completed",
                    "no_api_key",
                    "governance_required",
                ):
                    raise RuntimeError(
                        "; ".join(dispatched.get("errors", [])) or "agent dispatch failed"
                    )
                return dispatched

            dispatch_store = InMemoryTaskStore(max_attempts=2)
            dispatch_store.enqueue(str(packet_for_agent.get("run_id") or "build-agent"))
            dispatch_report = run_worker_pool(dispatch_store, _dispatch_once, max_workers=1)
            if dispatch_report.succeeded:
                agent_result = dispatch_report.succeeded[0].result
            else:
                dead = dispatch_store.get(dispatch_report.dead_letters[0]) if dispatch_report.dead_letters else None
                agent_result = {
                    "status": "failed",
                    "errors": [dead.error] if dead and dead.error else ["agent dispatch failed"],
                }

            if agent_result.get("status") == "completed":
                if manifest:
                    _refresh_manifest_hashes(repo_root, manifest)
                    # #6: attach generation -> acceptance traces after the
                    # build completes, so the persisted manifest carries the
                    # per-file acceptance linkage.
                    trace_manifest = _link_acceptance_traces(
                        repo_root, manifest, acceptance, agent_result,
                    )
                    write_generation_manifest(manifest, signalos_dir)
                if route == "agent-loop":
                    real_files = _agent_loop_produced_real_files(
                        repo_root,
                        agent_result,
                    )
                else:
                    generation_validation = validate_generation_output(
                        repo_root, generation_packet,
                    )
                    if not generation_validation.get("valid", False):
                        errors.extend(generation_validation.get("violations", []))
                        errors.extend(
                            f"missing generated file: {path}"
                            for path in generation_validation.get("files_missing", [])
                        )
                    # Legacy/local paths still promise the generated file specs.
                    real_files = _generation_produced_real_files(
                        repo_root, generation_packet, agent_result,
                    )
                # Even a "completed" status is fake-green if no real product
                # source/test files landed on disk.
                if not real_files:
                    generation_blocked = True
                    generation_blocker = (
                        "generation produced no real files: the build agent "
                        "reported completion but no product source or test files "
                        "were written"
                    )
                    errors.append(generation_blocker)
                update_delivery_phase(repo_root, "generated", "complete")
            elif agent_result.get("status") == "no_api_key":
                warnings.append("No API key; agent not dispatched. Packet written for external execution.")
            elif agent_result.get("status") == "governance_required":
                blocker = (
                    "; ".join(str(e) for e in agent_result.get("errors", []))
                    or "governed AgentLoop build requires signed G0-G3 before implementation writes"
                )
                errors.append(blocker)
                generation_blocked = True
                generation_blocker = blocker
            else:
                # Dispatch FAILED. This is a hard blocker -- a generation that
                # produced no real files is a failure, not a green build off the
                # scaffold stub (#23). Fail delivery closed.
                agent_errors = agent_result.get("errors", []) or ["agent dispatch failed"]
                errors.extend(agent_errors)
                generation_blocked = True
                generation_blocker = (
                    "generation dispatch failed; no product code was written: "
                    + "; ".join(str(e) for e in agent_errors)
                )
            _emit_progress("generation", "packet", "done", agent_result.get("status", "agent finished"))
        except Exception as exc:
            errors.append(f"agent dispatch failed: {exc}")
            generation_blocked = True
            generation_blocker = f"generation dispatch failed: {exc}"
            _emit_progress("generation", "packet", "error", str(exc))

    # ------------------------------------------------------------------
    # 5. VALIDATION phase
    # ------------------------------------------------------------------
    val_result = None
    closure = {"level": "not_started", "closeable": False, "blockers": []}
    _emit_progress("validation", "run_checks", "running", "Running product validation")
    try:
        # #27: re-pin any @mantine/* skew a generation agent's self-written
        # package.json introduced BEFORE install/build sees it.
        _enforce_design_deps(repo_root, design_deps)
        if generation_blocked:
            # Generation produced no real product. A real tsc/vitest run would
            # only exercise the trivially-building scaffold stub, and its verdict
            # is overridden by _mark_generation_failed below -- so skip the
            # expensive validation (and the repair loop) and synthesize a failed
            # result. This keeps a determined-failed delivery fast instead of
            # spending a full build+test cycle on a stub that will be rejected.
            val_result = {"can_close_delivery": False, "checks": [], "status": "failed"}
        else:
            val_plan = build_validation_plan(repo_root, actual_profile)
            val_result = run_validation(repo_root, val_plan, dry_run=dry_run)
        write_validation_result(val_result, signalos_dir)

        # Repair loop: if validation fails and agent_mode is active
        if (
            not dry_run
            and not generation_blocked
            and not val_result.get("can_close_delivery", False)
            and agent_mode != "none"
        ):
            # Collect governance so repair-cycle regeneration honors the same
            # standards as first-pass generation.
            try:
                from .generation import collect_governance_instructions
                repair_governance = collect_governance_instructions("build")
            except Exception:
                repair_governance = {}
            # #27: each repair cycle regenerates files and can re-skew the
            # agent's package.json, so re-pin design deps before every internal
            # re-validation, not just the first pass.
            def _repair_validate(rr: Path) -> dict:
                from .validation import build_validation_plan, run_validation
                _enforce_design_deps(rr, design_deps)
                plan = build_validation_plan(rr, actual_profile)
                return run_validation(rr, plan, dry_run=False)

            repair_dispatch_fn = None
            try:
                from .secrets_resolver import is_llm_available as _repair_llm_available

                repair_route = _choose_dispatch_route(
                    agent_mode,
                    actual_profile,
                    llm_available=_repair_llm_available(repo_root),
                )
            except Exception:
                repair_route = "agent-loop"
            if repair_route == "agent-loop":
                def _repair_agent_loop_dispatch(
                    repair_repo_root: Path,
                    repair_packet: dict,
                    repair_governance: dict[str, str],
                ) -> dict:
                    return _dispatch_agent_loop_build(
                        repo_root=repair_repo_root,
                        packet=repair_packet,
                        governance=repair_governance,
                        prompt=(
                            prompt
                            + "\n\nRepair the current validation failures using the "
                            "same governed G4 AgentLoop path."
                        ),
                        profile=actual_profile,
                    )

                repair_dispatch_fn = _repair_agent_loop_dispatch

            repair_result = run_repair_loop(
                repo_root=repo_root,
                validation_result=val_result,
                profile=actual_profile,
                max_cycles=resolve_repair_cycle_budget(max_repair_cycles),
                agent_mode=agent_mode,
                governance=repair_governance,
                validate_fn=_repair_validate,
                dispatch_fn=repair_dispatch_fn,
            )
            # Active modes re-validate INTERNALLY each cycle and return the
            # final validation, so adopt it directly (the build-error feedback
            # loop closed here, not by a blind re-run). packet-only/none pause
            # and leave val_result unchanged.
            final_val = repair_result.get("final_validation")
            if repair_result.get("status") in ("repaired", "max_cycles_reached", "dispatch_failed") \
                    and isinstance(final_val, dict) and final_val is not val_result:
                val_result = final_val
                write_validation_result(val_result, signalos_dir)

            # Repair cycles regenerate/add files AFTER the initial trace
            # linking (4b), so files written during repair would otherwise
            # show as neither covered nor unlinked in the trace report.
            # Re-link from the final on-disk state (same helper, merged
            # files_written) so the traceability view at reconciliation
            # reflects what is actually being delivered.
            repair_written = sorted({
                str(path)
                for record in repair_result.get("repairs", []) or []
                for path in record.get("files_written", []) or []
            })
            if repair_written and isinstance(manifest, dict) and acceptance:
                _refresh_manifest_hashes(repo_root, manifest)
                merged_written = sorted(
                    set(
                        str(p)
                        for p in (agent_result or {}).get("files_written") or []
                    )
                    | set(repair_written)
                )
                trace_manifest = _link_acceptance_traces(
                    repo_root, manifest, acceptance,
                    {"files_written": merged_written},
                )
                write_generation_manifest(manifest, signalos_dir)

        # #23 fake-green hard block: if generation produced no real files, the
        # trivially-building scaffold stub must NOT be allowed to report a green
        # build. Override the persisted validation result so build_status is
        # failed and the closeout carries the blocker -- fail-closed at the
        # source of the closeout's evidence, not just as an appended limitation.
        if generation_blocked and isinstance(val_result, dict):
            val_result = _mark_generation_failed(val_result, generation_blocker)
            write_validation_result(val_result, signalos_dir)

        # Layer 2 snapshot point 1/2 (validation). This sits HERE -- after
        # the repair loop has fully finished and the FINAL validation verdict
        # is adopted -- because the repair loop legitimately rewrites product
        # files between its internal validation cycles; hashing any earlier
        # would false-positive at closeout on a normal repair flow. The
        # snapshot binds VALIDATION_RESULT.json to the exact bytes validated.
        if isinstance(val_result, dict):
            try:
                # The trace manifest (manifest records + agent/repair extras)
                # is the widest honest "generated files" view, so it defines
                # the freshness scope when available.
                val_result["workspace_snapshot"] = snapshot_workspace(
                    repo_root,
                    workspace_snapshot_files(
                        repo_root,
                        trace_manifest
                        if trace_manifest is not None
                        else (manifest if isinstance(manifest, dict) else None),
                    ),
                )
                write_validation_result(val_result, signalos_dir)
            except Exception as exc:
                warnings.append(f"validation workspace snapshot failed: {exc}")

        closure = check_product_closure(val_result)
        if generation_blocked and generation_blocker not in closure.get("blockers", []):
            closure.setdefault("blockers", []).append(generation_blocker)
            closure["closeable"] = False
            if closure.get("level") in (None, "ready", "closeable"):
                closure["level"] = "partial"
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
    # When generation itself failed (failed dispatch / no files written) the
    # closeout already carries that blocker with a failed build_status. There
    # is no real product to serve, so skip the runtime + UX proofs rather than
    # spin up a dev server against a trivially-building scaffold stub: it adds
    # no evidence and, on a restricted network, can hang the delivery on health
    # polling until the proof timeout elapses.
    if generation_blocked:
        runtime_proof = {
            "status": "skipped",
            "profile": actual_profile,
            "preview_command": None,
            "port": None,
            "health_check": {
                "url": None,
                "status_code": None,
                "responded": False,
                "response_time_ms": None,
            },
            "server_log": "",
            "duration_s": 0.0,
            "errors": [
                "Runtime proof skipped: generation failed (no real product to serve)"
            ],
        }
        _emit_progress("proof", "runtime", "skipped", "Runtime proof skipped (generation failed)")
    else:
        _emit_progress("proof", "runtime", "running", "Running runtime proof")
        try:
            runtime_proof = run_runtime_proof(repo_root, actual_profile)
            _emit_progress("proof", "runtime", "done", runtime_proof.get("status", "Runtime proof complete"))
        except Exception as exc:
            errors.append(f"runtime proof failed: {exc}")
            _emit_progress("proof", "runtime", "error", str(exc))
            runtime_proof = {"status": "blocked", "errors": [str(exc)]}

    requires_ux_proof = requires_browser_ux_proof(repo_root, actual_profile)
    if requires_ux_proof and not generation_blocked:
        _emit_progress("proof", "ux", "running", "Running UX proof")
        try:
            ux_port = (
                runtime_proof.get("port")
                if runtime_proof.get("status") == "passed"
                else None
            )
            ux_html = (
                runtime_proof.get("html_snapshot")
                if runtime_proof.get("status") == "passed"
                else None
            )
            ux_proof = run_ux_proof(
                repo_root,
                actual_profile,
                port=ux_port,
                html=ux_html if isinstance(ux_html, str) and ux_html else None,
            )
            _emit_progress("proof", "ux", "done", ux_proof.get("status", "UX proof complete"))
        except Exception as exc:
            errors.append(f"ux proof failed: {exc}")
            _emit_progress("proof", "ux", "error", str(exc))
            ux_proof = {"status": "blocked", "errors": [str(exc)]}
    else:
        ux_proof = run_ux_proof(repo_root, actual_profile, port=None)
        _emit_progress("proof", "ux", "skipped", ux_proof.get("skip_reason", "UX proof skipped"))

    # Layer 2 snapshot point 2/2 (proof). This sits HERE -- after runtime AND
    # UX proof have both finished -- because proof is the LAST pipeline
    # activity that observes the product files as evidence; every later phase
    # (reconciliation, review, deploy decision, closeout) only reads/writes
    # .signalos/** evidence, which the snapshot excludes. This is therefore
    # the LATEST snapshot and the one the closeout freshness check verifies
    # against: any generated-file drift after this point means the proven
    # artifact is not the delivered one.
    try:
        runtime_proof["workspace_snapshot"] = snapshot_workspace(
            repo_root,
            workspace_snapshot_files(
                repo_root,
                trace_manifest
                if trace_manifest is not None
                else (manifest if isinstance(manifest, dict) else None),
            ),
        )
    except Exception as exc:
        warnings.append(f"proof workspace snapshot failed: {exc}")

    try:
        write_proof_artifacts(runtime_proof, ux_proof, repo_root)
    except Exception as exc:
        errors.append(f"proof artifact write failed: {exc}")

    requires_runtime_proof = bool(runtime_proof.get("preview_command"))
    proof_status = "complete" if (
        (
            not requires_runtime_proof
            and runtime_proof.get("status") == "skipped"
            and ux_proof.get("status") == "skipped"
        )
        or (
            requires_runtime_proof
            and runtime_proof.get("status") == "passed"
            and (
                ux_proof.get("status") == "passed"
                if requires_ux_proof
                else ux_proof.get("status") == "skipped"
            )
        )
    ) else "partial"
    try:
        update_delivery_phase(repo_root, "proved", proof_status)
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 6b. ACCEPTANCE RECONCILIATION phase
    # ------------------------------------------------------------------
    trace_report: dict | None = None
    if acceptance is not None:
        _emit_progress("acceptance", "reconcile", "running", "Reconciling acceptance evidence")
        try:
            acceptance = reconcile_acceptance_evidence(
                acceptance,
                repo_root,
                validation_result=val_result,
                runtime_proof=runtime_proof,
                ux_proof=ux_proof,
                security_result=security_result if "security_result" in locals() else None,
            )
            # #6: verify per-file acceptance traceability alongside evidence
            # reconciliation. The report is persisted in the matrix and folded
            # into the Review gate verdict below (strict blocks, warn records).
            if trace_manifest is not None:
                trace_report = verify_trace_completeness(
                    trace_manifest, acceptance,
                )
                acceptance["traceability"] = trace_report
            write_acceptance_matrix(acceptance, signalos_dir)
            readiness = acceptance.get("reconciliation", {})
            update_delivery_phase(
                repo_root,
                "acceptance",
                "complete" if readiness.get("ready") else "partial",
            )
            _emit_progress(
                "acceptance",
                "reconcile",
                "done",
                f"Acceptance passed: {readiness.get('passed', 0)}",
            )
        except Exception as exc:
            errors.append(f"acceptance reconciliation failed: {exc}")
            _emit_progress("acceptance", "reconcile", "error", str(exc))

    # ------------------------------------------------------------------
    # 6c. REVIEW gate — Build -> Test -> REVIEW (#21)
    # Build ran (generation), Test ran (validation/repair); REVIEW is the
    # spec-drift / test-evidence / correctness verdict that GATES closeout.
    # Governed by the `gate-compliance` rule (core invariant): strict blocks,
    # warn records. review_blocking is consumed by the closeout, below.
    # ------------------------------------------------------------------
    review_result: dict | None = None
    review_blocking = False
    _emit_progress("review", "gate", "running", "Running product review gate")
    try:
        from .review_gate import run_review_gate, write_review_result

        review_result = run_review_gate(
            repo_root, intent, manifest if isinstance(manifest, dict) else {}, val_result,
        )
        # #6: acceptance traceability flows through the SAME review channel --
        # under strict gate-compliance an uncovered criterion blocks; under
        # warn it is recorded. Advisory file->criteria findings never block.
        review_result = _apply_traceability_review(review_result, trace_report)
        # Layer 3: deterministic test-quality report, folded through the SAME
        # review channel as traceability (strict blocks, warn records; weak
        # criterion links are advisory-only in every mode). The trace manifest
        # (manifest records + agent extras) is preferred so agent-written
        # tests outside the manifest are analyzed too.
        try:
            test_quality_report = analyze_test_quality(
                repo_root,
                (
                    trace_manifest
                    if trace_manifest is not None
                    else (manifest if isinstance(manifest, dict) else None)
                ),
                acceptance_matrix=acceptance,
            )
            write_test_quality_report(test_quality_report, signalos_dir)
            review_result = _apply_test_quality_review(
                review_result, test_quality_report,
            )
        except Exception as exc:
            errors.append(f"test quality analysis failed: {exc}")
        # Layer 1: surface the contract-verification metric ("fraction of the
        # contract that is machine-proven") on the review verdict. Purely
        # informational -- never affects the verdict.
        if acceptance and isinstance(
            acceptance.get("verifiability_summary"), dict,
        ):
            review_result["verifiability"] = acceptance["verifiability_summary"]
        write_review_result(review_result, signalos_dir)
        review_blocking = bool(review_result.get("blocking"))
        # Findings surface in REVIEW_RESULT.json (always) and, when the verdict
        # is not clean, in the closeout's known_limitations (below) -- not in
        # `errors`, which is reserved for pipeline faults.
        _emit_progress(
            "review", "gate", "done",
            f"Review: {review_result.get('status', 'complete')} "
            f"({review_result.get('mode', 'strict')})",
        )
    except Exception as exc:
        errors.append(f"review gate failed: {exc}")
        _emit_progress("review", "gate", "error", str(exc))

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
            requires_ux_proof=requires_ux_proof if "requires_ux_proof" in locals() else False,
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
    # 7c. EVIDENCE FRESHNESS verification (Layer 2) -- before write_closeout:
    # the evidence must still be TRUE at delivery. Re-hash the generated
    # files and compare against the LATEST snapshot (proof if captured,
    # else validation). Drift after proof means the proven artifact is not
    # the artifact being delivered. Uses the SAME gate-compliance rule-mode
    # resolution as the review gate: strict blocks, warn records.
    # ------------------------------------------------------------------
    freshness_report: dict | None = None
    try:
        latest_snapshot = None
        if isinstance(runtime_proof, dict):
            latest_snapshot = runtime_proof.get("workspace_snapshot")
        if latest_snapshot is None and isinstance(val_result, dict):
            latest_snapshot = val_result.get("workspace_snapshot")
        if latest_snapshot:
            from .review_gate import _resolve_gate_mode

            freshness_report = verify_workspace_snapshot(
                repo_root,
                latest_snapshot,
                workspace_snapshot_files(
                    repo_root,
                    trace_manifest
                    if trace_manifest is not None
                    else (manifest if isinstance(manifest, dict) else None),
                ),
            )
            freshness_report["mode"] = _resolve_gate_mode(repo_root, None)
            write_freshness_report(freshness_report, signalos_dir)
    except Exception as exc:
        errors.append(f"evidence freshness verification failed: {exc}")

    # Gate provenance is reported for EVERY delivery, honestly, regardless of
    # agent_mode. Provisioning runs before dispatch even for no-build modes, so
    # closeout should normally carry assumed/reconstructed/founder-signed tiers.
    # If provisioning failed before computing the tier, still report whatever
    # signed/unsigned state is on disk rather than omitting governance.
    if governance_tier is None:
        try:
            from .provision_gates import governance_tier_summary
            governance_tier = governance_tier_summary(repo_root)
        except Exception as exc:
            errors.append(f"gate provenance report failed: {exc}")

    # ------------------------------------------------------------------
    # 8. CLOSEOUT phase
    # ------------------------------------------------------------------
    _emit_progress("closeout", "handoff", "running", "Writing closeout and handoff")
    try:
        closeout = build_closeout(
            repo_root, product_name, actual_profile, blueprint_id,
        )
        # Honest governance provenance: which prior gates a human actually
        # reviewed vs which were auto-provisioned. A reviewer must see this --
        # an assumed/reconstructed gate is NOT founder-reviewed.
        if governance_tier is not None:
            closeout["governance_tier"] = governance_tier
            _provisioned = [g for g, t in governance_tier.items()
                            if t in ("assumed", "reconstructed")]
            _unsigned = [g for g, t in governance_tier.items() if t == "unsigned"]
            if _provisioned:
                closeout.setdefault("known_limitations", []).append(
                    "Governance: gate(s) " + ", ".join(_provisioned)
                    + " were auto-provisioned (not founder-reviewed); founder "
                    "review pending -- review and correct the artifacts to "
                    "upgrade them to founder-signed.")
            if _unsigned:
                closeout.setdefault("known_limitations", []).append(
                    "Governance: gate(s) " + ", ".join(_unsigned)
                    + " are NOT signed -- no governed build ran for them.")
        # Layer 2: stale evidence fails the closeout CLOSED under strict
        # gate-compliance (drifted files listed); under warn it is recorded
        # in known_limitations without failing closed.
        closeout = _apply_evidence_freshness(closeout, freshness_report)
        if "readiness_result" in locals() and not readiness_result.get("ready", False):
            closeout.setdefault("known_limitations", []).extend(
                readiness_result.get("errors", [])
                + readiness_result.get("blockers", [])
            )
            if closeout.get("closure_level") == "ready":
                closeout["closure_level"] = "partial"
        # #21: the Review verdict. A blocking verdict (spec-drift / no test
        # evidence / build failed, under strict gate-compliance) fails the
        # closeout CLOSED -- the product cannot close "ready" past an
        # un-reviewed or spec-drifted build. A warn verdict records the same
        # findings as limitations without failing closed.
        if review_result and review_result.get("status") in ("blocked", "warn"):
            status_label = review_result["status"]
            closeout.setdefault("known_limitations", []).extend(
                f"review gate ({status_label}): {f}"
                for f in review_result.get("findings", [])
            )
            if review_blocking and closeout.get("closure_level") in (
                "ready", "closeable", None,
            ):
                closeout["closure_level"] = "partial"
        if errors:
            closeout.setdefault("known_limitations", []).extend(errors)
        # #11: handoff files run FIRST -- GTM generation records its outcome
        # (written / skipped / failed) on the closeout dict, so the persisted
        # CLOSEOUT.json carries that evidence honestly.
        write_handoff_files(closeout, signalos_dir)
        write_closeout(closeout, signalos_dir)
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
        # Even on a closeout-phase failure, report gate provenance honestly --
        # an assumed/reconstructed gate must never be silently dropped from the
        # persisted closeout.
        if governance_tier is not None:
            closeout["governance_tier"] = governance_tier
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
        "capability_profile": capability_profile,
        "switch_recommended": True,
    }

    # Write WORKSPACE.json for Tauri app to discover on next launch
    workspace_info = {
        "repo_root": str(repo_root),
        "product_name": product_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": actual_profile,
        "capability_profile": capability_profile,
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

def _link_acceptance_traces(
    repo_root: Path,
    manifest: dict | None,
    acceptance: dict | None,
    agent_result: dict | None,
) -> dict | None:
    """#6: attach generation -> acceptance traces after a completed build.

    Returns the "trace manifest" later consumed by verify_trace_completeness,
    or ``None`` when there is nothing to trace (no acceptance matrix / no
    manifest). The trace view is built HONESTLY from what actually landed on
    disk:

    - manifest file records that exist on disk (``link_generation_to_
      acceptance`` mutates these in place, so the traces persist into
      GENERATION_MANIFEST.json when the caller re-writes it);
    - plus files the agent reported writing that are not in the manifest.
      The AgentLoop path is acceptance-matrix-first and the agent owns
      implementation shape, so such extras (helpers, hooks, shared UI) are
      legitimate; they participate in the trace report only and are NOT
      added to the persisted manifest.
    """
    if not acceptance or not isinstance(manifest, dict):
        return None

    on_disk = [
        rec
        for rec in manifest.get("files", [])
        if isinstance(rec, dict)
        and rec.get("path")
        and (repo_root / str(rec["path"])).is_file()
    ]
    known = {str(rec["path"]).replace("\\", "/") for rec in on_disk}
    extras: list[dict] = []
    for rel in (agent_result or {}).get("files_written") or []:
        rel_str = str(rel).replace("\\", "/").lstrip("/")
        if (
            not rel_str
            or rel_str in known
            or not _is_real_product_file(rel_str)
            or not (repo_root / rel_str).is_file()
        ):
            continue
        known.add(rel_str)
        name = rel_str.rsplit("/", 1)[-1].lower()
        is_test = (
            ".test." in name
            or ".spec." in name
            or name.startswith("test_")
            or name.endswith(("_test.py", "_test.go"))
        )
        extras.append({
            "path": rel_str,
            "kind": "test" if is_test else "source",
            "acceptance_id": None,
        })

    trace_manifest = {"files": on_disk + extras}
    link_generation_to_acceptance(trace_manifest, acceptance)
    return trace_manifest


def _apply_traceability_review(
    review_result: dict | None,
    trace_report: dict | None,
) -> dict | None:
    """#6: fold acceptance traceability into the Review gate verdict.

    Chosen semantics (deliberately asymmetric so the check cannot
    false-positive on helper files):

    - criteria -> file coverage is STRICT: an acceptance criterion that no
      generated file traces to is a concrete gap. Under strict
      gate-compliance it becomes a BLOCKING review finding; under warn it is
      recorded as a finding without failing closed (the exact same contract
      as the other review checks -- no new enforcement mechanism).
    - file -> criteria linkage is ADVISORY ONLY: the AgentLoop path is
      acceptance-matrix-first and the agent owns implementation shape, so
      helper files (utils, hooks, shared UI) serving a traced component are
      legitimate. Unlinked files are recorded as advisory findings and NEVER
      block, in any mode.
    """
    if not review_result or not trace_report:
        return review_result

    uncovered = list(trace_report.get("uncovered_criteria") or [])
    unlinked = list(trace_report.get("unlinked_paths") or [])

    checks = review_result.setdefault("checks", {})
    checks["acceptance_traceability"] = not uncovered

    findings = review_result.setdefault("findings", [])
    findings.extend(
        f"traceability: acceptance criterion {cid} has no generated file "
        f"tracing to it"
        for cid in uncovered
    )
    findings.extend(
        f"traceability (advisory): {path} does not trace to an acceptance "
        f"criterion (helper files serving traced components are legitimate)"
        for path in unlinked
    )

    if uncovered:
        if review_result.get("mode") == "warn":
            if review_result.get("status") == "pass":
                review_result["status"] = "warn"
        else:  # strict (and the default-safe fallback)
            review_result["status"] = "blocked"
            review_result["blocking"] = True
    return review_result


def _apply_test_quality_review(
    review_result: dict | None,
    quality_report: dict | None,
) -> dict | None:
    """Layer 3: fold the deterministic test-quality report into the Review
    gate verdict -- the exact same asymmetric contract as traceability (#6):

    - vacuous tests / assertion-free files are CONCRETE evidence that the
      test suite does not test what it claims: BLOCKING under strict
      gate-compliance, recorded as findings under warn.
    - weak criterion links are ADVISORY ONLY in every mode: the check is a
      coarse string-level heuristic (first cut) that can under-detect
      legitimate indirect coverage, so it must never block.
    """
    if not review_result or not quality_report:
        return review_result

    vacuous = list(quality_report.get("vacuous_tests") or [])
    assertion_free = list(quality_report.get("assertion_free_files") or [])
    weak = list(quality_report.get("weak_criterion_links") or [])

    checks = review_result.setdefault("checks", {})
    checks["test_quality"] = not (vacuous or assertion_free)

    findings = review_result.setdefault("findings", [])
    findings.extend(
        f"test-quality: vacuous test '{item.get('test_name')}' in "
        f"{item.get('file')} contains no assertion"
        for item in vacuous
    )
    findings.extend(
        f"test-quality: {path} is a test file with no assertions"
        for path in assertion_free
    )
    findings.extend(
        f"test-quality (advisory): {item.get('file')} traces to "
        f"{item.get('acceptance_id')} but never references the criterion's "
        f"entity/operation words ({', '.join(item.get('missing_words') or [])})"
        for item in weak
    )

    if vacuous or assertion_free:
        if review_result.get("mode") == "warn":
            if review_result.get("status") == "pass":
                review_result["status"] = "warn"
        else:  # strict (and the default-safe fallback)
            review_result["status"] = "blocked"
            review_result["blocking"] = True
    return review_result


def _apply_evidence_freshness(
    closeout: dict,
    freshness_report: dict | None,
) -> dict:
    """Layer 2: fold the closeout-time freshness verdict into the closeout.

    The report (always attached as ``closeout["evidence_freshness"]`` for
    honest evidence, ``None`` when no snapshot existed) is consequential only
    when NOT fresh:

    - strict gate-compliance: BLOCKING review-gate-style finding -- the
      drifted files are listed in known_limitations and closure_level is
      downgraded to "partial" (same downgrade contract as the review gate).
    - warn: the same finding is recorded in known_limitations only.
    """
    closeout["evidence_freshness"] = freshness_report
    if not freshness_report or freshness_report.get("fresh", True):
        return closeout

    drifted = (
        [f"changed: {p}" for p in freshness_report.get("changed", [])]
        + [f"added: {p}" for p in freshness_report.get("added", [])]
        + [f"removed: {p}" for p in freshness_report.get("removed", [])]
    )
    closeout.setdefault("known_limitations", []).append(
        "evidence is stale: files changed after proof ("
        + "; ".join(drifted)
        + ")"
    )
    if freshness_report.get("mode") != "warn" and closeout.get(
        "closure_level",
    ) in ("ready", "closeable", None):
        closeout["closure_level"] = "partial"
    return closeout


def _dispatch_agent_loop_build(
    *,
    repo_root: Path,
    packet: dict,
    governance: dict[str, str],
    prompt: str,
    profile: str,
) -> dict[str, Any]:
    """Run the production build through the governed AgentLoop.

    This is the production LLM route. It deliberately refuses to proceed when
    prior gates cannot be validated instead of falling back to the legacy
    per-file generator. The interactive `agent:deliver` path remains the full
    G0->G5 experience; this bridge entrypoint is only allowed to execute G4 when
    the workspace already proves G0-G3 are signed.
    """

    from .secrets_resolver import apply_product_secrets, is_llm_available

    run_id = str(packet.get("run_id") or f"delivery-agent-loop-{int(time.time())}")
    if not is_llm_available(repo_root):
        _write_agent_loop_handoff(
            repo_root,
            run_id=run_id,
            status="no_api_key",
            prompt=prompt,
            profile=profile,
            blockers=[
                "No usable LLM provider is configured; governed AgentLoop build cannot start."
            ],
        )
        return {
            "status": "no_api_key",
            "run_id": run_id,
            "files_written": [],
            "errors": ["No usable LLM provider is configured."],
        }

    signed_prior, blockers = _signed_prior_gates_for_g4(repo_root)
    if blockers:
        _write_agent_loop_handoff(
            repo_root,
            run_id=run_id,
            status="governance_required",
            prompt=prompt,
            profile=profile,
            blockers=blockers,
        )
        return {
            "status": "governance_required",
            "run_id": run_id,
            "files_written": [],
            "errors": blockers,
        }

    events: list[dict[str, Any]] = []
    tool_call_budget = _agent_loop_tool_budget_from_packet(packet)

    def _capture(ev: dict[str, Any]) -> None:
        events.append(dict(ev))

    try:
        with apply_product_secrets(repo_root):
            from signalos_lib import agent_loader
            from signalos_lib.harness import _resolve_provider_name, resolve_model
            from signalos_lib.product.agent_loop import AgentLoop
            from signalos_lib.product.enforcement_state import FileEnforcementProvider
            from signalos_lib.product.provider_adapter import ProviderAdapter

            provider_name = _resolve_provider_name(None)
            model = resolve_model(None, provider_name)
            adapter = ProviderAdapter(model=model, provider_name=provider_name)
            agent = agent_loader.load_agent("G4")
            system_prompt = agent.get("content") or "You are the SignalOS G4 Build agent."
            loop = AgentLoop(
                adapter=adapter,
                repo_root=repo_root,
                enforcement_provider=FileEnforcementProvider(),
                run_id=run_id,
                tool_call_limit=tool_call_budget,
                emit=_capture,
                execution_context="delivery",
                active_gate="G4",
                signed_gates=signed_prior,
            )
            result = loop.run(
                system_prompt,
                _build_agent_loop_message(packet, governance, prompt, profile),
            )
    except Exception as exc:
        _write_build_evidence(
            repo_root,
            run_id=run_id,
            profile=profile,
            status="failed",
            files_written=[],
            events=events,
            errors=[str(exc)],
            tool_call_budget=tool_call_budget,
        )
        return {
            "status": "failed",
            "run_id": run_id,
            "files_written": [],
            "errors": [f"AgentLoop dispatch failed: {exc}"],
        }

    files_written = sorted({
        str(ev.get("path"))
        for ev in events
        if ev.get("type") == "diff" and ev.get("path")
    })
    errors: list[str] = []
    status = "completed"
    if result.status != "completed":
        status = "failed"
        errors.append(result.error or f"AgentLoop ended with status {result.status}")
    elif not _agent_loop_produced_real_files(
        repo_root,
        {"files_written": files_written},
    ):
        status = "failed"
        errors.append(
            "AgentLoop completed without producing real product source or test files."
        )

    _write_build_evidence(
        repo_root,
        run_id=run_id,
        profile=profile,
        status=status,
        files_written=files_written,
        events=events,
        errors=errors,
        tool_calls_made=result.tool_calls_made,
        tool_call_budget=tool_call_budget,
    )
    return {
        "status": status,
        "run_id": run_id,
        "files_written": files_written,
        "errors": errors,
        "tool_calls_made": result.tool_calls_made,
        "tool_call_budget": tool_call_budget,
    }


def _signed_prior_gates_for_g4(repo_root: Path) -> tuple[list[int], list[str]]:
    signed: list[int] = []
    blockers: list[str] = []
    try:
        from signalos_lib.commands.validate_gate import validate_gate
    except Exception as exc:
        return [], [f"cannot validate prior gates before G4 build: {exc}"]

    for number in range(0, 4):
        gate = f"G{number}"
        try:
            result = validate_gate(repo_root, gate, write_evidence=False)
        except Exception as exc:
            blockers.append(f"{gate} validation failed before G4 build: {exc}")
            continue
        if result.get("ok"):
            signed.append(number)
        else:
            messages = [
                str(item.get("message"))
                for item in result.get("blockers", [])
                if item.get("message")
            ]
            detail = (
                "; ".join(messages)
                if messages
                else "gate is not signed and audit-linked"
            )
            blockers.append(f"{gate} must be signed before AgentLoop build: {detail}")
    return signed, blockers


def _generation_packet_from_agent_packet(packet: dict | None) -> dict | None:
    if not isinstance(packet, dict):
        return None
    nested = packet.get("generation_packet")
    if isinstance(nested, dict):
        return nested
    generation = packet.get("generation")
    if isinstance(generation, dict):
        return generation
    return packet if packet.get("file_specs") else None


def _agent_loop_tool_budget_from_packet(packet: dict | None) -> int:
    if isinstance(packet, dict):
        budget = packet.get("execution_budget")
        if isinstance(budget, dict):
            raw = budget.get("tool_call_budget")
            if raw is not None:
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    pass
    from .budgets import resolve_agent_loop_tool_budget

    return resolve_agent_loop_tool_budget()


def _agent_loop_produced_real_files(
    repo_root: Path,
    agent_result: dict | None,
) -> bool:
    written = (agent_result or {}).get("files_written") or []
    for rel in written:
        rel_str = str(rel).replace("\\", "/").lstrip("/")
        if _is_real_product_file(rel_str) and (repo_root / rel_str).is_file():
            return True
    return False


def _is_real_product_file(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").lstrip("/")
    if not normalized or normalized.startswith(
        (".signalos/", ".git/", "node_modules/")
    ):
        return False
    if normalized in {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "pyproject.toml",
        "requirements.txt",
        "README.md",
    }:
        return False
    if normalized.startswith(
        (
            "src/",
            "tests/",
            "test/",
            "app/",
            "pages/",
            "components/",
            "lib/",
            "server/",
            "api/",
            "public/",
        )
    ):
        return True
    return normalized.endswith(
        (
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".vue",
            ".svelte",
            ".go",
            ".rs",
            ".java",
            ".cs",
            ".css",
            ".html",
        )
    )


def _build_agent_loop_message(
    packet: dict,
    governance: dict[str, str],
    prompt: str,
    profile: str,
) -> str:
    prompt_packet = _agent_loop_prompt_packet(packet)
    packet_json = json.dumps(prompt_packet, indent=2, ensure_ascii=False)
    governance_json = json.dumps(governance, indent=2, ensure_ascii=False)
    return (
        "Execute the approved G4 build through governed tools only.\n\n"
        f"Original product request:\n{prompt}\n\n"
        f"Profile: {profile}\n\n"
        "Output contract:\n"
        "- Treat the acceptance_matrix as the required product outcome.\n"
        "- Choose the file structure, component model, data model, and styling "
        "needed to satisfy the acceptance matrix.\n"
        "- Do not wait for a file-by-file template. The packet intentionally "
        "does not prescribe implementation files for production AgentLoop runs.\n"
        "- Use signed architecture, design, scope, and governance context as "
        "constraints, not as a CRUD scaffold.\n\n"
        "Rules:\n"
        "- Read the repo and signed artifacts before writing.\n"
        "- Write or update matching tests before implementation files.\n"
        "- Stay inside allowed paths and trust-tier policy.\n"
        "- Run the validation commands and iterate until they pass or the "
        "execution budget is exhausted.\n"
        "- Record exact blockers instead of guessing or fabricating evidence.\n"
        "- Update core/execution/BUILD_EVIDENCE.md before ending the turn.\n\n"
        f"Governance instructions:\n```json\n{governance_json}\n```\n\n"
        f"Acceptance-first agent packet:\n```json\n{packet_json}\n```\n"
    )


def _agent_loop_prompt_packet(packet: dict) -> dict:
    """Remove legacy per-file generation specs from AgentLoop prompt context."""

    sanitized = json.loads(json.dumps(packet, ensure_ascii=False))
    generation = sanitized.get("generation")
    if isinstance(generation, dict):
        for key in ("file_specs", "component_manifest", "allowed_paths"):
            generation.pop(key, None)
        if not generation:
            sanitized.pop("generation", None)
    return sanitized


def _write_agent_loop_handoff(
    repo_root: Path,
    *,
    run_id: str,
    status: str,
    prompt: str,
    profile: str,
    blockers: list[str],
) -> None:
    path = repo_root / ".signalos" / "product" / "AGENT_LOOP_HANDOFF.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "signalos.agent_loop_handoff.v1",
                "run_id": run_id,
                "status": status,
                "profile": profile,
                "prompt": prompt,
                "required_route": "agent:deliver",
                "blockers": blockers,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_build_evidence(
    repo_root: Path,
    *,
    run_id: str,
    profile: str,
    status: str,
    files_written: list[str],
    events: list[dict[str, Any]],
    errors: list[str],
    tool_calls_made: int | None = None,
    tool_call_budget: int | None = None,
) -> None:
    path = repo_root / "core" / "execution" / "BUILD_EVIDENCE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# BUILD_EVIDENCE",
        "",
        f"- run_id: {run_id}",
        f"- status: {status}",
        f"- profile: {profile}",
        f"- generated_at: {datetime.now(timezone.utc).isoformat()}",
    ]
    if tool_calls_made is not None:
        lines.append(f"- tool_calls_made: {tool_calls_made}")
    if tool_call_budget is not None:
        lines.append(f"- tool_call_budget: {tool_call_budget}")
    lines.extend(["", "## Files Written", ""])
    if files_written:
        lines.extend(f"- `{path}`" for path in files_written)
    else:
        lines.append("- None recorded")
    lines.extend(["", "## Errors", ""])
    if errors:
        lines.extend(f"- {err}" for err in errors)
    else:
        lines.append("- None")
    lines.extend(["", "## Agent Events", ""])
    diff_events = [ev for ev in events if ev.get("type") in {"diff", "tool_denied", "tool_error", "error"}]
    if diff_events:
        for ev in diff_events[:50]:
            event_type = ev.get("type", "event")
            detail = ev.get("path") or ev.get("reason") or ev.get("error") or ev.get("tool") or ""
            lines.append(f"- {event_type}: {detail}")
    else:
        lines.append("- No write/error events recorded")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _generation_produced_real_files(
    repo_root: Path,
    generation_packet: dict | None,
    agent_result: dict | None,
) -> bool:
    """Whether the generation actually wrote the expected product files.

    A "completed" agent status is still fake-green if nothing landed: an empty
    ``files_written`` with none of the packet's expected source specs present on
    disk means no real product exists (#23). Returns True only when at least one
    expected non-config source file (or any reported written file) is on disk.
    Config-only scaffolding (types/theme stubs) does not count as a product.
    """
    if not generation_packet:
        return True  # nothing was expected; not this check's concern
    written = (agent_result or {}).get("files_written") or []
    if written:
        # Trust a non-empty written list only if at least one file truly exists.
        for rel in written:
            if (repo_root / str(rel)).is_file():
                return True
    specs = generation_packet.get("file_specs", []) or []
    expected_sources = [
        s.get("path")
        for s in specs
        if s.get("path") and s.get("kind") in ("source", "registration", "test")
    ]
    if not expected_sources:
        # No source specs to check -- fall back to "any expected file present".
        expected_sources = [s.get("path") for s in specs if s.get("path")]
    for rel in expected_sources:
        if rel and (repo_root / str(rel)).is_file():
            return True
    return False


def _mark_generation_failed(val_result: dict, blocker: str) -> dict:
    """Force a validation result to reflect a failed generation (#23).

    Sets the build category to ``failed``, appends the blocker, and clears
    ``can_close_delivery`` so the trivially-building scaffold stub can never be
    reported as a green build when the agent wrote no real product files.
    """
    marked = dict(val_result)
    results = dict(marked.get("results", {}) or {})
    build = dict(results.get("build", {}) or {})
    build["status"] = "failed"
    existing_out = str(build.get("output") or "").strip()
    build["output"] = (existing_out + "\n" if existing_out else "") + blocker
    results["build"] = build
    marked["results"] = results
    marked["can_close_delivery"] = False
    blockers = list(marked.get("blockers", []) or [])
    if blocker not in blockers:
        blockers.append(blocker)
    marked["blockers"] = blockers
    return marked


def _run_delivery_feature_gate(repo_root: Path, request: str) -> dict[str, Any]:
    from signalos_lib.commands.feature_gate import (
        run_feature_gate,
        write_feature_gate_evidence,
    )

    wave_pointer = repo_root / ".signalos" / "wave.json"
    if not wave_pointer.is_file():
        payload: dict[str, Any] = {
            "executed": False,
            "blocked": False,
            "request": request,
            "reason": ".signalos/wave.json not present; no active wave pointer was available.",
            "delivery_phase": "before_generation",
        }
        write_feature_gate_evidence(payload, repo_root)
        return payload

    exit_code, result = run_feature_gate(
        request,
        repo_root=repo_root,
    )
    payload = {
        **result,
        "executed": True,
        "exit_code": exit_code,
        "blocked": exit_code != 0,
        "delivery_phase": "before_generation",
    }
    if payload["blocked"]:
        payload["reason"] = result.get("error") or (
            f"feature-gate refused generation with exit code {exit_code}"
        )
    else:
        payload.setdefault("reason", "feature-gate executed before writing product files.")
    write_feature_gate_evidence(payload, repo_root)
    return payload


def _refresh_manifest_hashes(repo_root: Path, manifest: dict) -> None:
    for record in manifest.get("files", []):
        if not isinstance(record, dict):
            continue
        rel_path = str(record.get("path") or "")
        if not rel_path:
            continue
        target = repo_root / rel_path
        if not target.is_file():
            continue
        try:
            record["sha256_lf"] = compute_sha256_lf(
                target.read_text(encoding="utf-8")
            )
            record["overwrite_mode"] = "generated"
        except (OSError, UnicodeDecodeError):
            continue


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
    capability_profile: dict | None = None,
) -> dict:
    entities = intent.get("entities") or ["product entity"]
    workflows = intent.get("primary_workflows") or ["core workflow"]
    surfaces = intent.get("ux_surfaces") or []
    integrations = intent.get("integrations") or []
    capabilities = capability_profile or build_capability_profile(
        intent,
        adapter_profile=profile,
    )
    infra = capabilities.get("infrastructure", {})
    layers = capabilities.get("application_layers", {})

    return build_arch_review(
        system_boundaries=[
            f"profile: {profile}",
            f"blueprint: {(blueprint or {}).get('id', 'custom')}",
            f"entities: {', '.join(str(e) for e in entities)}",
            f"technology preferences: {', '.join(capabilities.get('technology_preferences', []) or ['auto'])}",
            f"database choices: {', '.join(infra.get('databases', []) or ['auto'])}",
            f"cache choices: {', '.join(infra.get('caches', []) or ['auto'])}",
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
        ] + (
            [f"ux surfaces: {', '.join(str(s) for s in surfaces)}"]
            if surfaces else []
        ) + (
            [f"frontend choices: {', '.join(layers.get('frontend', []))}"]
            if layers.get("frontend") else []
        ) + (
            [f"backend choices: {', '.join(layers.get('backend', []))}"]
            if layers.get("backend") else []
        ),
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
    requires_ux_proof: bool = False,
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
    if requires_ux_proof and ux_status != "passed":
        blocking_items.append("UX proof must pass for UI products")
    ux_ready = (
        ux_status == "passed"
        if requires_ux_proof
        else ux_status in {"passed", "skipped"}
    )

    ready = (
        not blocking_items
        and validation_closeable
        and runtime_status in {"passed", "skipped"}
        and ux_ready
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


def _enforce_design_deps(repo_root: Path, deps: dict[str, str]) -> None:
    """Force package.json runtime deps onto the canonical design versions.

    #27: unlike ``_merge_design_deps`` (which only fills *missing* keys), this
    OVERWRITES skewed versions a generation/repair agent's self-written
    package.json introduced. Runs after generation and before every validation
    so a mismatched @mantine/* major (Mantine 9 needs React 19; the template
    ships React 18) can never reach ``npm install``/build.
    """
    pkg_path = repo_root / "package.json"
    if not pkg_path.is_file() or not deps:
        return
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    dependencies = pkg.setdefault("dependencies", {})
    if not isinstance(dependencies, dict):
        return
    from .design import enforce_dependency_versions

    if enforce_dependency_versions(dependencies, deps):
        pkg_path.write_text(
            json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
