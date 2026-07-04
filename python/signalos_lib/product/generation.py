# signalos_lib/product/generation.py
# Phase P7 - Product Generation Packet Builder
#
# Builds generation packets that define WHAT files the agent must create.
# SignalOS does NOT write application code -- it defines constraints,
# file specs, and acceptance criteria.  The AGENT writes the code.
# SignalOS validates the result.

from __future__ import annotations

__all__ = [
    "build_generation_manifest",
    "build_generation_packet",
    "check_cross_file_consistency",
    "check_file_ownership",
    "collect_governance_instructions",
    "compute_sha256_lf",
    "get_blueprint_dependencies",
    "link_generation_to_acceptance",
    "load_generation_manifest",
    "prepare_generation",
    "_sanitize_component_name",
    "validate_generation_output",
    "verify_trace_completeness",
    "write_generation_manifest",
]

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capabilities import build_capability_profile
from .stacks import get_adapter


# ---------------------------------------------------------------------------
# Governance instructions bundling
# ---------------------------------------------------------------------------

def _resolve_bundle_root() -> Path:
    """Resolve the governance bundle directory.

    In development: signalos_lib/_bundle/ (same tree).
    In frozen binary (--onefile): PyInstaller extracts data to
    sys._MEIPASS temp dir; signalos_lib/_bundle/ lives there.
    """
    import sys
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        frozen_path = Path(sys._MEIPASS) / "signalos_lib" / "_bundle"
        if frozen_path.is_dir():
            return frozen_path
    return Path(__file__).resolve().parent.parent / "_bundle"


_BUNDLE_ROOT = _resolve_bundle_root()

_GOVERNANCE_EXTENSIONS = {".md", ".mdc", ".yaml", ".yml"}

# Core files EVERY agent gets (constitution + enforcement only)
_CORE_GOVERNANCE = [
    "core/governance/Governance/CONSTITUTION.md",
    "core/governance/ENFORCEMENT.md",
    "core/governance/QUALITY_CHECK.md",
    "core/execution/templates/trust-tier-declaration-template.md",
]

# Per-agent-role governance mapping — orchestrator selects the right subset
_AGENT_GOVERNANCE: dict[str, list[str]] = {
    "build": [
        "core/execution/agents/build.md",
        "core/execution/templates/typescript-standards.md",
        "core/execution/build/scope-implement/references/security-checklist.md",
        "core/execution/build/test-generation/SKILL.md",
        "core/execution/build/test-generation/references/test-patterns.md",
        "core/execution/build/test-generation/references/test-type-matrix.md",
    ],
    "test": [
        "core/execution/agents/test.md",
        "core/execution/build/test-generation/SKILL.md",
        "core/execution/build/test-generation/references/test-patterns.md",
        "core/execution/build/test-generation/references/test-type-matrix.md",
    ],
    "design": [
        "core/execution/agents/design.md",
    ],
    "review": [
        "core/execution/agents/review.md",
        "core/execution/review/comprehensive-code-review/references/security-review.md",
    ],
    "security": [
        "core/execution/agents/security.md",
        "core/execution/review/comprehensive-code-review/references/security-review.md",
        "core/execution/build/scope-implement/references/security-checklist.md",
    ],
}


def collect_governance_instructions(
    agent_role: str = "build",
    extra_contexts: list[str] | None = None,
) -> dict[str, str]:
    """Collect governance files relevant to a specific agent role.

    The orchestrator selects WHICH governance the agent needs:
    - Core (constitution, enforcement, quality gate) — always
    - Agent contract + role-specific references — per role
    - Extra contexts (security, compliance) — when intent requires

    Does NOT dump 287 files.  Agents get what they need for their task.
    The full library is enforced at VALIDATION time, not at generation time.
    """
    files_to_load = list(_CORE_GOVERNANCE)

    # Add role-specific files
    role_files = _AGENT_GOVERNANCE.get(agent_role, _AGENT_GOVERNANCE["build"])
    files_to_load.extend(role_files)

    # Add extra context files
    if extra_contexts:
        for ctx in extra_contexts:
            ctx_files = _AGENT_GOVERNANCE.get(ctx, [])
            files_to_load.extend(ctx_files)

    # Deduplicate
    files_to_load = list(dict.fromkeys(files_to_load))

    instructions: dict[str, str] = {}
    for rel in files_to_load:
        path = _BUNDLE_ROOT / rel
        if path.is_file():
            try:
                instructions[rel] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                pass
    return instructions


# ---------------------------------------------------------------------------
# Reserved paths - generation must never write inside these
# ---------------------------------------------------------------------------

_RESERVED_PREFIXES = (
    ".signalos/",
    ".signalos\\",
    "node_modules/",
    "node_modules\\",
    ".git/",
    ".git\\",
)


def _is_reserved(rel_path: str) -> bool:
    normed = rel_path.replace("\\", "/")
    for prefix in (".signalos/", "node_modules/", ".git/"):
        if normed.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# SHA-256 helper
# ---------------------------------------------------------------------------

def compute_sha256_lf(content: str) -> str:
    """Compute SHA-256 of LF-normalized content."""
    normalised = content.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_snake(name: str) -> str:
    """Convert CamelCase to snake_case."""
    result: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            result.append("_")
        result.append(ch.lower())
    return "".join(result)


def _to_pascal(name: str) -> str:
    """Convert snake_case, kebab-case, or space-separated to PascalCase."""
    return _to_pascal_case(name)


def _to_pascal_case(name: str) -> str:
    """Convert any entity/workflow name to a PascalCase, path-safe token.

    Splits on ANY non-alphanumeric character (spaces, hyphens, underscores,
    AND illegal path characters like ';', ':', '*', '?') so a stray workflow
    phrase such as "categorize; see running total" can never produce an illegal
    file path (Fix #25: 'src/components/Category;SeeRunningTotal.tsx'). Any
    leading digits (illegal at the start of a JS identifier) are dropped.
    Already-PascalCase names pass through unchanged.

    Examples:
        "patient intake"              -> "PatientIntake"
        "clinical notes"              -> "ClinicalNotes"
        "lab results"                 -> "LabResults"
        "Task"                        -> "Task"
        "revenue-chart"               -> "RevenueChart"
        "some_thing"                  -> "SomeThing"
        "Category;See Running Total"  -> "CategorySeeRunningTotal"
    """
    # Split on every run of non-alphanumeric characters. This turns illegal
    # path/identifier characters into word boundaries rather than letting them
    # survive into a filename.
    words = re.split(r"[^0-9A-Za-z]+", name.strip())
    # Upper-case the first letter of each word; preserve the rest so that
    # already-PascalCase tokens like "ClinicalNote" are not flattened.
    pascal = "".join(
        (word[0].upper() + word[1:]) if word else "" for word in words if word
    )
    # A component identifier may not start with a digit.
    pascal = pascal.lstrip("0123456789")
    return pascal


def _sanitize_component_name(name: str) -> str:
    """Collapse any entity/workflow phrase to a valid PascalCase component name.

    Strips ';' and every other illegal path/identifier character (Fix #25) so
    the derived ``<name>.tsx`` file spec is always a legal path. Delegates to
    ``_to_pascal_case`` (the single normalization point) so callers cannot
    drift from it.
    """
    return _to_pascal_case(name)


