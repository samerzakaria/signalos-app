# Tests for the per-file focused prompt builder (Foundry gen fix, STEP 2).
#
# _build_single_file_prompt scopes the build prompt to ONE file_spec, carrying
# only the shared context that file needs, and -- crucially -- DEMANDS a
# functional component (real state + event handlers + CRUD where the entity
# implies it) for source specs, and an INTERACTION test (not render-only) for
# test specs. This is what turns the TDD source<->test pair from a shell into
# a real working product.

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.agent_dispatch import (
    _build_shared_context,
    _build_single_file_prompt,
)


def _packet() -> dict:
    return {
        "run_id": "sf-run",
        "generation": {
            "profile": "react-vite",
            "product": "Acme Tracker",
            "design_constraints": {
                "ui_library": "shadcn/ui",
                "state_management": "zustand",
                "form_handling": "react-hook-form",
                "design_tokens": {"primary_color": "#0ea5e9", "font_family": "Inter"},
            },
            "entities": [
                {
                    "name": "Task",
                    "fields": ["id", "title", "status", "priority", "due_date"],
                },
                {"name": "Project", "fields": ["id", "name"]},
            ],
            "workflows": [
                {"name": "create_task", "description": "Create a new task"},
                {"name": "complete_task", "description": "Mark a task done"},
            ],
            "acceptance_criteria": [
                {"id": "AC-1", "description": "CRUD for Task", "entity": "Task"},
            ],
            "file_specs": [
                {
                    "path": "src/components/TaskList.tsx",
                    "kind": "source",
                    "entity": "Task",
                    "description": "Task list component",
                    "constraints": ["Use the design system"],
                },
                {
                    "path": "src/components/TaskList.test.tsx",
                    "kind": "test",
                    "entity": "Task",
                    "description": "Task list tests",
                },
                {"path": "src/types.ts", "kind": "config", "description": "types"},
            ],
            "allowed_paths": ["src/**"],
            "forbidden_paths": [".env"],
        },
    }


def _gov() -> dict:
    return {"CONSTITUTION.md": "Be excellent."}


def _component_spec(packet):
    return packet["generation"]["file_specs"][0]


def _test_spec(packet):
    return packet["generation"]["file_specs"][1]


def test_prompt_contains_entity_fields():
    packet = _packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _component_spec(packet), gen, _gov(), shared,
    )
    for field in ("id", "title", "status", "priority", "due_date"):
        assert field in prompt, field
    # design-selected libraries are present
    assert "zustand" in prompt
    assert "react-hook-form" in prompt


def test_component_prompt_demands_interactivity():
    packet = _packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _component_spec(packet), gen, _gov(), shared,
    ).lower()
    # #37 operations contract: create + delete are always demanded; state +
    # handler wiring is mandatory. (Inline EDIT is deliberately NOT in the
    # contract -- see test_operations_contract_excludes_edit.)
    for token in ("create", "delete", "state", "handler"):
        assert token in prompt, token


def test_test_prompt_demands_interaction():
    packet = _packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _test_spec(packet), gen, _gov(), shared,
    ).lower()
    # Must demand a real interaction, not a render-only assertion.
    assert ("fireevent" in prompt) or ("userevent" in prompt) or ("user-event" in prompt)
    # explicitly rejects render-only
    assert "render" in prompt


def test_prompt_scoped_to_single_file():
    packet = _packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _component_spec(packet), gen, _gov(), shared,
    )
    # The "Files to Create" / output target names ONLY the target path.
    # Locate the output-instruction region (after the marker) and assert
    # sibling spec paths do not appear there as output targets.
    assert "src/components/TaskList.tsx" in prompt
    # Sibling spec paths are context, not an output block header.
    lower = prompt
    # The single-file variant must instruct exactly one output file.
    assert "exactly this one file" in lower.lower() or "one file" in lower.lower()
    # It must NOT instruct outputting the sibling test / types as targets.
    marker = "## File To Create"
    assert marker in prompt
    tail = prompt.split(marker, 1)[1]
    # Sibling specs are never listed as an OUTPUT TARGET (a `### `path`` header)
    # in the output region -- only the single target spec is.
    assert "### `src/components/TaskList.tsx`" in tail
    assert "### `src/components/TaskList.test.tsx`" not in tail
    assert "### `src/types.ts`" not in tail


