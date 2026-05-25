"""Generate an interactive HTML prototype of the product design.

The preview is a REAL interactive prototype — not a static component
showcase. The user can click through navigation, see forms respond,
see tables sort. It's what an agent would produce if asked "build me
a working prototype of this app."

When an API key is available, the LLM generates the prototype
(full interactivity, real UX). When no key is available, a
deterministic fallback produces a reasonable static preview.
"""

from __future__ import annotations

__all__ = ["generate_design_preview_html"]

import html
import json
import os
from typing import Any


def generate_design_preview_html(design: dict, intent: dict) -> str:
    """Generate an interactive HTML prototype for design approval.

    With API key: dispatches to LLM to produce a full interactive
    single-page HTML app (clickable nav, working forms, sortable
    tables, responsive layout) using the selected design system.

    Without API key: falls back to a deterministic static preview
    showing layout, colors, typography, and component samples.

    The HTML is self-contained (CDN deps only, no local files).
    """
    # Try LLM-generated interactive prototype first
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("SIGNALOS_LLM_PROVIDER"):
        result = _generate_with_llm(design, intent)
        if result:
            return result

    # Fallback: deterministic static preview
    return _generate_deterministic(design, intent)


def _generate_with_llm(design: dict, intent: dict) -> str | None:
    """Use LLM to generate a full interactive HTML prototype."""
    try:
        from signalos_lib.harness import _resolve_provider, DEFAULT_MODEL
    except ImportError:
        return None

    ui_lib = design.get("ui_library", {})
    ui_name = ui_lib.get("name", "shadcn/ui") if isinstance(ui_lib, dict) else str(ui_lib)
    tokens = design.get("design_tokens", {})
    primary_color = tokens.get("primary_color", "#3b82f6")
    font = tokens.get("font_family", "Inter, sans-serif")
    entities = intent.get("entities", [])
    product_name = intent.get("product_name", "Product")
    workflows = intent.get("primary_workflows", [])
    surfaces = intent.get("ux_surfaces", [])

    prompt = f"""Generate a single self-contained HTML file that is a fully interactive
prototype of a product called "{product_name}".

Design system:
- UI library style: {ui_name}
- Primary color: {primary_color}
- Font: {font}
- Entities: {json.dumps(entities)}
- Workflows: {json.dumps(workflows)}
- UX surfaces: {json.dumps(surfaces)}

Requirements:
- Self-contained single HTML file (use CDN for Tailwind/styles)
- Fully interactive: clickable navigation between pages/views
- Working forms with validation feedback (client-side)
- Sortable/filterable tables with sample data
- Responsive layout (works on mobile and desktop)
- Sidebar navigation listing all entities
- Each entity has a list view and a detail/form view
- Use realistic sample data (5-10 rows per entity)
- No placeholder text like "Lorem ipsum" — use domain-realistic content
- Apply the primary color and font throughout
- Professional, production-quality appearance

Return ONLY the HTML. No explanation, no markdown fences, just the raw HTML starting with <!DOCTYPE html>."""

    try:
        provider = _resolve_provider()
        response_text, _, _ = provider.call(prompt, DEFAULT_MODEL)
    except Exception:
        return None

    # Extract HTML from response (handle markdown fences if present)
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    # Validate it's actual HTML
    if not text.startswith("<!") and not text.startswith("<html"):
        return None

    return text


def _generate_deterministic(design: dict, intent: dict) -> str:
    """Fallback: generate a static but visually complete preview.

    Shows layout, colors, typography, and component samples.

    Includes:
    - App shell / layout (header, sidebar, content area)
    - Sample cards (one per entity from intent)
    - Sample form (representing a typical entity form)
    - Sample table (representing a data list)
    - Sample buttons (primary, secondary, destructive)
    - Color palette display
    - Typography samples

    The HTML loads the UI library from CDN for preview purposes only.
    """
    ui_library = _get_ui_name(design)
    tokens = design.get("design_tokens", {})
    primary_color = tokens.get("primary_color", "#3b82f6")
    font_family = tokens.get("font_family", "Inter, sans-serif")
    border_radius = tokens.get("border_radius", "8px")
    spacing_unit = tokens.get("spacing_unit", 8)
    color_scheme = tokens.get("color_scheme", "light")

    product_name = intent.get("product_name", "My Product")
    entities = intent.get("entities", [])
    workflows = intent.get("primary_workflows", intent.get("workflows", []))

    if ui_library == "shadcn/ui":
        return _generate_shadcn_preview(
            product_name=product_name,
            entities=entities,
            workflows=workflows,
            primary_color=primary_color,
            font_family=font_family,
            border_radius=border_radius,
            spacing_unit=spacing_unit,
            color_scheme=color_scheme,
        )
    else:
        # Default: Mantine-style preview
        return _generate_mantine_preview(
            product_name=product_name,
            entities=entities,
            workflows=workflows,
            primary_color=primary_color,
            font_family=font_family,
            border_radius=border_radius,
            spacing_unit=spacing_unit,
            color_scheme=color_scheme,
        )


