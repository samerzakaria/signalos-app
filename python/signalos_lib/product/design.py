# signalos_lib/product/design.py
# Design Phase -- selects UX library, design tokens, state management,
# and data layer based on product intent.
#
# LLM architect agent is FIRST choice; deterministic logic is FALLBACK.

from __future__ import annotations

__all__ = [
    "UILibraryAdapter",
    "build_design_system",
    "get_design_dependencies",
    "get_design_instructions",
    "get_ui_library",
    "load_design",
    "select_design_with_llm",
    "supported_ui_library_names",
    "ui_library_registry",
    "write_design",
]

import json
import os
from pathlib import Path
from typing import Any
from .llm_provider import is_llm_available

# #27: pinned to the Mantine v7 line, which is built for React 18. The
# react-vite scaffold (stacks.py) ships react/react-dom ^18.3.1, and Mantine v9
# requires React 19 (its core imports React 19's `use` hook) -- shipping v9 on
# React 18 produces the vitest `SyntaxError: Named export 'use' not found`. Every
# @mantine/* sub-package MUST resolve to this single version; enforce_dependency_
# versions() re-pins any skew a generation agent's self-written package.json adds.
_MANTINE_VERSION = "7.13.2"


def enforce_dependency_versions(
    dependencies: dict[str, str], design_deps: dict[str, str]
) -> bool:
    """Force a package.json ``dependencies`` map onto the canonical design
    versions. Returns True when anything changed.

    #27: a remote build agent regenerates its OWN package.json and can emit
    skewed @mantine/* majors (e.g. core@9 with hooks@7). ``_merge_design_deps``
    only fills *missing* keys, so it cannot correct a version the agent already
    wrote. This coerces (a) every design-system dependency to the version
    ``get_design_dependencies`` computed, and (b) ANY stray ``@mantine/*`` the
    agent added to the single ``_MANTINE_VERSION`` so the majors never skew.
    """
    changed = False
    for name, version in design_deps.items():
        if dependencies.get(name) != version:
            dependencies[name] = version
            changed = True
    for name in list(dependencies):
        if name.startswith("@mantine/") and dependencies[name] != _MANTINE_VERSION:
            dependencies[name] = _MANTINE_VERSION
            changed = True
    return changed


# ---------------------------------------------------------------------------
# #44: UI-library adapter REGISTRY -- the single source of truth for the
# supported design systems.
#
# The choice used to be a hardcoded pair repeated in FIVE places (the LLM
# prompt, _parse_design_response's validator, _select_ui_library's heuristic,
# get_design_dependencies, and agent_dispatch's import allowlist). That made
# the set unextendable and gave the founder no say. Now a design system is one
# registry entry; registering it wires it EVERYWHERE. A curated set is fine --
# a hardcoded, unextendable one was the problem.
# ---------------------------------------------------------------------------

from dataclasses import dataclass  # noqa: E402
from typing import Callable, Optional  # noqa: E402


@dataclass(frozen=True)
class UILibraryAdapter:
    """A pluggable UI-library adapter. One entry makes the library a first-class
    choice across the whole design + generation pipeline."""
    id: str
    name: str                            # the ui_library.name value used everywhere
    version: str
    prompt_desc: str                     # one-line description in the LLM options
    dependencies: dict[str, str]         # npm deps get_design_dependencies installs
    import_packages: tuple[str, ...]     # bare packages a component/test may import
    selection_priority: int = 0          # higher = considered first by the heuristic
    is_default: bool = False             # heuristic fallback when nothing else fits
    default_reason: str = ""
    # fit(intent, blueprint) -> a reason string if this library fits, else None.
    fit: Optional[Callable[[dict, Optional[dict]], Optional[str]]] = None


def _mantine_fit(intent: dict, blueprint: dict | None) -> str | None:
    surfaces = set(intent.get("ux_surfaces", []))
    entities = intent.get("entities", [])
    if len(entities) >= 4 or surfaces & {"form", "table", "calendar"}:
        return (
            "Entity-rich product needs robust form controls, tables, and "
            "date pickers"
        )
    return None


def _shadcn_fit(intent: dict, blueprint: dict | None) -> str | None:
    surfaces = set(intent.get("ux_surfaces", []))
    if (
        intent.get("product_type") == "financial-dashboard"
        or surfaces & {"chart", "gauge"}
    ):
        return (
            "Data visualization product benefits from composable "
            "primitives + recharts"
        )
    return None