def _clean_contract(value: Any) -> Any:
    """Return a JSON-safe contract copy with empty noise removed."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            cleaned[str(key)] = _clean_contract(item)
        return cleaned
    if isinstance(value, list):
        return [_clean_contract(item) for item in value if item not in (None, "")]
    return value


def _build_generation_contracts(
    *,
    arch_review: dict | None,
    design_decisions: dict | None,
    scope_decisions: dict | None,
) -> dict[str, Any]:
    """Build the binding contract bundle consumed by generation.

    These artifacts are authored and validated before generation.  Passing them
    into the packet prevents architecture/design/scope gates from becoming
    signed side documents that code generation ignores.
    """
    contracts: dict[str, Any] = {
        "source_artifacts": {},
        "binding_rules": [],
    }
    if isinstance(arch_review, dict):
        contracts["source_artifacts"]["architecture"] = "ARCH_REVIEW.yaml"
        contracts["architecture"] = _clean_contract(arch_review)
        contracts["binding_rules"].append(
            "Generated files must honor ARCH_REVIEW.yaml system boundaries, "
            "data flow, trust boundaries, edge cases, and test strategy."
        )
    if isinstance(design_decisions, dict):
        contracts["source_artifacts"]["design"] = "DESIGN_DECISIONS.yaml"
        contracts["design_decisions"] = _clean_contract(design_decisions)
        contracts["binding_rules"].append(
            "Generated UI must honor DESIGN_DECISIONS.yaml selected_variant, "
            "selection_reason, and accepted taste findings."
        )
    if isinstance(scope_decisions, dict):
        contracts["source_artifacts"]["scope"] = "SCOPE_DECISIONS.yaml"
        contracts["scope_decisions"] = _clean_contract(scope_decisions)
        contracts["binding_rules"].append(
            "Generated scope must implement accepted SCOPE_DECISIONS.yaml items "
            "and must not add rejected or deferred scope as built functionality."
        )
    if not contracts["source_artifacts"]:
        return {}
    return contracts


def _generation_contract_constraints(contracts: dict[str, Any]) -> list[str]:
    if not contracts:
        return []
    constraints = [
        "Treat generation_contracts as binding signed product contracts.",
    ]
    if "architecture" in contracts:
        constraints.append(
            "Obey ARCH_REVIEW.yaml architecture: boundaries, data flow, trust "
            "boundaries, edge cases, and test strategy are binding."
        )
    if "design_decisions" in contracts:
        constraints.append(
            "Obey DESIGN_DECISIONS.yaml: selected variant and accepted taste "
            "findings are binding UI decisions."
        )
    if "scope_decisions" in contracts:
        constraints.append(
            "Obey SCOPE_DECISIONS.yaml: implement accepted scope only; do not "
            "quietly add rejected/deferred scope."
        )
    return constraints


def _apply_design_decision_constraints(
    design_constraints: dict[str, Any],
    design_decisions: dict | None,
) -> None:
    if not isinstance(design_decisions, dict):
        return
    selected = design_decisions.get("selected_variant")
    if selected:
        design_constraints["selected_variant"] = str(selected)
    reason = design_decisions.get("selection_reason")
    if reason:
        design_constraints["selection_reason"] = str(reason)
    findings = []
    for finding in design_decisions.get("taste_findings", []) or []:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("disposition", "")).lower() != "accepted":
            continue
        label = finding.get("finding") or finding.get("summary") or finding.get("id")
        if label:
            findings.append(str(label))
    if findings:
        design_constraints["accepted_taste_findings"] = findings


# ---------------------------------------------------------------------------
# Blueprint -> component mapping (data-driven)
# ---------------------------------------------------------------------------


def _derive_components_from_blueprint(blueprint: dict) -> list[dict[str, str]]:
    """Derive component specs from blueprint ui_detail data.

    Reads the ``ui_detail`` key (merged from ui.json by the registry loader)
    and builds a component spec for each surface.  Falls back to the top-level
    ``ui`` list of surface IDs when ``ui_detail`` is absent.

    Each returned dict has:
        component  - PascalCase component name
        entity_name - best-guess entity (from surface data or first entity)
        surface    - the surface id
    """
    specs: list[dict[str, str]] = []

    ui_detail = blueprint.get("ui_detail", {})
    surfaces = ui_detail.get("surfaces", []) if isinstance(ui_detail, dict) else []

    if surfaces:
        for surface in surfaces:
            # Sanitize even an explicit blueprint `component` value so an
            # illegal path character can never reach a file spec (Fix #25).
            comp_name = _sanitize_component_name(
                surface.get("component") or surface.get("id", "")
            )
            surface_id = surface.get("id", "")
            # Try to infer entity from surface metadata or from surface id
            entity_name = surface.get("entity", "")
            if not entity_name:
                # Infer from component name / data_bindings heuristic
                entity_name = _infer_entity_for_surface(surface, blueprint)
            specs.append({
                "component": comp_name,
                "entity_name": entity_name,
                "surface": surface_id,
            })
    else:
        # Fallback: use the top-level ui list of surface IDs
        ui_list = blueprint.get("ui", [])
        for surface_id in ui_list:
            if isinstance(surface_id, str):
                comp_name = _to_pascal_case(surface_id)
                specs.append({
                    "component": comp_name,
                    "entity_name": "",
                    "surface": surface_id,
                })

    return specs


def _infer_entity_for_surface(surface: dict, blueprint: dict) -> str:
    """Best-effort entity inference for a UI surface.

    Checks data_bindings for entity-like path segments, then falls back
    to the first entity in the blueprint.
    """
    entities = blueprint.get("entities", [])
    entity_names_lower = {
        e["name"].lower(): e["name"] for e in entities if isinstance(e, dict)
    }

    # Check data_bindings for entity references like "GET /tasks"
    for binding in surface.get("data_bindings", []):
        # Extract path segments: "GET /tasks/:id" -> ["tasks"]
        parts = binding.split()
        if len(parts) >= 2:
            path_parts = [
                seg for seg in parts[1].strip("/").split("/")
                if not seg.startswith(":")
            ]
            for seg in path_parts:
                # Try singular and as-is
                for candidate in (seg, seg.rstrip("s")):
                    if candidate.lower() in entity_names_lower:
                        return entity_names_lower[candidate.lower()]

    # Fallback: first entity
    if entities and isinstance(entities[0], dict):
        return entities[0].get("name", "")
    return ""


def _entity_by_name(
    entities: list[dict], name: str
) -> dict | None:
    for e in entities:
        if e.get("name") == name:
            return e
    return None


def _resolve_entities_to_gen(
    bp_entities: list[dict], intent: dict
) -> list[dict]:
    """Resolve the entity list a builder should generate code for.

    Behavior-preserving extraction of the entity-resolution preamble shared
    by the API ``_build_*_file_specs`` builders: prefer blueprint entities,
    fall back to PascalCased intent entities, and default to a single
    ``ProductResource`` entity when neither is present.
    """
    entities_to_gen = (
        bp_entities
        if bp_entities
        else [{"name": _to_pascal(e), "fields": []} for e in intent.get("entities", [])]
    )
    if not entities_to_gen:
        entities_to_gen = [{"name": "ProductResource", "fields": []}]
    return entities_to_gen


# ---------------------------------------------------------------------------
# Public: build_generation_packet
# ---------------------------------------------------------------------------

def build_generation_packet(
    repo_root: Path,
    intent: dict,
    blueprint: dict | None,
    profile: str,
    design: dict | None = None,
    arch_review: dict | None = None,
    design_decisions: dict | None = None,
    scope_decisions: dict | None = None,
    wave: str = "1",
    task_ids: list[str] | None = None,
    acceptance_matrix: dict | None = None,
) -> dict:
    """Build the generation packet that tells the agent what to create.

    SignalOS does NOT write the code. It defines:
    - What files need to exist (from blueprint/intent)
    - What each file should contain (acceptance criteria)
    - Where files go (from adapter resolve_targets)
    - Design constraints (UI library, tokens, conventions)
    - What tests must pass
    - What paths are forbidden

    Returns:
    {
        "schema_version": "signalos.generation_packet.v1",
        "product": str,
        "profile": str,
        "blueprint_id": str | None,
        "wave": str,
        "design": dict | None,
        "file_specs": [
            {
                "path": str,
                "kind": "test" | "source" | "config" | "registration",
                "description": str,
                "entity": str | None,
                "acceptance_id": str | None,
                "task_id": str | None,
                "constraints": list[str],
            },
            ...
        ],
        "entities": list[dict],
        "workflows": list[dict],
        "acceptance_criteria": list[dict],
        "design_constraints": {
            "ui_library": str,
            "state_management": str,
            "data_layer": str,
            "form_handling": str,
            "design_tokens": dict,
            "conventions": list[str],
        },
        "allowed_paths": list[str],
        "forbidden_paths": list[str],
        "validation_commands": list[str],
    }
    """
    if task_ids is None:
        task_ids = []

    adapter = get_adapter(profile)
    targets = adapter.resolve_targets(repo_root)
    val_plan = adapter.validation_plan(repo_root)
    validation_commands = _flatten_validation(val_plan)

    product_name = intent.get("product_name", "")
    blueprint_id = blueprint.get("id") if blueprint else None

    # Build file specs from blueprint or intent
    file_specs: list[dict[str, Any]] = []
    react_vite_meta: dict[str, Any] = {}
    if profile == "react-vite":
        file_specs = _build_react_vite_file_specs(
            intent, blueprint, targets, design,
        )
        react_vite_meta = _REACT_VITE_META_CACHE.pop(id(file_specs), {})
    elif profile == "nextjs-app":
        file_specs = _build_nextjs_file_specs(
            intent, blueprint, targets, design,
        )
    elif profile == "vue-vite":
        file_specs = _build_vue_vite_file_specs(
            intent, blueprint, targets, design,
        )
    elif profile == "flutter-app":
        file_specs = _build_flutter_app_file_specs(
            intent, blueprint, targets, design,
        )
    elif profile == "expo-react-native":
        file_specs = _build_expo_react_native_file_specs(
            intent, blueprint, targets, design,
        )
    elif profile == "node-api":
        file_specs = _build_node_api_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "nestjs-api":
        file_specs = _build_nestjs_api_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "fastapi-api":
        file_specs = _build_fastapi_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "django-api":
        file_specs = _build_django_api_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "flask-api":
        file_specs = _build_flask_api_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "go-api":
        file_specs = _build_go_api_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "java-api":
        file_specs = _build_java_api_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "spring-boot-api":
        file_specs = _build_spring_boot_api_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "rust-api":
        file_specs = _build_rust_api_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "dotnet-minimal-api":
        file_specs = _build_dotnet_minimal_api_file_specs(
            intent, blueprint, targets,
        )
    elif profile == "angular":
        file_specs = _build_angular_file_specs(
            intent, blueprint, targets, design,
        )
    elif profile == "agent-selected":
        file_specs = _build_agent_selected_file_specs(
            intent, blueprint, targets,
        )
    else:
        file_specs = _build_generic_file_specs(
            intent, blueprint, targets,
        )

    generation_contracts = _build_generation_contracts(
        arch_review=arch_review,
        design_decisions=design_decisions,
        scope_decisions=scope_decisions,
    )
    contract_constraints = _generation_contract_constraints(generation_contracts)
    if contract_constraints:
        for spec in file_specs:
            constraints = list(spec.get("constraints") or [])
            for item in contract_constraints:
                if item not in constraints:
                    constraints.append(item)
            spec["constraints"] = constraints

    # Assign task_ids round-robin if provided
    if task_ids:
        for i, spec in enumerate(file_specs):
            spec["task_id"] = task_ids[i % len(task_ids)]

    # Link to acceptance criteria if matrix provided
    if acceptance_matrix is not None:
        _link_specs_to_acceptance(file_specs, acceptance_matrix)

    # Extract entities and workflows
    bp_entities = (blueprint or {}).get("entities", [])
    bp_workflows = (blueprint or {}).get("workflows", [])
    if not bp_entities:
        bp_entities = [
            {"name": _to_pascal(e), "fields": []}
            for e in intent.get("entities", [])
        ]
    if not bp_workflows:
        bp_workflows = [
            {"name": w} for w in intent.get("primary_workflows", [])
        ]

    # Design constraints
    design_constraints = _extract_design_constraints(design)
    _apply_design_decision_constraints(design_constraints, design_decisions)
    capability_profile = build_capability_profile(
        intent,
        adapter_profile=profile,
    )

    # Allowed / forbidden paths
    source_base = targets.get("source", "src")
    test_base = targets.get("tests", source_base)
    allowed_paths = [f"{source_base}/**", f"{test_base}/**"]
    if source_base != test_base:
        allowed_paths.append(f"{test_base}/**")
    # Deduplicate
    allowed_paths = list(dict.fromkeys(allowed_paths))

    forbidden_paths = [".signalos/", "node_modules/", ".git/",
                       ".env", ".env.local", "*.pem", "*.key"]

    # Acceptance criteria
    acceptance_criteria = (acceptance_matrix or {}).get("criteria", [])

    return {
        "schema_version": "signalos.generation_packet.v1",
        "product": product_name,
        "profile": profile,
        "blueprint_id": blueprint_id,
        "wave": wave,
        "design": design,
        "file_specs": file_specs,
        "entities": bp_entities,
        "workflows": bp_workflows,
        "acceptance_criteria": acceptance_criteria,
        "design_constraints": design_constraints,
        "generation_contracts": generation_contracts,
        # Fix #12: authoritative cross-file contract for the per-file dispatcher.
        "component_manifest": react_vite_meta.get("component_manifest", []),
        "types_module_names": react_vite_meta.get("types_module_names", []),
        "entity_field_map": react_vite_meta.get("entity_field_map", {}),
        "capability_profile": capability_profile,
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "validation_commands": validation_commands,
        "governance_instructions": collect_governance_instructions(
            agent_role="build",
            extra_contexts=(
                ["security"] if intent.get("security_constraints") else None
            ),
        ),
        "governance_enforcement": {
            "mode": "strict",
            "constitution_required": True,
            "trust_tier_ceiling": "T2",
            "permanently_t3": [
                "auth", "payments", "migrations", "secrets",
                "infrastructure-as-code", "constitution",
            ],
            "refusal_on_violation": True,
            "validators": [
                "gate-signature-guard",
                "trust-tier-guard",
                "artifact-shape-guard",
                "path-consistency-guard",
            ],
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Internal: build file specs for react-vite
# ---------------------------------------------------------------------------

def _build_react_vite_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
    design: dict | None = None,
) -> list[dict[str, Any]]:
    """Build file spec list for a react-vite profile."""
    file_specs: list[dict[str, Any]] = []
    component_names: list[str] = []
    source_base = targets.get("source", "src")

    bp_entities = (blueprint or {}).get("entities", [])

    ui_name = (design or {}).get("ui_library", {}).get("name", "")
    state_name = (design or {}).get("state_management", {}).get("name", "")
    form_name = (design or {}).get("form_handling", {}).get("name", "")

    # Build constraints list based on design
    base_constraints = []
    if ui_name:
        base_constraints.append(f"Import UI components from {ui_name}")
    if state_name:
        base_constraints.append(f"Use {state_name} for state management")
    if form_name and form_name != "native":
        base_constraints.append(f"Use {form_name} for form inputs")
    base_constraints.append("Follow PascalCase naming")
    base_constraints.append("Co-locate test file as <Component>.test.tsx")

    # Shared product UI infrastructure.  These are implementation files the
    # agent owns inside src/, distinct from governance evidence in .signalos/.
    ui_specs = [
        {
            "path": f"{source_base}/ui/theme.ts",
            "kind": "config",
            "description": "Shared design tokens used by generated product UI.",
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Export a theme object",
                "Follow the selected design system instructions from the packet",
                # Fix #12/E: keep `tsc --strict` (noUnusedParameters) green.
                "This file MUST typecheck under `tsc --strict` with "
                "noUnusedLocals/noUnusedParameters -- no unused imports, "
                "variables, or function parameters.",
                "If the UI library is Mantine, every entry in a "
                "MantineColorsTuple `colors` map MUST be a readonly 10-string "
                "tuple, e.g. `['#f0f','#e0e', ... 10 shades]` (or cast with "
                "`as const`/`as MantineColorsTuple`). Never assign a plain "
                "string[] to a Mantine colors key.",
            ],
        },
        {
            "path": f"{source_base}/ui/index.ts",
            "kind": "config",
            "description": "Barrel exports for generated product UI helpers.",
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": ["Export from ./theme"],
        },
        {
            "path": f"{source_base}/ui/layouts/AppLayout.tsx",
            "kind": "config",
            "description": "Reusable application layout for generated product screens.",
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": ["Accept children and title props", "Use semantic layout elements"],
        },
        {
            "path": f"{source_base}/ui/layouts/PageLayout.tsx",
            "kind": "config",
            "description": "Reusable page layout for generated product sections.",
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": ["Accept children and title props", "Use semantic section elements"],
        },
        {
            "path": f"{source_base}/product.css",
            "kind": "config",
            "description": "Product stylesheet for generated screens and proof-visible UI.",
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Stay compatible with the selected design system",
                "Responsive layout",
            ],
        },
    ]
    for spec in ui_specs:
        if not _is_reserved(spec["path"]):
            file_specs.append(spec)

    # Fix #12/D: a vitest setup file that registers @testing-library/jest-dom
    # matchers (toBeInTheDocument/toBeChecked). Referenced by vite.config's
    # setupFiles (see stacks._VITE_CONFIG) so tests typecheck AND run.
    setup_path = f"{source_base}/test/setup.ts"
    if not _is_reserved(setup_path):
        file_specs.append({
            "path": setup_path,
            "kind": "config",
            "description": (
                "Vitest setup file. Import '@testing-library/jest-dom' so its "
                "custom matchers (toBeInTheDocument, toBeChecked, etc.) are "
                "registered for every test. Referenced by vite.config setupFiles."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Import '@testing-library/jest-dom' (side-effect import)",
                "Keep it minimal -- matcher registration only",
            ],
        })

    # Fix #12/A+B+C: resolve the authoritative shared contract ONCE.
    #   - types_module_names: the exact exported interface names (from entities)
    #   - entity_field_map: entity -> exact field names (source AND test agree)
    #   - component_manifest: {filePath, componentName, importPath} for App.tsx
    entities_for_types = _resolve_entities_to_gen(bp_entities, intent)
    types_module_names = [
        _to_pascal(e.get("name", "")) for e in entities_for_types if e.get("name")
    ]
    entity_field_map: dict[str, list[str]] = {}
    for e in entities_for_types:
        nm = e.get("name")
        if nm:
            entity_field_map[str(nm)] = [str(f) for f in (e.get("fields") or [])]
    component_manifest: list[dict[str, str]] = []

    def _fields_desc_for(entity_name: str, entity: dict | None) -> str:
        fields = (entity or {}).get("fields") or entity_field_map.get(entity_name, [])
        if fields:
            return (
                f" Use these EXACT entity field names (do not rename): "
                f"{', '.join(str(f) for f in fields)}."
            )
        return ""

    def _record_component(comp_name: str) -> None:
        component_manifest.append({
            "filePath": f"{source_base}/components/{comp_name}.tsx",
            "componentName": comp_name,
            "importPath": f"./components/{comp_name}",
        })

    # Data-driven: derive component list from blueprint ui data
    component_specs = (
        _derive_components_from_blueprint(blueprint) if blueprint else []
    )

    if component_specs:
        for spec in component_specs:
            comp_name = spec["component"]
            entity_name = spec["entity_name"]
            entity = _entity_by_name(bp_entities, entity_name)
            component_names.append(comp_name)

            test_path = f"{source_base}/components/{comp_name}.test.tsx"
            source_path = f"{source_base}/components/{comp_name}.tsx"

            if _is_reserved(test_path) or _is_reserved(source_path):
                continue

            _record_component(comp_name)
            fields_desc = _fields_desc_for(entity_name, entity)
            surface_desc = spec.get("surface", "")
            entity_ref = entity_name or comp_name

            # TDD: test first. The test imports the REAL component under test
            # (default export from ./<comp>) and uses the SAME exact fields as
            # the source -- no inventing sub-components or store paths.
            file_specs.append({
                "path": test_path,
                "kind": "test",
                "description": (
                    f"Vitest test file for {comp_name}. "
                    f"Import the {comp_name} default export from ./{comp_name} "
                    f"(the real generated component -- do NOT import sibling "
                    f"sub-components or stores that are not in the file_specs)."
                    f"{fields_desc} "
                    f"Tests rendering, user interactions, and data display."
                ),
                "entity": entity_name or None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use @testing-library/react for rendering",
                    "Use vitest describe/it/expect",
                    f"Import {comp_name} from './{comp_name}' (default export)",
                    "Do not import modules or sub-components not in file_specs",
                    "Test renders without crashing",
                    "Test displays expected content",
                ],
            })
            file_specs.append({
                "path": source_path,
                "kind": "source",
                "description": (
                    f"React component named {comp_name} (default export). "
                    f"For {entity_ref}. Surface: {surface_desc}.{fields_desc} "
                    f"Type against src/types.ts. "
                    f"Uses design tokens from src/ui/theme.ts."
                ),
                "entity": entity_name or None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": list(base_constraints) + [
                    f"Export a default React component named {comp_name}",
                ],
            })
    else:
        # No blueprint - generate from intent entities
        for ent_name in intent.get("entities", []):
            pascal = _to_pascal(ent_name)
            component_names.append(pascal)

            test_path = f"{source_base}/components/{pascal}.test.tsx"
            source_path = f"{source_base}/components/{pascal}.tsx"

            if _is_reserved(test_path) or _is_reserved(source_path):
                continue

            _record_component(pascal)
            entity = _entity_by_name(bp_entities, pascal) or _entity_by_name(
                bp_entities, ent_name
            )
            fields_desc = _fields_desc_for(pascal, entity)

            file_specs.append({
                "path": test_path,
                "kind": "test",
                "description": (
                    f"Vitest test file for {pascal}. "
                    f"Import the {pascal} default export from ./{pascal} "
                    f"(the real generated component -- do NOT import sibling "
                    f"sub-components or stores that are not in the file_specs)."
                    f"{fields_desc} "
                    f"Tests rendering, user interactions, and data display."
                ),
                "entity": ent_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use @testing-library/react for rendering",
                    "Use vitest describe/it/expect",
                    f"Import {pascal} from './{pascal}' (default export)",
                    "Do not import modules or sub-components not in file_specs",
                    "Test renders without crashing",
                    "Test displays expected content",
                ],
            })
            file_specs.append({
                "path": source_path,
                "kind": "source",
                "description": (
                    f"React component named {pascal} (default export) for the "
                    f"{pascal} entity.{fields_desc} "
                    f"Renders with CRUD operations. Type against src/types.ts. "
                    f"Uses design tokens from src/ui/theme.ts."
                ),
                "entity": ent_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": list(base_constraints) + [
                    f"Export a default React component named {pascal}",
                ],
            })

    # Fix #12/A: types.ts is a MANDATORY foundation file for react-vite --
    # ALWAYS emitted (even without blueprint entities) so components that
    # import '../types' / 'src/types' resolve (no TS2307). It names the exact
    # interfaces every component/test must import.
    types_path = f"{source_base}/types.ts"
    if not _is_reserved(types_path):
        entity_names = types_module_names or ["ProductRecord"]
        file_specs.append({
            "path": types_path,
            "kind": "config",
            "description": (
                f"TypeScript type definitions for entities: "
                f"{', '.join(entity_names)}. "
                f"Export ONE interface per entity with these EXACT names: "
                f"{', '.join(entity_names)}. Components and tests import these "
                f"from 'src/types' / '../types'."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Export one interface per entity",
                f"Interface names MUST be exactly: {', '.join(entity_names)}",
                "Use appropriate TypeScript types for each field",
            ],
        })

    # App registration
    if component_names:
        app_test_path = f"{source_base}/App.test.tsx"
        if not _is_reserved(app_test_path):
            file_specs.append({
                "path": app_test_path,
                "kind": "test",
                "description": (
                    "Vitest test file for the root App. Verifies the generated "
                    "product shell and primary product title render."
                ),
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use @testing-library/react for rendering",
                    "Assert the product title is visible",
                ],
            })
        app_path = f"{source_base}/App.tsx"
        if not _is_reserved(app_path):
            # Fix #12/B: App.tsx must import the EXACT generated components from
            # the canonical manifest -- no inventing ExpenseManager vs Expense.
            import_lines = "; ".join(
                f"import {m['componentName']} from '{m['importPath']}'"
                for m in component_manifest
            )
            file_specs.append({
                "path": app_path,
                "kind": "registration",
                "description": (
                    f"Root App component that imports and renders the real "
                    f"generated components using EXACTLY these imports: "
                    f"{import_lines}. Do not invent component names or import "
                    f"paths -- use the ones listed. Render every component in "
                    f"the JSX tree."
                ),
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Import each component from its exact path (see description)",
                    "Use the exact default-export component names listed",
                    "Render all components in the JSX tree",
                ],
            })

    # Fix #12/A+B+C: publish the authoritative cross-file contract so
    # build_generation_packet can lift it onto the packet without changing the
    # _build_*_file_specs return signature shared by every profile. Keyed by
    # the returned list's id; build_generation_packet pops it immediately.
    # Bounded so direct callers (tests) that never pop cannot grow it forever.
    if len(_REACT_VITE_META_CACHE) > 64:
        _REACT_VITE_META_CACHE.clear()
    _REACT_VITE_META_CACHE[id(file_specs)] = {
        "component_manifest": component_manifest,
        "types_module_names": types_module_names,
        "entity_field_map": entity_field_map,
    }
    return file_specs


# Cross-file contract computed alongside react-vite file_specs, keyed by the
# list's id so build_generation_packet can lift it onto the packet without
# changing the _build_*_file_specs return signature shared by every profile.
_REACT_VITE_META_CACHE: dict[int, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Internal: build file specs for agent-selected profile
# ---------------------------------------------------------------------------

def _build_agent_selected_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build technology-neutral file specs for agent-selected products."""
    source_base = targets.get("source", "src")
    test_base = targets.get("tests", "tests")
    product_type = intent.get("product_type") or (blueprint or {}).get("id", "custom")
    return [
        {
            "path": "PRODUCT_STACK.md",
            "kind": "config",
            "description": (
                "Technology decision record for the generated product. Must "
                "name the selected stack, runtime, package manager, database, "
                "cache, build command, test command, and preview command when applicable."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Honor explicit user technology preferences from the capability profile",
                "Do not choose .NET or ABP unless the user explicitly requested it",
                "Explain unsupported choices as blockers instead of silently changing technology",
            ],
        },
        {
            "path": "README.md",
            "kind": "config",
            "description": (
                f"Runbook for the {product_type} product, including local setup, "
                "environment variables, build, test, and preview instructions."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Commands must match real files written by the agent",
                "No production secrets or fake endpoints",
            ],
        },
        {
            "path": f"{source_base}/.keep",
            "kind": "config",
            "description": "Placeholder keeping the source root available for the agent-selected stack.",
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Agent may replace this with real framework source files inside allowed paths",
            ],
        },
        {
            "path": f"{test_base}/acceptance-map.md",
            "kind": "test",
            "description": (
                "Acceptance-to-test map for the chosen technology. Must list "
                "which real test files cover each acceptance criterion."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Reference real test files once generated",
                "Keep pending criteria explicit",
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Internal: build file specs for API profiles
# ---------------------------------------------------------------------------

def _build_dotnet_minimal_api_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the .NET Minimal API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "SignalOSProduct.Api")
    test_base = targets.get("tests", "tests")
    bp_entities = (blueprint or {}).get("entities", [])
    bp_workflows = (blueprint or {}).get("workflows", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    route_names: list[str] = []
    for entity in entities_to_gen:
        name = entity["name"]
        pascal = _to_pascal(name)
        snake = _to_snake(name)
        route_names.append(pascal)
        fields = [str(field) for field in entity.get("fields", [])]
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""

        model_rel = f"{source_base}/Models/{pascal}.cs".lstrip("/")
        route_rel = f"{source_base}/Routes/{pascal}Routes.cs".lstrip("/")
        test_rel = f"{test_base}/{snake}.http".lstrip("/")

        if not _is_reserved(model_rel):
            file_specs.append({
                "path": model_rel,
                "kind": "source",
                "description": (
                    f"C# record/model definitions for the {pascal} resource.{fields_desc} "
                    "Keep request and response contracts explicit."
                ),
                "entity": pascal,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use nullable-aware C# types",
                    "Separate create/update request shapes when behavior differs",
                    "Do not embed secrets or concrete production connection strings",
                ],
            })

        if not _is_reserved(route_rel):
            file_specs.append({
                "path": route_rel,
                "kind": "source",
                "description": (
                    f"Minimal API route extension methods for the {pascal} resource.{fields_desc} "
                    "Expose endpoints that can be mapped from ProductRoutes.Map."
                ),
                "entity": pascal,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use MapGet/MapPost/MapPut/MapDelete as appropriate",
                    "Return typed Results or JSON payloads",
                    "Keep persistence adapter-ready: in-memory is acceptable until a database adapter is configured",
                ],
            })

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": (
                    f"HTTP acceptance checks for the {pascal} Minimal API routes. "
                    "List health, list, create/update, and validation requests."
                ),
                "entity": pascal,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use localhost:5050 unless a proof runner supplies another base URL",
                    "Include expected HTTP status and JSON shape comments",
                    "Do not require a real database unless the capability profile explicitly provides one",
                ],
            })

    store_path = f"{source_base}/Stores/InMemoryStore.cs"
    if not _is_reserved(store_path):
        file_specs.append({
            "path": store_path,
            "kind": "config",
            "description": (
                "Small repository/store layer used by generated Minimal API routes. "
                "Keep it replaceable by PostgreSQL, SQL Server, MySQL, Redis, or another selected infrastructure adapter."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Export explicit read/write operations",
                "Keep infrastructure choices injectable",
                "Do not hardcode production secrets or connection strings",
            ],
        })

    product_routes = f"{source_base}/ProductRoutes.cs"
    if not _is_reserved(product_routes):
        file_specs.append({
            "path": product_routes,
            "kind": "registration",
            "description": (
                "Minimal API route registration. Preserve /health and import/map generated routes for: "
                + ", ".join(route_names)
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve the Health method used by --self-test",
                "Map generated route extension methods",
                "Keep /health responding with status=ok",
            ],
        })

    if bp_workflows:
        workflow_path = f"{source_base}/Workflows/ProductWorkflows.cs"
        if not _is_reserved(workflow_path):
            file_specs.append({
                "path": workflow_path,
                "kind": "source",
                "description": (
                    "Workflow orchestration helpers for Minimal API handlers: "
                    + ", ".join(str(workflow.get("name", workflow)) for workflow in bp_workflows)
                ),
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Keep workflow logic testable outside HTTP handlers",
                    "Do not call external services without injected adapters",
                ],
            })

    return file_specs