def _get_ui_name(design: dict) -> str:
    """Extract the UI library name from design dict."""
    ui = design.get("ui_library", {})
    if isinstance(ui, dict):
        return ui.get("name", "")
    return str(ui) if ui else ""


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(text))


def _humanize(name: str) -> str:
    """Convert PascalCase/camelCase/snake_case to human-readable."""
    import re
    # Insert space before uppercase letters in PascalCase
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Replace underscores/hyphens with spaces
    s = s.replace("_", " ").replace("-", " ")
    # Title case
    return s.strip().title()


def _derive_secondary_color(primary: str) -> str:
    """Derive a muted secondary color from the primary."""
    try:
        r = int(primary[1:3], 16)
        g = int(primary[3:5], 16)
        b = int(primary[5:7], 16)
        # Lighten by mixing with white
        r2 = min(255, r + (255 - r) * 7 // 10)
        g2 = min(255, g + (255 - g) * 7 // 10)
        b2 = min(255, b + (255 - b) * 7 // 10)
        return f"#{r2:02x}{g2:02x}{b2:02x}"
    except (ValueError, IndexError):
        return "#e2e8f0"


def _derive_dark_color(primary: str) -> str:
    """Derive a darker shade from the primary."""
    try:
        r = int(primary[1:3], 16)
        g = int(primary[3:5], 16)
        b = int(primary[5:7], 16)
        r2 = max(0, r * 6 // 10)
        g2 = max(0, g * 6 // 10)
        b2 = max(0, b * 6 // 10)
        return f"#{r2:02x}{g2:02x}{b2:02x}"
    except (ValueError, IndexError):
        return "#1e293b"


def _sample_fields_for_entity(entity: str) -> list[str]:
    """Generate plausible sample field names for an entity."""
    base = _humanize(entity).lower()
    # Common fields based on entity semantics
    if any(w in base for w in ("patient", "person", "user", "contact")):
        return ["Full Name", "Email", "Phone", "Date of Birth"]
    if any(w in base for w in ("note", "record", "log", "entry")):
        return ["Title", "Content", "Created Date", "Author"]
    if any(w in base for w in ("order", "invoice", "transaction")):
        return ["Order ID", "Amount", "Status", "Date"]
    if any(w in base for w in ("product", "item", "inventory")):
        return ["Name", "Category", "Price", "Stock"]
    if any(w in base for w in ("task", "ticket", "issue")):
        return ["Title", "Assignee", "Priority", "Due Date"]
    if any(w in base for w in ("prescription", "medication", "drug")):
        return ["Medication", "Dosage", "Frequency", "Prescriber"]
    if any(w in base for w in ("lab", "test", "result")):
        return ["Test Name", "Result Value", "Reference Range", "Date"]
    # Default fields
    return ["Name", "Description", "Status", "Created"]


# ---------------------------------------------------------------------------
# Mantine-style preview
# ---------------------------------------------------------------------------

def _generate_mantine_preview(
    product_name: str,
    entities: list[str],
    workflows: list[str],
    primary_color: str,
    font_family: str,
    border_radius: str,
    spacing_unit: int,
    color_scheme: str,
) -> str:
    """Generate HTML that uses Mantine's CSS variables and component styling."""
    secondary_color = _derive_secondary_color(primary_color)
    dark_color = _derive_dark_color(primary_color)

    # Build sidebar nav items
    nav_items = ""
    for entity in entities[:8]:
        label = _esc(_humanize(entity))
        nav_items += f'        <a class="nav-link" href="#">{label}</a>\n'
    if not nav_items:
        nav_items = '        <a class="nav-link" href="#">Dashboard</a>\n'

    # Build entity cards
    cards_html = ""
    for entity in entities[:6]:
        label = _esc(_humanize(entity))
        fields = _sample_fields_for_entity(entity)
        fields_html = "".join(
            f'          <div class="card-field"><span class="field-label">{_esc(f)}</span><span class="field-value">Sample data</span></div>\n'
            for f in fields[:3]
        )
        cards_html += f"""      <div class="card">
        <div class="card-header">{label}</div>
        <div class="card-body">
{fields_html}        </div>
      </div>
"""

    if not cards_html:
        cards_html = """      <div class="card">
        <div class="card-header">Sample Item</div>
        <div class="card-body">
          <div class="card-field"><span class="field-label">Name</span><span class="field-value">Sample data</span></div>
          <div class="card-field"><span class="field-label">Status</span><span class="field-value">Active</span></div>
        </div>
      </div>
"""

    # Build sample table
    first_entity = entities[0] if entities else "Item"
    table_fields = _sample_fields_for_entity(first_entity)
    thead = "".join(f"<th>{_esc(f)}</th>" for f in table_fields)
    tbody_rows = ""
    for i in range(4):
        cells = "".join(f"<td>Sample {i+1}</td>" for _ in table_fields)
        tbody_rows += f"          <tr>{cells}</tr>\n"

    # Build sample form
    form_entity = entities[0] if entities else "Item"
    form_fields = _sample_fields_for_entity(form_entity)
    form_inputs = ""
    for field in form_fields[:4]:
        form_inputs += f"""        <div class="form-group">
          <label class="form-label">{_esc(field)}</label>
          <input type="text" class="form-input" placeholder="Enter {_esc(field.lower())}">
        </div>
"""

    bg_color = "#ffffff" if color_scheme == "light" else "#1a1b1e"
    text_color = "#212529" if color_scheme == "light" else "#c1c2c5"
    surface_color = "#f8f9fa" if color_scheme == "light" else "#25262b"
    border_color = "#dee2e6" if color_scheme == "light" else "#373a40"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(product_name)} - Design Preview</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --primary: {primary_color};
    --primary-light: {secondary_color};
    --primary-dark: {dark_color};
    --bg: {bg_color};
    --text: {text_color};
    --surface: {surface_color};
    --border: {border_color};
    --radius: {border_radius};
    --spacing: {spacing_unit}px;
    --font: {font_family};
  }}

  body {{
    font-family: var(--font);
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    min-height: 100vh;
  }}

  /* Layout */
  .app-shell {{
    display: grid;
    grid-template-columns: 240px 1fr;
    grid-template-rows: 56px 1fr;
    min-height: 100vh;
  }}

  .app-header {{
    grid-column: 1 / -1;
    background: var(--primary);
    color: #fff;
    display: flex;
    align-items: center;
    padding: 0 calc(var(--spacing) * 3);
    gap: calc(var(--spacing) * 4);
    box-shadow: 0 1px 3px rgba(0,0,0,0.12);
  }}

  .app-header h1 {{
    font-size: 1.125rem;
    font-weight: 600;
    letter-spacing: -0.01em;
  }}

  .header-nav {{
    display: flex;
    gap: calc(var(--spacing) * 2);
  }}

  .header-nav a {{
    color: rgba(255,255,255,0.85);
    text-decoration: none;
    font-size: 0.875rem;
    font-weight: 500;
    padding: calc(var(--spacing) * 0.5) var(--spacing);
    border-radius: var(--radius);
    transition: background 0.15s;
  }}

  .header-nav a:hover {{
    background: rgba(255,255,255,0.15);
    color: #fff;
  }}

  .app-sidebar {{
    background: var(--surface);
    border-right: 1px solid var(--border);
    padding: calc(var(--spacing) * 2) 0;
    overflow-y: auto;
  }}

  .nav-section {{
    padding: calc(var(--spacing) * 1.5) calc(var(--spacing) * 2);
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--primary-dark);
    opacity: 0.7;
  }}

  .nav-link {{
    display: block;
    padding: calc(var(--spacing) * 1) calc(var(--spacing) * 2.5);
    font-size: 0.875rem;
    color: var(--text);
    text-decoration: none;
    border-radius: 0 var(--radius) var(--radius) 0;
    margin-right: calc(var(--spacing) * 1);
    transition: background 0.15s, color 0.15s;
  }}

  .nav-link:first-of-type {{
    background: var(--primary-light);
    color: var(--primary-dark);
    font-weight: 500;
  }}

  .nav-link:hover {{
    background: var(--primary-light);
  }}

  .app-content {{
    padding: calc(var(--spacing) * 3);
    overflow-y: auto;
  }}

  /* Sections */
  .section {{
    margin-bottom: calc(var(--spacing) * 4);
  }}

  .section-title {{
    font-size: 1.25rem;
    font-weight: 600;
    margin-bottom: calc(var(--spacing) * 2);
    color: var(--text);
  }}

  /* Cards */
  .card-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: calc(var(--spacing) * 2);
  }}

  .card {{
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    transition: box-shadow 0.15s;
  }}

  .card:hover {{
    box-shadow: 0 4px 12px rgba(0,0,0,0.08);
  }}

  .card-header {{
    padding: calc(var(--spacing) * 1.5) calc(var(--spacing) * 2);
    font-weight: 600;
    font-size: 0.9375rem;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }}

  .card-body {{
    padding: calc(var(--spacing) * 2);
  }}

  .card-field {{
    display: flex;
    justify-content: space-between;
    padding: calc(var(--spacing) * 0.75) 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.8125rem;
  }}

  .card-field:last-child {{
    border-bottom: none;
  }}

  .field-label {{
    font-weight: 500;
    color: var(--text);
    opacity: 0.7;
  }}

  .field-value {{
    color: var(--text);
  }}

  /* Table */
  .table-wrapper {{
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8125rem;
  }}

  th {{
    background: var(--surface);
    padding: calc(var(--spacing) * 1.5) calc(var(--spacing) * 2);
    text-align: left;
    font-weight: 600;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text);
    opacity: 0.7;
    border-bottom: 1px solid var(--border);
  }}

  td {{
    padding: calc(var(--spacing) * 1.5) calc(var(--spacing) * 2);
    border-bottom: 1px solid var(--border);
  }}

  tr:last-child td {{
    border-bottom: none;
  }}

  tr:hover td {{
    background: var(--primary-light);
  }}

  /* Form */
  .form-card {{
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: calc(var(--spacing) * 3);
    max-width: 480px;
  }}

  .form-group {{
    margin-bottom: calc(var(--spacing) * 2);
  }}

  .form-label {{
    display: block;
    font-size: 0.8125rem;
    font-weight: 500;
    margin-bottom: calc(var(--spacing) * 0.5);
    color: var(--text);
  }}

  .form-input {{
    width: 100%;
    padding: calc(var(--spacing) * 1) calc(var(--spacing) * 1.5);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font-family: var(--font);
    font-size: 0.875rem;
    background: var(--bg);
    color: var(--text);
    transition: border-color 0.15s, box-shadow 0.15s;
  }}

  .form-input:focus {{
    outline: none;
    border-color: var(--primary);
    box-shadow: 0 0 0 3px var(--primary-light);
  }}

  /* Buttons */
  .btn-group {{
    display: flex;
    gap: calc(var(--spacing) * 1.5);
    flex-wrap: wrap;
  }}

  .btn {{
    padding: calc(var(--spacing) * 1) calc(var(--spacing) * 2.5);
    border-radius: var(--radius);
    font-family: var(--font);
    font-size: 0.875rem;
    font-weight: 500;
    border: none;
    cursor: pointer;
    transition: background 0.15s, transform 0.1s;
  }}

  .btn:active {{
    transform: scale(0.97);
  }}

  .btn-primary {{
    background: var(--primary);
    color: #fff;
  }}

  .btn-primary:hover {{
    background: var(--primary-dark);
  }}

  .btn-secondary {{
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
  }}

  .btn-secondary:hover {{
    background: var(--primary-light);
  }}

  .btn-destructive {{
    background: #e03131;
    color: #fff;
  }}

  .btn-destructive:hover {{
    background: #c92a2a;
  }}

  /* Color palette */
  .palette {{
    display: flex;
    gap: calc(var(--spacing) * 1);
    flex-wrap: wrap;
  }}

  .swatch {{
    width: 64px;
    height: 64px;
    border-radius: var(--radius);
    display: flex;
    align-items: flex-end;
    justify-content: center;
    padding-bottom: 4px;
    font-size: 0.625rem;
    font-weight: 500;
    color: #fff;
    text-shadow: 0 1px 2px rgba(0,0,0,0.4);
    border: 1px solid var(--border);
  }}

  /* Typography samples */
  .type-samples {{
    display: flex;
    flex-direction: column;
    gap: calc(var(--spacing) * 2);
  }}

  .type-sample {{
    display: flex;
    align-items: baseline;
    gap: calc(var(--spacing) * 3);
  }}

  .type-label {{
    font-size: 0.75rem;
    color: var(--text);
    opacity: 0.6;
    min-width: 80px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}

  /* Badge */
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 99px;
    font-size: 0.6875rem;
    font-weight: 600;
    background: var(--primary-light);
    color: var(--primary-dark);
  }}

  /* Responsive */
  @media (max-width: 768px) {{
    .app-shell {{
      grid-template-columns: 1fr;
    }}
    .app-sidebar {{
      display: none;
    }}
    .card-grid {{
      grid-template-columns: 1fr;
    }}
  }}
