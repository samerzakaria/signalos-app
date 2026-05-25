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

    # Check API key availability
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("SIGNALOS_LLM_PROVIDER")):
        result["status"] = "no_api_key"
        result["errors"].append(
            "No LLM API key configured. Set ANTHROPIC_API_KEY or "
            "SIGNALOS_LLM_PROVIDER to enable agent dispatch."
        )
        return result

    # Resolve provider
    try:
        from signalos_lib.harness import _resolve_provider, DEFAULT_MODEL
        provider = _resolve_provider(provider_name)
    except Exception as exc:
        result["errors"].append(f"Provider resolution failed: {exc}")
        return result

    # Build prompt
    prompt = _build_agent_prompt(packet, governance)
    use_model = model or DEFAULT_MODEL

    # Call LLM
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