def test_types_context_present_for_typing():
    # The component must be typed via the generated src/types.ts.
    packet = _packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _component_spec(packet), gen, _gov(), shared,
    )
    assert "src/types.ts" in prompt


def test_config_spec_prompt_does_not_demand_crud():
    # A non-source, non-test spec (types.ts) should not carry the CRUD demand.
    packet = _packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    config_spec = gen["file_specs"][2]
    prompt = _build_single_file_prompt(
        config_spec, gen, _gov(), shared,
    ).lower()
    # No interaction-test demand and no CRUD-component demand for a config file.
    assert "fireevent" not in prompt


def test_prompt_includes_binding_generation_contracts():
    packet = _packet()
    gen = packet["generation"]
    gen["generation_contracts"] = {
        "binding_rules": [
            "ARCH_REVIEW.yaml, DESIGN_DECISIONS.yaml, and SCOPE_DECISIONS.yaml "
            "are signed source-of-truth inputs.",
        ],
        "architecture": {
            "system_boundaries": [
                "profile: react-vite",
                "frontend boundary owns task CRUD",
            ],
            "trust_boundaries": ["browser-only local state"],
            "test_strategy": ["interaction tests for task workflows"],
        },
        "design_decisions": {
            "selected_variant": "variant-focused",
            "selection_reason": "Dense task workflow wins",
        },
        "scope_decisions": {
            "decisions": [
                {"proposal": "Build task CRUD", "disposition": "accepted"},
                {"proposal": "Add Slack sync", "disposition": "rejected"},
            ],
        },
    }
    shared = _build_shared_context(gen)

    prompt = _build_single_file_prompt(
        _component_spec(packet), gen, _gov(), shared,
    )

    assert "## Binding Product Contracts" in prompt
    assert "These signed artifacts are binding" in prompt
    assert "Architecture boundaries: profile: react-vite" in prompt
    assert "frontend boundary owns task CRUD" in prompt
    assert "Selected design variant: variant-focused" in prompt
    assert "Design selection reason: Dense task workflow wins" in prompt
    assert "Accepted scope: Build task CRUD" in prompt
    assert "Out-of-scope unless later signed: Add Slack sync" in prompt


# ---------------------------------------------------------------------------
# Fix #12: cross-file cohesion -- the manifest + shared fields carried into
# the App/test prompts so imports resolve and source<->test agree.
# ---------------------------------------------------------------------------

def _cohesion_packet() -> dict:
    return {
        "run_id": "cohesion-run",
        "generation": {
            "profile": "react-vite",
            "product": "Expense Tracker",
            "design_constraints": {"state_management": "zustand"},
            "entities": [
                {
                    "name": "Expense",
                    "fields": ["id", "amount", "category", "reimbursed", "date"],
                },
            ],
            "workflows": [{"name": "add expense", "description": "Add an expense"}],
            "acceptance_criteria": [],
            "component_manifest": [
                {
                    "filePath": "src/components/ExpenseManager.tsx",
                    "componentName": "ExpenseManager",
                    "importPath": "./components/ExpenseManager",
                },
                {
                    "filePath": "src/components/CategoryManager.tsx",
                    "componentName": "CategoryManager",
                    "importPath": "./components/CategoryManager",
                },
            ],
            "types_module_names": ["Expense", "Category"],
            "file_specs": [
                {
                    "path": "src/App.tsx",
                    "kind": "registration",
                    "description": "Root app that renders the components",
                },
                {
                    "path": "src/components/ExpenseManager.tsx",
                    "kind": "source",
                    "entity": "Expense",
                    "description": "Expense manager. Entity fields: id, amount, category, reimbursed, date.",
                },
                {
                    "path": "src/components/ExpenseManager.test.tsx",
                    "kind": "test",
                    "entity": "Expense",
                    "description": "Tests for ExpenseManager. Entity fields: id, amount, category, reimbursed, date.",
                },
                {"path": "src/types.ts", "kind": "config", "description": "types"},
            ],
            "allowed_paths": ["src/**"],
            "forbidden_paths": [".env"],
        },
    }