</style>
</head>
<body>
<div class="app-shell">
  <header class="app-header">
    <h1>{_esc(product_name)}</h1>
    <nav class="header-nav">
      <a href="#">Overview</a>
      <a href="#">Records</a>
      <a href="#">Reports</a>
      <a href="#">Settings</a>
    </nav>
  </header>

  <aside class="app-sidebar">
    <div class="nav-section">Navigation</div>
{nav_items}  </aside>

  <main class="app-content">
    <!-- Cards Section -->
    <div class="section">
      <h2 class="section-title">Overview</h2>
      <div class="card-grid">
{cards_html}      </div>
    </div>

    <!-- Table Section -->
    <div class="section">
      <h2 class="section-title">{_esc(_humanize(first_entity))} Records</h2>
      <div class="table-wrapper">
        <table>
          <thead><tr>{thead}</tr></thead>
          <tbody>
{tbody_rows}          </tbody>
        </table>
      </div>
    </div>

    <!-- Form Section -->
    <div class="section">
      <h2 class="section-title">New {_esc(_humanize(form_entity))}</h2>
      <div class="form-card">
{form_inputs}        <div class="btn-group">
          <button class="btn btn-primary">Save</button>
          <button class="btn btn-secondary">Cancel</button>
        </div>
      </div>
    </div>

    <!-- Buttons Section -->
    <div class="section">
      <h2 class="section-title">Actions</h2>
      <div class="btn-group">
        <button class="btn btn-primary">Create new</button>
        <button class="btn btn-secondary">Export</button>
        <button class="btn btn-destructive">Delete</button>
        <span class="badge">Active</span>
      </div>
    </div>

    <!-- Color Palette -->
    <div class="section">
      <h2 class="section-title">Color Palette</h2>
      <div class="palette">
        <div class="swatch" style="background:{primary_color}">Primary</div>
        <div class="swatch" style="background:{dark_color}">Dark</div>
        <div class="swatch" style="background:{secondary_color};color:{dark_color};text-shadow:none">Light</div>
        <div class="swatch" style="background:{surface_color};color:{text_color};text-shadow:none;border:1px solid {border_color}">Surface</div>
        <div class="swatch" style="background:{text_color}">Text</div>
      </div>
    </div>

    <!-- Typography -->
    <div class="section">
      <h2 class="section-title">Typography</h2>
      <div class="type-samples">
        <div class="type-sample">
          <span class="type-label">Heading</span>
          <span style="font-size:1.5rem;font-weight:700">{_esc(product_name)}</span>
        </div>
        <div class="type-sample">
          <span class="type-label">Subhead</span>
          <span style="font-size:1.125rem;font-weight:600">Section Title</span>
        </div>
        <div class="type-sample">
          <span class="type-label">Body</span>
          <span style="font-size:0.875rem">This is regular body text used for descriptions and content throughout the application.</span>
        </div>
        <div class="type-sample">
          <span class="type-label">Caption</span>
          <span style="font-size:0.75rem;opacity:0.7">Supplementary information and metadata</span>
        </div>
      </div>
    </div>
  </main>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# shadcn/ui (Tailwind) preview
