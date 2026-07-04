# signalos_lib/product/agent_dispatch.py
# Agent Dispatch -- invokes LLM to execute generation packets.
#
# SignalOS is the software house. Agents are the team. This module
# dispatches work to agents (LLM calls) within governance boundaries.
# The agent receives: task scope, file specs, constraints, governance.
# The agent returns: file contents. SignalOS validates the output.
#
# Key invariant: agent output is non-binding until validated.

from __future__ import annotations

__all__ = [
    "dispatch_build_agent",
    "dispatch_build_agent_chunked",
    "dispatch_local_build_agent",
    "parse_agent_response",
    "write_agent_files",
]

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are the highest-level software engineer ever for the selected stack and
product domain, acting as the SignalOS Build agent in a SignalOS-governed
software house.

SignalOS owns product scope, governance, evidence, and validation. You own
implementation quality inside the allowed files. Apply highest-level domain
judgment for the selected stack, including architecture fit, maintainability,
accessibility, security, testability, production readiness, and real user
workflows. If the packet is ambiguous or asks for work outside scope, stop and
report the blocker instead of guessing.

You MUST follow the governance instructions provided. Violations will be
caught by validators and your output will be rejected.

Your job: write the product code files specified in the generation packet.
You do NOT decide what to build -- that's already decided. You implement
exactly what the file specs describe, within the design constraints.

Rules:
- Write ONLY the files listed in file_specs
- Follow ALL constraints listed per file
- Satisfy the packet success criteria before claiming completion
- Preserve every required evidence item or report the exact blocker
- Follow the design system (UI library, state management, tokens)
- Consult applicable SignalOS skills before producing files
- Write tests FIRST (TDD -- test files before source files)
- Never violate forbidden rules, forbidden paths, or forbidden actions
- Never fabricate validation results or weaken tests/evidence to pass
- Use the specified coding standards
- Every file must be non-empty and syntactically valid

Output format:
For each file, output a fenced block with the file path as header:

```path/to/file.tsx
// file content here
```

Output ALL files in one response. Do not skip any file from file_specs.
"""


def _build_agent_prompt(
    packet: dict,
    governance: dict[str, str],
) -> str:
    """Construct the prompt for the build agent from the packet."""
    lines: list[str] = []

    role = packet.get("agent_role", "SignalOS Build agent")
    expertise_frame = packet.get("expertise_frame", "")
    lines.append("# Agent Role")
    lines.append(f"Role: {role}")
    if expertise_frame:
        lines.append(f"Expertise frame: {expertise_frame}")
    lines.append("")

    applicable_skills = packet.get("applicable_skills", [])
    if applicable_skills:
        lines.append("## Applicable SignalOS Skills")
        lines.append("")
        lines.append(
            "Read and apply these skill docs before writing code. They are "
            "selected from the full SignalOS skill catalog for this run."
        )
        lines.append("")
        for skill in applicable_skills:
            lines.append(
                f"### {skill.get('name', skill.get('key', 'unknown'))} "
                f"(`{skill.get('key', '')}`)"
            )
            if skill.get("path"):
                lines.append(f"Source: `{skill['path']}`")
            content = str(skill.get("content", "")).strip()
            if content:
                lines.append(content[:4000])
            lines.append("")

    skills_catalog = packet.get("skills_catalog", [])
    if skills_catalog:
        lines.append("## Available SignalOS Skill Catalog")
        lines.append("")
        lines.append(
            "The product repo contains these additional skill docs. Consult "
            "them if your task needs that guidance."
        )
        for skill in skills_catalog:
            lines.append(
                f"- `{skill.get('key', '')}` -- {skill.get('name', '')} "
                f"({skill.get('path', '')})"
            )
        lines.append("")

    lesson_context = packet.get("lesson_context", {})
    selected_lessons = (
        lesson_context.get("selected_lessons", [])
        if isinstance(lesson_context, dict)
        else []
    )
    if selected_lessons:
        lines.append("## Product Lessons")
        lines.append("")
        lines.append(
            "Account for every required lesson in RESULT.json using "
            "applied_lessons plus lesson_evidence, or not_applicable_lessons "
            "with a concrete reason."
        )
        lines.append("")
        for lesson in selected_lessons:
            lines.append(
                f"- `{lesson.get('id', '')}` "
                f"[{lesson.get('enforcement', '')}, {lesson.get('lesson_kind', '')}]: "
                f"{lesson.get('summary', '')}"
            )
            if lesson.get("required_evidence"):
                lines.append(f"  Evidence: {lesson['required_evidence']}")
        lines.append("")

    # Product context
    gen = packet.get("generation", packet)
    product = gen.get("product", "")
    profile = gen.get("profile", "")
    lines.append(f"# Product: {product}")
    lines.append(f"# Profile: {profile}")
    lines.append("")

    for heading, key in (
        ("Success Criteria", "success_criteria"),
        ("Evidence Required", "evidence_required"),
        ("Forbidden Rules (Hard Walls)", "forbidden_rules"),
        ("Escalation Policy", "escalation_policy"),
    ):
        values = packet.get(key, [])
        if values:
            lines.append(f"## {heading}")
            for item in values:
                lines.append(f"- {item}")
            lines.append("")

    repair_policy = packet.get("repair_policy", {})
    if repair_policy:
        lines.append("## Repair/Rework Policy")
        for key, value in repair_policy.items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    source_policy = packet.get("source_policy", {})
    if source_policy:
        lines.append("## Source Policy")
        for key, value in source_policy.items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    team_contract = packet.get("team_contract", {})
    if team_contract:
        lines.append("## SignalOS Team Contract")
        lines.append(
            "You are operating as a SignalOS team member managed by SignalOS, "
            "not as a separate user-managed agent."
        )
        for key, value in team_contract.items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    # Design constraints
    dc = gen.get("design_constraints", {})
    if dc:
        lines.append("## Design Constraints")
        if dc.get("ui_library"):
            lines.append(f"- UI Library: {dc['ui_library']}")
        if dc.get("state_management"):
            lines.append(f"- State: {dc['state_management']}")
        if dc.get("data_layer"):
            lines.append(f"- Data: {dc['data_layer']}")
        if dc.get("form_handling"):
            lines.append(f"- Forms: {dc['form_handling']}")
        if dc.get("conventions"):
            lines.append("- Conventions:")
            for conv in dc["conventions"]:
                lines.append(f"  - {conv}")
        tokens = dc.get("design_tokens", {})
        if tokens:
            lines.append(f"- Primary color: {tokens.get('primary_color', '')}")
            lines.append(f"- Font: {tokens.get('font_family', '')}")
        lines.append("")

    lines.extend(_generation_contract_prompt_lines(gen.get("generation_contracts", {})))

    # Entities
    entities = gen.get("entities", [])
    if entities:
        lines.append("## Entities")
        for e in entities:
            name = e.get("name", e) if isinstance(e, dict) else e
            fields = e.get("fields", []) if isinstance(e, dict) else []
            lines.append(f"- {name}" + (f" ({', '.join(fields)})" if fields else ""))
        lines.append("")

    # File specs (the actual task)
    file_specs = gen.get("file_specs", [])
    if file_specs:
        lines.append("## Files to Create")
        lines.append("")
        for spec in file_specs:
            lines.append(f"### `{spec['path']}`")
            lines.append(f"Kind: {spec['kind']}")
            lines.append(f"Description: {spec['description']}")
            if spec.get("entity"):
                lines.append(f"Entity: {spec['entity']}")
            if spec.get("constraints"):
                lines.append("Constraints:")
                for c in spec["constraints"]:
                    lines.append(f"  - {c}")
            lines.append("")

    # Governance (selected subset -- not all 287)
    if governance:
        lines.append("## Governance Instructions")
        lines.append("")
        # Include only the most critical files in the prompt
        # (constitution + agent contract + coding standards)
        priority_keys = [
            k for k in governance
            if "CONSTITUTION" in k or "build.md" in k
            or "typescript-standards" in k or "ENFORCEMENT" in k
        ]
        other_keys = [k for k in governance if k not in priority_keys]

        for key in priority_keys:
            lines.append(f"### {key}")
            lines.append(governance[key][:3000])  # Cap per file
            lines.append("")

        # Summarize remaining governance as rules
        if other_keys:
            lines.append(f"(+{len(other_keys)} additional governance files enforced at validation)")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-file focused prompt (STEP 2) -- the chunked dispatch path
# ---------------------------------------------------------------------------

# System prompt for the single-file variant. Same governance framing as the
# monolithic _SYSTEM_PROMPT, but instructs the agent to output EXACTLY ONE
# file (one closed fenced block) so the paired-fence parser is unambiguous and
# a single file can never be truncated mid-multi-file-response.
_SINGLE_FILE_SYSTEM_PROMPT = """\
You are the highest-level software engineer ever for the selected stack and
product domain, acting as the SignalOS Build agent in a SignalOS-governed
software house.

SignalOS owns product scope, governance, evidence, and validation. You own
implementation quality inside the ONE allowed file for this task. Apply
highest-level domain judgment for the selected stack: architecture fit,
maintainability, accessibility, security, testability, production readiness,
and real user workflows.

You MUST follow the governance instructions provided. Violations will be
caught by validators and your output will be rejected.

Rules:
- Write ONLY the single file named in "File To Create"
- Follow ALL constraints and design system choices (UI library, state, tokens)
- Type everything against the generated shared types (src/types.ts)
- Every file must be non-empty, complete, and syntactically valid
- Never violate forbidden rules, forbidden paths, or forbidden actions
- Never fabricate validation results or weaken tests/evidence to pass

Output format:
Output exactly this one file in a single fenced block, with the file path as
the header line:

```path/to/file.tsx
// complete file content here
```