_UI_LIBRARY_REGISTRY: tuple[UILibraryAdapter, ...] = (
    UILibraryAdapter(
        id="shadcn",
        name="shadcn/ui",
        version="latest",
        prompt_desc=(
            "composable primitives, lightweight; good for dashboards and "
            "general UI"
        ),
        dependencies={
            "tailwindcss": "^3.4.0",
            "class-variance-authority": "^0.7.0",
            "clsx": "^2.1.0",
            "tailwind-merge": "^2.3.0",
            "lucide-react": "^0.378.0",
        },
        import_packages=(
            "lucide-react", "class-variance-authority", "clsx", "tailwind-merge",
        ),
        # dashboard-fit is checked BEFORE mantine's forms-fit (preserves the
        # prior precedence where a dashboard product picked shadcn).
        selection_priority=20,
        is_default=True,
        default_reason="General-purpose composable UI primitives",
        fit=_shadcn_fit,
    ),
    UILibraryAdapter(
        id="mantine",
        name="@mantine/core",
        version=_MANTINE_VERSION,
        prompt_desc=(
            "rich form controls, tables, date pickers; good for entity-heavy apps"
        ),
        dependencies={
            "@mantine/core": _MANTINE_VERSION,
            "@mantine/hooks": _MANTINE_VERSION,
            "@mantine/form": _MANTINE_VERSION,
            "@mantine/dates": _MANTINE_VERSION,
            "@mantine/charts": _MANTINE_VERSION,
            # #27: @mantine/charts needs recharts as a peer.
            "recharts": "^2.12.0",
            "@tabler/icons-react": "^3.5.0",
            "dayjs": "^1.11.11",
        },
        import_packages=(
            "@mantine/core", "@mantine/hooks", "@mantine/form",
            "@mantine/dates", "@mantine/charts", "@tabler/icons-react", "dayjs",
        ),
        selection_priority=10,
        fit=_mantine_fit,
    ),
)


def ui_library_registry() -> tuple[UILibraryAdapter, ...]:
    """The ordered tuple of supported UI-library adapters."""
    return _UI_LIBRARY_REGISTRY


def get_ui_library(name: str) -> UILibraryAdapter | None:
    """Look up an adapter by its ui_library.name (e.g. '@mantine/core') or its
    short id (e.g. 'mantine'). None when unsupported."""
    for lib in _UI_LIBRARY_REGISTRY:
        if lib.name == name or lib.id == name:
            return lib
    return None


def supported_ui_library_names() -> tuple[str, ...]:
    """The ui_library.name values the design phase accepts."""
    return tuple(lib.name for lib in _UI_LIBRARY_REGISTRY)


def _ui_library_options_block() -> str:
    """Render the 'UI library (pick one)' options for the LLM prompt from the
    registry, so a newly-registered library is offered automatically."""
    return "\n".join(
        f'- "{lib.name}" — {lib.prompt_desc}' for lib in _UI_LIBRARY_REGISTRY
    )


# ---------------------------------------------------------------------------
# LLM-driven design selection (Architect agent)
# ---------------------------------------------------------------------------

# The design agent contract, loaded lazily.
_DESIGN_CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent
    / "_bundle" / "core" / "execution" / "agents" / "design.md"
)