def _find_spec(gen, path):
    for s in gen["file_specs"]:
        if s["path"] == path:
            return s
    raise AssertionError(path)


def test_app_prompt_carries_canonical_manifest():
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _find_spec(gen, "src/App.tsx"), gen, _gov(), shared,
    )
    # Exact component names + import paths -- no inventing ExpenseForm/List.
    assert "ExpenseManager" in prompt
    assert "CategoryManager" in prompt
    assert "./components/ExpenseManager" in prompt
    assert "./components/CategoryManager" in prompt
    # It must instruct importing/rendering the REAL generated components.
    lower = prompt.lower()
    assert "import" in lower and "render" in lower


def test_component_test_prompt_carries_manifest_and_fields():
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _find_spec(gen, "src/components/ExpenseManager.test.tsx"),
        gen, _gov(), shared,
    )
    # The test must import the exact component under test from the manifest.
    assert "ExpenseManager" in prompt
    assert "./ExpenseManager" in prompt or "./components/ExpenseManager" in prompt
    # And use the EXACT entity field name (reimbursed) as a listed field. The
    # wrong name must never appear as a field bullet the agent should use.
    assert "reimbursed" in prompt
    assert "- isReimbursed" not in prompt


def test_component_source_prompt_uses_exact_fields():
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _find_spec(gen, "src/components/ExpenseManager.tsx"),
        gen, _gov(), shared,
    )
    # `reimbursed` is listed as an exact field bullet; the wrong `isReimbursed`
    # name is never presented as a field to use.
    assert "- reimbursed" in prompt
    assert "- isReimbursed" not in prompt


def test_types_prompt_names_exact_interfaces():
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _find_spec(gen, "src/types.ts"), gen, _gov(), shared,
    )
    # The shared types file must be told the exact interface names to export.
    assert "Expense" in prompt
    assert "Category" in prompt


def test_shared_context_exposes_manifest_and_field_map():
    gen = _cohesion_packet()["generation"]
    shared = _build_shared_context(gen)
    assert shared.get("component_manifest")
    names = {m["componentName"] for m in shared["component_manifest"]}
    assert "ExpenseManager" in names
    field_map = shared.get("entity_field_map", {})
    assert "reimbursed" in field_map.get("Expense", [])


def test_config_spec_still_no_crud_after_cohesion():
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _find_spec(gen, "src/types.ts"), gen, _gov(), shared,
    ).lower()
    assert "fireevent" not in prompt


# ---------------------------------------------------------------------------
# #26: two-pass ground-truth injection. When a test spec carries the FINAL
# on-disk source of the component under test (stamped as
# spec["source_under_test"] by the pass-2 dispatcher), the test prompt embeds
# that exact source and frames it as ground truth.
# ---------------------------------------------------------------------------

