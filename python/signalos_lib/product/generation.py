# signalos_lib/product/generation.py
# Phase P7 - Generic Product Generation
#
# Generates product files from intent, blueprint, stack adapter, and
# approved wave scope.  Every generated file is tracked in a manifest.

from __future__ import annotations

__all__ = [
    "build_generation_manifest",
    "check_file_ownership",
    "compute_sha256_lf",
    "generate_file_content",
    "generate_product",
    "get_blueprint_dependencies",
    "link_generation_to_acceptance",
    "load_generation_manifest",
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
# File content generators - react-vite profile
# ---------------------------------------------------------------------------

def _react_component(name: str, entity: dict | None) -> str:
    """Generate a minimal React component."""
    fields_comment = ""
    if entity and "fields" in entity:
        fields_comment = f"// Entity fields: {', '.join(entity['fields'])}\n"
    return (
        f"import {{ useState }} from 'react';\n"
        f"\n"
        f"{fields_comment}"
        f"interface {name}Props {{\n"
        f"  title?: string;\n"
        f"}}\n"
        f"\n"
        f"function {name}({{ title = '{name}' }}: {name}Props) {{\n"
        f"  return (\n"
        f"    <div data-testid=\"{name}\">\n"
        f"      <h2>{{title}}</h2>\n"
        f"    </div>\n"
        f"  );\n"
        f"}}\n"
        f"\n"
        f"export default {name};\n"
    )


def _react_test(name: str) -> str:
    """Generate a minimal vitest test for a React component."""
    return (
        f"import {{ render, screen }} from '@testing-library/react';\n"
        f"import {{ describe, expect, it }} from 'vitest';\n"
        f"import {name} from './{name}';\n"
        f"\n"
        f"describe('{name}', () => {{\n"
        f"  it('renders without crashing', () => {{\n"
        f"    render(<{name} />);\n"
        f"    expect(screen.getByTestId('{name}')).toBeDefined();\n"
        f"  }});\n"
        f"\n"
        f"  it('displays the title', () => {{\n"
        f"    render(<{name} title=\"Test\" />);\n"
        f"    expect(screen.getByText('Test')).toBeDefined();\n"
        f"  }});\n"
        f"}});\n"
    )


def _react_types(entities: list[dict]) -> str:
    """Generate TypeScript type definitions from blueprint entities."""
    lines = ["// Auto-generated type definitions\n"]
    for entity in entities:
        name = _to_pascal_case(entity["name"])
        lines.append(f"export interface {name} {{")
        for field in entity.get("fields", []):
            ts_type = "string"
            if field == "id":
                ts_type = "string"
            elif field.endswith("_id"):
                ts_type = "string"
            elif field in ("amount", "value", "mrr", "burn_rate",
                           "runway_months", "mrr_lost", "balance"):
                ts_type = "number"
            elif field in ("recurring"):
                ts_type = "boolean"
            lines.append(f"  {field}: {ts_type};")
        lines.append("}\n")
    return "\n".join(lines) + "\n"


def _react_app_registration(component_names: list[str]) -> str:
    """Generate an App.tsx that imports and renders components."""
    imports = "\n".join(
        f"import {name} from './components/{name}';"
        for name in component_names
    )
    components = "\n      ".join(f"<{name} />" for name in component_names)
    return (
        f"{imports}\n"
        f"\n"
        f"function App() {{\n"
        f"  return (\n"
        f"    <div className=\"app\">\n"
        f"      <h1>SignalOS Product</h1>\n"
        f"      {components}\n"
        f"    </div>\n"
        f"  );\n"
        f"}}\n"
        f"\n"
        f"export default App;\n"
    )


# ---------------------------------------------------------------------------
# File content generators - generic (Python) profile
# ---------------------------------------------------------------------------

def _python_module(name: str, entity: dict | None) -> str:
    """Generate a minimal Python module for an entity."""
    snake = _to_snake(name)
    fields_comment = ""
    if entity and "fields" in entity:
        fields_comment = f"# Fields: {', '.join(entity['fields'])}\n"
    return (
        f'"""{name} module."""\n'
        f"\n"
        f"from __future__ import annotations\n"
        f"\n"
        f"{fields_comment}"
        f"\n"
        f"class {name}:\n"
        f"    \"\"\"Represents a {name} entity.\"\"\"\n"
        f"\n"
        f"    def __init__(self, **kwargs):\n"
        f"        for key, value in kwargs.items():\n"
        f"            setattr(self, key, value)\n"
        f"\n"
        f"    def to_dict(self):\n"
        f"        return self.__dict__.copy()\n"
    )


def _python_test(name: str) -> str:
    """Generate a minimal Python unittest file."""
    snake = _to_snake(name)
    return (
        f"import unittest\n"
        f"\n"
        f"from {snake} import {name}\n"
        f"\n"
        f"\n"
        f"class Test{name}(unittest.TestCase):\n"
        f"    def test_create(self):\n"
        f"        obj = {name}(id='1')\n"
        f"        self.assertEqual(obj.id, '1')\n"
        f"\n"
        f"    def test_to_dict(self):\n"
        f"        obj = {name}(id='1', name='test')\n"
        f"        data = obj.to_dict()\n"
        f"        self.assertIn('id', data)\n"
        f"\n"
        f"\n"
        f"if __name__ == '__main__':\n"
        f"    unittest.main()\n"
    )


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
    # Capitalize each word; filter out empty strings from leading/trailing separators
    return "".join(word.capitalize() for word in words if word)


# ---------------------------------------------------------------------------
# Blueprint -> component mapping
# ---------------------------------------------------------------------------

_TASK_MANAGEMENT_COMPONENTS = [
    {"component": "TaskList", "entity_name": "Task", "surface": "task-list"},
    {"component": "TaskForm", "entity_name": "Task", "surface": "task-detail"},
    {"component": "ProjectBoard", "entity_name": "Project", "surface": "project-board"},
]

_FINANCIAL_DASHBOARD_COMPONENTS = [
    {"component": "RevenueChart", "entity_name": "Revenue", "surface": "revenue-chart"},
    {"component": "ChurnChart", "entity_name": "Churn", "surface": "churn-chart"},
    {"component": "RunwayGauge", "entity_name": "CashRunway", "surface": "runway-gauge"},
]

_BLUEPRINT_COMPONENT_MAP: dict[str, list[dict[str, str]]] = {
    "task-management": _TASK_MANAGEMENT_COMPONENTS,
    "financial-dashboard": _FINANCIAL_DASHBOARD_COMPONENTS,
}


def _entity_by_name(
    entities: list[dict], name: str
) -> dict | None:
    for e in entities:
        if e.get("name") == name:
            return e
    return None


# ---------------------------------------------------------------------------
# Public: generate_file_content
# ---------------------------------------------------------------------------

def generate_file_content(
    entity: dict | None,
    workflow: dict | None,
    surface: str | None,
    kind: str,
    profile: str,
    blueprint: dict | None,
) -> str:
    """Generate file content for a single product file.

    For react-vite profile:
    - "test" kind  -> vitest test file with describe/it blocks
    - "source" kind -> React component (.tsx)
    - "config" kind -> config/types
    - "registration" kind -> route registration or app wiring

    For generic profile:
    - "test" kind  -> Python unittest file
    - "source" kind -> Python module

    Content is real code -- not stubs or empty files.  It is minimal
    but syntactically valid and runnable.
    """
    name = ""
    if entity:
        name = entity.get("name", "Entity")
    elif surface:
        name = _to_pascal(surface)
    elif workflow:
        name = _to_pascal(workflow.get("name", "workflow"))
    else:
        name = "Generated"

    if profile == "react-vite":
        if kind == "test":
            return _react_test(name)
        if kind == "source":
            return _react_component(name, entity)
        if kind == "config":
            entities = (blueprint or {}).get("entities", [])
            if entities:
                return _react_types(entities)
            return "// Config\nexport {};\n"
        if kind == "registration":
            return ""  # handled specially in generate_product
        return f"// {kind}\n"

    # generic / python
    if kind == "test":
        return _python_test(name)
    if kind == "source":
        return _python_module(name, entity)
    return f"# {kind}\n"


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

    Returns ``{"dependencies": {...}, "devDependencies": {...}}``.
    """
    deps: dict[str, str] = {}
    dev_deps: dict[str, str] = {}

    if profile != "react-vite" or blueprint is None:
        return {"dependencies": deps, "devDependencies": dev_deps}

    bp_id = blueprint.get("id", "")

    if bp_id == "financial-dashboard":
        deps["recharts"] = "^2.12.0"

    # task-management needs react-router-dom which is already in the base
    # scaffold, so no extra deps needed.

    return {"dependencies": deps, "devDependencies": dev_deps}


def _merge_blueprint_deps_into_package_json(
    repo_root: Path, extra: dict[str, dict[str, str]]
) -> None:
    """Merge extra deps into an existing package.json if present."""
    pkg_path = repo_root / "package.json"
    if not pkg_path.is_file():
        return
    extra_deps = extra.get("dependencies", {})
    extra_dev = extra.get("devDependencies", {})
    if not extra_deps and not extra_dev:
        return
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if extra_deps:
        pkg.setdefault("dependencies", {}).update(extra_deps)
    if extra_dev:
        pkg.setdefault("devDependencies", {}).update(extra_dev)
    pkg_path.write_text(
        json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Public: generate_product
# ---------------------------------------------------------------------------

def generate_product(
    repo_root: Path,
    intent: dict,
    blueprint: dict | None,
    profile: str,
    wave: str = "1",
    task_ids: list[str] | None = None,
    acceptance_matrix: dict | None = None,
) -> dict:
    """Generate product files from intent, blueprint, and profile.

    Steps:
    1. Resolve target paths from adapter
    2. Generate tests first (TDD)
    3. Generate implementation files
    4. Generate route/module registration
    5. Link files to acceptance criteria (if matrix provided)
    6. Write generation manifest

    Returns generation manifest dict.
    """
    if task_ids is None:
        task_ids = []

    adapter = get_adapter(profile)
    targets = adapter.resolve_targets(repo_root)
    val_plan = adapter.validation_plan(repo_root)
    validation_commands = _flatten_validation(val_plan)

    product_name = intent.get("product_name", "")
    blueprint_id = blueprint.get("id") if blueprint else None

    file_records: list[dict[str, Any]] = []
    component_names: list[str] = []

    if profile == "react-vite":
        file_records, component_names = _generate_react_vite(
            repo_root, intent, blueprint, blueprint_id, targets,
        )
    else:
        file_records = _generate_generic(
            repo_root, intent, blueprint, blueprint_id, targets,
        )

    # Assign task_ids round-robin if provided
    if task_ids:
        for i, rec in enumerate(file_records):
            rec["task_id"] = task_ids[i % len(task_ids)]

    # Link to acceptance criteria if matrix provided
    if acceptance_matrix is not None:
        _link_records_to_acceptance(file_records, acceptance_matrix)

    # Write files to disk respecting overwrite rules
    for rec in file_records:
        abs_path = repo_root / rec["path"]
        if _is_reserved(rec["path"]):
            continue  # safety - should not happen
        if rec["overwrite_mode"] == "create" and abs_path.exists():
            rec["overwrite_mode"] = "skip"
            continue
        if rec["overwrite_mode"] == "skip":
            continue
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(rec["_content"], encoding="utf-8")

    # Merge blueprint-specific dependencies into package.json
    if blueprint is not None:
        extra = get_blueprint_dependencies(blueprint, profile)
        _merge_blueprint_deps_into_package_json(repo_root, extra)

    # Strip internal _content before manifest
    clean_records = [
        {k: v for k, v in rec.items() if k != "_content"}
        for rec in file_records
    ]

    manifest = build_generation_manifest(
        product_name=product_name,
        blueprint_id=blueprint_id,
        profile=profile,
        wave=wave,
        task_ids=task_ids,
        files=clean_records,
        validation_commands=validation_commands,
    )

    # Write manifest
    signalos_dir = repo_root / ".signalos"
    write_generation_manifest(manifest, signalos_dir)

    return manifest


# ---------------------------------------------------------------------------
# Internal: react-vite generation
# ---------------------------------------------------------------------------

def _generate_react_vite(
    repo_root: Path,
    intent: dict,
    blueprint: dict | None,
    blueprint_id: str | None,
    targets: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Generate file records for a react-vite profile."""
    file_records: list[dict[str, Any]] = []
    component_names: list[str] = []
    source_base = targets.get("source", "src")

    bp_entities = (blueprint or {}).get("entities", [])
    component_specs = _BLUEPRINT_COMPONENT_MAP.get(blueprint_id or "", [])

    if component_specs:
        # Known blueprint - use predefined component map
        for spec in component_specs:
            comp_name = spec["component"]
            entity_name = spec["entity_name"]
            entity = _entity_by_name(bp_entities, entity_name)
            component_names.append(comp_name)

            test_path = f"{source_base}/components/{comp_name}.test.tsx"
            source_path = f"{source_base}/components/{comp_name}.tsx"

            if _is_reserved(test_path) or _is_reserved(source_path):
                continue

            test_content = _react_test(comp_name)
            source_content = _react_component(comp_name, entity)

            # TDD: test first
            file_records.append(_file_record(
                test_path, "test", test_content,
                task_id=None, acceptance_id=None, mode="create",
            ))
            file_records.append(_file_record(
                source_path, "source", source_content,
                task_id=None, acceptance_id=None, mode="create",
            ))
    else:
        # Unknown blueprint or no blueprint - generate from intent entities
        for ent_name in intent.get("entities", []):
            pascal = _to_pascal(ent_name)
            component_names.append(pascal)

            test_path = f"{source_base}/components/{pascal}.test.tsx"
            source_path = f"{source_base}/components/{pascal}.tsx"

            if _is_reserved(test_path) or _is_reserved(source_path):
                continue

            test_content = _react_test(pascal)
            source_content = _react_component(pascal, None)

            file_records.append(_file_record(
                test_path, "test", test_content,
                task_id=None, acceptance_id=None, mode="create",
            ))
            file_records.append(_file_record(
                source_path, "source", source_content,
                task_id=None, acceptance_id=None, mode="create",
            ))

    # Types file (config)
    if bp_entities:
        types_path = f"{source_base}/types.ts"
        if not _is_reserved(types_path):
            types_content = _react_types(bp_entities)
            file_records.append(_file_record(
                types_path, "config", types_content,
                task_id=None, acceptance_id=None, mode="create",
            ))

    # App registration (patch - may overwrite)
    if component_names:
        app_path = f"{source_base}/App.tsx"
        if not _is_reserved(app_path):
            app_content = _react_app_registration(component_names)
            file_records.append(_file_record(
                app_path, "registration", app_content,
                task_id=None, acceptance_id=None, mode="patch",
            ))

    return file_records, component_names


# ---------------------------------------------------------------------------
# Internal: generic (Python) generation
# ---------------------------------------------------------------------------

def _generate_generic(
    repo_root: Path,
    intent: dict,
    blueprint: dict | None,
    blueprint_id: str | None,
    targets: dict[str, str],
) -> list[dict[str, Any]]:
    """Generate file records for the generic (Python) profile."""
    file_records: list[dict[str, Any]] = []
    source_base = targets.get("source", "")
    test_base = targets.get("tests", "")

    bp_entities = (blueprint or {}).get("entities", [])

    if bp_entities:
        entities_to_gen = bp_entities
    else:
        # Build from intent entities
        entities_to_gen = [
            {"name": _to_pascal(e), "fields": []}
            for e in intent.get("entities", [])
        ]

    for entity in entities_to_gen:
        name = entity["name"]
        snake = _to_snake(name)

        test_rel = f"{test_base}/test_{snake}.py" if test_base else f"test_{snake}.py"
        source_rel = f"{source_base}/{snake}.py" if source_base else f"{snake}.py"

        # Clean up double slashes
        test_rel = test_rel.lstrip("/")
        source_rel = source_rel.lstrip("/")

        if _is_reserved(test_rel) or _is_reserved(source_rel):
            continue

        test_content = _python_test(name)
        source_content = _python_module(name, entity)

        # TDD: test first
        file_records.append(_file_record(
            test_rel, "test", test_content,
            task_id=None, acceptance_id=None, mode="create",
        ))
        file_records.append(_file_record(
            source_rel, "source", source_content,
            task_id=None, acceptance_id=None, mode="create",
        ))

    return file_records


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _file_record(
    path: str,
    kind: str,
    content: str,
    *,
    task_id: str | None,
    acceptance_id: str | None,
    mode: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "kind": kind,
        "task_id": task_id,
        "acceptance_id": acceptance_id,
        "sha256_lf": compute_sha256_lf(content),
        "overwrite_mode": mode,
        "_content": content,  # stripped before manifest write
    }


def _flatten_validation(plan: dict[str, list[str]]) -> list[str]:
    """Flatten a validation plan into a single command list."""
    result: list[str] = []
    for key in ("install", "build", "test", "lint", "qa",
                "e2e", "runtime_smoke", "ux_smoke", "security"):
        result.extend(plan.get(key, []))
    return result


# ---------------------------------------------------------------------------
# Internal: acceptance-criteria linking for file records
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


def _link_records_to_acceptance(
    file_records: list[dict[str, Any]],
    acceptance_matrix: dict[str, Any],
) -> None:
    """Link file records to acceptance criteria in place."""
    criteria = acceptance_matrix.get("criteria", [])
    test_scenarios = acceptance_matrix.get("test_scenarios", [])

    for rec in file_records:
        if rec.get("acceptance_id") is not None:
            continue  # already linked
        matched = _match_criterion_for_file(rec, criteria, test_scenarios)
        if matched is not None:
            rec["acceptance_id"] = matched


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