# ---------------------------------------------------------------------------

def _generate_shadcn_preview(
    product_name: str,
    entities: list[str],
    workflows: list[str],
    primary_color: str,
    font_family: str,
    border_radius: str,
    spacing_unit: int,
    color_scheme: str,
) -> str:
    """Generate HTML that uses Tailwind CSS CDN with shadcn-style components."""
    secondary_color = _derive_secondary_color(primary_color)
    dark_color = _derive_dark_color(primary_color)

    # Build sidebar nav items
    nav_items = ""
    for entity in entities[:8]:
        label = _esc(_humanize(entity))
        nav_items += f'          <a href="#" class="block px-3 py-2 rounded-md text-sm hover:bg-gray-100 text-gray-700">{label}</a>\n'
    if not nav_items:
        nav_items += '          <a href="#" class="block px-3 py-2 rounded-md text-sm hover:bg-gray-100 text-gray-700">Dashboard</a>\n'

    # Build entity cards
    cards_html = ""
    for entity in entities[:6]:
        label = _esc(_humanize(entity))
        fields = _sample_fields_for_entity(entity)
        fields_html = "".join(
            f'              <div class="flex justify-between py-1.5 text-sm border-b border-gray-100 last:border-0"><span class="text-gray-500">{_esc(f)}</span><span>Sample data</span></div>\n'
            for f in fields[:3]
        )
        cards_html += f"""          <div class="rounded-lg border bg-white shadow-sm">
            <div class="px-4 py-3 border-b bg-gray-50/50 font-semibold text-sm">{label}</div>
            <div class="px-4 py-3">
{fields_html}            </div>
          </div>
"""

    if not cards_html:
        cards_html = """          <div class="rounded-lg border bg-white shadow-sm">
            <div class="px-4 py-3 border-b bg-gray-50/50 font-semibold text-sm">Sample Item</div>
            <div class="px-4 py-3">
              <div class="flex justify-between py-1.5 text-sm"><span class="text-gray-500">Name</span><span>Sample data</span></div>
            </div>
          </div>
"""

    # Table
    first_entity = entities[0] if entities else "Item"
    table_fields = _sample_fields_for_entity(first_entity)
    thead = "".join(f'<th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{_esc(f)}</th>' for f in table_fields)
    tbody_rows = ""
    for i in range(4):
        cells = "".join(f'<td class="px-4 py-3 text-sm">Sample {i+1}</td>' for _ in table_fields)
        tbody_rows += f'            <tr class="border-b hover:bg-gray-50">{cells}</tr>\n'

    # Form
    form_entity = entities[0] if entities else "Item"
    form_fields = _sample_fields_for_entity(form_entity)
    form_inputs = ""
    for field in form_fields[:4]:
        form_inputs += f"""            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">{_esc(field)}</label>
              <input type="text" class="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-offset-1" placeholder="Enter {_esc(field.lower())}" style="focus:ring-color:{primary_color}">
            </div>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(product_name)} - Design Preview</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script>
tailwind.config = {{
  theme: {{
    extend: {{
      colors: {{
        brand: {{
          DEFAULT: '{primary_color}',
          light: '{secondary_color}',
          dark: '{dark_color}',
        }}
      }},
      fontFamily: {{
        sans: ['{font_family.split(",")[0].strip()}', 'sans-serif'],
      }},
      borderRadius: {{
        DEFAULT: '{border_radius}',
      }}
    }}
  }}
}}
</script>
<style>
  body {{ font-family: {font_family}; }}
</style>
</head>
<body class="bg-gray-50 text-gray-900 min-h-screen">
  <div class="min-h-screen grid" style="grid-template-columns:240px 1fr;grid-template-rows:56px 1fr">
    <!-- Header -->
    <header class="col-span-2 flex items-center px-6 gap-8 shadow-sm" style="background:{primary_color}">
      <h1 class="text-white font-semibold text-lg">{_esc(product_name)}</h1>
      <nav class="flex gap-3">
        <a href="#" class="text-white/80 hover:text-white text-sm font-medium px-2 py-1 rounded">Overview</a>
        <a href="#" class="text-white/80 hover:text-white text-sm font-medium px-2 py-1 rounded">Records</a>
        <a href="#" class="text-white/80 hover:text-white text-sm font-medium px-2 py-1 rounded">Reports</a>
        <a href="#" class="text-white/80 hover:text-white text-sm font-medium px-2 py-1 rounded">Settings</a>
      </nav>
    </header>

    <!-- Sidebar -->
    <aside class="bg-white border-r p-4 overflow-y-auto">
      <div class="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2 px-3">Navigation</div>
{nav_items}    </aside>

    <!-- Main Content -->
    <main class="p-6 overflow-y-auto space-y-8">
      <!-- Cards -->
      <section>
        <h2 class="text-xl font-semibold mb-4">Overview</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
{cards_html}        </div>
      </section>

      <!-- Table -->
      <section>
        <h2 class="text-xl font-semibold mb-4">{_esc(_humanize(first_entity))} Records</h2>
        <div class="rounded-lg border bg-white overflow-hidden">
          <table class="w-full">
            <thead class="bg-gray-50"><tr>{thead}</tr></thead>
            <tbody>
{tbody_rows}            </tbody>
          </table>
        </div>
      </section>

      <!-- Form -->
      <section>
        <h2 class="text-xl font-semibold mb-4">New {_esc(_humanize(form_entity))}</h2>
        <div class="rounded-lg border bg-white p-6 max-w-lg space-y-4">
{form_inputs}          <div class="flex gap-3 pt-2">
            <button class="px-4 py-2 rounded-md text-white text-sm font-medium" style="background:{primary_color}">Save</button>
            <button class="px-4 py-2 rounded-md border text-sm font-medium text-gray-700 hover:bg-gray-50">Cancel</button>
          </div>
        </div>
      </section>

      <!-- Buttons -->
      <section>
        <h2 class="text-xl font-semibold mb-4">Actions</h2>
        <div class="flex flex-wrap gap-3 items-center">
          <button class="px-4 py-2 rounded-md text-white text-sm font-medium" style="background:{primary_color}">Create new</button>
          <button class="px-4 py-2 rounded-md border text-sm font-medium text-gray-700 hover:bg-gray-50">Export</button>
          <button class="px-4 py-2 rounded-md text-white text-sm font-medium bg-red-600 hover:bg-red-700">Delete</button>
          <span class="inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold" style="background:{secondary_color};color:{dark_color}">Active</span>
        </div>
      </section>

      <!-- Color Palette -->
      <section>
        <h2 class="text-xl font-semibold mb-4">Color Palette</h2>
        <div class="flex flex-wrap gap-3">
          <div class="w-16 h-16 rounded-lg flex items-end justify-center pb-1 text-white text-xs font-medium shadow-sm" style="background:{primary_color}">Primary</div>
          <div class="w-16 h-16 rounded-lg flex items-end justify-center pb-1 text-white text-xs font-medium shadow-sm" style="background:{dark_color}">Dark</div>
          <div class="w-16 h-16 rounded-lg flex items-end justify-center pb-1 text-xs font-medium border shadow-sm" style="background:{secondary_color};color:{dark_color}">Light</div>
          <div class="w-16 h-16 rounded-lg flex items-end justify-center pb-1 text-xs font-medium border shadow-sm bg-gray-50 text-gray-600">Surface</div>
          <div class="w-16 h-16 rounded-lg flex items-end justify-center pb-1 text-white text-xs font-medium shadow-sm bg-gray-900">Text</div>
        </div>
      </section>

      <!-- Typography -->
      <section>
        <h2 class="text-xl font-semibold mb-4">Typography</h2>
        <div class="space-y-4">
          <div class="flex items-baseline gap-6">
            <span class="text-xs uppercase tracking-wide text-gray-400 w-20">Heading</span>
            <span class="text-2xl font-bold">{_esc(product_name)}</span>
          </div>
          <div class="flex items-baseline gap-6">
            <span class="text-xs uppercase tracking-wide text-gray-400 w-20">Subhead</span>
            <span class="text-lg font-semibold">Section Title</span>
          </div>
          <div class="flex items-baseline gap-6">
            <span class="text-xs uppercase tracking-wide text-gray-400 w-20">Body</span>
            <span class="text-sm">This is regular body text used for descriptions and content throughout the application.</span>
          </div>
          <div class="flex items-baseline gap-6">
            <span class="text-xs uppercase tracking-wide text-gray-400 w-20">Caption</span>
            <span class="text-xs text-gray-500">Supplementary information and metadata</span>
          </div>
        </div>
      </section>
    </main>
  </div>
</body>
</html>"""