def test_test_prompt_embeds_source_under_test_when_present():
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    test_spec = dict(_find_spec(gen, "src/components/ExpenseManager.test.tsx"))
    source_text = (
        "import React from 'react';\n"
        "// UNIQUE_GROUND_TRUTH_TOKEN_42\n"
        "export default function ExpenseManager(){\n"
        "  const [showForm, setShowForm] = React.useState(false);\n"
        "  return <button onClick={() => setShowForm(true)}>Add</button>;\n"
        "}\n"
    )
    test_spec["source_under_test"] = {
        "path": "src/components/ExpenseManager.tsx",
        "text": source_text,
    }
    prompt = _build_single_file_prompt(test_spec, gen, _gov(), shared)
    # The EXACT source text is embedded verbatim (unique token proves it).
    assert "UNIQUE_GROUND_TRUTH_TOKEN_42" in prompt
    assert "setShowForm" in prompt
    low = prompt.lower()
    # Ground-truth framing + the "drive the UI to the state under test" guidance.
    assert "final" in low and "source" in low
    assert "do not assume" in low
    assert "reveal" in low or "drive the ui" in low


def test_test_prompt_without_source_under_test_is_spec_based():
    # No source_under_test stamped -> the prompt is the original spec-based
    # test prompt (graceful fallback, no ground-truth section, no crash).
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    test_spec = _find_spec(gen, "src/components/ExpenseManager.test.tsx")
    prompt = _build_single_file_prompt(test_spec, gen, _gov(), shared)
    assert "UNIQUE_GROUND_TRUTH_TOKEN_42" not in prompt
    # Still a real interaction-test prompt.
    low = prompt.lower()
    assert ("fireevent" in low) or ("userevent" in low)


# ---------------------------------------------------------------------------
# #24b: frozen authoritative type contract injected verbatim into prompts
# ---------------------------------------------------------------------------

def test_component_prompt_injects_frozen_type_contract():
    # The exact rendered types.ts (field names + types) must appear verbatim in
    # every component prompt, so the component cannot invent `category` when the
    # contract says `categoryId`, or `Date` when the contract says `string`.
    packet = _packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(_component_spec(packet), gen, _gov(), shared)
    assert "AUTHORITATIVE" in prompt
    assert "export interface Task {" in prompt
    assert "export interface Project {" in prompt
    # The contract forbids drift explicitly.
    low = prompt.lower()
    assert "do not redefine" in low
    assert ("rename" in low) and ("add, remove" in low or "add," in low)


def test_prompt_and_on_disk_contract_are_identical():
    # The contract injected into the prompt is byte-identical to what run_task
    # writes to disk (both come from types_module_source) -- so the prompt and
    # the on-disk types.ts can never disagree.
    from signalos_lib.product.agent_dispatch import _render_types

    gen = _packet()["generation"]
    shared = _build_shared_context(gen)
    assert shared["types_module_source"] == _render_types(gen["entities"])
    assert "export interface Task {" in shared["types_module_source"]


def test_types_file_does_not_inject_contract_into_itself():
    # The types.ts spec must NOT carry the "conform to this contract" block --
    # it IS the contract.
    packet = _packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    types_spec = _find_spec(gen, "src/types.ts")
    prompt = _build_single_file_prompt(types_spec, gen, _gov(), shared)
    assert "AUTHORITATIVE — `src/types.ts`" not in prompt


def test_boolean_status_field_typed_in_contract():
    # A status-toggle field (e.g. `reimbursed`, `completed`) must be `boolean`
    # in the frozen contract so a component's toggle can't re-drift the type.
    from signalos_lib.product.agent_dispatch import _render_types

    src = _render_types([
        {"name": "Expense", "fields": ["id", "amount", "category", "reimbursed"]},
    ])
    assert "amount: number;" in src
    assert "reimbursed: boolean;" in src
    assert "category: string;" in src


# ---------------------------------------------------------------------------
# #36: component prop-drift eliminated by a symmetric PROPLESS contract.
# App is a composer that renders `<Component />` with no props; every component
# is self-contained and declares no required props. With nothing passed and
# nothing required, there is no prop shape that can drift between the two files.
# ---------------------------------------------------------------------------