_ARCHITECT_SYSTEM_PROMPT = """\
You are the highest-level UI/UX designer ever for this product's domain, the
best UI/UX designer in the world, and a world-class frontend architect acting
as the SignalOS Architect agent in a SignalOS-governed software house.

Your job: select the best design system composition for a product based on
its intent (entities, surfaces, workflows, users), profile constraints, and
optional blueprint context.

Apply world-class judgment for accessibility, information architecture,
interaction states, content hierarchy, visual clarity, empty/loading/error
states, mobile ergonomics, maintainability, and implementation fit. SignalOS
owns the scope and governance contract; you own the design-system quality of
the recommendation. If the intent is not sufficient to choose safely, return
the closest supported option with an explicit reason instead of inventing
unsupported libraries.

You must pick from the SUPPORTED OPTIONS below. Do not invent new libraries.

## Supported Options

UI library (pick one):
__UI_LIBRARY_OPTIONS__

State management (pick one):
- "zustand" — minimal boilerplate, scales well
- "jotai" — atomic state, good for many independent pieces
- "redux-toolkit" — heavy but powerful, good for complex shared state

Data layer (pick one):
- "@tanstack/react-query" — API-backed data fetching with caching
- "swr" — lightweight alternative to react-query
- "local" — no external data sources, local state only

Form handling (pick one):
- "react-hook-form" — performant validated forms for entity-rich apps
- "formik" — established form library, simpler API
- "native" — native controlled components for simple inputs

Primary color: any valid hex color appropriate for the product domain

Font (pick one):
- "Inter" — clean sans-serif, general purpose
- "JetBrains Mono" — monospace, good for developer tools
- "system" — system font stack

## Output Format

Return ONLY valid JSON (no markdown fencing, no explanation outside the JSON):
{
  "ui_library": {"name": "<choice>", "version": "<semver or latest>", "reason": "<why>"},
  "design_tokens": {
    "color_scheme": "light",
    "primary_color": "<hex>",
    "border_radius": "8px",
    "font_family": "<choice>, sans-serif",
    "spacing_unit": 8
  },
  "state_management": {"name": "<choice>", "version": "<semver>", "reason": "<why>"},
  "data_layer": {"name": "<choice>", "version": "<semver or null>", "reason": "<why>"},
  "form_handling": {"name": "<choice>", "version": "<semver or null>", "reason": "<why>"},
  "additional_deps": {}
}
"""

# #44: inject the registry's UI-library options so a newly-registered library is
# offered to the architect automatically (single source of truth).
_ARCHITECT_SYSTEM_PROMPT = _ARCHITECT_SYSTEM_PROMPT.replace(
    "__UI_LIBRARY_OPTIONS__", _ui_library_options_block()
)


def select_design_with_llm(
    intent: dict,
    profile: str,
    blueprint: dict | None = None,
    provider_name: str | None = None,
    model: str | None = None,
) -> dict | None:
    """Use an LLM architect agent to select the best design system.

    The agent receives:
    - Product intent (entities, surfaces, workflows, users)
    - Profile constraints
    - Blueprint context (if matched)
    - Design agent governance contract

    The agent returns design decisions as JSON.
    Falls back to None if LLM unavailable (caller uses deterministic fallback).
    """
    try:
        from signalos_lib.harness import _resolve_provider, resolve_model
    except Exception:
        return None

    try:
        provider = _resolve_provider(provider_name)
    except Exception:
        return None

    # Build the user prompt
    parts: list[str] = []

    # Include governance contract if available
    if _DESIGN_CONTRACT_PATH.is_file():
        try:
            contract_text = _DESIGN_CONTRACT_PATH.read_text(encoding="utf-8")
            parts.append(f"## Design Agent Governance Contract\n\n{contract_text}\n")
        except OSError:
            pass

    parts.append("## Product Intent\n")
    parts.append(json.dumps(intent, indent=2, default=str))
    parts.append(f"\n## Profile: {profile}\n")

    if blueprint:
        parts.append("## Blueprint Context\n")
        parts.append(json.dumps(blueprint, indent=2, default=str))

    parts.append(
        "\nSelect the best design system composition for this product. "
        "Return ONLY the JSON object described in the output format."
    )

    user_prompt = "\n".join(parts)

    try:
        # No hardcoded default: explicit model → SIGNALOS_LLM_MODEL → discovery.
        use_model = resolve_model(model, provider_name)
        response_text, _, _ = provider.call(
            f"{_ARCHITECT_SYSTEM_PROMPT}\n\n{user_prompt}",
            use_model,
        )
    except Exception:
        return None

    # Parse the LLM response as JSON
    return _parse_design_response(response_text)


