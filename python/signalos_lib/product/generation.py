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
    "check_file_ownership",
    "collect_governance_instructions",
    "compute_sha256_lf",
    "get_blueprint_dependencies",
    "link_generation_to_acceptance",
    "load_generation_manifest",
    "prepare_generation",
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

from .stacks import get_adapter


# ---------------------------------------------------------------------------
# Governance instructions bundling
# ---------------------------------------------------------------------------

def _resolve_bundle_root() -> Path:
    """Resolve the governance bundle directory.

    In development: signalos_lib/_bundle/ (same tree).
    In frozen binary (--onedir): PyInstaller places data files relative
    to sys._MEIPASS or the executable directory.
    """
    import sys
    if getattr(sys, "frozen", False):
        # PyInstaller --onedir: data is relative to the exe dir
        base = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).parent
        frozen_path = base / "signalos_lib" / "_bundle"
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
    """Convert any entity/workflow name to PascalCase.

    Handles spaces, hyphens, and underscores as word separators.
    Already-PascalCase names pass through unchanged.

    Examples:
        "patient intake" -> "PatientIntake"
        "clinical notes" -> "ClinicalNotes"
        "lab results"    -> "LabResults"
        "Task"           -> "Task"
        "revenue-chart"  -> "RevenueChart"
        "some_thing"     -> "SomeThing"
    """
    # Split on spaces, hyphens, and underscores
    words = re.split(r"[\s\-_]+", name.strip())
    # Upper-case the first letter of each word; preserve the rest so that
    # already-PascalCase tokens like "ClinicalNote" are not flattened.
    return "".join((word[0].upper() + word[1:]) if word else "" for word in words if word)


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
            comp_name = surface.get("component", _to_pascal_case(surface.get("id", "")))
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


# ---------------------------------------------------------------------------
# Public: build_generation_packet
# ---------------------------------------------------------------------------

def build_generation_packet(
    repo_root: Path,
    intent: dict,
    blueprint: dict | None,
    profile: str,
    design: dict | None = None,
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
    if profile == "react-vite":
        file_specs = _build_react_vite_file_specs(
            intent, blueprint, targets, design,
        )
    else:
        file_specs = _build_generic_file_specs(
            intent, blueprint, targets,
        )

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

            # Build description from surface data
            fields_desc = ""
            if entity and entity.get("fields"):
                fields_desc = f" Entity fields: {', '.join(entity['fields'])}."

            surface_desc = spec.get("surface", "")
            entity_ref = entity_name or comp_name

            # TDD: test first
            file_specs.append({
                "path": test_path,
                "kind": "test",
                "description": (
                    f"Vitest test file for {comp_name}. "
                    f"Tests rendering, user interactions, and data display."
                ),
                "entity": entity_name or None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use @testing-library/react for rendering",
                    "Use vitest describe/it/expect",
                    "Test renders without crashing",
                    "Test displays expected content",
                ],
            })
            file_specs.append({
                "path": source_path,
                "kind": "source",
                "description": (
                    f"React component for {entity_ref}. "
                    f"Surface: {surface_desc}.{fields_desc} "
                    f"Uses design tokens from src/ui/theme.ts."
                ),
                "entity": entity_name or None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": list(base_constraints),
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

            file_specs.append({
                "path": test_path,
                "kind": "test",
                "description": (
                    f"Vitest test file for {pascal}. "
                    f"Tests rendering, user interactions, and data display."
                ),
                "entity": ent_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Use @testing-library/react for rendering",
                    "Use vitest describe/it/expect",
                    "Test renders without crashing",
                    "Test displays expected content",
                ],
            })
            file_specs.append({
                "path": source_path,
                "kind": "source",
                "description": (
                    f"React component for the {pascal} entity. "
                    f"Renders with CRUD operations. "
                    f"Uses design tokens from src/ui/theme.ts."
                ),
                "entity": ent_name,
                "acceptance_id": None,
                "task_id": None,
                "constraints": list(base_constraints),
            })

    # Types file (config)
    if bp_entities:
        types_path = f"{source_base}/types.ts"
        if not _is_reserved(types_path):
            entity_names = [e.get("name", "") for e in bp_entities]
            file_specs.append({
                "path": types_path,
                "kind": "config",
                "description": (
                    f"TypeScript type definitions for entities: "
                    f"{', '.join(entity_names)}. "
                    f"Export an interface for each entity with its fields."
                ),
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    "Export one interface per entity",
                    "Use appropriate TypeScript types for each field",
                ],
            })

    # App registration
    if component_names:
        app_path = f"{source_base}/App.tsx"
        if not _is_reserved(app_path):
            file_specs.append({
                "path": app_path,
                "kind": "registration",
                "description": (
                    f"Root App component that imports and renders: "
                    f"{', '.join(component_names)}. "
                    f"Wire all generated components into the app."
                ),
                "entity": None,
                "acceptance_id": None,
                "task_id": None,
                "constraints": [
                    f"Import each component from ./components/",
                    "Render all components in the JSX tree",
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
# Public: validate_generation_output
# ---------------------------------------------------------------------------

def validate_generation_output(
    repo_root: Path,
    packet: dict,
) -> dict:
    """Validate that the agent's output matches the generation packet.

    Checks:
    1. Every file_spec has a corresponding file on disk
    2. Files are within allowed_paths
    3. No files in forbidden_paths
    4. Files are non-empty
    5. Test files exist for source files (TDD check)

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
        if src.endswith(".tsx"):
            expected_test = src.replace(".tsx", ".test.tsx")
        elif src.endswith(".ts"):
            expected_test = src.replace(".ts", ".test.ts")
        elif src.endswith(".py"):
            parts = src.rsplit("/", 1)
            if len(parts) == 2:
                expected_test = f"{parts[0]}/test_{parts[1]}"
            else:
                expected_test = f"test_{parts[0]}"
        else:
            continue
        if expected_test not in test_paths:
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

    valid = len(violations) == 0 and len(files_missing) == 0
    return {
        "valid": valid,
        "files_expected": files_expected,
        "files_found": files_found,
        "files_missing": files_missing,
        "files_unexpected": files_unexpected,
        "violations": violations,
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