def _build_go_api_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the Go API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "internal/app")
    test_base = targets.get("tests", source_base)
    bp_entities = (blueprint or {}).get("entities", [])
    bp_workflows = (blueprint or {}).get("workflows", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    route_names: list[str] = []
    for entity in entities_to_gen:
        name = entity["name"]
        snake = _to_snake(name)
        route_names.append(snake)
        fields = [str(field) for field in entity.get("fields", [])]
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""

        test_rel = f"{test_base}/{snake}_test.go".lstrip("/")
        source_rel = f"{source_base}/{snake}.go".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": (
                    f"Go test coverage for the {name} API route. "
                    "Covers health, list, create/update behavior where applicable, validation errors, and JSON responses."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use testing and net/http/httptest",
                    "Exercise NewHandler on an in-memory recorder",
                    "Assert HTTP status codes and JSON payloads",
                    "Do not require a real database unless the capability profile explicitly provides one",
                ],
            })

        if not _is_reserved(source_rel):
            file_specs.append({
                "path": source_rel,
                "kind": "source",
                "description": (
                    f"Go HTTP handlers and route registration helpers for the {name} resource.{fields_desc} "
                    "Keep handlers mountable from NewHandler."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use only standard library net/http unless the stack decision adds a dependency",
                    "Validate request bodies before mutating state",
                    "Keep persistence adapter-ready: in-memory is acceptable until a database adapter is configured",
                ],
            })

    store_path = f"{source_base}/store.go"
    if not _is_reserved(store_path):
        file_specs.append({
            "path": store_path,
            "kind": "config",
            "description": (
                "Small repository/store layer used by generated Go API handlers. "
                "Keep it replaceable by PostgreSQL, SQL Server, MySQL, Redis, or another selected infrastructure adapter."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Export explicit read/write methods",
                "Keep infrastructure choices injectable",
                "Do not hardcode production secrets or connection strings",
            ],
        })

    app_path = f"{source_base}/app.go"
    if not _is_reserved(app_path):
        file_specs.append({
            "path": app_path,
            "kind": "registration",
            "description": (
                "Go API handler registration. Preserve /health and mount generated handlers for: "
                + ", ".join(route_names)
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve NewHandler and /health",
                "Use http.NewServeMux or a documented standard-library compatible router",
                "Keep handlers testable through httptest",
            ],
        })

    if bp_workflows:
        workflow_path = f"{source_base}/workflows.go"
        workflow_test_path = f"{test_base}/workflows_test.go"
        if not _is_reserved(workflow_test_path):
            file_specs.append({
                "path": workflow_test_path,
                "kind": "test",
                "description": "Go tests for workflow/service functions.",
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use testing",
                    "Cover at least one success and one invalid-input path",
                ],
            })
        if not _is_reserved(workflow_path):
            file_specs.append({
                "path": workflow_path,
                "kind": "source",
                "description": (
                    "Workflow/service functions for blueprint workflows: "
                    + ", ".join(str(workflow.get("name", workflow)) for workflow in bp_workflows)
                ),
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Keep business workflow logic separate from HTTP handlers",
                    "Export pure functions where practical",
                ],
            })

    return file_specs


