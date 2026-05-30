"""Delivery ownership map for SignalOS product delivery.

The ownership map answers a user-facing question that the rest of the
pipeline cannot infer from artifacts alone: which steps are executed by the
SignalOS platform, which are delegated to SignalOS agent teams, and which
steps require human approval.
"""

from __future__ import annotations

__all__ = [
    "SCHEMA_VERSION",
    "build_delivery_ownership_map",
    "load_delivery_ownership_map",
    "write_delivery_ownership_map",
]

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "signalos.delivery_ownership.v1"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def build_delivery_ownership_map(
    *,
    prompt: str,
    intent: dict[str, Any],
    blueprint_id: str | None,
    profile: str,
    deploy_mode: str,
) -> dict[str, Any]:
    """Build the platform/agent/human responsibility map."""
    product_type = intent.get("product_type") or "custom"
    enterprise_slice = _enterprise_slice(intent)

    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _utc_now(),
        "source_prompt": prompt,
        "product_name": intent.get("product_name", ""),
        "product_type": product_type,
        "blueprint": blueprint_id,
        "profile": profile,
        "deploy_mode": deploy_mode,
        "minimum_prompt_contract": {
            "accepted_minimum_prompt": True,
            "inferred_enterprise_slice": enterprise_slice,
            "non_technical_user_mode": True,
            "technical_choices_owned_by": "signalos-system",
        },
        "team_contract": {
            "agents_are_signalos_team": True,
            "user_manages_agents": False,
            "signalos_orchestrates_team": True,
            "user_visible_name": "SignalOS team",
            "user_role": (
                "Describe the product outcome, answer product/domain blockers, "
                "and approve the build plan and handoff."
            ),
            "signalos_role": (
                "Assign, scope, constrain, validate, and repair team work under "
                "the delivery governance contract."
            ),
            "agent_team_role": (
                "Analyze, design, implement, test, review, and repair only inside "
                "approved packets and allowed paths."
            ),
        },
        "ownership": [
            {
                "step": "capture_prompt_and_create_workspace",
                "owner": "signalos-system",
                "team": "Platform Orchestrator",
                "responsibility": "Create/select the product repo, capture the source prompt, and initialize governance evidence.",
                "human_action": "Choose the projects root once during onboarding.",
            },
            {
                "step": "extract_intent_and_assumptions",
                "owner": "signalos-system",
                "team": "Intent Engine",
                "responsibility": "Infer product type, users, workflows, entities, enterprise defaults, and assumptions from the minimum prompt.",
                "human_action": "Answer only product/domain questions if a blocker remains.",
            },
            {
                "step": "domain_analysis",
                "owner": "signalos-agent-team",
                "team": "Domain Analysis Agent",
                "responsibility": "Pressure-test product workflows, data rules, metrics, edge cases, and domain assumptions.",
                "human_action": "Approve or correct business meaning, not technical stack details.",
            },
            {
                "step": "ux_and_product_design",
                "owner": "signalos-agent-team",
                "team": "Product Design Agent",
                "responsibility": "Design the product screens, workflow states, empty states, reporting surfaces, and accessibility behavior.",
                "human_action": "Approve the experience direction.",
            },
            {
                "step": "scaffold_and_generation_packet",
                "owner": "signalos-system",
                "team": "Delivery Bridge",
                "responsibility": "Choose the stack/profile, scaffold deployable project files, build acceptance criteria, and create scoped build packets.",
                "human_action": "No action unless a toolchain or policy blocker is reported.",
            },
            {
                "step": "implementation",
                "owner": "signalos-agent-team",
                "team": "Build Agent Team",
                "responsibility": "Implement product code inside allowed files only, covering the approved entities, workflows, roles, reporting, and security behavior.",
                "human_action": "No action unless scope changes are requested.",
            },
            {
                "step": "test_and_quality",
                "owner": "signalos-agent-team",
                "team": "QA Agent Team",
                "responsibility": "Write and run tests for acceptance criteria, edge cases, accessibility, and regression risk.",
                "human_action": "Review failures that require product tradeoffs.",
            },
            {
                "step": "security_review",
                "owner": "signalos-agent-team",
                "team": "Security Review Agent",
                "responsibility": "Review auth, RBAC, tenant isolation, audit trail, injection risks, and secret handling.",
                "human_action": "Approve any explicit security exception.",
            },
            {
                "step": "validation_proof_and_repair",
                "owner": "signalos-system",
                "team": "Validation Harness",
                "responsibility": "Run build, tests, runtime proof, UX proof, security checks, and bounded repair cycles until pass or honest blocker.",
                "human_action": "Review blocker evidence if max repair cycles are exhausted.",
            },
            {
                "step": "deploy_package_and_handoff",
                "owner": "signalos-system",
                "team": "Release Bridge",
                "responsibility": "Prepare deployment package when requested and write evidence-derived closeout and runbook.",
                "human_action": "Explicitly authorize live deployment; SignalOS never deploys live by default.",
            },
        ],
        "forbidden": [
            "No live deployment without explicit human approval.",
            "No forged signatures, fabricated evidence, or hidden validation failures.",
            "No agent writes outside allowed paths or into governance evidence.",
            "No technical questions to non-technical users unless they explicitly request technical control.",
        ],
    }


def _enterprise_slice(intent: dict[str, Any]) -> list[str]:
    values: list[str] = []
    field_labels = (
        ("primary_workflows", "approved workflows"),
        ("entities", "domain entities"),
        ("target_users", "target users"),
        ("ux_surfaces", "user experience surfaces"),
        ("permissions", "permission model"),
        ("security_constraints", "security constraints"),
        ("audit_requirements", "audit requirements"),
    )
    for field, label in field_labels:
        raw = intent.get(field)
        if isinstance(raw, list) and raw:
            values.append(f"{label}: {', '.join(str(item) for item in raw[:8])}")

    deployment = intent.get("deployment_intent")
    if deployment and deployment != "none":
        values.append(f"deployment intent: {deployment}")

    return values or ["blueprint-approved product scope"]


def write_delivery_ownership_map(ownership: dict[str, Any], signalos_dir: Path) -> Path:
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "DELIVERY_OWNERSHIP.json"
    path.write_text(
        json.dumps(ownership, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_delivery_ownership_map(signalos_dir: Path) -> dict[str, Any] | None:
    path = signalos_dir / "product" / "DELIVERY_OWNERSHIP.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None
