# signalos_lib/product/design.py
# Design Phase -- selects UX library, design tokens, state management,
# and data layer based on product intent.  All deterministic -- no LLM.

from __future__ import annotations

__all__ = [
    "build_design_system",
    "get_design_dependencies",
    "get_design_instructions",
    "load_design",
    "write_design",
]

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_design_system(
    intent: dict,
    profile: str,
    blueprint: dict | None = None,
) -> dict:
    """Select design system, UX library, and tech composition for this product.

    Decision logic is fully deterministic -- no LLM, no network.

    Returns a ``signalos.design_system.v1`` dict describing the selected
    UI library, design tokens, state management, data layer, form handling,
    component conventions, and consistency rules.
    """
    # Generic / non-UI profiles get a minimal stub
    if profile == "generic":
        return _empty_design()

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
    """Select UI library based on product characteristics."""
    surfaces = set(intent.get("ux_surfaces", []))
    entities = intent.get("entities", [])
    product_type = intent.get("product_type", "custom")

    # Dashboard products -> shadcn + recharts
    if (
        product_type == "financial-dashboard"
        or "chart" in surfaces
        or "gauge" in surfaces
    ):
        return {
            "name": "shadcn/ui",
            "version": "latest",
            "reason": (
                "Data visualization product benefits from composable "
                "primitives + recharts"
            ),
        }

    # Forms-heavy / records -> Mantine (rich form controls, tables, dates)
    if (
        len(entities) >= 4
        or "form" in surfaces
        or "table" in surfaces
        or "calendar" in surfaces
    ):
        return {
            "name": "@mantine/core",
            "version": "^7.11.0",
            "reason": (
                "Entity-rich product needs robust form controls, "
                "tables, and date pickers"
            ),
        }

    # Default -> shadcn/ui (lightweight)
    return {
        "name": "shadcn/ui",
        "version": "latest",
        "reason": "General-purpose composable UI primitives",
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

def _empty_design() -> dict:
    return {
        "schema_version": "signalos.design_system.v1",
        "ui_library": {"name": "", "version": None, "reason": "Non-UI profile"},
        "design_tokens": {
            "color_scheme": "light",
            "primary_color": "#3b82f6",
            "border_radius": "8px",
            "font_family": "Inter, sans-serif",
            "spacing_unit": 8,
        },
        "state_management": {"name": "", "version": None, "reason": "Non-UI profile"},
        "data_layer": {"name": "", "version": None, "reason": "Non-UI profile"},
        "form_handling": {"name": "", "version": None, "reason": "Non-UI profile"},
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

    ui = design.get("ui_library", {}).get("name", "")
    if ui == "@mantine/core":
        deps["@mantine/core"] = "^7.11.0"
        deps["@mantine/hooks"] = "^7.11.0"
        deps["@mantine/form"] = "^7.11.0"
        deps["@mantine/dates"] = "^7.11.0"
        deps["@tabler/icons-react"] = "^3.5.0"
        deps["dayjs"] = "^1.11.11"
    elif ui == "shadcn/ui":
        deps["tailwindcss"] = "^3.4.0"
        deps["class-variance-authority"] = "^0.7.0"
        deps["clsx"] = "^2.1.0"
        deps["tailwind-merge"] = "^2.3.0"
        deps["lucide-react"] = "^0.378.0"

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