def _build_rust_api_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the Rust API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src")
    test_base = targets.get("tests", "tests")
    bp_entities = (blueprint or {}).get("entities", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    modules: list[str] = []
    for entity in entities_to_gen:
        name = entity["name"]
        snake = _to_snake(name)
        modules.append(snake)
        fields = [str(field) for field in entity.get("fields", [])]
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""
        test_rel = f"{test_base}/{snake}_api.rs".lstrip("/")
        source_rel = f"{source_base}/{snake}.rs".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": (
                    f"Rust API tests for the {name} resource. Cover health, list, create/update behavior, "
                    "validation errors, and serialized JSON contract."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use cargo test",
                    "Keep tests deterministic without external services unless configured",
                    "Do not require a live database unless capability profile selects one",
                ],
            })

        if not _is_reserved(source_rel):
            file_specs.append({
                "path": source_rel,
                "kind": "source",
                "description": (
                    f"Rust module for {name} request/response types and handlers.{fields_desc} "
                    "Keep functions callable from lib.rs and main.rs."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Keep public functions testable without a network socket",
                    "Validate input before mutation",
                    "Keep persistence replaceable by selected infrastructure adapters",
                ],
            })

    lib_path = f"{source_base}/lib.rs"
    if not _is_reserved(lib_path):
        file_specs.append({
            "path": lib_path,
            "kind": "registration",
            "description": "Rust library module registration for generated API resources: " + ", ".join(modules),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve health_payload",
                "Expose generated modules",
                "Keep cargo test passing",
            ],
        })

    return file_specs


def _build_java_api_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the Java API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src/main/java/com/signalos/product")
    test_base = targets.get("tests", "src/test/java/com/signalos/product")
    bp_entities = (blueprint or {}).get("entities", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    resources: list[str] = []
    for entity in entities_to_gen:
        name = _to_pascal(entity["name"])
        resources.append(name)
        fields = [str(field) for field in entity.get("fields", [])]
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""
        source_rel = f"{source_base}/{name}Resource.java".lstrip("/")
        test_rel = f"{test_base}/{name}ResourceTest.java".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": (
                    f"Java tests for the {name} resource contract. Cover request validation, "
                    "happy path, and JSON response shape."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Keep tests runnable by the adapter validation command",
                    "Avoid external services unless the capability profile selects them",
                ],
            })

        if not _is_reserved(source_rel):
            file_specs.append({
                "path": source_rel,
                "kind": "source",
                "description": (
                    f"Java API resource/service for {name}.{fields_desc} "
                    "Keep it mountable from ProductServer."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use explicit request/response shapes",
                    "Keep business logic testable without the HTTP server",
                    "Keep persistence replaceable by selected infrastructure adapters",
                ],
            })

    server_path = f"{source_base}/ProductServer.java"
    if not _is_reserved(server_path):
        file_specs.append({
            "path": server_path,
            "kind": "registration",
            "description": "Java API route registration. Preserve /health and register resources: " + ", ".join(resources),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve healthPayload",
                "Register generated resource handlers",
                "Keep validation commands runnable without hidden setup",
            ],
        })

    return file_specs


def _build_spring_boot_api_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the Spring Boot API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src/main/java/com/signalos/product")
    test_base = targets.get("tests", "src/test/java/com/signalos/product")
    bp_entities = (blueprint or {}).get("entities", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    controllers: list[str] = []
    for entity in entities_to_gen:
        name = _to_pascal(entity["name"])
        package_segment = _to_snake(name).replace("_", "")
        controllers.append(name)
        fields = [str(field) for field in entity.get("fields", [])]
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""
        base = f"{source_base}/{package_segment}"
        test_dir = f"{test_base}/{package_segment}"
        dto_rel = f"{base}/{name}Dto.java".lstrip("/")
        service_rel = f"{base}/{name}Service.java".lstrip("/")
        controller_rel = f"{base}/{name}Controller.java".lstrip("/")
        test_rel = f"{test_dir}/{name}ControllerTest.java".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": (
                    f"JUnit coverage for the {name} Spring Boot controller/service contract. "
                    "Cover validation errors, happy path, and JSON response shape."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use JUnit 5 and Spring Boot test support where needed",
                    "Avoid live infrastructure unless the capability profile selects it",
                    "Keep tests runnable by mvn test",
                ],
            })
        for rel, desc in (
            (dto_rel, f"Spring Boot DTOs and validation helpers for {name}.{fields_desc}"),
            (service_rel, f"Spring service for {name} business behavior and repository boundary.{fields_desc}"),
            (controller_rel, f"Spring REST controller for the {name} API resource.{fields_desc}"),
        ):
            if not _is_reserved(rel):
                file_specs.append({
                    "path": rel,
                    "kind": "source",
                    "description": desc,
                    "entity": name,
                    "acceptance_id": None,
                    "task_id": None,
                    "constraints": [
                        "Use Spring Boot idioms without hiding infrastructure choices",
                        "Keep storage replaceable by selected adapters",
                        "Do not hardcode production secrets or connection strings",
                    ],
                })

    app_path = f"{source_base}/ProductApplication.java"
    if not _is_reserved(app_path):
        file_specs.append({
            "path": app_path,
            "kind": "registration",
            "description": "Spring Boot application registration. Preserve health endpoints and package scanning for: " + ", ".join(controllers),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve application startup",
                "Keep generated controllers under scanned packages",
                "Keep mvn test passing",
            ],
        })

    return file_specs