def _parse_design_response(response: str) -> dict | None:
    """Parse LLM response into a valid design dict, or None on failure."""
    if not response or not response.strip():
        return None

    text = response.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        import re
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None

    # Validate required keys
    required = {"ui_library", "design_tokens", "state_management", "data_layer", "form_handling"}
    if not required.issubset(data.keys()):
        return None

    # Validate ui_library is from the supported set (#44: from the registry).
    ui_name = data.get("ui_library", {}).get("name", "")
    if ui_name not in supported_ui_library_names():
        return None

    # Validate state_management
    valid_state = {"zustand", "jotai", "redux-toolkit"}
    state_name = data.get("state_management", {}).get("name", "")
    if state_name not in valid_state:
        return None

    # Validate data_layer
    valid_data = {"@tanstack/react-query", "swr", "local"}
    data_name = data.get("data_layer", {}).get("name", "")
    if data_name not in valid_data:
        return None

    # Validate form_handling
    valid_form = {"react-hook-form", "formik", "native"}
    form_name = data.get("form_handling", {}).get("name", "")
    if form_name not in valid_form:
        return None

    # Build the full design system dict with standard envelope
    return {
        "schema_version": "signalos.design_system.v1",
        "ui_library": data["ui_library"],
        "design_tokens": data["design_tokens"],
        "state_management": data["state_management"],
        "data_layer": data["data_layer"],
        "form_handling": data["form_handling"],
        "additional_deps": data.get("additional_deps", {}),
        "component_conventions": {
            "file_structure": "feature-based",
            "naming": "PascalCase",
            "test_co_location": True,
            "shared_ui_path": "src/ui",
            "theme_path": "src/ui/theme.ts",
        },
        "consistency_rules": [
            "All components import from src/ui for primitives",
            "No inline styles -- use design tokens via theme",
            "Shared layout components in src/ui/layouts",
            "Consistent spacing via theme.spacing",
        ],
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_design_system(
    intent: dict,
    profile: str,
    blueprint: dict | None = None,
) -> dict:
    """Select design system, UX library, and tech composition for this product.

    Tries LLM architect agent first; falls back to deterministic selection
    if LLM is unavailable or returns an invalid response.

    Returns a ``signalos.design_system.v1`` dict describing the selected
    UI library, design tokens, state management, data layer, form handling,
    component conventions, and consistency rules.
    """
    # Non-UI and agent-selected profiles must not receive React-specific
    # dependency recommendations.
    if profile in {"generic", "node-api", "fastapi-api", "go-api", "dotnet-minimal-api"}:
        return _empty_design(f"{profile} profile")
    if profile == "agent-selected":
        return _portable_design(intent, profile)

    # Try LLM architect agent first
    if is_llm_available():
        llm_result = select_design_with_llm(intent, profile, blueprint)
        if llm_result:
            return llm_result

    # Fallback: deterministic selection (existing logic)
    return _deterministic_design(intent, profile, blueprint)


def _deterministic_design(intent: dict, profile: str, blueprint: dict | None = None) -> dict:
    """Deterministic design selection -- no LLM, no network."""
    ui = _select_ui_library(intent, blueprint)
    state = _select_state_management(intent)
    data = _select_data_layer(intent)
    form = _select_form_handling(intent)
    primary_color = _derive_color_scheme(intent)

    additional_deps: dict[str, str] = {}
    # recharts for dashboard/chart products
    if ui["name"] == "shadcn/ui":
        surfaces = set(intent.get("ux_surfaces", []))
        product_type = intent.get("product_type", "custom")
        if product_type == "financial-dashboard" or "chart" in surfaces or "gauge" in surfaces:
            additional_deps["recharts"] = "^2.12.0"

    return {
        "schema_version": "signalos.design_system.v1",
        "ui_library": ui,
        "design_tokens": {
            "color_scheme": "light",
            "primary_color": primary_color,
            "border_radius": "8px",
            "font_family": "Inter, sans-serif",
            "spacing_unit": 8,
        },
        "state_management": state,
        "data_layer": data,
        "form_handling": form,
        "additional_deps": additional_deps,
        "component_conventions": {
            "file_structure": "feature-based",
            "naming": "PascalCase",
            "test_co_location": True,
            "shared_ui_path": "src/ui",
            "theme_path": "src/ui/theme.ts",
        },
        "consistency_rules": [
            "All components import from src/ui for primitives",
            "No inline styles -- use design tokens via theme",
            "Shared layout components in src/ui/layouts",
            "Consistent spacing via theme.spacing",
        ],
    }


# ---------------------------------------------------------------------------
# Selection helpers (all deterministic)
# ---------------------------------------------------------------------------

def _select_ui_library(intent: dict, blueprint: dict | None) -> dict:
    """Select a UI library from the registry (#44).

    Each adapter's ``fit`` votes on the intent; the highest-priority match wins,
    else the ``is_default`` library. Registering a new adapter with a ``fit``
    makes it selectable here too -- no edit to this function."""
    candidates = sorted(
        (lib for lib in _UI_LIBRARY_REGISTRY if lib.fit is not None),
        key=lambda lib: lib.selection_priority,
        reverse=True,
    )
    for lib in candidates:
        reason = lib.fit(intent, blueprint)
        if reason:
            return {"name": lib.name, "version": lib.version, "reason": reason}

    default = next(
        (lib for lib in _UI_LIBRARY_REGISTRY if lib.is_default),
        _UI_LIBRARY_REGISTRY[0],
    )
    return {
        "name": default.name,
        "version": default.version,
        "reason": default.default_reason or "Default UI library",
    }


def _select_state_management(intent: dict) -> dict:
    """Always zustand for now -- simple, scales well."""
    return {
        "name": "zustand",
        "version": "^4.5.0",
        "reason": "Minimal boilerplate state management",
    }


def _select_data_layer(intent: dict) -> dict:
    """Select data layer based on API / data source presence."""
    if intent.get("api_surfaces") or intent.get("data_sources"):
        return {
            "name": "@tanstack/react-query",
            "version": "^5.40.0",
            "reason": "API-backed data fetching with caching",
        }
    return {
        "name": "local",
        "version": None,
        "reason": "No external data sources detected -- local state sufficient",
    }


def _select_form_handling(intent: dict) -> dict:
    """Select form handling approach based on entity count."""
    entities = intent.get("entities", [])
    if len(entities) >= 3:
        return {
            "name": "react-hook-form",
            "version": "^7.52.0",
            "reason": "Multiple entities require validated form inputs",
        }
    return {
        "name": "native",
        "version": None,
        "reason": "Simple inputs -- native controlled components sufficient",
    }


def _derive_color_scheme(intent: dict) -> str:
    """Pick a primary color based on product domain."""
    product_type = intent.get("product_type", "custom")
    colors = {
        "financial-dashboard": "#2563eb",  # blue (trust/finance)
        "task-management": "#7c3aed",      # violet (productivity)
        "medical": "#059669",              # green (health)
        "custom": "#3b82f6",               # standard blue
    }

    # Check for medical/health keywords in entities
    entities_text = " ".join(intent.get("entities", [])).lower()
    if any(
        w in entities_text
        for w in ("patient", "clinical", "medical", "health", "prescription")
    ):
        return colors["medical"]

    return colors.get(product_type, colors["custom"])


# ---------------------------------------------------------------------------
# Empty design (generic / non-UI profiles)
# ---------------------------------------------------------------------------

def _portable_design(intent: dict, profile: str) -> dict:
    """Technology-neutral design brief for agent-selected stacks."""
    return {
        "schema_version": "signalos.design_system.v1",
        "ui_library": {
            "name": "",
            "version": None,
            "reason": (
                "Agent-selected profile: design principles apply, but package "
                "choices belong to the selected product technology."
            ),
        },
        "design_tokens": {
            "color_scheme": "light",
            "primary_color": _derive_color_scheme(intent),
            "border_radius": "8px",
            "font_family": "Inter, sans-serif",
            "spacing_unit": 8,
        },
        "state_management": {
            "name": "",
            "version": None,
            "reason": "State library depends on the selected product technology.",
        },
        "data_layer": {
            "name": "",
            "version": None,
            "reason": "Data layer depends on the selected product technology.",
        },
        "form_handling": {
            "name": "",
            "version": None,
            "reason": "Form handling depends on the selected product technology.",
        },
        "additional_deps": {},
        "component_conventions": {
            "file_structure": "adapter-selected",
            "naming": "follow selected stack conventions",
            "test_co_location": False,
            "shared_ui_path": "",
            "theme_path": "",
        },
        "consistency_rules": [
            "Use the capability profile as the source of truth for framework choices",
            "Keep design tokens portable across the selected framework",
            "Do not add React-specific dependencies unless React is selected",
        ],
    }


def _empty_design(reason: str = "Non-UI profile") -> dict:
    return {
        "schema_version": "signalos.design_system.v1",
        "ui_library": {"name": "", "version": None, "reason": reason},
        "design_tokens": {
            "color_scheme": "light",
            "primary_color": "#3b82f6",
            "border_radius": "8px",
            "font_family": "Inter, sans-serif",
            "spacing_unit": 8,
        },
        "state_management": {"name": "", "version": None, "reason": reason},
        "data_layer": {"name": "", "version": None, "reason": reason},
        "form_handling": {"name": "", "version": None, "reason": reason},
        "additional_deps": {},
        "component_conventions": {
            "file_structure": "feature-based",
            "naming": "PascalCase",
            "test_co_location": True,
            "shared_ui_path": "src/ui",
            "theme_path": "src/ui/theme.ts",
        },
        "consistency_rules": [],
    }


# ---------------------------------------------------------------------------
# Dependency aggregation
# ---------------------------------------------------------------------------

def get_design_dependencies(design: dict) -> dict[str, str]:
    """Return all npm dependencies implied by the design system selection."""
    deps: dict[str, str] = {}

    # #44: the selected library's npm deps come straight from its registry entry.
    ui = design.get("ui_library", {}).get("name", "")
    ui_lib = get_ui_library(ui)
    if ui_lib:
        deps.update(ui_lib.dependencies)

    state = design.get("state_management", {}).get("name", "")
    if state == "zustand":
        deps["zustand"] = "^4.5.0"

    data = design.get("data_layer", {}).get("name", "")
    if data == "@tanstack/react-query":
        deps["@tanstack/react-query"] = "^5.40.0"

    form = design.get("form_handling", {}).get("name", "")
    if form == "react-hook-form":
        deps["react-hook-form"] = "^7.52.0"
        deps["zod"] = "^3.23.0"
        deps["@hookform/resolvers"] = "^3.6.0"

    # Additional deps from design (e.g. recharts)
    deps.update(design.get("additional_deps", {}))

    return deps


# ---------------------------------------------------------------------------
# Design instructions for the agent packet
# ---------------------------------------------------------------------------

def get_design_instructions(design: dict) -> dict:
    """Return design instructions for the agent packet.

    Does NOT write files. Returns what the agent should create:
    {
        "design_system_files": {
            "src/ui/theme.ts": { ... },
            "src/ui/index.ts": { ... },
            "src/ui/layouts/AppLayout.tsx": { ... },
            "src/ui/layouts/PageLayout.tsx": { ... },
        },
        "conventions": [...],
    }
    """
    ui_name = design.get("ui_library", {}).get("name", "")
    if not ui_name:
        return {"design_system_files": {}, "conventions": []}

    tokens = design.get("design_tokens", {})

    design_system_files: dict[str, dict] = {
        "src/ui/theme.ts": {
            "description": "Design tokens file exporting theme object with colors, spacing, radius, and fonts",
            "tokens": {
                "primary_color": tokens.get("primary_color", "#3b82f6"),
                "font_family": tokens.get("font_family", "Inter, sans-serif"),
                "spacing_unit": tokens.get("spacing_unit", 8),
                "border_radius": tokens.get("border_radius", "8px"),
                "color_scheme": tokens.get("color_scheme", "light"),
            },
        },
        "src/ui/index.ts": {
            "description": f"Barrel re-export from theme and {ui_name} UI library primitives",
        },
        "src/ui/layouts/AppLayout.tsx": {
            "description": f"Shared app shell with header, main content area, and footer. Uses {ui_name}.",
        },
        "src/ui/layouts/PageLayout.tsx": {
            "description": "Page-level wrapper with title prop and consistent spacing from theme",
        },
    }

    conventions = design.get("consistency_rules", [])

    return {
        "design_system_files": design_system_files,
        "conventions": conventions,
    }


# Backward-compatible alias -- old code that calls scaffold_design_system
# now gets a no-op that returns the file list the agent should create.
def scaffold_design_system(repo_root: Path, design: dict) -> list[str]:
    """Return list of design system files the agent should create.

    Does NOT write any files to disk. Returns the paths that the
    agent is expected to produce.
    """
    ui_name = design.get("ui_library", {}).get("name", "")
    if not ui_name:
        return []

    return [
        "src/ui/theme.ts",
        "src/ui/index.ts",
        "src/ui/layouts/AppLayout.tsx",
        "src/ui/layouts/PageLayout.tsx",
    ]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_design(design: dict, signalos_dir: Path) -> Path:
    """Write to ``.signalos/product/DESIGN.json``."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "DESIGN.json"
    path.write_text(
        json.dumps(design, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_design(signalos_dir: Path) -> dict | None:
    """Load design from ``.signalos/product/DESIGN.json``, or *None*."""
    path = signalos_dir / "product" / "DESIGN.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None
