# signalos_lib/product/design.py
# Design Phase — selects UX library, design tokens, state management,
# and data layer based on product intent.  All deterministic — no LLM.

from __future__ import annotations

__all__ = [
    "build_design_system",
    "get_design_dependencies",
    "load_design",
    "scaffold_design_system",
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

    Decision logic is fully deterministic — no LLM, no network.

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
            "No inline styles — use design tokens via theme",
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
    """Always zustand for now — simple, scales well."""
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
        "reason": "No external data sources detected — local state sufficient",
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
        "reason": "Simple inputs — native controlled components sufficient",
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
# Scaffold design-system files into the repo
# ---------------------------------------------------------------------------

def scaffold_design_system(repo_root: Path, design: dict) -> list[str]:
    """Create the shared UI layer files based on design decisions.

    Creates:
    - src/ui/theme.ts      -- design tokens (colors, spacing, fonts)
    - src/ui/index.ts      -- re-exports of UI primitives
    - src/ui/layouts/AppLayout.tsx  -- shared app shell / layout
    - src/ui/layouts/PageLayout.tsx -- consistent page wrapper

    Returns list of created file paths (relative to *repo_root*).
    """
    ui_name = design.get("ui_library", {}).get("name", "")
    if not ui_name:
        return []  # non-UI profile, nothing to scaffold

    tokens = design.get("design_tokens", {})
    created: list[str] = []

    def _write(rel: str, content: str) -> None:
        target = repo_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created.append(rel)

    # --- theme.ts ---
    _write("src/ui/theme.ts", _build_theme_ts(tokens, ui_name))

    # --- index.ts ---
    _write("src/ui/index.ts", _build_index_ts(ui_name))

    # --- AppLayout.tsx ---
    _write("src/ui/layouts/AppLayout.tsx", _build_app_layout(ui_name))

    # --- PageLayout.tsx ---
    _write("src/ui/layouts/PageLayout.tsx", _build_page_layout(ui_name))

    return created


# ---------------------------------------------------------------------------
# Template builders
# ---------------------------------------------------------------------------

def _build_theme_ts(tokens: dict, ui_name: str) -> str:
    primary = tokens.get("primary_color", "#3b82f6")
    radius = tokens.get("border_radius", "8px")
    font = tokens.get("font_family", "Inter, sans-serif")
    spacing_unit = tokens.get("spacing_unit", 8)

    return (
        "export const theme = {\n"
        "  colors: {\n"
        f'    primary: "{primary}",\n'
        '    background: "#ffffff",\n'
        '    surface: "#f8fafc",\n'
        '    text: "#0f172a",\n'
        '    muted: "#64748b",\n'
        '    border: "#e2e8f0",\n'
        "  },\n"
        "  spacing: {\n"
        f"    xs: {spacing_unit // 2},\n"
        f"    sm: {spacing_unit},\n"
        f"    md: {spacing_unit * 2},\n"
        f"    lg: {spacing_unit * 3},\n"
        f"    xl: {spacing_unit * 4},\n"
        "  },\n"
        "  radius: {\n"
        f'    sm: "{_halve_px(radius)}",\n'
        f'    md: "{radius}",\n'
        f'    lg: "{_double_px(radius)}",\n'
        "  },\n"
        "  fonts: {\n"
        f'    body: "{font}",\n'
        '    mono: "JetBrains Mono, monospace",\n'
        "  },\n"
        "} as const;\n"
        "\n"
        "export type Theme = typeof theme;\n"
    )


def _halve_px(val: str) -> str:
    """'8px' -> '4px'."""
    try:
        num = int(val.replace("px", ""))
        return f"{num // 2}px"
    except (ValueError, AttributeError):
        return val


def _double_px(val: str) -> str:
    """'8px' -> '16px'."""
    try:
        num = int(val.replace("px", ""))
        return f"{num * 2}px"
    except (ValueError, AttributeError):
        return val


def _build_index_ts(ui_name: str) -> str:
    lines = [
        "// Shared UI primitives — all components import from here\n",
        "export { theme } from './theme';\n",
    ]

    if ui_name == "@mantine/core":
        lines.append(
            "export { Button, TextInput, Table, Select, Modal, Tabs, "
            "Group, Stack, Paper, Title, Text } from '@mantine/core';\n"
        )
    elif ui_name == "shadcn/ui":
        lines.append(
            "// Re-export shadcn primitives as they are added\n"
            "// e.g. export { Button } from './button';\n"
        )

    return "".join(lines)


def _build_app_layout(ui_name: str) -> str:
    if ui_name == "@mantine/core":
        return (
            "import React from 'react';\n"
            "import { AppShell, Group, Title } from '@mantine/core';\n"
            "\n"
            "interface AppLayoutProps {\n"
            "  children: React.ReactNode;\n"
            "}\n"
            "\n"
            "export function AppLayout({ children }: AppLayoutProps) {\n"
            "  return (\n"
            "    <AppShell header={{ height: 56 }} padding=\"md\">\n"
            "      <AppShell.Header>\n"
            "        <Group h=\"100%\" px=\"md\">\n"
            "          <Title order={3}>SignalOS Product</Title>\n"
            "        </Group>\n"
            "      </AppShell.Header>\n"
            "      <AppShell.Main>{children}</AppShell.Main>\n"
            "    </AppShell>\n"
            "  );\n"
            "}\n"
        )

    # shadcn / default
    return (
        "import React from 'react';\n"
        "import { theme } from '../theme';\n"
        "\n"
        "interface AppLayoutProps {\n"
        "  children: React.ReactNode;\n"
        "}\n"
        "\n"
        "export function AppLayout({ children }: AppLayoutProps) {\n"
        "  return (\n"
        "    <div style={{ minHeight: '100vh', background: theme.colors.background }}>\n"
        "      <header style={{\n"
        "        height: 56,\n"
        "        display: 'flex',\n"
        "        alignItems: 'center',\n"
        "        padding: `0 ${theme.spacing.md}px`,\n"
        "        borderBottom: `1px solid ${theme.colors.border}`,\n"
        "      }}>\n"
        "        <h1 style={{ fontSize: 18, margin: 0 }}>SignalOS Product</h1>\n"
        "      </header>\n"
        "      <main style={{ padding: theme.spacing.md }}>{children}</main>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )


def _build_page_layout(ui_name: str) -> str:
    if ui_name == "@mantine/core":
        return (
            "import React from 'react';\n"
            "import { Stack, Title } from '@mantine/core';\n"
            "\n"
            "interface PageLayoutProps {\n"
            "  title: string;\n"
            "  children: React.ReactNode;\n"
            "}\n"
            "\n"
            "export function PageLayout({ title, children }: PageLayoutProps) {\n"
            "  return (\n"
            "    <Stack gap=\"md\">\n"
            "      <Title order={2}>{title}</Title>\n"
            "      {children}\n"
            "    </Stack>\n"
            "  );\n"
            "}\n"
        )

    # shadcn / default
    return (
        "import React from 'react';\n"
        "import { theme } from '../theme';\n"
        "\n"
        "interface PageLayoutProps {\n"
        "  title: string;\n"
        "  children: React.ReactNode;\n"
        "}\n"
        "\n"
        "export function PageLayout({ title, children }: PageLayoutProps) {\n"
        "  return (\n"
        "    <div style={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.md }}>\n"
        "      <h2 style={{ margin: 0 }}>{title}</h2>\n"
        "      {children}\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )


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