def _build_django_api_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the Django API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src/signalos_product_django")
    test_base = targets.get("tests", "tests")
    bp_entities = (blueprint or {}).get("entities", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    for init_rel in (
        f"{source_base}/product/__init__.py",
        f"{source_base}/product/models.py",
    ):
        if not _is_reserved(init_rel):
            file_specs.append({
                "path": init_rel,
                "kind": "config",
                "description": "Django app package marker/model registration file.",
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": ["Keep importable by Django"],
            })

    views: list[str] = []
    for entity in entities_to_gen:
        name = _to_pascal(entity["name"])
        snake = _to_snake(name)
        views.append(snake)
        fields = [str(field) for field in entity.get("fields", [])]
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""
        test_rel = f"{test_base}/test_{snake}.py".lstrip("/")
        view_rel = f"{source_base}/product/{snake}_views.py".lstrip("/")
        serializer_rel = f"{source_base}/product/{snake}_schemas.py".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": (
                    f"Pytest/Django Client coverage for the {name} API route. "
                    "Cover list, create/update behavior where applicable, validation errors, and JSON responses."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use django.test.Client",
                    "Assert HTTP status codes and JSON payloads",
                    "Do not require a real database unless capability profile selects one",
                ],
            })

        if not _is_reserved(serializer_rel):
            file_specs.append({
                "path": serializer_rel,
                "kind": "source",
                "description": (
                    f"Typed request/response helpers for the {name} Django API.{fields_desc} "
                    "Keep validation explicit even without a DRF dependency."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Avoid hidden framework dependencies not declared in pyproject.toml",
                    "Keep request validation explicit",
                ],
            })

        if not _is_reserved(view_rel):
            file_specs.append({
                "path": view_rel,
                "kind": "source",
                "description": (
                    f"Django view functions for the {name} API resource.{fields_desc} "
                    "Export URL patterns for registration."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use JsonResponse",
                    "Validate request bodies before mutation",
                    "Keep persistence adapter-ready",
                ],
            })

    urls_path = f"{source_base}/urls.py"
    if not _is_reserved(urls_path):
        file_specs.append({
            "path": urls_path,
            "kind": "registration",
            "description": "Django URL registration. Preserve /health and mount generated views for: " + ", ".join(views),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve health endpoint",
                "Register generated API routes",
                "Keep Django Client tests passing",
            ],
        })

    return file_specs


def _build_flask_api_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the Flask API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src/signalos_product_flask")
    test_base = targets.get("tests", "tests")
    bp_entities = (blueprint or {}).get("entities", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    for init_rel in (
        f"{source_base}/routes/__init__.py",
        f"{source_base}/store.py",
    ):
        if not _is_reserved(init_rel):
            file_specs.append({
                "path": init_rel,
                "kind": "config",
                "description": "Flask route/store package support file.",
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Keep imports lightweight",
                    "Keep infrastructure choices injectable",
                ],
            })

    route_modules: list[str] = []
    for entity in entities_to_gen:
        name = _to_pascal(entity["name"])
        snake = _to_snake(name)
        route_modules.append(snake)
        fields = [str(field) for field in entity.get("fields", [])]
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""
        test_rel = f"{test_base}/test_{snake}.py".lstrip("/")
        route_rel = f"{source_base}/routes/{snake}.py".lstrip("/")
        schema_rel = f"{source_base}/routes/{snake}_schemas.py".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": (
                    f"Pytest/Flask client coverage for the {name} API route. "
                    "Cover list, create/update behavior where applicable, validation errors, and JSON responses."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use create_app().test_client()",
                    "Assert HTTP status codes and JSON payloads",
                    "Do not require a real database unless the capability profile selects one",
                ],
            })
        if not _is_reserved(schema_rel):
            file_specs.append({
                "path": schema_rel,
                "kind": "source",
                "description": f"Explicit validation helpers for the {name} Flask route.{fields_desc}",
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Avoid undeclared framework dependencies",
                    "Keep request validation explicit",
                ],
            })
        if not _is_reserved(route_rel):
            file_specs.append({
                "path": route_rel,
                "kind": "source",
                "description": f"Flask Blueprint routes for the {name} API resource.{fields_desc}",
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use flask.Blueprint",
                    "Export the blueprint for app registration",
                    "Keep persistence adapter-ready",
                ],
            })

    app_path = f"{source_base}/app.py"
    if not _is_reserved(app_path):
        file_specs.append({
            "path": app_path,
            "kind": "registration",
            "description": "Flask app factory registration. Preserve /health and register blueprints: " + ", ".join(route_modules),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Keep create_app exported for tests and runtime",
                "Preserve /health",
                "Register generated route blueprints under /api/<resource>",
            ],
        })

    return file_specs


def _build_angular_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
    design: dict | None = None,
) -> list[dict[str, Any]]:
    """Build file spec list for the Angular profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src/app")
    components = _derive_components_from_blueprint(blueprint or {}) if blueprint else []
    if not components:
        entities = intent.get("entities", []) or ["Product"]
        components = [
            {
                "component": f"{_to_pascal(entity)}Component",
                "entity_name": _to_pascal(entity),
                "surface": _to_snake(str(entity)),
            }
            for entity in entities
        ]

    for component in components:
        component_name = component.get("component") or "ProductComponent"
        entity_name = component.get("entity_name") or component_name.replace("Component", "")
        folder = _to_snake(component_name.replace("Component", "")).replace("_", "-")
        class_name = component_name if component_name.endswith("Component") else f"{component_name}Component"
        spec_rel = f"{source_base}/{folder}/{folder}.component.spec.ts".lstrip("/")
        component_rel = f"{source_base}/{folder}/{folder}.component.ts".lstrip("/")
        template_rel = f"{source_base}/{folder}/{folder}.component.html".lstrip("/")
        style_rel = f"{source_base}/{folder}/{folder}.component.css".lstrip("/")

        if not _is_reserved(spec_rel):
            file_specs.append({
                "path": spec_rel,
                "kind": "test",
                "description": f"Angular component test for {class_name} covering the {entity_name} workflow.",
                "entity": entity_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use Angular TestBed",
                    "Assert rendered user-facing states and empty/loading/error states",
                    "Keep tests deterministic without live backend calls",
                ],
            })
        for rel, kind, desc in (
            (component_rel, "source", f"Standalone Angular component class for {entity_name}."),
            (template_rel, "source", f"Accessible Angular template for {entity_name}."),
            (style_rel, "source", f"Scoped Angular styles for {entity_name}."),
        ):
            if not _is_reserved(rel):
                file_specs.append({
                    "path": rel,
                    "kind": kind,
                    "description": desc,
                    "entity": entity_name,
                    "acceptance_id": None,
                    "task_id": None,
                    "constraints": [
                        "Use standalone Angular component conventions",
                        "Keep visible text concise and domain-specific",
                        "Do not hardcode production secrets or fake API keys",
                    ],
                })

    app_rel = f"{source_base}/app.component.ts"
    if not _is_reserved(app_rel):
        file_specs.append({
            "path": app_rel,
            "kind": "registration",
            "description": "Angular app shell registration that imports and renders generated standalone components.",
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve app bootstrap compatibility",
                "Import generated standalone components explicitly",
                "Keep npm run build and npm test passing",
            ],
        })

    return file_specs


def _build_nextjs_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
    design: dict | None = None,
) -> list[dict[str, Any]]:
    """Build file spec list for the Next.js App Router profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "app")
    components = _derive_components_from_blueprint(blueprint or {}) if blueprint else []
    if not components:
        entities = intent.get("entities", []) or ["Product"]
        components = [
            {
                "component": _to_pascal(entity),
                "entity_name": _to_pascal(entity),
                "surface": _to_snake(str(entity)),
            }
            for entity in entities
        ]

    for component in components:
        component_name = _to_pascal(component.get("component") or "ProductPanel")
        if component_name.endswith("Component"):
            component_name = component_name[:-9]
        entity_name = component.get("entity_name") or component_name
        test_rel = f"{source_base}/components/{component_name}.test.tsx".lstrip("/")
        source_rel = f"{source_base}/components/{component_name}.tsx".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": f"Next.js component test for {component_name} covering {entity_name} user states.",
                "entity": entity_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use Testing Library and Vitest",
                    "Cover empty/loading/error and primary success states",
                    "Do not require a live backend unless selected infrastructure is configured",
                ],
            })
        if not _is_reserved(source_rel):
            file_specs.append({
                "path": source_rel,
                "kind": "source",
                "description": f"Next.js UI component for {entity_name}, implemented as an accessible React component.",
                "entity": entity_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Keep component compatible with the App Router",
                    "Use explicit props and typed data shapes",
                    "Do not hardcode production secrets or fake API keys",
                ],
            })

    page_rel = f"{source_base}/page.tsx"
    if not _is_reserved(page_rel):
        file_specs.append({
            "path": page_rel,
            "kind": "registration",
            "description": "Next.js App Router page that imports and renders generated product components.",
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve route compatibility",
                "Import generated components explicitly",
                "Keep npm run build and npm test passing",
            ],
        })

    return file_specs


def _build_vue_vite_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
    design: dict | None = None,
) -> list[dict[str, Any]]:
    """Build file spec list for the Vue + Vite profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src")
    components = _derive_components_from_blueprint(blueprint or {}) if blueprint else []
    if not components:
        entities = intent.get("entities", []) or ["Product"]
        components = [
            {
                "component": _to_pascal(entity),
                "entity_name": _to_pascal(entity),
                "surface": _to_snake(str(entity)),
            }
            for entity in entities
        ]

    for component in components:
        component_name = _to_pascal(component.get("component") or "ProductPanel")
        if component_name.endswith("Component"):
            component_name = component_name[:-9]
        entity_name = component.get("entity_name") or component_name
        test_rel = f"{source_base}/components/{component_name}.spec.ts".lstrip("/")
        source_rel = f"{source_base}/components/{component_name}.vue".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": f"Vue component test for {component_name} covering {entity_name} user states.",
                "entity": entity_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use @vue/test-utils with Vitest",
                    "Assert rendered accessible states",
                    "Keep tests deterministic without live backend calls",
                ],
            })
        if not _is_reserved(source_rel):
            file_specs.append({
                "path": source_rel,
                "kind": "source",
                "description": f"Vue single-file component for {entity_name}.",
                "entity": entity_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use Vue 3 composition conventions",
                    "Keep props and emitted events explicit",
                    "Do not hardcode production secrets or fake API keys",
                ],
            })

    app_rel = f"{source_base}/App.vue"
    if not _is_reserved(app_rel):
        file_specs.append({
            "path": app_rel,
            "kind": "registration",
            "description": "Vue app shell that imports and renders generated product components.",
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve Vite bootstrap compatibility",
                "Import generated components explicitly",
                "Keep npm run build and npm test passing",
            ],
        })

    return file_specs


def _build_flutter_app_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
    design: dict | None = None,
) -> list[dict[str, Any]]:
    """Build file spec list for the Flutter mobile profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "lib")
    test_base = targets.get("tests", "test")
    components = _derive_components_from_blueprint(blueprint or {}) if blueprint else []
    if not components:
        entities = intent.get("entities", []) or ["Product"]
        components = [
            {
                "component": f"{_to_pascal(entity)}Screen",
                "entity_name": _to_pascal(entity),
                "surface": _to_snake(str(entity)),
            }
            for entity in entities
        ]

    screens: list[str] = []
    for component in components:
        base_name = _to_pascal(component.get("component") or "ProductScreen")
        if base_name.endswith("Component"):
            base_name = base_name[:-9] + "Screen"
        if not base_name.endswith("Screen"):
            base_name = f"{base_name}Screen"
        entity_name = component.get("entity_name") or base_name.replace("Screen", "")
        snake = _to_snake(base_name)
        screens.append(base_name)
        test_rel = f"{test_base}/{snake}_test.dart".lstrip("/")
        source_rel = f"{source_base}/screens/{snake}.dart".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": f"Flutter widget test for {base_name} covering {entity_name} mobile states.",
                "entity": entity_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use flutter_test",
                    "Cover empty/loading/error and primary success states",
                    "Keep tests deterministic without live backend calls",
                ],
            })
        if not _is_reserved(source_rel):
            file_specs.append({
                "path": source_rel,
                "kind": "source",
                "description": f"Flutter screen widget for {entity_name} with accessible mobile layout and state handling.",
                "entity": entity_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use Material widgets unless the design packet chooses otherwise",
                    "Keep state and data dependencies injectable",
                    "Do not hardcode production secrets or fake API keys",
                ],
            })

    app_rel = f"{source_base}/main.dart"
    if not _is_reserved(app_rel):
        file_specs.append({
            "path": app_rel,
            "kind": "registration",
            "description": "Flutter app registration that imports and routes generated screens: " + ", ".join(screens),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve runnable main() entry point",
                "Register generated screens explicitly",
                "Keep flutter analyze and flutter test passing",
            ],
        })

    return file_specs