Output exactly ONE file. Do not output any other file, prose, or explanation.
"""


def _generation_contract_prompt_lines(contracts: dict[str, Any]) -> list[str]:
    if not contracts:
        return []

    lines = [
        "## Binding Product Contracts",
        "These signed artifacts are binding. Do not treat them as advisory.",
    ]
    for rule in contracts.get("binding_rules", []) or []:
        lines.append(f"- {rule}")

    architecture = contracts.get("architecture", {}) or {}
    if architecture:
        boundaries = architecture.get("system_boundaries", []) or []
        trust = architecture.get("trust_boundaries", []) or []
        tests = architecture.get("test_strategy", []) or []
        if boundaries:
            lines.append("- Architecture boundaries: " + "; ".join(map(str, boundaries[:4])))
        if trust:
            lines.append("- Trust boundaries: " + "; ".join(map(str, trust[:4])))
        if tests:
            lines.append("- Test strategy: " + "; ".join(map(str, tests[:4])))

    design = contracts.get("design_decisions", {}) or {}
    if design:
        selected = design.get("selected_variant")
        reason = design.get("selection_reason")
        if selected:
            lines.append(f"- Selected design variant: {selected}")
        if reason:
            lines.append(f"- Design selection reason: {reason}")

    scope = contracts.get("scope_decisions", {}) or {}
    accepted: list[str] = []
    blocked: list[str] = []
    for decision in scope.get("decisions", []) or []:
        if not isinstance(decision, dict):
            continue
        title = decision.get("proposal") or decision.get("title") or decision.get("id")
        disposition = str(decision.get("disposition", "")).lower()
        if disposition == "accepted" and title:
            accepted.append(str(title))
        elif disposition in {"rejected", "deferred"} and title:
            blocked.append(str(title))
    if accepted:
        lines.append("- Accepted scope: " + "; ".join(accepted[:6]))
    if blocked:
        lines.append("- Out-of-scope unless later signed: " + "; ".join(blocked[:6]))

    lines.append("")
    return lines


def _build_shared_context(gen: dict[str, Any]) -> dict[str, Any]:
    """Assemble the run-wide context a single-file prompt draws from, ONCE
    per run (so every per-file task shares the same resolved entities /
    workflows / design constraints instead of recomputing them). Returned as
    a plain dict; passed into every _build_single_file_prompt call."""
    entities = gen.get("entities", []) or []
    entity_by_name: dict[str, dict] = {}
    for e in entities:
        if isinstance(e, dict) and e.get("name"):
            entity_by_name[str(e["name"])] = e
    all_file_paths = [
        str(spec.get("path", "")).replace("\\", "/")
        for spec in gen.get("file_specs", [])
        if spec.get("path")
    ]
    # Fix #12: the authoritative cross-file contract, resolved once and shared
    # into every per-file prompt. component_manifest is the canonical
    # {filePath, componentName, importPath}; entity_field_map/types_module_names
    # are the exact field/interface names source AND test must agree on.
    component_manifest = list(gen.get("component_manifest", []) or [])
    entity_field_map = dict(gen.get("entity_field_map", {}) or {})
    if not entity_field_map:
        for e in entities:
            if isinstance(e, dict) and e.get("name"):
                entity_field_map[str(e["name"])] = [
                    str(f) for f in (e.get("fields") or [])
                ]
    types_module_names = list(gen.get("types_module_names", []) or [])
    # #24b: the AUTHORITATIVE, frozen shared type contract. Rendered once,
    # deterministically, from the approved entities and injected verbatim into
    # every source/test prompt so a component cannot invent field names/types
    # that drift from types.ts (e.g. `category` vs `categoryId`, `Date` vs
    # `string`). The LLM never owns this file -- run_task writes this same source
    # to disk -- so prompt contract and on-disk contract are byte-identical.
    types_module_source = _render_types(entities)
    return {
        "product": gen.get("product", ""),
        "profile": gen.get("profile", ""),
        "design_constraints": gen.get("design_constraints", {}) or {},
        "entities": entities,
        "entity_by_name": entity_by_name,
        "workflows": gen.get("workflows", []) or [],
        "acceptance_criteria": gen.get("acceptance_criteria", []) or [],
        "component_names_override": gen.get("_component_names_override", []) or [],
        "component_manifest": component_manifest,
        "entity_field_map": entity_field_map,
        "types_module_names": types_module_names,
        "types_module_source": types_module_source,
        "generation_contracts": gen.get("generation_contracts", {}) or {},
        "applicable_skills": gen.get("applicable_skills", [])
        or gen.get("_applicable_skills", []),
        "has_types_module": any(
            p.endswith("src/types.ts") or p == "types.ts" for p in all_file_paths
        ),
    }


def _relevant_workflows_for_entity(
    entity: str | None, workflows: list[dict],
) -> list[dict]:
    """Workflows whose name/description references the entity (best-effort);
    fall back to all workflows when nothing matches so the agent still sees
    the product's behavior surface."""
    if not entity:
        return list(workflows)
    key = entity.lower()
    matched = [
        w for w in workflows
        if key in str(w.get("name", "")).lower()
        or key in str(w.get("description", "")).lower()
    ]
    return matched or list(workflows)


# PART 2: the bare npm packages a react-vite component/test may import from
# directly. Kept small and stack-canonical; the design's ui_library is folded
# in so a Mantine product also allows @mantine/core + @mantine/hooks.
_REACT_VITE_BARE_ALLOWLIST = (
    "react",
    "react-dom",
    "react/jsx-runtime",
    "@testing-library/react",
    "@testing-library/jest-dom",
    # #40: the interaction-test prompt permits `userEvent`, and the scaffold now
    # ships @testing-library/user-event -- so it is an allowed bare import (a
    # test that reaches for it must not be flagged as importing a phantom).
    "@testing-library/user-event",
    "vitest",
)


def _error_context_lines(spec: dict[str, Any]) -> list[str]:
    """Render the per-file compile/test diagnostics (if any) as a verbatim
    "Fix these errors" section. Empty when the spec carries no error_context
    (i.e. first-pass generation, not a repair cycle)."""
    ec = spec.get("error_context") or []
    if not ec:
        return []
    lines: list[str] = ["## Fix these errors (MANDATORY)"]
    lines.append(
        "The previous version of THIS file did not compile. tsc reported the "
        "following diagnostics for this exact file. Fix THIS file so every one "
        "is resolved; do not change any other file."
    )
    for e in ec:
        if not isinstance(e, dict):
            lines.append(f"- {e}")
            continue
        loc = ""
        if e.get("line") is not None:
            loc = f":{e['line']}"
            if e.get("col") is not None:
                loc += f":{e['col']}"
        code = e.get("code", "")
        msg = e.get("message", "")
        code_part = f"{code}: " if code else ""
        lines.append(f"- tsc reported{loc} -- {code_part}{msg}".rstrip())
    lines.append(
        "Common cause: importing a module that does not exist. You may ONLY "
        "import react, the packages in this product's 'Allowed imports' list "
        "(the SELECTED design system -- do NOT import a different UI library), "
        "./types, and the listed manifest components. Never invent ../ui/* or "
        "@/* modules or a '@' path alias."
    )
    lines.append("")
    return lines


def _import_allowlist_lines(
    path: str,
    kind: str,
    shared_context: dict[str, Any],
    manifest: list[dict],
) -> list[str]:
    """Pin the ALLOWED imports for a react-vite App/component/test file and
    forbid the '@/' alias + phantom ../ui/* modules. Derived from the #12
    manifest (exact component import paths) + the shared types module + the
    design-selected bare packages. Only emitted for react-vite JS/TSX files."""
    if shared_context.get("profile") != "react-vite":
        return []
    bare = list(_REACT_VITE_BARE_ALLOWLIST)
    ui_lib = (shared_context.get("design_constraints", {}) or {}).get("ui_library")
    # Fold in ONLY the SELECTED design system's packages. Never hardcode
    # @mantine -- doing so told a shadcn product it may import @mantine/core
    # (not a dependency -> build fails, then the repair loop burns cycles/tokens
    # chasing it, cf. #31). Each branch lists the real npm deps that design's
    # get_design_dependencies() installs.
    if ui_lib == "@mantine/core":
        bare += [
            p for p in (
                "@mantine/core", "@mantine/hooks", "@mantine/form",
                "@mantine/dates", "@mantine/charts", "@tabler/icons-react", "dayjs",
            ) if p not in bare
        ]
    elif ui_lib == "shadcn/ui":
        bare += [
            p for p in (
                "lucide-react", "class-variance-authority", "clsx",
                "tailwind-merge",
            ) if p not in bare
        ]
    elif ui_lib and ui_lib not in bare:
        bare.append(ui_lib)

    lines: list[str] = ["## Allowed imports (obey EXACTLY -- anything else fails tsc)"]
    lines.append("Bare packages you may import: " + ", ".join(f"`{b}`" for b in bare) + ".")
    if shared_context.get("has_types_module") or shared_context.get("types_module_names"):
        lines.append("Shared types: import from `./types` (or the correct relative path to `src/types.ts`).")
    if manifest:
        paths = ", ".join(f"`{m.get('importPath','')}`" for m in manifest if m.get("importPath"))
        if paths:
            lines.append(f"Local components: import ONLY from these exact paths: {paths}.")
    lines.append(
        "FORBIDDEN: do NOT import from `@/` (there is no `@` path alias "
        "configured -- it will not resolve), and never invent `../ui/*`, "
        "`@/components/*`, `@/lib/*`, or store modules that are not listed "
        "above. Use relative paths only."
    )
    lines.append("")
    return lines


