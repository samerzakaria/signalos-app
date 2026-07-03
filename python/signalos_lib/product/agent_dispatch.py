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
# Dispatch
# ---------------------------------------------------------------------------

def dispatch_build_agent(
    repo_root: Path,
    packet: dict,
    governance: dict[str, str],
    provider_name: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Dispatch the build agent to execute the generation packet.

    Calls the LLM with the packet-derived prompt, parses the response
    into file contents, writes them to disk, and returns a result dict.

    Falls back gracefully if no API key is configured.

    Returns:
    {
        "status": "completed" | "failed" | "no_api_key",
        "run_id": str,
        "files_written": list[str],
        "errors": list[str],
        "tokens_in": int | None,
        "tokens_out": int | None,
    }
    """
    import os

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
    }

    # Check API key availability (product secret wins, else app-level keychain).
    from .llm_provider import is_llm_available
    from .secrets_resolver import apply_product_secrets
    if not is_llm_available(repo_root):
        result["status"] = "no_api_key"
        result["errors"].append(
            "No LLM API key configured. Add a provider key in the "
            "Vault to enable agent dispatch."
        )
        return result

    # Build prompt
    prompt = _build_agent_prompt(packet, governance)

    # Resolve + call with the product's provider keys overlaid (product wins).
    with apply_product_secrets(repo_root):
        try:
            from signalos_lib.harness import _resolve_provider, resolve_model
            provider = _resolve_provider(provider_name)
            # No hardcoded default: explicit model → SIGNALOS_LLM_MODEL →
            # discovery from the resolved provider's API.
            use_model = resolve_model(model, provider_name)
        except Exception as exc:
            result["errors"].append(f"Provider resolution failed: {exc}")
            return result

        try:
            response_text, tokens_in, tokens_out = provider.call(
                f"{_SYSTEM_PROMPT}\n\n{prompt}",
                use_model,
            )
            result["tokens_in"] = tokens_in
            result["tokens_out"] = tokens_out
        except Exception as exc:
            result["errors"].append(f"LLM call failed: {exc}")
            return result

    # Parse response into files
    files = parse_agent_response(response_text)
    if not files:
        result["errors"].append("Agent response contained no parseable file blocks")
        return result

    # Validate paths before writing
    gen = packet.get("generation", packet)
    forbidden = gen.get("forbidden_paths", [])
    for path in list(files.keys()):
        if _is_forbidden(path, forbidden):
            result["errors"].append(f"Agent attempted forbidden path: {path}")
            del files[path]

    # Write files to disk
    written = write_agent_files(repo_root, files)
    result["files_written"] = written
    result["actions_taken"].append(f"wrote {len(written)} file(s)")
    result["status"] = "completed" if written else "failed"
    _write_agent_result(repo_root, result)

    return result


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
) -> dict[str, Any]:
    """1.1: run the local build agent's independent react-vite component
    groups through the real parallel executor (executor.py) -- claim,
    heartbeat, retry, worktree isolation, merge queue -- instead of one
    synchronous call.

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

    from .executor import run_isolated_build_tasks

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

    report = run_isolated_build_tasks(repo_root, packets, max_workers=max_workers)

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
) -> dict[str, Any]:
    """Execute a generation packet with the built-in governed local agent.

    This is a deterministic agent-team implementation for supported
    blueprints/profiles. It does not replace external agents; it gives the
    delivery bridge a reliable baseline that still obeys the same packet,
    allowed-path, forbidden-path, RESULT.json, and validation contract.
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

        generation_validation = validate_generation_output(repo_root, gen)
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
        if path == "src/types.ts":
            files[path] = _render_types(entities)
        elif path == "src/ui/theme.ts":
            files[path] = _render_theme(gen.get("design_constraints", {}))
        elif path == "src/ui/index.ts":
            files[path] = "export * from './theme';\n"
        elif path == "src/ui/layouts/AppLayout.tsx":
            files[path] = _render_app_layout()
        elif path == "src/ui/layouts/PageLayout.tsx":
            files[path] = _render_page_layout()
        elif path == "src/product.css":
            files[path] = _render_product_css()
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


def _infer_ts_type(field: str) -> str:
    lower = field.lower()
    if any(token in lower for token in ("hours", "percent", "value", "target", "amount", "count", "rate", "total")):
        return "number"
    if lower.startswith("is_") or lower in {"recurring", "active", "enabled"}:
        return "boolean"
    return "string"


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