def _build_expo_react_native_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
    design: dict | None = None,
) -> list[dict[str, Any]]:
    """Build file spec list for the Expo React Native mobile profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src")
    test_base = targets.get("tests", "tests")
    components = _derive_components_from_blueprint(blueprint or {}) if blueprint else []
    if not components:
        entities = intent.get("entities", []) or ["Product"]
        components = [
            {
                "component": f"{_to_pascal(entity)}Screen",
                "entity_name": _to_pascal(entity),
                "surface": _to_snake(str(entity)),
            }
            for entity in entities
        ]

    screens: list[str] = []
    for component in components:
        base_name = _to_pascal(component.get("component") or "ProductScreen")
        if base_name.endswith("Component"):
            base_name = base_name[:-9] + "Screen"
        if not base_name.endswith("Screen"):
            base_name = f"{base_name}Screen"
        entity_name = component.get("entity_name") or base_name.replace("Screen", "")
        snake = _to_snake(base_name)
        screens.append(base_name)
        test_rel = f"{test_base}/{snake}.test.js".lstrip("/")
        source_rel = f"{source_base}/screens/{base_name}.js".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": f"Node/React Native compatible tests for {base_name} behavior and data-state helpers.",
                "entity": entity_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use node:test for pure state helpers or declared React Native test utilities",
                    "Cover empty/loading/error and primary success states",
                    "Keep tests deterministic without live backend calls",
                ],
            })
        if not _is_reserved(source_rel):
            file_specs.append({
                "path": source_rel,
                "kind": "source",
                "description": f"Expo React Native screen for {entity_name} with accessible mobile layout and state handling.",
                "entity": entity_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use React Native components and Expo-compatible imports",
                    "Keep data dependencies injectable",
                    "Do not hardcode production secrets or fake API keys",
                ],
            })

    app_rel = "App.js"
    if not _is_reserved(app_rel):
        file_specs.append({
            "path": app_rel,
            "kind": "registration",
            "description": "Expo app registration that imports and renders generated screens: " + ", ".join(screens),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve Expo-compatible default export",
                "Register generated screens explicitly",
                "Keep npm run build and npm test passing",
            ],
        })

    return file_specs


def _build_fastapi_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the FastAPI API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src/signalos_product_fastapi")
    test_base = targets.get("tests", "tests")
    bp_entities = (blueprint or {}).get("entities", [])
    bp_workflows = (blueprint or {}).get("workflows", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    for init_rel in (
        f"{source_base}/routes/__init__.py",
        f"{source_base}/models/__init__.py",
    ):
        if not _is_reserved(init_rel):
            file_specs.append({
                "path": init_rel,
                "kind": "config",
                "description": "Package marker for generated FastAPI modules.",
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": ["Keep importable by Python"],
            })

    route_modules: list[str] = []
    for entity in entities_to_gen:
        name = entity["name"]
        snake = _to_snake(name)
        route_modules.append(snake)
        fields = [str(field) for field in entity.get("fields", [])]
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""

        test_rel = f"{test_base}/test_{snake}.py".lstrip("/")
        source_rel = f"{source_base}/routes/{snake}.py".lstrip("/")
        model_rel = f"{source_base}/models/{snake}.py".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": (
                    f"Pytest/FastAPI TestClient coverage for the {name} API route. "
                    "Covers list, create/update behavior where applicable, validation errors, and JSON responses."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use fastapi.testclient.TestClient",
                    "Import app from signalos_product_fastapi.app",
                    "Assert HTTP status codes and JSON payloads",
                    "Do not require a real database unless the capability profile explicitly provides one",
                ],
            })

        if not _is_reserved(model_rel):
            file_specs.append({
                "path": model_rel,
                "kind": "source",
                "description": (
                    f"Pydantic model definitions for the {name} resource.{fields_desc} "
                    "Keep request and response models explicit."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use pydantic BaseModel or FastAPI-compatible models",
                    "Define create/update request models separately when behavior differs",
                    "Keep fields typed and validation-oriented",
                ],
            })

        if not _is_reserved(source_rel):
            file_specs.append({
                "path": source_rel,
                "kind": "source",
                "description": (
                    f"FastAPI APIRouter module for the {name} resource.{fields_desc} "
                    "Export router for registration in app.py."
                ),
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use fastapi.APIRouter",
                    "Export router",
                    "Validate request bodies with typed models",
                    "Keep persistence adapter-ready: in-memory is acceptable until a database adapter is configured",
                ],
            })

    store_path = f"{source_base}/store.py"
    if not _is_reserved(store_path):
        file_specs.append({
            "path": store_path,
            "kind": "config",
            "description": (
                "Small repository/store layer used by generated FastAPI routes. "
                "Keep it replaceable by PostgreSQL, SQL Server, MySQL, Redis, or another selected infrastructure adapter."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Export explicit read/write functions",
                "Do not hardcode production secrets or connection strings",
                "Keep infrastructure choices injectable",
            ],
        })

    app_test_path = f"{test_base}/test_app.py"
    if not _is_reserved(app_test_path):
        file_specs.append({
            "path": app_test_path,
            "kind": "test",
            "description": (
                "Pytest file for root app registration. Verifies health and generated routes are mounted."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Use fastapi.testclient.TestClient",
                "Assert /health responds",
                "Assert at least one generated API route responds",
            ],
        })

    app_path = f"{source_base}/app.py"
    if not _is_reserved(app_path):
        file_specs.append({
            "path": app_path,
            "kind": "registration",
            "description": (
                "FastAPI app registration. Import generated routers and include "
                f"routes for: {', '.join(route_modules)}."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve /health",
                "Use app.include_router(router, prefix='/api/<resource>')",
                "Keep app exported for tests and uvicorn",
            ],
        })

    if bp_workflows:
        workflow_path = f"{source_base}/workflows.py"
        if not _is_reserved(workflow_path):
            file_specs.append({
                "path": workflow_path,
                "kind": "source",
                "description": (
                    "Workflow orchestration helpers for FastAPI handlers: "
                    + ", ".join(str(workflow.get("name", workflow)) for workflow in bp_workflows)
                ),
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Keep workflow logic testable outside HTTP handlers",
                    "Do not call external services without injected adapters",
                ],
            })

    return file_specs


def _build_node_api_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the Node.js API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src")
    test_base = targets.get("tests", "tests")
    bp_entities = (blueprint or {}).get("entities", [])
    bp_workflows = (blueprint or {}).get("workflows", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    route_names: list[str] = []
    for entity in entities_to_gen:
        name = entity["name"]
        snake = _to_snake(name)
        route_names.append(snake)
        fields = entity.get("fields", [])
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""

        test_rel = f"{test_base}/{snake}.test.js".lstrip("/")
        source_rel = f"{source_base}/routes/{snake}.js".lstrip("/")

        if _is_reserved(test_rel) or _is_reserved(source_rel):
            continue

        file_specs.append({
            "path": test_rel,
            "kind": "test",
            "description": (
                f"Node test file for the {name} API route. Covers list, "
                "create/update behavior where applicable, validation errors, "
                "and JSON responses."
            ),
            "entity": name,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Use node:test and node:assert/strict",
                "Start the Express app on an ephemeral port",
                "Assert HTTP status codes and JSON payloads",
                "Do not require a real database unless the capability profile explicitly provides one",
            ],
        })
        file_specs.append({
            "path": source_rel,
            "kind": "source",
            "description": (
                f"Express Router module for the {name} resource.{fields_desc} "
                "Export a router that can be mounted by src/app.js."
            ),
            "entity": name,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Use express.Router()",
                "Export the router as default",
                "Validate request bodies before mutating state",
                "Keep persistence adapter-ready: in-memory is acceptable until a database adapter is configured",
            ],
        })

    store_path = f"{source_base}/store.js"
    if not _is_reserved(store_path):
        file_specs.append({
            "path": store_path,
            "kind": "config",
            "description": (
                "Small repository/store layer used by generated API routes. "
                "Keep it replaceable by PostgreSQL, SQL Server, MySQL, Redis, "
                "or another selected infrastructure adapter."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Export explicit read/write functions",
                "Do not hardcode production secrets or connection strings",
                "Keep infrastructure choices injectable",
            ],
        })

    app_test_path = f"{test_base}/app.test.js"
    if not _is_reserved(app_test_path):
        file_specs.append({
            "path": app_test_path,
            "kind": "test",
            "description": (
                "Node test file for root app registration. Verifies health and "
                "generated routes are mounted."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Use node:test and node:assert/strict",
                "Assert /health responds",
                "Assert at least one generated API route responds",
            ],
        })

    app_path = f"{source_base}/app.js"
    if not _is_reserved(app_path):
        file_specs.append({
            "path": app_path,
            "kind": "registration",
            "description": (
                "Express app registration. Import generated routers and mount "
                f"routes for: {', '.join(route_names)}."
            ),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve /health",
                "Use app.use('/api/<resource>', router)",
                "Keep createApp exported for tests",
            ],
        })

    if bp_workflows:
        workflow_test_path = f"{test_base}/workflows.test.js"
        if not _is_reserved(workflow_test_path):
            file_specs.append({
                "path": workflow_test_path,
                "kind": "test",
                "description": "Node test file for workflow/service functions.",
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use node:test and node:assert/strict",
                    "Cover at least one success and one invalid-input path",
                ],
            })
        workflow_path = f"{source_base}/workflows.js"
        if not _is_reserved(workflow_path):
            file_specs.append({
                "path": workflow_path,
                "kind": "source",
                "description": (
                    "Workflow/service functions for blueprint workflows: "
                    + ", ".join(str(w.get("name", w)) for w in bp_workflows)
                ),
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Keep business workflow logic separate from route handlers",
                    "Export pure functions where practical",
                ],
            })

    return file_specs


def _build_nestjs_api_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the NestJS API profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "src")
    test_base = targets.get("tests", "src")
    bp_entities = (blueprint or {}).get("entities", [])
    bp_workflows = (blueprint or {}).get("workflows", [])

    entities_to_gen = _resolve_entities_to_gen(bp_entities, intent)

    modules: list[str] = []
    for entity in entities_to_gen:
        name = _to_pascal(entity["name"])
        kebab = _to_snake(name).replace("_", "-")
        modules.append(kebab)
        fields = [str(field) for field in entity.get("fields", [])]
        fields_desc = f" Fields: {', '.join(fields)}." if fields else ""
        test_rel = f"{test_base}/{kebab}/{kebab}.controller.spec.ts".lstrip("/")
        controller_rel = f"{source_base}/{kebab}/{kebab}.controller.ts".lstrip("/")
        service_rel = f"{source_base}/{kebab}/{kebab}.service.ts".lstrip("/")
        dto_rel = f"{source_base}/{kebab}/{kebab}.dto.ts".lstrip("/")

        if not _is_reserved(test_rel):
            file_specs.append({
                "path": test_rel,
                "kind": "test",
                "description": f"Vitest coverage for the {name} NestJS controller and service contract.",
                "entity": name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Instantiate controller/service directly or through Nest testing utilities",
                    "Cover validation errors and happy paths",
                    "Do not require live infrastructure unless selected",
                ],
            })
        for rel, desc in (
            (dto_rel, f"Typed DTOs and validation helpers for {name}.{fields_desc}"),
            (service_rel, f"NestJS service for {name} business behavior and persistence boundary.{fields_desc}"),
            (controller_rel, f"NestJS controller exposing the {name} API routes.{fields_desc}"),
        ):
            if not _is_reserved(rel):
                file_specs.append({
                    "path": rel,
                    "kind": "source",
                    "description": desc,
                    "entity": name,
                    "acceptance_id": None,
                    "task_id": None,
                    "constraints": [
                        "Use NestJS decorators and dependency injection",
                        "Keep storage replaceable by selected infrastructure adapters",
                        "Do not hardcode production secrets or connection strings",
                    ],
                })

    app_module = f"{source_base}/app.module.ts"
    if not _is_reserved(app_module):
        file_specs.append({
            "path": app_module,
            "kind": "registration",
            "description": "NestJS module registration for generated controllers/services: " + ", ".join(modules),
            "entity": None,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Preserve health controller registration",
                "Register generated controllers and providers explicitly",
                "Keep npm run build and npm test passing",
            ],
        })

    if bp_workflows:
        workflow_rel = f"{source_base}/workflows/workflows.service.ts"
        if not _is_reserved(workflow_rel):
            file_specs.append({
                "path": workflow_rel,
                "kind": "source",
                "description": (
                    "NestJS workflow service for blueprint workflows: "
                    + ", ".join(str(workflow.get("name", workflow)) for workflow in bp_workflows)
                ),
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Keep workflow logic testable outside HTTP controllers",
                    "Do not call external services without injected adapters",
                ],
            })

    return file_specs


# ---------------------------------------------------------------------------
# Internal: build file specs for generic (Python) profile
# ---------------------------------------------------------------------------

def _build_generic_file_specs(
    intent: dict,
    blueprint: dict | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Build file spec list for the generic (Python) profile."""
    file_specs: list[dict[str, Any]] = []
    source_base = targets.get("source", "")
    test_base = targets.get("tests", "")

    bp_entities = (blueprint or {}).get("entities", [])

    if bp_entities:
        entities_to_gen = bp_entities
    else:
        entities_to_gen = [
            {"name": _to_pascal(e), "fields": []}
            for e in intent.get("entities", [])
        ]

    for entity in entities_to_gen:
        name = entity["name"]
        snake = _to_snake(name)
        fields = entity.get("fields", [])

        test_rel = f"{test_base}/test_{snake}.py" if test_base else f"test_{snake}.py"
        source_rel = f"{source_base}/{snake}.py" if source_base else f"{snake}.py"

        # Clean up double slashes
        test_rel = test_rel.lstrip("/")
        source_rel = source_rel.lstrip("/")

        if _is_reserved(test_rel) or _is_reserved(source_rel):
            continue

        fields_desc = ""
        if fields:
            fields_desc = f" Fields: {', '.join(fields)}."

        # TDD: test first
        file_specs.append({
            "path": test_rel,
            "kind": "test",
            "description": (
                f"Python unittest file for {name}. "
                f"Tests creation, serialization, and field access."
            ),
            "entity": name,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Use unittest.TestCase",
                "Test object creation with kwargs",
                "Test to_dict serialization",
            ],
        })
        file_specs.append({
            "path": source_rel,
            "kind": "source",
            "description": (
                f"Python module for the {name} entity.{fields_desc} "
                f"Implements a class with __init__, to_dict, and field access."
            ),
            "entity": name,
            "acceptance_id": None,
            "task_id": None,
            "constraints": [
                "Use dataclass or __init__ with **kwargs",
                "Implement to_dict() method",
                "Use snake_case naming",
            ],
        })

    return file_specs