def _build_single_file_prompt(
    spec: dict[str, Any],
    gen: dict[str, Any],
    governance: dict[str, str],
    shared_context: dict[str, Any],
) -> str:
    """Construct a prompt scoped to ONE file_spec, carrying only the shared
    context that file needs. Source-component specs demand a FUNCTIONAL
    component (real state via the design-selected library, event handlers,
    working add/edit/delete/status where the entity's fields/workflows imply
    it, typed via src/types.ts). Test specs demand an INTERACTION test
    (render -> type -> submit -> assert the list grew; click delete -> assert
    row gone), not a render-only assertion."""
    path = str(spec.get("path", "")).replace("\\", "/")
    kind = str(spec.get("kind", "source"))
    entity = spec.get("entity")
    lines: list[str] = []

    lines.append(f"# Product: {shared_context.get('product', '')}")
    lines.append(f"# Profile: {shared_context.get('profile', '')}")
    lines.append("")

    # Design constraints (state management / forms / tokens the file must honor)
    dc = shared_context.get("design_constraints", {}) or {}
    if dc:
        lines.append("## Design Constraints")
        if dc.get("ui_library"):
            lines.append(f"- UI Library: {dc['ui_library']}")
        if dc.get("state_management"):
            lines.append(f"- State management: {dc['state_management']}")
        if dc.get("data_layer"):
            lines.append(f"- Data: {dc['data_layer']}")
        if dc.get("form_handling"):
            lines.append(f"- Form handling: {dc['form_handling']}")
        tokens = dc.get("design_tokens", {}) or {}
        if tokens:
            lines.append(f"- Primary color: {tokens.get('primary_color', '')}")
            lines.append(f"- Font: {tokens.get('font_family', '')}")
        if dc.get("conventions"):
            lines.append("- Conventions:")
            for conv in dc["conventions"]:
                lines.append(f"  - {conv}")
        lines.append("")

    # The resolved entity for this file, with its full fields array. Fix #12/C:
    # the EXACT field names flow from the single entity definition into BOTH the
    # component and its test, so source and test never disagree (reimbursed, not
    # isReimbursed). Falls back to the shared entity_field_map when the entity
    # object carried no inline fields.
    entity_by_name = shared_context.get("entity_by_name", {}) or {}
    field_map = shared_context.get("entity_field_map", {}) or {}
    resolved = entity_by_name.get(str(entity)) if entity else None
    if entity:
        fields = (resolved or {}).get("fields") or field_map.get(str(entity), [])
        lines.append(f"## Entity: {(resolved or {}).get('name', entity)}")
        if fields:
            lines.append(
                "Fields -- use these EXACT names (do NOT rename, e.g. keep "
                "`reimbursed`, never `isReimbursed`), type via src/types.ts:"
            )
            for f in fields:
                lines.append(f"- {f}")
        lines.append("")

    # Relevant workflows + acceptance criteria for the entity.
    workflows = _relevant_workflows_for_entity(
        str(entity) if entity else None, shared_context.get("workflows", []),
    )
    if workflows:
        lines.append("## Workflows this file supports")
        for w in workflows:
            name = w.get("name", "") if isinstance(w, dict) else str(w)
            desc = w.get("description", "") if isinstance(w, dict) else ""
            lines.append(f"- {name}" + (f": {desc}" if desc else ""))
        lines.append("")

    acs = [
        ac for ac in shared_context.get("acceptance_criteria", [])
        if not entity or ac.get("entity") in (entity, None)
    ]
    if acs:
        lines.append("## Acceptance criteria this file must satisfy")
        for ac in acs:
            lines.append(f"- {ac.get('id', '')}: {ac.get('description', '')}")
        lines.append("")

    lines.extend(
        _generation_contract_prompt_lines(
            shared_context.get("generation_contracts", {}) or {}
        )
    )

    # Fix #12/B: the canonical component manifest -- the EXACT { componentName,
    # importPath, filePath } for every generated component. Injected into the
    # App.tsx prompt (so App imports/renders the real components, never an
    # invented ExpenseManager/ExpenseForm) AND into every component test prompt
    # (so the test imports the real component under test, not a phantom
    # sub-component or store path).
    manifest = shared_context.get("component_manifest", []) or []
    is_app = path.endswith("src/App.tsx") or path.endswith("/App.tsx") or path == "App.tsx"
    is_component = (
        "/components/" in path and path.endswith(".tsx")
    )
    if manifest and (is_app or is_component):
        lines.append("## Canonical component manifest (authoritative -- obey exactly)")
        lines.append(
            "These are the ONLY generated components and their exact import "
            "paths. Import by these exact names/paths; never invent component "
            "names, sub-components, or store modules that are not listed here."
        )
        for m in manifest:
            lines.append(
                f"- `{m.get('componentName','')}` (default export) -- "
                f"import from `{m.get('importPath','')}` "
                f"(file `{m.get('filePath','')}`)"
            )
        if is_app:
            lines.append("")
            lines.append(
                "This is App.tsx: import EACH component above using its exact "
                "name and import path, and render every one of them in the JSX "
                "tree."
            )
            # #36 prop-drift fix: App is a COMPOSER. Every component owns its own
            # state, so App renders each one PROPLESS. Passing data/state props
            # to a component whose Props interface doesn't declare them is the
            # exact drift that breaks the type-check -- forbid it here so App and
            # the components agree by contract (App gives nothing; components
            # require nothing).
            lines.append(
                "Render each component PROPLESS -- `<Xyz />`, NEVER "
                "`<Xyz items={...} onChange={...} />`. Each component is "
                "self-contained and owns all of its own state, so App passes it "
                "NOTHING. Hold no entity/business state in App and define no "
                "`Props` for these children; App only arranges them in a layout."
            )
        elif kind == "test":
            lines.append("")
            lines.append(
                "This is a component test: import the component under test by "
                "its exact default-export name and relative path from the "
                "manifest. Do not import ExpenseForm/ExpenseList/store-style "
                "modules that are not in the manifest or file_specs."
            )
        lines.append("")
        # PART 2: pin the import allowlist so first-pass generation drifts
        # LESS before repair even runs. The react-vite scaffold ships NO `@`
        # path alias in tsconfig/vite, so `@/*` imports never resolve (TS2307);
        # the simple robust choice is to FORBID them in the prompt rather than
        # wire an alias into every config. The allowlist is derived from the
        # #12 manifest (exact component import paths) + the shared types module
        # + the design-selected bare packages.
        lines.extend(
            _import_allowlist_lines(path, kind, shared_context, manifest)
        )
    elif shared_context.get("component_names_override"):
        # Back-compat: components-only list when no full manifest is present.
        comp_names = shared_context["component_names_override"]
        lines.append("## Component names in this product (for import resolution)")
        lines.append(", ".join(comp_names))
        lines.append("")

    # Fix #12/A + #24b: inject the FROZEN shared type contract verbatim so a
    # component/test cannot invent field names or types that drift from
    # src/types.ts. The contract is rendered deterministically (types_module_
    # source) and is byte-identical to what run_task writes to disk, so the
    # prompt and the on-disk types.ts can never disagree.
    types_source = shared_context.get("types_module_source") or ""
    types_names = shared_context.get("types_module_names", []) or []
    is_types_file = path.endswith("src/types.ts") or path == "types.ts"
    if is_types_file and (types_source or types_names):
        lines.append("## Shared types module (AUTHORITATIVE)")
        if types_names:
            lines.append(
                "Export these exact interface names: "
                + ", ".join(str(name) for name in types_names)
                + "."
            )
        if types_source:
            lines.append(
                "The entity field contract below is binding; keep field names "
                "and types aligned with it."
            )
            lines.append("```typescript")
            lines.append(types_source.rstrip())
            lines.append("```")
        lines.append("")
    elif types_source:
        lines.append("## Shared types contract (AUTHORITATIVE — `src/types.ts`)")
        lines.append(
            "These interfaces ALREADY EXIST in `src/types.ts`. Import what you "
            "need from `./types` (or the correct relative path) and use these "
            "EXACT field names and types. Do NOT redefine, rename, add, remove, "
            "or re-type any field — your code MUST compile against this contract "
            "verbatim:"
        )
        lines.append("```typescript")
        lines.append(types_source.rstrip())
        lines.append("```")
        lines.append("")
    elif (shared_context.get("has_types_module") or types_names) and not is_types_file:
        # Fallback: names-only when no rendered contract is available.
        line = (
            "## Shared types: `src/types.ts` holds every entity's TypeScript "
            "type. Import and use those types -- do NOT redefine them here."
        )
        if types_names:
            line += (
                " Exact exported interface names: " + ", ".join(types_names) + "."
            )
        lines.append(line)
        lines.append("")

    # The single file to produce.
    lines.append("## File To Create")
    lines.append(
        "Output exactly this one file in a single fenced block. Do not output "
        "any other file."
    )
    lines.append("")
    lines.append(f"### `{path}`")
    lines.append(f"Kind: {kind}")
    if spec.get("description"):
        lines.append(f"Description: {spec['description']}")
    if entity:
        lines.append(f"Entity: {entity}")
    if spec.get("constraints"):
        lines.append("Constraints:")
        for c in spec["constraints"]:
            lines.append(f"  - {c}")
    lines.append("")

    # PART 2: error-driven repair. When this file_spec carries an
    # ``error_context`` (the EXACT tsc/vitest diagnostics the build reported
    # for THIS file, injected by build_repair_packet), quote them verbatim so
    # regeneration is a targeted FIX, not a blind rebuild.
    lines.extend(_error_context_lines(spec))

    # FUNCTIONAL DEMAND -- the heart of the fix.
    is_react = shared_context.get("profile") == "react-vite"
    state_lib = dc.get("state_management") or "React state (useState/useReducer)"
    form_lib = dc.get("form_handling") or "controlled inputs"
    # #37: the authoritative OPERATIONS contract, derived deterministically from
    # this entity + its fields. Injected verbatim into BOTH the component and
    # its test so both work from ONE list of operations and cannot drift on
    # WHICH operations exist (the create/delete/toggle convergence fix).
    _entity_fields = (
        (resolved or {}).get("fields") or field_map.get(str(entity), [])
    ) if entity else []
    ops_contract = _operations_contract(
        str(entity) if entity else None, _entity_fields
    )
    if kind == "source" and is_react and path.endswith(".tsx"):
        lines.append("## Functional Requirements (MANDATORY)")
        lines.append(
            "This is a REAL, interactive component -- not a static placeholder. "
            "It MUST:"
        )
        lines.append(
            f"- Hold real state using the design-selected state management "
            f"({state_lib}) -- e.g. a list of items plus the create-form state."
        )
        lines.append(
            f"- Render a working list of the entity AND a form (using {form_lib}) "
            "to create new items."
        )
        lines.append(
            "- Implement EXACTLY these operations -- each a real control wired to "
            "a handler that mutates state (no dead buttons), no more and no "
            "fewer, so the test (which asserts this SAME list) passes:"
        )
        for op in ops_contract:
            lines.append(f"  - {op['component']}")
        lines.append(
            "- Be SELF-CONTAINED: own ALL of your state internally. Declare NO "
            "REQUIRED props -- the App renders you as `<Component />` with no "
            "props, so any required prop would be `undefined` at runtime and "
            "break the type-check. If you genuinely need a callback prop, give "
            "it a default value so the component still works with zero props."
        )
        lines.append(
            "- Be fully typed against the shared `src/types.ts` entity types."
        )
        lines.append(
            "- Be accessible (labels, roles) and follow the design system."
        )
        lines.append("")
    elif kind == "test" and is_react and path.endswith(".test.tsx"):
        lines.append("## Functional Requirements (MANDATORY)")
        lines.append(
            "This is a MEANINGFUL interaction test -- NOT a render-only smoke "
            "test. It MUST:"
        )
        lines.append(
            "- render() the component, then drive each operation below with a "
            "real user INTERACTION (fireEvent or userEvent, React Testing "
            "Library) and assert its observable effect. Cover EXACTLY these "
            "operations -- the SAME contract the component implements -- and "
            "assert NOTHING beyond them (do NOT test edit/update/cancel or any "
            "operation not listed; those are not part of this component):"
        )
        for op in ops_contract:
            lines.append(f"  - {op['test']}")
        lines.append(
            "- Assert on observable behavior/state, not merely that the "
            "component rendered without throwing."
        )
        # #32: the stack is VITEST, not jest -- gpt-class models default to
        # jest idioms that fail tsc ("Cannot use namespace 'jest' as a value").
        lines.append(
            "- Use VITEST, not jest: `describe`/`it`/`test`/`expect` are GLOBAL "
            "(no import needed). For mocks/spies use `vi` imported from "
            "'vitest' (e.g. `import { vi } from 'vitest'`) -- NEVER `jest`, "
            "`jest.fn`, or `jest.mock`."
        )
        lines.append(
            "- Do NOT `import React` (the react-jsx transform is automatic; an "
            "unused React import fails the build under noUnusedLocals)."
        )
        lines.append("")
        # #26: when the pass-2 dispatcher has stamped the FINAL on-disk source
        # of the component under test onto this spec, embed it verbatim as
        # ground truth so the test asserts on what the component ACTUALLY
        # renders -- not on elements/labels/initial-state the spec merely
        # implied. Absent -> spec-based prompt (graceful fallback), no crash.
        lines.extend(_source_under_test_lines(spec))

    # Governance (priority subset -- same selection logic as the monolith).
    if governance:
        lines.append("## Governance Instructions")
        lines.append("")
        priority_keys = [
            k for k in governance
            if "CONSTITUTION" in k or "build.md" in k
            or "typescript-standards" in k or "ENFORCEMENT" in k
        ]
        other_keys = [k for k in governance if k not in priority_keys]
        for key in priority_keys:
            lines.append(f"### {key}")
            lines.append(governance[key][:3000])
            lines.append("")
        if other_keys:
            lines.append(
                f"(+{len(other_keys)} additional governance files enforced at validation)"
            )
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