def test_app_prompt_forbids_passing_props_to_components():
    # The App.tsx prompt must instruct rendering each component PROPLESS and
    # explicitly forbid passing data/state props (the drift the type-check would
    # otherwise reject as `Property 'x' does not exist on IntrinsicAttributes`).
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _find_spec(gen, "src/App.tsx"), gen, _gov(), shared,
    )
    assert "PROPLESS" in prompt
    low = prompt.lower()
    # forbids passing props + demands self-contained children
    assert "never" in low and "<xyz items=" in low
    assert "self-contained" in low
    assert "passes it" in low  # "App passes it NOTHING"


def test_component_prompt_forbids_required_props():
    # Each entity component must be told to be self-contained and declare NO
    # required props -- so App's propless `<Component />` type-checks and there
    # is no prop contract to drift.
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _find_spec(gen, "src/components/ExpenseManager.tsx"),
        gen, _gov(), shared,
    )
    low = prompt.lower()
    assert "self-contained" in low
    assert "no required props" in low or "declare no" in low
    # The rationale (undefined-at-runtime) is spelled out so the model complies.
    assert "undefined" in low


def test_app_prompt_is_not_given_crud_component_requirements():
    # App.tsx (kind="registration") must NOT receive the entity-component CRUD
    # "render a working list AND a form" demand -- that is only for the feature
    # components. App is a composer.
    packet = _cohesion_packet()
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    prompt = _build_single_file_prompt(
        _find_spec(gen, "src/App.tsx"), gen, _gov(), shared,
    )
    assert "Render a working list of the entity AND a form" not in prompt


# ---------------------------------------------------------------------------
# #37: deterministic OPERATIONS contract shared, verbatim, by a component and
# its test -- both work from ONE list of operations so they cannot drift on
# WHICH operations exist (the vitest 17/37 convergence fix). Inline edit/update
# is deliberately excluded (it was the exact source of edit/update/cancel
# test<->source drift).
# ---------------------------------------------------------------------------

def test_operations_contract_create_delete_and_boolean_toggle():
    from signalos_lib.product.agent_dispatch import _operations_contract

    ops = _operations_contract("Expense", ["id", "amount", "category", "reimbursed", "date"])
    keys = [o["key"] for o in ops]
    assert keys[:2] == ["create", "delete"]
    # `reimbursed` is boolean -> a toggle op; `amount`/`category`/`date` are not.
    assert "toggle_reimbursed" in keys
    assert not any(k.startswith("toggle_") and k != "toggle_reimbursed" for k in keys)


def test_operations_contract_excludes_edit():
    from signalos_lib.product.agent_dispatch import _operations_contract

    ops = _operations_contract("User", ["id", "name", "email"])
    keys = [o["key"] for o in ops]
    assert "create" in keys and "delete" in keys
    # No inline edit/update operation -- that flow is out of the contract.
    assert not any("edit" in k or "update" in k for k in keys)
    # No boolean field -> exactly create + delete, nothing else.
    assert keys == ["create", "delete"]


def test_component_and_test_prompts_share_the_same_operations():
    # The component's demanded operations and the test's asserted operations are
    # rendered from the SAME contract -> the create/delete/toggle phrasings both
    # appear, and the test explicitly refuses edit/update/cancel.
    packet = _cohesion_packet()  # Expense has boolean `reimbursed`
    gen = packet["generation"]
    shared = _build_shared_context(gen)
    comp = _build_single_file_prompt(
        _find_spec(gen, "src/components/ExpenseManager.tsx"), gen, _gov(), shared,
    )
    test = _build_single_file_prompt(
        _find_spec(gen, "src/components/ExpenseManager.test.tsx"), gen, _gov(), shared,
    )
    # Both name the SAME three operations for the Expense entity.
    for prompt in (comp, test):
        assert "CREATE:" in prompt
        assert "DELETE:" in prompt
        assert "TOGGLE `reimbursed`:" in prompt
    # The test explicitly forbids asserting operations outside the contract.
    low = test.lower()
    assert "assert nothing beyond them" in low
    assert "edit/update/cancel" in low