# ---------------------------------------------------------------------------
# Design constraint extraction
# ---------------------------------------------------------------------------

def _extract_design_constraints(design: dict | None) -> dict:
    """Extract design constraints from the design system selection."""
    if not design:
        return {
            "ui_library": "",
            "state_management": "",
            "data_layer": "",
            "form_handling": "",
            "design_tokens": {},
            "conventions": [],
        }

    return {
        "ui_library": design.get("ui_library", {}).get("name", ""),
        "state_management": design.get("state_management", {}).get("name", ""),
        "data_layer": design.get("data_layer", {}).get("name", ""),
        "form_handling": design.get("form_handling", {}).get("name", ""),
        "design_tokens": design.get("design_tokens", {}),
        "conventions": design.get("consistency_rules", []),
    }


# ---------------------------------------------------------------------------
# Public: build & write manifest
# ---------------------------------------------------------------------------

def build_generation_manifest(
    product_name: str,
    blueprint_id: str | None,
    profile: str,
    wave: str,
    task_ids: list[str],
    files: list[dict],
    validation_commands: list[str],
    capability_profile: dict | None = None,
) -> dict:
    """Build the generation manifest."""
    return {
        "schema_version": "signalos.generation_manifest.v1",
        "product": product_name,
        "blueprint": blueprint_id,
        "profile": profile,
        "wave": wave,
        "task_ids": task_ids,
        "files": files,
        "validation_commands": validation_commands,
        "capability_profile": capability_profile or {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_generation_manifest(manifest: dict, signalos_dir: Path) -> Path:
    """Write to .signalos/product/GENERATION_MANIFEST.json"""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "GENERATION_MANIFEST.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_generation_manifest(signalos_dir: Path) -> dict | None:
    """Load generation manifest."""
    path = signalos_dir / "product" / "GENERATION_MANIFEST.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def check_file_ownership(path: str, manifest: dict) -> bool:
    """Check if a file is bridge-owned (in the manifest)."""
    normed = path.replace("\\", "/")
    for f in manifest.get("files", []):
        if f.get("path", "").replace("\\", "/") == normed:
            return True
    return False


# ---------------------------------------------------------------------------
# Public: get_blueprint_dependencies
# ---------------------------------------------------------------------------

def get_blueprint_dependencies(
    blueprint: dict | None, profile: str
) -> dict[str, dict[str, str]]:
    """Return additional dependencies needed for this blueprint.

    Reads explicit ``dependencies`` from the blueprint when available,
    otherwise infers based on UI surface types (e.g. chart surfaces
    require a charting library).

    Returns ``{"dependencies": {...}, "devDependencies": {...}}``.
    """
    deps: dict[str, str] = {}
    dev_deps: dict[str, str] = {}

    if profile != "react-vite" or blueprint is None:
        return {"dependencies": deps, "devDependencies": dev_deps}

    # Explicit dependencies in blueprint take priority
    explicit = blueprint.get("npm_dependencies", {})
    if explicit:
        deps.update(explicit.get("dependencies", {}))
        dev_deps.update(explicit.get("devDependencies", {}))
        return {"dependencies": deps, "devDependencies": dev_deps}

    # Infer from UI surfaces: any chart/gauge surfaces need recharts
    ui_detail = blueprint.get("ui_detail", {})
    surfaces = ui_detail.get("surfaces", []) if isinstance(ui_detail, dict) else []
    ui_ids = blueprint.get("ui", [])

    has_chart_surface = any(
        "chart" in (s.get("id", "") if isinstance(s, dict) else s).lower()
        or "gauge" in (s.get("id", "") if isinstance(s, dict) else s).lower()
        for s in (surfaces or ui_ids)
    )

    if has_chart_surface:
        deps["recharts"] = "^2.12.0"

    return {"dependencies": deps, "devDependencies": dev_deps}


# ---------------------------------------------------------------------------
# Public: prepare_generation (replaces generate_product)
# ---------------------------------------------------------------------------

def prepare_generation(
    repo_root: Path,
    intent: dict,
    blueprint: dict | None,
    profile: str,
    wave: str = "1",
    task_ids: list[str] | None = None,
    acceptance_matrix: dict | None = None,
    design: dict | None = None,
    arch_review: dict | None = None,
    design_decisions: dict | None = None,
    scope_decisions: dict | None = None,
) -> dict:
    """Prepare the generation packet and manifest.

    Unlike the old generate_product(), this does NOT write application
    source code.  It builds a packet describing what the agent should
    create, writes governance metadata (manifest + packet), and returns
    the packet.

    Steps:
    1. Build the generation packet (file specs, constraints, etc.)
    2. Build the generation manifest (metadata tracking)
    3. Write both to .signalos/product/
    4. Return the packet

    The packet is consumed by the agent (or user) to actually build
    the product.  Validation runs AFTER the agent works.
    """
    if task_ids is None:
        task_ids = []

    # Build the generation packet
    packet = build_generation_packet(
        repo_root=repo_root,
        intent=intent,
        blueprint=blueprint,
        profile=profile,
        design=design,
        arch_review=arch_review,
        design_decisions=design_decisions,
        scope_decisions=scope_decisions,
        wave=wave,
        task_ids=task_ids,
        acceptance_matrix=acceptance_matrix,
    )

    product_name = intent.get("product_name", "")
    blueprint_id = blueprint.get("id") if blueprint else None

    # Build manifest file records from the packet file_specs
    manifest_files = [
        {
            "path": spec["path"],
            "kind": spec["kind"],
            "task_id": spec.get("task_id"),
            "acceptance_id": spec.get("acceptance_id"),
            "sha256_lf": None,  # not yet written -- agent will create
            "overwrite_mode": "create",
            "description": spec.get("description", ""),
        }
        for spec in packet["file_specs"]
    ]

    manifest = build_generation_manifest(
        product_name=product_name,
        blueprint_id=blueprint_id,
        profile=profile,
        wave=wave,
        task_ids=task_ids,
        files=manifest_files,
        validation_commands=packet["validation_commands"],
        capability_profile=packet.get("capability_profile"),
    )

    # Write manifest and packet
    signalos_dir = repo_root / ".signalos"
    write_generation_manifest(manifest, signalos_dir)

    # Write the packet itself
    packet_path = signalos_dir / "product" / "GENERATION_PACKET.json"
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return packet


# Backward-compatible alias
generate_product = prepare_generation


# ---------------------------------------------------------------------------
# Public: check_cross_file_consistency (Fix #12)
# ---------------------------------------------------------------------------

# Relative ES-module import specifiers (./ or ../). Bare specifiers (react,
# @mantine/core, zustand) are dependency-provided and never flagged.
_REL_IMPORT_RE = re.compile(
    r"""(?:import|export)\b[^;'"]*?\bfrom\s*['"](\.[^'"]+)['"]"""
    r"""|import\s*['"](\.[^'"]+)['"]"""  # side-effect import './x'
    r"""|(?:require|import)\(\s*['"](\.[^'"]+)['"]\s*\)""",  # dynamic/require
)

# Extensions a TS/TSX import may resolve to on disk.
_RESOLVE_EXTS = (
    "", ".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs",
    "/index.ts", "/index.tsx", "/index.js", "/index.jsx",
)


def _resolves_on_disk(repo_root: Path, importer_rel: str, spec: str) -> bool:
    """True if a relative import specifier from *importer_rel* resolves to a
    real file under *repo_root* (trying the standard TS/TSX extension order)."""
    importer_dir = (repo_root / importer_rel).parent
    base = (importer_dir / spec).resolve()
    for ext in _RESOLVE_EXTS:
        candidate = Path(str(base) + ext) if ext and not ext.startswith("/") else (
            base / ext.lstrip("/") if ext.startswith("/") else base
        )
        try:
            if candidate.is_file():
                return True
        except OSError:
            continue
    return False


def check_cross_file_consistency(
    repo_root: Path,
    packet: dict,
) -> dict:
    """Cross-file import-resolution check (Fix #12).

    The chunked per-file dispatch generates each file independently, so one
    file can import a module/path that NO generated file (or dependency)
    provides -- e.g. App importing ``./components/ExpenseManager`` while only
    ``Expense.tsx`` exists, or a component importing ``../types`` when no
    ``types.ts`` was generated (TS2307). ``tsc`` catches this only later; this
    lifts it into SignalOS governance so import drift is caught here too.

    Only RELATIVE imports (``./`` / ``../``) are checked -- bare package
    specifiers (react, @mantine/core, zustand) are provided by dependencies,
    not generated files, and are never flagged. Consistent with
    ``validate_generation_output``: returns violations, never weakens rules.

    Returns ``{"valid": bool, "violations": list[str], "checked": int}``.
    """
    file_specs = packet.get("file_specs", [])
    # Only source/registration/config TS-family files declare imports we own.
    candidates = [
        s["path"].replace("\\", "/")
        for s in file_specs
        if str(s.get("path", "")).replace("\\", "/").endswith(
            (".ts", ".tsx", ".js", ".jsx")
        )
    ]

    violations: list[str] = []
    checked = 0
    for rel in candidates:
        abs_path = repo_root / rel
        if not abs_path.is_file():
            continue  # missing-file drift is reported by validate_generation_output
        try:
            content = abs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        checked += 1
        seen: set[str] = set()
        for match in _REL_IMPORT_RE.finditer(content):
            spec = match.group(1) or match.group(2) or match.group(3)
            if not spec or spec in seen:
                continue
            seen.add(spec)
            if not _resolves_on_disk(repo_root, rel, spec):
                violations.append(
                    f"{rel} imports '{spec}' which no generated file provides "
                    f"(unresolved cross-file import)"
                )

    return {
        "valid": len(violations) == 0,
        "violations": violations,
        "checked": checked,
    }


# ---------------------------------------------------------------------------
# Public: validate_generation_output
# ---------------------------------------------------------------------------

def validate_generation_output(
    repo_root: Path,
    packet: dict,
    *,
    check_cross_file: bool = True,
) -> dict:
    """Validate that the agent's output matches the generation packet.

    Checks:
    1. Every file_spec has a corresponding file on disk
    2. Files are within allowed_paths
    3. No files in forbidden_paths
    4. Files are non-empty
    5. Test files exist for source files (TDD check)
    6. Cross-file import resolution (Fix #12), when ``check_cross_file`` is set.

    ``check_cross_file`` must be False when validating a PARTIAL subset of a
    product (e.g. one file-disjoint sub-task of the parallel in-place build):
    a component sub-task can legitimately import ``../types`` before the
    concurrent foundation sub-task has written ``types.ts``. The authoritative
    whole-repo cross-file check runs once, after all tasks complete, over the
    FULL packet (see delivery.py). Standalone/full-packet validation keeps it
    on (the default).

    Returns:
    {
        "valid": bool,
        "files_expected": int,
        "files_found": int,
        "files_missing": list[str],
        "files_unexpected": list[str],
        "violations": list[str],
    }
    """
    import fnmatch

    file_specs = packet.get("file_specs", [])
    allowed_paths = packet.get("allowed_paths", [])
    forbidden_paths = packet.get("forbidden_paths", [])

    files_expected = len(file_specs)
    files_found = 0
    files_missing: list[str] = []
    violations: list[str] = []

    for spec in file_specs:
        rel_path = spec["path"]
        abs_path = repo_root / rel_path

        # Check existence
        if not abs_path.is_file():
            files_missing.append(rel_path)
            continue

        files_found += 1

        # Check non-empty
        try:
            content = abs_path.read_text(encoding="utf-8")
            if not content.strip():
                violations.append(f"File is empty: {rel_path}")
        except (OSError, UnicodeDecodeError):
            violations.append(f"File unreadable: {rel_path}")

        # Check allowed paths
        normed = rel_path.replace("\\", "/")
        in_allowed = False
        for pattern in allowed_paths:
            pat = pattern.replace("\\", "/")
            if fnmatch.fnmatch(normed, pat):
                in_allowed = True
                break
            if pat.endswith("/**"):
                prefix = pat[:-3]
                if normed.startswith(prefix + "/") or normed.startswith(prefix):
                    in_allowed = True
                    break
        if not in_allowed and allowed_paths:
            violations.append(f"File outside allowed paths: {rel_path}")

        # Check forbidden paths
        for pat in forbidden_paths:
            p = pat.replace("\\", "/").rstrip("/")
            if pat.endswith("/"):
                if normed.startswith(p + "/") or normed == p:
                    violations.append(f"File in forbidden path: {rel_path}")
            elif "*" in pat:
                if fnmatch.fnmatch(normed, p):
                    violations.append(f"File matches forbidden pattern: {rel_path}")

    # TDD check: source files should have co-located tests
    source_paths = {s["path"] for s in file_specs if s["kind"] == "source"}
    test_paths = {s["path"] for s in file_specs if s["kind"] == "test"}
    for src in source_paths:
        # Derive expected test path
        alternatives: set[str] = set()
        if src.endswith(".tsx"):
            alternatives.add(src.replace(".tsx", ".test.tsx"))
        elif src.endswith(".ts"):
            alternatives.add(src.replace(".ts", ".test.ts"))
        elif src.endswith(".py"):
            parts = src.rsplit("/", 1)
            if len(parts) == 2:
                alternatives.add(f"{parts[0]}/test_{parts[1]}")
            else:
                alternatives.add(f"test_{parts[0]}")
            basename = parts[-1]
            alternatives.add(f"tests/test_{basename}")
        elif src.endswith(".js"):
            basename = src.rsplit("/", 1)[-1].replace(".js", "")
            alternatives.add(f"tests/{basename}.test.js")
            alternatives.add(src.replace(".js", ".test.js"))
        else:
            continue
        if not alternatives & test_paths:
            violations.append(f"Missing test file for source: {src}")

    # Check for unexpected files (files on disk in source dirs not in specs)
    files_unexpected: list[str] = []
    # Only check src/components if react-vite
    spec_paths = {s["path"].replace("\\", "/") for s in file_specs}
    components_dir = repo_root / "src" / "components"
    if components_dir.is_dir():
        for disk_file in components_dir.iterdir():
            if disk_file.is_file():
                rel = disk_file.relative_to(repo_root).as_posix()
                if rel not in spec_paths:
                    files_unexpected.append(rel)

    # Fix #12: cross-file import-resolution drift is a governance violation.
    # A file importing a module no generated file (or dependency) provides
    # would fail `tsc && vite build`; catch it here, not only at npm time.
    # Skipped for partial-subset validation (parallel sub-tasks) -- see the
    # check_cross_file docstring.
    cross_file = (
        check_cross_file_consistency(repo_root, packet)
        if check_cross_file
        else {"valid": True, "violations": [], "checked": 0, "skipped": True}
    )
    violations.extend(cross_file.get("violations", []))

    valid = len(violations) == 0 and len(files_missing) == 0
    return {
        "valid": valid,
        "files_expected": files_expected,
        "files_found": files_found,
        "files_missing": files_missing,
        "files_unexpected": files_unexpected,
        "violations": violations,
        "cross_file_consistency": cross_file,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _flatten_validation(plan: dict[str, list[str]]) -> list[str]:
    """Flatten a validation plan into a single command list."""
    result: list[str] = []
    for key in ("install", "build", "test", "lint", "qa",
                "e2e", "runtime_smoke", "ux_smoke", "security"):
        result.extend(plan.get(key, []))
    return result


# ---------------------------------------------------------------------------
# Internal: acceptance-criteria linking for file specs
# ---------------------------------------------------------------------------

def _match_criterion_for_file(
    file_rec: dict[str, Any],
    criteria: list[dict[str, Any]],
    test_scenarios: list[dict[str, Any]],
) -> str | None:
    """Find the best-matching acceptance criterion ID for a file record.

    Matching strategy (case-insensitive):
    1. Extract entity/component name from the file path
    2. Match against criterion entity field
    3. Match against criterion workflow field
    4. For test files, match against test scenario descriptions
    """
    path_lower = file_rec.get("path", "").lower()

    # Extract a meaningful name from the path (e.g. "TaskList" from
    # "src/components/TaskList.tsx" or "task" from "task.py")
    path_stem = Path(file_rec.get("path", "")).stem
    # Strip test prefixes/suffixes
    clean_stem = path_stem.replace(".test", "").replace("test_", "")
    stem_lower = clean_stem.lower()

    # 1. Match by entity name
    for crit in criteria:
        entity = crit.get("entity")
        if entity and entity.lower() in stem_lower:
            return crit["id"]

    # 2. Match by workflow name
    for crit in criteria:
        workflow = crit.get("workflow")
        if workflow:
            # Check if any word from the workflow appears in the path
            workflow_words = [w.lower() for w in workflow.split() if len(w) > 2]
            for word in workflow_words:
                if word in stem_lower or word in path_lower:
                    return crit["id"]

    # 3. For test files, match against test scenario descriptions
    if file_rec.get("kind") == "test":
        for scenario in test_scenarios:
            desc_words = [
                w.lower() for w in scenario.get("description", "").split()
                if len(w) > 3
            ]
            for word in desc_words:
                if word in stem_lower:
                    return scenario.get("acceptance_id")

    return None


def _link_specs_to_acceptance(
    file_specs: list[dict[str, Any]],
    acceptance_matrix: dict[str, Any],
) -> None:
    """Link file specs to acceptance criteria in place."""
    criteria = acceptance_matrix.get("criteria", [])
    test_scenarios = acceptance_matrix.get("test_scenarios", [])

    for spec in file_specs:
        if spec.get("acceptance_id") is not None:
            continue  # already linked
        matched = _match_criterion_for_file(spec, criteria, test_scenarios)
        if matched is not None:
            spec["acceptance_id"] = matched


# Backward-compatible alias for the old name
_link_records_to_acceptance = _link_specs_to_acceptance


# ---------------------------------------------------------------------------
# Public: link_generation_to_acceptance
# ---------------------------------------------------------------------------

def link_generation_to_acceptance(
    manifest: dict,
    acceptance_matrix: dict,
) -> dict:
    """Link generated files to acceptance criteria.

    For each file in the manifest:
    - Match by entity name -> find AC with matching entity
    - Match by workflow name -> find AC with matching workflow
    - Match test files to test scenarios by description keywords

    Updates manifest files in place with acceptance_id and task_id.
    Returns the updated manifest.
    """
    criteria = acceptance_matrix.get("criteria", [])
    test_scenarios = acceptance_matrix.get("test_scenarios", [])

    for file_rec in manifest.get("files", []):
        if file_rec.get("acceptance_id") is not None:
            continue
        matched = _match_criterion_for_file(file_rec, criteria, test_scenarios)
        if matched is not None:
            file_rec["acceptance_id"] = matched

    return manifest


# ---------------------------------------------------------------------------
# Public: verify_trace_completeness
# ---------------------------------------------------------------------------

def verify_trace_completeness(
    manifest: dict,
    acceptance_matrix: dict,
) -> dict:
    """Verify that every generated file traces back to acceptance criteria.

    Returns:
    {
        "complete": bool,
        "linked_files": int,
        "unlinked_files": int,
        "unlinked_paths": list[str],
        "covered_criteria": list[str],   # AC IDs covered by at least one file
        "uncovered_criteria": list[str],  # AC IDs with no linked file
    }
    """
    files = manifest.get("files", [])
    linked = 0
    unlinked = 0
    unlinked_paths: list[str] = []
    covered_set: set[str] = set()

    for f in files:
        aid = f.get("acceptance_id")
        if aid is not None:
            linked += 1
            covered_set.add(aid)
        else:
            unlinked += 1
            unlinked_paths.append(f.get("path", ""))

    all_criteria_ids = [
        c["id"] for c in acceptance_matrix.get("criteria", [])
    ]
    uncovered = [cid for cid in all_criteria_ids if cid not in covered_set]

    return {
        "complete": unlinked == 0 and len(uncovered) == 0,
        "linked_files": linked,
        "unlinked_files": unlinked,
        "unlinked_paths": unlinked_paths,
        "covered_criteria": sorted(covered_set),
        "uncovered_criteria": uncovered,
    }