# Per-file output budget bounds. A single file never truncates now: even a
# small config file gets MIN, a full CRUD component can use up to MAX. MAX
# stays within claude-opus-4-8's 128K max-output ceiling.
_MIN_FILE_MAX_TOKENS = 8000
_MAX_FILE_MAX_TOKENS = 128000

# #30: per-model completion-token ceilings. `_file_max_tokens` scales the budget
# up to 24K for a CRUD component, which Claude tolerates but gpt-4o hard-400s
# ("max_tokens is too large: 24000; model supports at most 16384"). A SINGLE
# file never needs more than ~16K output, so the budget is clamped to the
# model's real cap before the call. Prefix-matched; unknown models take the
# conservative default that no mainstream chat model 400s on.
_MODEL_MAX_OUTPUT_TOKENS: tuple[tuple[str, int], ...] = (
    ("gpt-4o", 16384),
    ("gpt-4-turbo", 4096),
    ("gpt-4.1", 32768),
    ("gpt-3.5", 4096),
    ("o1", 32768),
    ("o3", 32768),
    ("claude", 64000),
    ("gemini", 32768),
)
_DEFAULT_MODEL_MAX_OUTPUT = 16384


def _model_max_output_tokens(model: str | None) -> int:
    low = (model or "").lower()
    for prefix, cap in _MODEL_MAX_OUTPUT_TOKENS:
        if prefix in low:
            return cap
    return _DEFAULT_MODEL_MAX_OUTPUT


def _file_max_tokens(spec: dict[str, Any]) -> int:
    """Per-file output budget scaled by what the spec is. A CRUD `.tsx`
    component or a page needs room; a small types/config file needs less.
    Clamped to [_MIN_FILE_MAX_TOKENS, _MAX_FILE_MAX_TOKENS] so nothing ever
    truncates and nothing exceeds the model's output ceiling."""
    path = str(spec.get("path", "")).replace("\\", "/")
    kind = str(spec.get("kind", "source"))
    estimate = _MIN_FILE_MAX_TOKENS
    if kind == "source" and path.endswith((".tsx", ".jsx")):
        estimate = 24000  # interactive CRUD component
    elif kind == "test":
        estimate = 16000  # interaction test with multiple assertions
    elif kind == "source":
        estimate = 16000  # source module
    elif kind in ("config", "registration"):
        estimate = 8000
    return max(_MIN_FILE_MAX_TOKENS, min(_MAX_FILE_MAX_TOKENS, estimate))


def dispatch_build_agent(
    repo_root: Path,
    packet: dict,
    governance: dict[str, str],
    provider_name: str | None = None,
    model: str | None = None,
    *,
    provider: Any = None,
) -> dict[str, Any]:
    """Dispatch the build agent to execute the generation packet.

    Thin back-compat wrapper: delegates to dispatch_build_agent_chunked, which
    now performs concurrent PER-FILE LLM calls (adequate max_tokens each,
    parse+validate+retry per file, git-free worker pool) instead of one
    monolithic call that truncated at 1024 tokens and discarded most files.

    Same result contract as before:
    {
        "status": "completed" | "failed" | "no_api_key",
        "run_id": str,
        "files_written": list[str],
        "errors": list[str],
        "tokens_in": int | None,
        "tokens_out": int | None,
    }
    """
    return dispatch_build_agent_chunked(
        repo_root,
        packet,
        governance,
        provider_name=provider_name,
        model=model,
        provider=provider,
    )


def dispatch_build_agent_chunked(
    repo_root: Path,
    packet: dict,
    governance: dict[str, str],
    provider_name: str | None = None,
    model: str | None = None,
    max_workers: int | None = None,
    *,
    provider: Any = None,
) -> dict[str, Any]:
    """Execute a generation packet with concurrent PER-FILE LLM calls.

    One task per file_spec is enqueued into an in-memory TaskStore and drained
    by executor.run_worker_pool (git-free: claim/heartbeat/retry/dead-letter).
    Each task builds a single-file focused prompt (functional component +
    interaction test), calls the LLM with an adequate per-file max_tokens,
    parses exactly one fenced block, validates the path (non-empty, allowed,
    not forbidden, matches the expected spec path), and returns the file.
    Empty/no-block/forbidden/TruncatedResponseError raise so the worker pool
    RETRIES; after max_attempts the task dead-letters.

    Concurrent writes are safe: file_specs are path-disjoint. Status is
    TRUTHFUL -- "completed" only if all files written with no dead-letters and
    no errors; any dead-letter or error -> "failed" (never a false completed).
    """
    run_id = packet.get("run_id", "unknown")
    result: dict[str, Any] = {
        "status": "failed",
        "run_id": run_id,
        "files_written": [],
        "actions_taken": [],
        "validation_results": {},
        "errors": [],
        "tokens_in": None,
        "tokens_out": None,
        "agent": "signalos-build-agent-chunked",
    }

    # Check API key availability (product secret wins, else app-level keychain).
    # Honored even when a provider is injected: availability is the product's
    # gate for "should we call an LLM at all", independent of how the provider
    # is obtained. The delivery path never injects a provider.
    from .llm_provider import is_llm_available
    from .secrets_resolver import apply_product_secrets
    if not is_llm_available(repo_root):
        result["status"] = "no_api_key"
        result["errors"].append(
            "No LLM API key configured. Add a provider key in the "
            "Vault to enable agent dispatch."
        )
        return result

    gen = packet.get("generation", packet)
    file_specs = list(gen.get("file_specs", []) or [])
    if not file_specs:
        result["errors"].append("packet has no file_specs to build")
        _write_agent_result(repo_root, result)
        return result

    forbidden = gen.get("forbidden_paths", []) or []
    allowed = gen.get("allowed_paths", []) or []
    shared_context = _build_shared_context(gen)

    from .executor import run_worker_pool
    from signalos_lib.task_store import InMemoryTaskStore
    from signalos_lib.harness import TruncatedResponseError

    # Resolve provider/model ONCE and reuse for every file. When a provider is
    # injected (tests / callers), skip resolution but still overlay product
    # secrets for parity.
    with apply_product_secrets(repo_root):
        use_provider = provider
        use_model = model
        if use_provider is None:
            try:
                from signalos_lib.harness import _resolve_provider, resolve_model
                use_provider = _resolve_provider(provider_name)
                use_model = resolve_model(model, provider_name)
            except Exception as exc:
                result["errors"].append(f"Provider resolution failed: {exc}")
                _write_agent_result(repo_root, result)
                return result
        elif use_model is None:
            # Injected provider but no model: resolve best-effort, else a
            # harmless placeholder (the injected fake ignores the model).
            try:
                from signalos_lib.harness import resolve_model
                use_model = resolve_model(model, provider_name)
            except Exception:
                use_model = "auto"

        # #26: TWO-PASS generation to kill test<->component behavioral drift by
        # construction. PASS 1 generates every SOURCE spec (kind != "test":
        # types, components, App/registration, config) and writes them to disk.
        # PASS 2 then generates each TEST spec with the FINAL on-disk source of
        # its component under test injected as ground truth, so a test cannot
        # assume an element/label/initial-state the component never renders.
        # Source<->test pairing is preserved: every source still gets its test,
        # just generated in the second pass.
        non_test_specs = [
            s for s in file_specs if str(s.get("kind", "source")) != "test"
        ]
        test_specs = [
            s for s in file_specs if str(s.get("kind", "source")) == "test"
        ]

        def _enqueue(store: Any, specs: list[dict]) -> int:
            n = 0
            for spec in specs:
                spec_path = str(spec.get("path", "")).replace("\\", "/")
                if not spec_path:
                    continue
                store.enqueue(spec_path, {"spec": spec})
                n += 1
            return n

        def run_task(stored_task: Any) -> dict:
            spec = stored_task.payload["spec"]
            spec_path = str(spec.get("path", "")).replace("\\", "/")
            # #24b + #31: scaffold/boilerplate files (types.ts contract, the
            # src/ui/* layer, vitest setup, product.css) are DETERMINISTIC --
            # render them directly instead of spending an LLM call that would
            # invent non-existent imports (react-jss, @mantine/core in a shadcn
            # app, ./List/./Chart barrels) and break the build. Only real product
            # files (components, their tests, App.tsx) reach the model.
            foundation = _render_foundation_file(
                spec_path, gen, gen.get("entities", []) or [],
            )
            if foundation is not None:
                return {
                    "path": spec_path,
                    "content": foundation,
                    "tokens_in": None,
                    "tokens_out": None,
                }
            prompt = _build_single_file_prompt(
                spec, gen, governance, shared_context,
            )
            # #30: clamp the per-file budget to the MODEL's completion ceiling
            # so a 24K .tsx budget never 400s on a model that caps lower (e.g.
            # gpt-4o at 16384). The model cap wins outright -- even below the
            # normal floor -- since a request over the cap is a hard 400.
            budget = min(_file_max_tokens(spec), _model_max_output_tokens(use_model))
            # TruncatedResponseError propagates -> retryable failure.
            response_text, t_in, t_out = use_provider.call(
                f"{_SINGLE_FILE_SYSTEM_PROMPT}\n\n{prompt}",
                use_model,
                max_tokens=budget,
            )
            files = parse_agent_response(response_text)
            if not files:
                raise RuntimeError(
                    f"no parseable file block returned for {spec_path}"
                )
            # Belt-and-suspenders: drop anything forbidden/disallowed/off-target
            # so a single retryable failure names the real problem.
            accepted: dict[str, str] = {}
            for path, content in files.items():
                if _is_forbidden(path, forbidden):
                    raise RuntimeError(f"agent attempted forbidden path: {path}")
                if allowed and not _is_allowed(path, allowed):
                    raise RuntimeError(f"agent attempted disallowed path: {path}")
                if not content.strip():
                    raise RuntimeError(f"agent produced empty file: {path}")
                accepted[path] = content
            # The single-file task must yield its target path. Extra files are
            # dropped (single-file contract); the target must be present.
            if spec_path not in accepted:
                raise RuntimeError(
                    f"agent did not return the target file {spec_path} "
                    f"(returned: {sorted(accepted)})"
                )
            return {
                "path": spec_path,
                "content": accepted[spec_path],
                "tokens_in": t_in,
                "tokens_out": t_out,
            }

        files_to_write: dict[str, str] = {}
        total_in = 0
        total_out = 0
        saw_tokens = False
        reports: list[Any] = []
        stores: list[Any] = []

        def _drain(specs: list[dict]) -> None:
            nonlocal total_in, total_out, saw_tokens
            if not specs:
                return
            store = InMemoryTaskStore(max_attempts=3)
            enqueued = _enqueue(store, specs)
            if not enqueued:
                return
            workers = max_workers or min(8, max(1, enqueued))
            rep = run_worker_pool(store, run_task, max_workers=workers)
            reports.append(rep)
            stores.append(store)
            pass_files: dict[str, str] = {}
            for outcome in rep.outcomes:
                if outcome.status == "done" and isinstance(outcome.result, dict):
                    r = outcome.result
                    pass_files[r["path"]] = r["content"]
                    files_to_write[r["path"]] = r["content"]
                    if r.get("tokens_in") is not None:
                        total_in += r["tokens_in"]
                        saw_tokens = True
                    if r.get("tokens_out") is not None:
                        total_out += r["tokens_out"]
                        saw_tokens = True
            # Write this pass's files to disk NOW so the next pass can read them.
            # PASS 1 sources become the FINAL on-disk ground truth handed to the
            # PASS 2 test prompts.
            if pass_files:
                write_agent_files(repo_root, pass_files)

        # PASS 1: all non-test (source/config/registration) specs first.
        _drain(non_test_specs)

        # PASS 2: test specs, each stamped with its component's FINAL on-disk
        # source (read from what PASS 1 just wrote). Missing sibling -> the spec
        # is left unstamped and the prompt falls back to spec-based generation.
        for spec in test_specs:
            loaded = _load_source_under_test(repo_root, spec)
            if loaded is not None:
                src_path, src_text = loaded
                spec["source_under_test"] = {"path": src_path, "text": src_text}
        _drain(test_specs)

    # Each pass already wrote its files to disk (so PASS 2 could read PASS 1
    # sources). files_to_write is the union across passes; keys are unique.
    written = list(files_to_write)
    result["files_written"] = written
    result["actions_taken"].append(
        f"chunked build: {len(written)} file(s) across {len(file_specs)} task(s)"
    )
    if saw_tokens:
        result["tokens_in"] = total_in
        result["tokens_out"] = total_out

    # Truthful status: any dead-letter (from EITHER pass) names the missing
    # file(s) -> failed.
    for rep, store in zip(reports, stores):
        for dead_id in rep.dead_letters:
            dead = store.get(dead_id)
            detail = dead.error if dead and dead.error else "unknown failure"
            result["errors"].append(f"failed to generate {dead_id}: {detail}")

    expected = {
        str(spec.get("path", "")).replace("\\", "/")
        for spec in file_specs
        if spec.get("path")
    }
    missing = sorted(expected - set(written))
    for path in missing:
        if not any(path in e for e in result["errors"]):
            result["errors"].append(f"missing generated file: {path}")

    result["status"] = (
        "completed" if written and not result["errors"] and not missing
        else "failed"
    )
    _write_agent_result(repo_root, result)
    return result


