"""Tests for PART 2: per-file prompt carries repair error context + a pinned
import allowlist, and forbids the '@/' path alias.

Two seams under test in agent_dispatch._build_single_file_prompt:

  * error_context injection: when a file_spec carries an ``error_context``
    (the EXACT tsc/vitest diagnostics for that file), the single-file prompt
    emits a "Fix these errors" section quoting them verbatim, so repair
    regeneration is error-driven -- not a blind rebuild.

  * import allowlist: the component/App/test prompt pins the ALLOWED imports
    (bare packages react/@mantine/core/@mantine/hooks + ./types + the manifest
    components) and forbids inventing ../ui/* or @/* modules or a '@' path
    alias -- so first-pass generation drifts less before repair even runs.

These are pure prompt-construction tests: no LLM, no toolchain.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.agent_dispatch import _build_single_file_prompt


def _shared_context(**over):
    ctx = {
        "product": "Acme",
        "profile": "react-vite",
        "design_constraints": {"ui_library": "@mantine/core", "state_management": "zustand"},
        "entities": [{"name": "Task", "fields": ["id", "title", "status"]}],
        "entity_by_name": {"Task": {"name": "Task", "fields": ["id", "title", "status"]}},
        "workflows": [],
        "acceptance_criteria": [],
        "component_manifest": [
            {
                "componentName": "TaskList",
                "importPath": "./components/TaskList",
                "filePath": "src/components/TaskList.tsx",
            },
        ],
        "entity_field_map": {"Task": ["id", "title", "status"]},
        "types_module_names": ["Task"],
        "has_types_module": True,
    }
    ctx.update(over)
    return ctx


def test_error_context_injected_verbatim():
    spec = {
        "path": "src/components/TaskList.tsx",
        "kind": "source",
        "entity": "Task",
        "error_context": [
            {
                "file": "src/components/TaskList.tsx",
                "line": 12,
                "code": "TS2307",
                "message": "Cannot find module '@/ui/button' or its type declarations.",
            },
        ],
    }
    prompt = _build_single_file_prompt(spec, {}, {}, _shared_context())
    # The section exists and quotes the EXACT compiler diagnostic.
    assert "Fix these errors" in prompt
    assert "tsc reported" in prompt
    assert "TS2307" in prompt
    assert "Cannot find module '@/ui/button'" in prompt
    assert "12" in prompt


def test_error_context_absent_no_fix_section():
    spec = {"path": "src/components/TaskList.tsx", "kind": "source", "entity": "Task"}
    prompt = _build_single_file_prompt(spec, {}, {}, _shared_context())
    assert "Fix these errors" not in prompt


def test_import_allowlist_pinned_for_component():
    spec = {"path": "src/components/TaskList.tsx", "kind": "source", "entity": "Task"}
    prompt = _build_single_file_prompt(spec, {}, {}, _shared_context())
    # Bare packages allowed.
    assert "react" in prompt
    assert "@mantine/core" in prompt
    assert "@mantine/hooks" in prompt
    # Manifest import paths + shared types allowed.
    assert "./components/TaskList" in prompt or "./types" in prompt
    # Forbidden module families are named explicitly.
    assert "../ui/" in prompt
    assert "@/" in prompt


def test_at_alias_forbidden():
    spec = {"path": "src/App.tsx", "kind": "source", "entity": "Task"}
    prompt = _build_single_file_prompt(spec, {}, {}, _shared_context())
    lowered = prompt.lower()
    assert "@/" in prompt
    # The prompt must forbid the alias (never silently rely on tsconfig paths).
    assert "never" in lowered or "do not" in lowered or "forbidden" in lowered


def test_allowlist_absent_for_plain_config_file():
    # A non-component, non-test file (e.g. a config) should not get the
    # component import allowlist block (it is scoped to App/component/test).
    spec = {"path": "vite.config.ts", "kind": "config"}
    ctx = _shared_context(component_manifest=[], has_types_module=False, types_module_names=[])
    prompt = _build_single_file_prompt(spec, {}, {}, ctx)
    assert "Allowed imports" not in prompt


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