def _sibling_source_path(path: str) -> str | None:
    """Map a react-vite TEST spec path to the SOURCE file it exercises, or
    None if the path is not a test file.

    Pairing is path-deterministic: ``Foo.test.tsx`` -> ``Foo.tsx``. This holds
    for both component tests (``src/components/Foo.test.tsx`` ->
    ``src/components/Foo.tsx``) and the App test (``src/App.test.tsx`` ->
    ``src/App.tsx``, whose source spec kind is ``registration``). Non-test
    paths return None -- only tests have a "component under test"."""
    p = str(path).replace("\\", "/")
    if p.endswith(".test.tsx"):
        return p[: -len(".test.tsx")] + ".tsx"
    return None


def _load_source_under_test(
    repo_root: Path, spec: dict[str, Any]
) -> tuple[str, str] | None:
    """Read the FINAL on-disk source of the component a test spec exercises.

    Returns ``(relative_source_path, source_text)`` when the sibling source
    file exists on disk (i.e. PASS 1 wrote it), else None. Never raises: a
    missing/unreadable sibling degrades to the spec-based test prompt (#26
    graceful fallback), it does not crash generation."""
    source_path = _sibling_source_path(str(spec.get("path", "")))
    if not source_path:
        return None
    try:
        text = (repo_root / source_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.strip():
        return None
    return source_path, text


def _source_under_test_lines(spec: dict[str, Any]) -> list[str]:
    """Render the FINAL component source (stamped onto a test spec by the
    pass-2 dispatcher as ``spec["source_under_test"]``) as a GROUND-TRUTH
    section of the test prompt. Empty when the spec carries no such source
    (first-pass source specs, or a test whose sibling was not found -> the
    original spec-based test prompt is used unchanged)."""
    sut = spec.get("source_under_test") or {}
    text = sut.get("text") if isinstance(sut, dict) else None
    if not text or not str(text).strip():
        return []
    sut_path = sut.get("path", "the component under test")
    lines: list[str] = [
        "## Component under test -- EXACT FINAL source (GROUND TRUTH)",
        (
            f"Here is the EXACT, FINAL source of the component under test "
            f"(`{sut_path}`). Write a test that exercises what this component "
            f"ACTUALLY renders and does on mount and interaction. Do NOT assume "
            f"any element, label, initial state, or behavior that is not present "
            f"in this source. If the component defaults to a non-form view, "
            f"drive the UI to the state under test first (e.g. click the control "
            f"that reveals the form) before asserting on its fields. This real "
            f"code -- not the spec description -- is the source of truth for what "
            f"to assert."
        ),
        "",
        "```tsx",
        str(text).rstrip("\n"),
        "```",
        "",
    ]
    return lines


def _component_group_key(path: str) -> str | None:
    """Return the component name a react-vite file_spec path belongs to, or
    None if it's a foundation/shared file. Components are only ever imported
    from App.tsx (never from each other -- see _render_app), so grouping by
    this key produces path-disjoint, dependency-safe task groups: real
    parallelism, not an inferred/risky guess at independence."""
    parts = path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "src" and parts[1] == "components":
        name = parts[2]
        for suffix in (".test.tsx", ".tsx"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
    return None


def dispatch_local_build_agent_parallel(
    repo_root: Path,
    packet: dict,
    *,
    max_workers: int = 3,
    isolate: bool = False,
) -> dict[str, Any]:
    """Run the local build agent's independent react-vite component groups
    through the real parallel executor (executor.py) -- claim, heartbeat,
    retry, dead-letter -- instead of one synchronous call.

    ``isolate`` selects the isolation strategy for the (already file-disjoint)
    task groups:

    - ``isolate=False`` (DEFAULT, the fast path): each group writes its
      disjoint files straight into *repo_root* in parallel via
      ``run_inprocess_build_tasks`` -- NO git worktrees, NO per-task commits,
      NO merge queue. The component groups are path-disjoint by construction
      (see ``_component_group_key``: components are only ever imported from
      App.tsx, never from each other), so isolation buys nothing here and the
      worktree machinery (git init + ~dozens of AV-scanned subprocesses on
      Windows + a serialized merge queue) is pure overhead that timed out a
      7-component app. This is the correct default for governed, file-disjoint
      generation.

    - ``isolate=True`` (opt-in): route through ``run_isolated_build_tasks``
      (one git worktree per task, merges serialized through a queue) for
      callers that genuinely need filesystem isolation (e.g. tasks that are
      NOT provably path-disjoint, or that must be revertable as a unit).

    Falls back verbatim to dispatch_local_build_agent for every other
    profile and for react-vite products with fewer than 2 components,
    since there's nothing to parallelize -- same result, same contract,
    no behavior change for the common case.
    """
    gen = packet.get("generation", packet)
    profile = gen.get("profile") or packet.get("profile")
    file_specs = gen.get("file_specs", [])
    run_id = str(packet.get("run_id", "unknown"))

    groups: dict[str, list[dict]] = {}
    foundation: list[dict] = []
    if profile == "react-vite":
        for spec in file_specs:
            component = _component_group_key(str(spec.get("path", "")))
            if component:
                groups.setdefault(component, []).append(spec)
            else:
                foundation.append(spec)

    if profile != "react-vite" or len(groups) < 2:
        return dispatch_local_build_agent(repo_root, packet)

    from .executor import run_inprocess_build_tasks, run_isolated_build_tasks

    task_groups: list[tuple[str, list[dict]]] = [("foundation", foundation)]
    task_groups.extend(sorted(groups.items()))

    packets: list[dict] = []
    all_component_names = sorted(groups.keys())
    for task_id, specs in task_groups:
        sub_gen = dict(gen)
        sub_gen["file_specs"] = specs
        sub_gen["_component_names_override"] = all_component_names
        sub_packet = dict(packet)
        sub_packet["generation"] = sub_gen
        sub_packet["run_id"] = f"{run_id}-{task_id}"
        sub_packet["task_id"] = f"{run_id}-{task_id}"
        packets.append(sub_packet)

    # Both parallel paths split a product into FILE-DISJOINT sub-tasks (App.tsx
    # in the foundation task, each component in its own task). A per-sub-task
    # cross-file check is therefore architecturally wrong -- App.tsx's import of
    # a component that lives in ANOTHER task's worktree is "unresolved" in this
    # one -- and in the isolated path it also RACED on merge timing (flaky
    # `'failed' != 'completed'`). Suppress the per-task cross-file check +
    # RESULT.json via write_result=False for BOTH paths; a single aggregate
    # RESULT.json is written below, and cohesion is guaranteed by the #12 shared
    # contract (App.tsx is rendered from the full component_names list).
    def _dispatch_disjoint(sub_repo: Path, sub_packet: dict) -> dict[str, Any]:
        return dispatch_local_build_agent(sub_repo, sub_packet, write_result=False)

    if isolate:
        report = run_isolated_build_tasks(
            repo_root, packets, dispatch=_dispatch_disjoint, max_workers=max_workers,
        )
    else:
        report = run_inprocess_build_tasks(
            repo_root, packets, dispatch=_dispatch_disjoint, max_workers=max_workers,
        )

    aggregate: dict[str, Any] = {
        "status": "completed" if not report.dead_letters else "failed",
        "run_id": run_id,
        "files_written": [],
        "actions_taken": [f"parallel local build across {len(task_groups)} task(s)"],
        "validation_results": {},
        "errors": [],
        "tokens_in": None,
        "tokens_out": None,
        "agent": "signalos-local-build-agent-parallel",
    }
    for outcome in report.outcomes:
        sub_result = outcome.result or {}
        aggregate["files_written"].extend(sub_result.get("files_written", []))
        if outcome.status != "done":
            aggregate["errors"].append(outcome.error or f"task {outcome.task_id} failed")
        else:
            aggregate["errors"].extend(sub_result.get("errors", []))

    if aggregate["errors"] and aggregate["status"] == "completed":
        aggregate["status"] = "failed"

    _write_agent_result(repo_root, aggregate)
    return aggregate


def dispatch_local_build_agent(
    repo_root: Path,
    packet: dict,
    *,
    write_result: bool = True,
) -> dict[str, Any]:
    """Execute a generation packet with the built-in governed local agent.

    This is a deterministic agent-team implementation for supported
    blueprints/profiles. It does not replace external agents; it gives the
    delivery bridge a reliable baseline that still obeys the same packet,
    allowed-path, forbidden-path, RESULT.json, and validation contract.

    *write_result* controls whether this call persists its own
    ``.signalos/product/agent-runs/<run_id>/RESULT.json``. It defaults to
    True (every standalone dispatch records its result). The git-free
    parallel path (``dispatch_local_build_agent_parallel`` with
    ``isolate=False``) runs each sub-packet in place against the SAME
    repo_root, so it passes ``write_result=False`` for the sub-tasks and
    writes a single aggregate RESULT.json for the whole delivery instead --
    exactly one agent-run entry, matching the worktree path (which achieved
    the same by excluding ``.signalos`` from each task's merge).
    """
    run_id = packet.get("run_id", "unknown")
    result: dict[str, Any] = {
        "status": "failed",
        "run_id": run_id,
        "files_written": [],
        "actions_taken": [],
        "validation_results": {},
        "errors": [],
        "tokens_in": None,
        "tokens_out": None,
        "agent": "signalos-local-build-agent",
    }

    gen = packet.get("generation", packet)
    profile = gen.get("profile") or packet.get("profile")
    if profile == "react-vite":
        files = _render_react_vite_files(gen)
    elif profile == "generic":
        files = _render_generic_python_files(gen)
    elif profile == "fastapi-api":
        files = _render_fastapi_files(gen)
    else:
        result["status"] = "partial"
        result["errors"].append(
            f"local build agent does not support profile: {profile}"
        )
        if write_result:
            _write_agent_result(repo_root, result)
        return result

    file_specs = gen.get("file_specs", [])
    expected_paths = {
        str(spec.get("path", "")).replace("\\", "/")
        for spec in file_specs
        if spec.get("path")
    }

    missing = sorted(expected_paths - set(files))
    if missing:
        result["errors"].append(
            "local build agent did not render expected files: "
            + ", ".join(missing)
        )

    allowed = gen.get("allowed_paths", [])
    forbidden = gen.get("forbidden_paths", [])
    accepted_files: dict[str, str] = {}
    for path, content in files.items():
        if path not in expected_paths:
            result["errors"].append(f"local agent produced unexpected path: {path}")
            continue
        if _is_forbidden(path, forbidden):
            result["errors"].append(f"local agent attempted forbidden path: {path}")
            continue
        if not _is_allowed(path, allowed):
            result["errors"].append(f"local agent attempted disallowed path: {path}")
            continue
        if not content.strip():
            result["errors"].append(f"local agent produced empty file: {path}")
            continue
        accepted_files[path] = content

    written = write_agent_files(repo_root, accepted_files)
    result["files_written"] = written
    result["actions_taken"].append(
        f"rendered {len(written)} governed file(s) from generation packet"
    )

    try:
        from .generation import validate_generation_output

        # Fix #12: when write_result is False this is a PARTIAL sub-task of the
        # parallel in-place build -- a component subset can import ../types
        # before the concurrent foundation task writes types.ts, so the
        # cross-file check would false-positive. The authoritative whole-repo
        # cross-file check runs once over the FULL packet in delivery.py.
        generation_validation = validate_generation_output(
            repo_root, gen, check_cross_file=write_result,
        )
        result["validation_results"]["generation_output"] = generation_validation
        if not generation_validation.get("valid", False):
            result["errors"].extend(generation_validation.get("violations", []))
            result["errors"].extend(
                f"missing file: {path}"
                for path in generation_validation.get("files_missing", [])
            )
    except Exception as exc:
        result["errors"].append(f"generation output validation failed: {exc}")

    result["status"] = "completed" if written and not result["errors"] else "failed"
    if write_result:
        _write_agent_result(repo_root, result)
    return result


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_FILE_BLOCK_PATTERN = re.compile(
    r"```([^\n]+)\n(.*?)```",
    re.DOTALL,
)


def parse_agent_response(response: str) -> dict[str, str]:
    """Parse fenced code blocks from agent response into {path: content}.

    Expected format:
    ```path/to/file.tsx
    content here
    ```
    """
    files: dict[str, str] = {}
    for match in _FILE_BLOCK_PATTERN.finditer(response):
        path = match.group(1).strip()
        content = match.group(2)

        # Skip language-only fences (e.g., ```typescript)
        if "/" not in path and "." not in path:
            continue

        # Normalize path
        path = path.lstrip("/").replace("\\", "/")
        if path:
            files[path] = content

    return files


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

def write_agent_files(repo_root: Path, files: dict[str, str]) -> list[str]:
    """Write parsed files to disk. Returns list of paths written."""
    written: list[str] = []
    for rel_path, content in files.items():
        target = repo_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel_path)
    return written


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_foundation_file(
    path: str, gen: dict[str, Any], entities: list[Any],
) -> str | None:
    """Deterministic content for a scaffold/boilerplate file, or None if `path`
    is a product file the LLM must generate.

    #31: these `src/ui/*` + setup + css files are pure boilerplate. An LLM asked
    to write them invents imports that don't exist (`react-jss`,
    `@fontsource/inter`, `@mantine/core` in a shadcn app, `./List`/`./Chart`
    barrels for components that were never generated), so they are rendered
    deterministically in BOTH the local path and the chunked-LLM `run_task` --
    exactly like the #24b `types.ts` contract. Product files (components, their
    tests, App.tsx, App.test.tsx) return None and stay LLM-generated.
    """
    if path == "src/types.ts":
        return _render_types(entities)
    if path == "src/test/setup.ts":
        return _render_vitest_setup()
    if path == "src/ui/theme.ts":
        return _render_theme(gen.get("design_constraints", {}) or {})
    if path == "src/ui/index.ts":
        return "export * from './theme';\n"
    if path == "src/ui/layouts/AppLayout.tsx":
        return _render_app_layout()
    if path == "src/ui/layouts/PageLayout.tsx":
        return _render_page_layout()
    if path == "src/product.css":
        return _render_product_css()
    return None


def _render_react_vite_files(gen: dict[str, Any]) -> dict[str, str]:
    file_specs = gen.get("file_specs", [])
    product_name = gen.get("product") or "SignalOS Product"
    entities = gen.get("entities", [])
    workflows = gen.get("workflows", [])
    criteria = gen.get("acceptance_criteria", [])

    component_paths = [
        str(spec.get("path", "")).replace("\\", "/")
        for spec in file_specs
        if str(spec.get("path", "")).replace("\\", "/").startswith("src/components/")
        and str(spec.get("path", "")).endswith(".tsx")
        and not str(spec.get("path", "")).endswith(".test.tsx")
    ]
    # 1.1: when file_specs has been split into a per-task subset (parallel
    # local build -- see dispatch_local_build_agent_parallel), a task that
    # doesn't own any component files still needs the FULL component list to
    # render App.tsx's imports/usage correctly. Explicit override, falls
    # back to deriving from this call's own file_specs otherwise.
    component_names = gen.get("_component_names_override") or [
        Path(path).stem for path in component_paths
    ]

    files: dict[str, str] = {}
    for spec in file_specs:
        path = str(spec.get("path", "")).replace("\\", "/")
        if not path:
            continue
        foundation = _render_foundation_file(path, gen, entities)
        if foundation is not None:
            files[path] = foundation
        elif path == "src/App.tsx":
            files[path] = _render_app(product_name, component_names, workflows, criteria)
        elif path == "src/App.test.tsx":
            files[path] = _render_app_test(product_name)
        elif path.startswith("src/components/") and path.endswith(".test.tsx"):
            component = Path(path).name.replace(".test.tsx", "")
            files[path] = _render_component_test(component)
        elif path.startswith("src/components/") and path.endswith(".tsx"):
            component = Path(path).stem
            files[path] = _render_component(component, spec, entities, criteria)

    return files


def _render_generic_python_files(gen: dict[str, Any]) -> dict[str, str]:
    file_specs = gen.get("file_specs", [])
    files: dict[str, str] = {}
    source_specs = {
        str(spec.get("entity") or ""): spec
        for spec in file_specs
        if str(spec.get("path", "")).replace("\\", "/").endswith(".py")
        and spec.get("kind") == "source"
        and spec.get("entity")
    }

    for spec in file_specs:
        path = str(spec.get("path", "")).replace("\\", "/")
        if not path:
            continue
        if path.endswith("__init__.py"):
            files[path] = ""
        elif path.endswith(".py") and spec.get("kind") == "test":
            source_spec = source_specs.get(str(spec.get("entity") or ""))
            files[path] = _render_python_entity_test(spec, source_spec)
        elif path.endswith(".py") and spec.get("kind") == "source":
            files[path] = _render_python_entity(spec)

    return files


def _render_fastapi_files(gen: dict[str, Any]) -> dict[str, str]:
    file_specs = gen.get("file_specs", [])
    files: dict[str, str] = {}
    route_modules: list[tuple[str, str]] = []
    entity_specs = [
        spec
        for spec in file_specs
        if spec.get("entity") and str(spec.get("path", "")).replace("\\", "/").endswith(".py")
    ]

    for spec in file_specs:
        path = str(spec.get("path", "")).replace("\\", "/")
        if not path:
            continue
        if path.endswith("__init__.py"):
            files[path] = ""
        elif path.endswith("/store.py"):
            files[path] = _render_fastapi_store()
        elif path.endswith("/app.py"):
            # app.py is rendered after route modules are known below.
            continue
        elif "/models/" in path and path.endswith(".py"):
            files[path] = _render_fastapi_model(spec)
        elif "/routes/" in path and path.endswith(".py"):
            module = Path(path).stem
            entity = _to_pascal(str(spec.get("entity") or module))
            route_modules.append((module, _route_prefix(module)))
            files[path] = _render_fastapi_route(spec)
        elif path.endswith(".py") and spec.get("kind") == "test":
            files[path] = _render_fastapi_test(spec, entity_specs)
        elif path.endswith("/workflows.py"):
            files[path] = _render_fastapi_workflows()

    for spec in file_specs:
        path = str(spec.get("path", "")).replace("\\", "/")
        if path.endswith("/app.py"):
            files[path] = _render_fastapi_app(route_modules)

    return files


def _render_fastapi_store() -> str:
    return """\
from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4


class InMemoryStore:
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self._items: dict[str, dict[str, Any]] = {}

    def list(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self._items.values()]

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = {"id": str(uuid4()), **payload}
        self._items[item["id"]] = item
        return deepcopy(item)

    def get(self, item_id: str) -> dict[str, Any] | None:
        item = self._items.get(item_id)
        return deepcopy(item) if item is not None else None
"""


def _render_fastapi_model(spec: dict[str, Any]) -> str:
    entity = _to_pascal(str(spec.get("entity") or Path(str(spec.get("path", "record.py"))).stem))
    fields = [field for field in _python_fields_from_spec(spec) if field != "id"]
    create_fields = "\n".join(f"    {field}: str | None = None" for field in fields) or "    name: str | None = None"
    record_fields = "\n".join(f"    {field}: str | None = None" for field in fields) or "    name: str | None = None"
    return f"""\
from __future__ import annotations

from pydantic import BaseModel


class {entity}Create(BaseModel):
{create_fields}


class {entity}Record({entity}Create):
    id: str
{record_fields}
"""


def _render_fastapi_route(spec: dict[str, Any]) -> str:
    entity = _to_pascal(str(spec.get("entity") or Path(str(spec.get("path", "record.py"))).stem))
    module = _to_snake(entity)
    return f"""\
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from signalos_product_fastapi.models.{module} import {entity}Create, {entity}Record
from signalos_product_fastapi.store import InMemoryStore


router = APIRouter()
_store = InMemoryStore({json.dumps(_route_prefix(module))})


@router.get("/", response_model=list[{entity}Record])
def list_{module}() -> list[dict]:
    return _store.list()


@router.post("/", response_model={entity}Record, status_code=201)
def create_{module}(payload: {entity}Create) -> dict:
    return _store.create(payload.model_dump(exclude_none=True))


@router.get("/{{item_id}}", response_model={entity}Record)
def get_{module}(item_id: str) -> dict:
    item = _store.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="{entity} not found")
    return item
"""


def _render_fastapi_app(route_modules: list[tuple[str, str]]) -> str:
    imports: list[str] = []
    includes: list[str] = []
    for module, prefix in sorted(dict(route_modules).items()):
        alias = f"{module}_router"
        imports.append(f"from signalos_product_fastapi.routes.{module} import router as {alias}")
        includes.append(f"app.include_router({alias}, prefix='/api/{prefix}', tags=['{prefix}'])")

    imports_text = "\n".join(imports)
    includes_text = "\n".join(includes)
    if includes_text:
        includes_text += "\n"
    return f"""\
from __future__ import annotations

from fastapi import FastAPI

{imports_text}

app = FastAPI(title="SignalOS FastAPI Product")


@app.get("/health")
def health() -> dict[str, str]:
    return {{"status": "ok"}}


{includes_text}"""


def _render_fastapi_test(spec: dict[str, Any], entity_specs: list[dict[str, Any]]) -> str:
    entity = _to_pascal(str(spec.get("entity") or "ProductResource"))
    module = _to_snake(entity)
    fields = [field for field in _python_fields_from_spec(spec) if field != "id"]
    payload = {field: _sample_python_value(field) for field in (fields or ["name"])}
    route = _route_prefix(module)
    if spec.get("entity"):
        return f"""\
from fastapi.testclient import TestClient

from signalos_product_fastapi.app import app


def test_{module}_api_create_and_list() -> None:
    client = TestClient(app)
    create_response = client.post("/api/{route}/", json={json.dumps(payload, sort_keys=True)})
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["id"]

    list_response = client.get("/api/{route}/")
    assert list_response.status_code == 200
    assert any(item["id"] == created["id"] for item in list_response.json())
"""
    route_assert = ""
    for candidate in entity_specs:
        candidate_entity = _to_snake(str(candidate.get("entity") or ""))
        if candidate_entity:
            route_assert = f'\n    assert client.get("/api/{_route_prefix(candidate_entity)}/").status_code == 200'
            break
    return f"""\
from fastapi.testclient import TestClient

from signalos_product_fastapi.app import app


def test_app_health_and_routes() -> None:
    client = TestClient(app)
    assert client.get("/health").json() == {{"status": "ok"}}{route_assert}
"""


def _render_fastapi_workflows() -> str:
    return """\
from __future__ import annotations


def record_workflow_event(name: str, payload: dict) -> dict:
    return {"workflow": name, "payload": payload, "status": "recorded"}
"""


def _route_prefix(module: str) -> str:
    return module if module.endswith("s") else f"{module}s"


def _render_python_entity(spec: dict[str, Any]) -> str:
    entity = _to_pascal(str(spec.get("entity") or Path(str(spec.get("path", "record.py"))).stem))
    fields = _python_fields_from_spec(spec)
    field_lines = "\n".join(f"    {field}: Any = None" for field in fields)
    return f"""\
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class {entity}:
{field_lines}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
"""


def _render_python_entity_test(
    spec: dict[str, Any],
    source_spec: dict[str, Any] | None,
) -> str:
    entity = _to_pascal(str(spec.get("entity") or "ProductRecord"))
    source_path = str((source_spec or {}).get("path") or "")
    module_name = Path(source_path).stem if source_path else _to_snake(entity)
    package_name = _package_from_source_path(source_path)
    fields = _python_fields_from_spec(source_spec or spec)
    first_field = fields[0] if fields else "id"
    kwargs = ", ".join(f"{field}={json.dumps(_sample_python_value(field))}" for field in fields)
    import_line = (
        f"from {package_name}.{module_name} import {entity}"
        if package_name
        else f"from {module_name} import {entity}"
    )
    return f"""\
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

{import_line}


class Test{entity}(unittest.TestCase):
    def test_create_and_serialize_{_to_snake(entity)}(self) -> None:
        item = {entity}({kwargs})
        data = item.to_dict()
        self.assertEqual(data[{json.dumps(first_field)}], {json.dumps(_sample_python_value(first_field))})


if __name__ == "__main__":
    unittest.main()
"""


def _python_fields_from_spec(spec: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    description = str(spec.get("description") or "")
    match = re.search(r"Fields:\s*([^\.]+)", description)
    if match:
        fields.extend(match.group(1).split(","))
    if not fields:
        fields = ["id", "name", "status"]
    cleaned = [_safe_py_field(str(field)) for field in fields]
    return list(dict.fromkeys(field for field in cleaned if field))


def _safe_py_field(field: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", field.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "value"
    if cleaned[0].isdigit():
        cleaned = f"field_{cleaned}"
    if cleaned in {
        "class", "def", "return", "from", "import", "for", "while", "if",
        "else", "try", "except", "with", "as", "pass", "None", "True",
        "False",
    }:
        cleaned = f"{cleaned}_value"
    return cleaned


def _sample_python_value(field: str) -> str:
    lower = field.lower()
    if "id" in lower:
        return "sample-id"
    if "status" in lower:
        return "ready"
    if "email" in lower:
        return "user@example.test"
    return f"sample-{lower.replace('_', '-')}"


def _package_from_source_path(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "src":
        return parts[1]
    return ""


def _render_vitest_setup() -> str:
    return (
        "// Generated by the SignalOS local build agent from the approved packet.\n"
        "// Registers @testing-library/jest-dom matchers for every test.\n"
        "import '@testing-library/jest-dom';\n"
    )


def _render_types(entities: list[Any]) -> str:
    lines = [
        "// Generated by the SignalOS local build agent from the approved packet.",
        "",
    ]
    if not entities:
        lines.extend([
            "export interface ProductRecord {",
            "  id: string;",
            "  name: string;",
            "}",
            "",
        ])
        return "\n".join(lines)

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        name = _to_pascal(str(entity.get("name", "ProductRecord")))
        fields = entity.get("fields") or ["id", "name"]
        lines.append(f"export interface {name} {{")
        for raw_field in fields:
            field = _safe_ts_field(str(raw_field))
            lines.append(f"  {field}: {_infer_ts_type(field)};")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def _render_theme(design_constraints: dict[str, Any]) -> str:
    tokens = design_constraints.get("design_tokens", {}) or {}
    primary = tokens.get("primary_color") or "#2563eb"
    radius = tokens.get("border_radius") or "8px"
    font = tokens.get("font_family") or "Inter, system-ui, sans-serif"
    return f"""\
// Generated by the SignalOS local build agent from the approved packet.
export type Theme = {{
  colors: {{ primary: string; text: string; muted: string; surface: string; border: string }};
  radius: string;
  fontFamily: string;
}};

export const theme: Theme = {{
  colors: {{
    primary: {json.dumps(primary)},
    text: '#111827',
    muted: '#6b7280',
    surface: '#ffffff',
    border: '#e5e7eb',
  }},
  radius: {json.dumps(radius)},
  fontFamily: {json.dumps(font)},
}};
"""


def _render_app_layout() -> str:
    return """\
import type { ReactNode } from 'react';

type AppLayoutProps = {
  title: string;
  children: ReactNode;
};

export function AppLayout({ title, children }: AppLayoutProps) {
  return (
    <div className="product-shell">
      <header className="product-header">
        <p className="eyebrow">SignalOS generated product</p>
        <h1>{title}</h1>
      </header>
      <main>{children}</main>
    </div>
  );
}
"""


def _render_page_layout() -> str:
    return """\
import type { ReactNode } from 'react';

type PageLayoutProps = {
  title: string;
  children: ReactNode;
};

export function PageLayout({ title, children }: PageLayoutProps) {
  return (
    <section className="product-card" aria-label={title}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}
"""


def _render_product_css() -> str:
    return """\
:root {
  color: #111827;
  background: #f6f7f9;
  font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

body {
  margin: 0;
}

.product-shell {
  min-height: 100vh;
  padding: 32px;
}

.product-header {
  max-width: 1160px;
  margin: 0 auto 24px;
}

.eyebrow {
  color: #2563eb;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0;
  margin: 0 0 8px;
  text-transform: uppercase;
}

.product-header h1 {
  font-size: 36px;
  line-height: 1.1;
  margin: 0;
}

.summary-grid,
.product-grid {
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  max-width: 1160px;
  margin: 0 auto 16px;
}

.product-card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(17, 24, 39, 0.06);
  padding: 18px;
}

.product-card h2 {
  font-size: 18px;
  margin: 0 0 12px;
}

.metric {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid #eef0f3;
}

.metric:first-of-type {
  border-top: 0;
}

.metric span {
  color: #6b7280;
}

.metric strong,
.status-pill {
  font-weight: 700;
}

.status-pill {
  background: #e0f2fe;
  border-radius: 999px;
  color: #075985;
  display: inline-flex;
  padding: 4px 10px;
}

@media (max-width: 720px) {
  .product-shell {
    padding: 18px;
  }

  .product-header h1 {
    font-size: 28px;
  }
}
"""


def _render_app(
    product_name: str,
    component_names: list[str],
    workflows: list[Any],
    criteria: list[Any],
) -> str:
    imports = [
        "import './product.css';",
        "import { AppLayout } from './ui/layouts/AppLayout';",
    ]
    for component in component_names:
        imports.append(f"import {component} from './components/{component}';")

    workflow_count = len(workflows)
    criteria_count = len(criteria)
    cards = "\n".join(
        f"        <{component} />" for component in component_names
    ) or "        <p>No generated components were specified.</p>"

    return f"""\
{chr(10).join(imports)}

function App() {{
  return (
    <AppLayout title={json.dumps(product_name)}>
      <div className="summary-grid" aria-label="Delivery summary">
        <section className="product-card">
          <h2>Governed delivery scope</h2>
          <div className="metric"><span>Approved workflows</span><strong>{workflow_count}</strong></div>
          <div className="metric"><span>Acceptance criteria</span><strong>{criteria_count}</strong></div>
          <div className="metric"><span>Agent boundary</span><strong>Scoped files only</strong></div>
        </section>
      </div>
      <div className="product-grid">
{cards}
      </div>
    </AppLayout>
  );
}}

export default App;
"""


def _render_app_test(product_name: str) -> str:
    return f"""\
import {{ render, screen }} from '@testing-library/react';
import {{ expect, test }} from 'vitest';
import App from './App';

test('renders generated product shell', () => {{
  render(<App />);
  expect(screen.getByText({json.dumps(product_name)})).toBeDefined();
  expect(screen.getByText(/Governed delivery scope/i)).toBeDefined();
}});
"""


def _render_component(
    component: str,
    spec: dict[str, Any],
    entities: list[Any],
    criteria: list[Any],
) -> str:
    title = _label_from_component(component)
    entity_name = spec.get("entity") or _entity_name_for_component(component, entities)
    matched = _criteria_for_text(title, criteria)
    metrics = [
        ("Surface", title),
        ("Domain object", entity_name or "Product workflow"),
        ("Evidence", matched or "Acceptance linked in packet"),
    ]
    metric_rows = ",\n".join(
        f"  {{ label: {json.dumps(label)}, value: {json.dumps(value)} }}"
        for label, value in metrics
    )
    return f"""\
const metrics = [
{metric_rows},
] as const;

export function {component}() {{
  return (
    <section className="product-card" aria-label={json.dumps(title)}>
      <h2>{title}</h2>
      {{metrics.map((metric) => (
        <div className="metric" key={{metric.label}}>
          <span>{{metric.label}}</span>
          <strong>{{metric.value}}</strong>
        </div>
      ))}}
      <span className="status-pill">Ready for validation</span>
    </section>
  );
}}

export default {component};
"""


def _render_component_test(component: str) -> str:
    title = _label_from_component(component)
    return f"""\
import {{ render, screen }} from '@testing-library/react';
import {{ expect, test }} from 'vitest';
import {component} from './{component}';

test('renders {title}', () => {{
  render(<{component} />);
  expect(screen.getByRole('heading', {{ name: /{re.escape(title)}/i }})).toBeDefined();
  expect(screen.getByText(/Ready for validation/i)).toBeDefined();
}});
"""


def _label_from_component(component: str) -> str:
    words = re.sub(r"(?<!^)([A-Z])", r" \1", component).strip()
    return words or component


def _entity_name_for_component(component: str, entities: list[Any]) -> str:
    lower = component.lower()
    for entity in entities:
        if isinstance(entity, dict):
            name = str(entity.get("name", ""))
        else:
            name = str(entity)
        if name and name.lower() in lower:
            return name
    return ""


def _criteria_for_text(text: str, criteria: list[Any]) -> str:
    words = {word.lower() for word in re.findall(r"[A-Za-z]{4,}", text)}
    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        desc = str(criterion.get("description", ""))
        if any(word in desc.lower() for word in words):
            return desc
    return ""


def _safe_ts_field(field: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", field.strip())
    if not cleaned:
        return "value"
    if cleaned[0].isdigit():
        cleaned = f"field_{cleaned}"
    return cleaned


_BOOLEAN_FIELD_WORDS = frozenset({
    "recurring", "active", "enabled", "disabled", "reimbursed", "paid",
    "completed", "done", "archived", "verified", "approved", "published",
    "featured", "pinned", "starred", "favorite", "favorited", "read", "unread",
    "locked", "deleted", "cancelled", "canceled", "confirmed", "resolved",
    "closed", "shared", "public", "private", "required",
})
_NUMBER_FIELD_WORDS = (
    "hours", "percent", "value", "target", "amount", "count", "rate", "total",
    "price", "cost", "quantity", "qty", "balance", "budget", "score", "age",
    "weight", "height", "duration", "number", "num", "sum", "limit", "size",
)


def _infer_ts_type(field: str) -> str:
    # #24b: the frozen types.ts is the authoritative contract every component
    # conforms to, so its field types must be SEMANTICALLY right -- a status
    # toggle typed as `string` forces the component to re-drift when it sets a
    # boolean.
    lower = field.lower()
    is_bool = (
        lower in _BOOLEAN_FIELD_WORDS
        or lower.startswith(("is_", "has_"))
        # camelCase isX/hasX (guard bare `island`/`hash` etc.)
        or (lower[:2] in ("is", "ha") and len(field) > 2 and field[2:3].isupper())
    )
    if is_bool:
        return "boolean"
    if any(token in lower for token in _NUMBER_FIELD_WORDS):
        return "number"
    return "string"


def _operations_contract(
    entity: str | None, fields: list[str] | None
) -> list[dict[str, str]]:
    """The deterministic per-entity OPERATIONS contract shared, verbatim, by a
    component's prompt AND its test's prompt (the #37 test-convergence fix,
    same principle as the #24b frozen type contract one layer over: both files
    work from one authoritative list, so they cannot disagree on WHICH
    operations exist).

    Scope is deliberate. It lists only operations that are BOTH reliably
    generatable and reliably assertable via React Testing Library:

      - CREATE  -- a form; submitting grows the list.
      - DELETE  -- a per-row control; clicking removes the row.
      - TOGGLE <field> -- for each boolean field; clicking flips the value.

    Inline EDIT / update-in-place is intentionally EXCLUDED. It is the
    least-reliably-generated flow (pre-fill a form, save, cancel, reconcile the
    row) and was the exact source of the real e2e's `edit/update/cancel`
    test<->source drift (17/37). create + delete + toggle is a coherent,
    fully-functional CRUD-lite: honest, not a lowered bar. Each entry carries a
    `component` phrasing (what to build) and a `test` phrasing (what to assert)
    of the SAME operation."""
    label = (entity or "item").strip() or "item"
    ops: list[dict[str, str]] = [
        {
            "key": "create",
            "component": (
                f"CREATE: a form to add a new {label}; submitting it appends a "
                f"new {label} to the list."
            ),
            "test": (
                f"CREATE: fill the form and submit it, then assert the list GREW "
                f"by the new {label}."
            ),
        },
        {
            "key": "delete",
            "component": (
                f"DELETE: a control on each {label} row that removes that "
                f"{label} from the list."
            ),
            "test": (
                f"DELETE: click a {label}'s delete control, then assert that row "
                f"is GONE from the list."
            ),
        },
    ]
    for f in fields or []:
        if _infer_ts_type(str(f)) == "boolean":
            ops.append({
                "key": f"toggle_{f}",
                "component": (
                    f"TOGGLE `{f}`: a control that flips `{f}` on a {label} and "
                    f"reflects the new value in the UI."
                ),
                "test": (
                    f"TOGGLE `{f}`: toggle `{f}` on a {label} and assert its "
                    f"displayed value FLIPPED."
                ),
            })
    return ops


def _to_pascal(value: str) -> str:
    words = re.split(r"[\s\-_]+", value.strip())
    return "".join(word[:1].upper() + word[1:] for word in words if word)


def _to_snake(value: str) -> str:
    value = re.sub(r"(?<!^)([A-Z])", r"_\1", value)
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_").lower()
    return value or "product_record"


def _is_allowed(path: str, allowed: list[str]) -> bool:
    import fnmatch

    normed = path.replace("\\", "/")
    for pattern in allowed:
        pat = pattern.replace("\\", "/")
        if fnmatch.fnmatch(normed, pat):
            return True
    return False


def _write_agent_result(repo_root: Path, result: dict[str, Any]) -> None:
    run_id = result.get("run_id")
    if not run_id:
        return
    run_dir = repo_root / ".signalos" / "product" / "agent-runs" / str(run_id)
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "RESULT.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return

def _is_forbidden(path: str, forbidden: list[str]) -> bool:
    """Check if a path matches any forbidden pattern."""
    import fnmatch
    normed = path.replace("\\", "/")
    for pat in forbidden:
        p = pat.replace("\\", "/").rstrip("/")
        if pat.endswith("/"):
            if normed.startswith(p + "/") or normed == p:
                return True
        elif "*" in pat:
            if fnmatch.fnmatch(normed, p):
                return True
        elif normed == p:
            return True
    return False
