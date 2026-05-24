# signalos_lib/product/agent_packets.py
# Phase P8 - Agent Execution Bridge
#
# Builds scoped agent execution packets that let external agents (Codex,
# Claude, Cursor, or the built-in orchestrator) build missing product
# pieces while SignalOS controls scope and evidence.
#
# Key invariant: unowned agent output is non-binding until validated.

from __future__ import annotations

__all__ = [
    "build_agent_packet",
    "validate_agent_result",
    "write_agent_packet",
]

import fnmatch
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .validation import build_validation_plan


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_FORBIDDEN_PATHS: list[str] = [
    ".signalos/",
    "node_modules/",
    ".git/",
    ".env",
    ".env.local",
    "*.pem",
    "*.key",
]

_DEFAULT_FORBIDDEN_ACTIONS: list[str] = [
    "git push",
    "npm publish",
    "deploy",
    "rm -rf",
]


# ---------------------------------------------------------------------------
# Result schema the agent is expected to write as RESULT.json
# ---------------------------------------------------------------------------

_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["run_id", "status", "files_written"],
    "properties": {
        "run_id": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["completed", "failed", "partial"],
        },
        "files_written": {
            "type": "array",
            "items": {"type": "string"},
        },
        "actions_taken": {
            "type": "array",
            "items": {"type": "string"},
        },
        "validation_results": {
            "type": "object",
        },
        "error": {"type": "string"},
    },
}


# ---------------------------------------------------------------------------
# build_agent_packet
# ---------------------------------------------------------------------------

def build_agent_packet(
    repo_root: Path,
    intent: dict,
    blueprint: dict | None,
    acceptance_matrix: dict,
    profile: str,
    wave: str,
    tasks: list[dict],
    allowed_paths: list[str],
    forbidden_actions: list[str] | None = None,
) -> dict:
    """Build a scoped agent execution packet.

    The packet contains everything an external agent needs to execute a
    bounded set of tasks: intent context, acceptance criteria, allowed
    and forbidden file paths, validation commands, and the expected
    result schema.

    Forbidden paths always include ``.signalos/``, ``node_modules/``,
    ``.git/``, ``.env``, ``.env.local``, ``*.pem``, and ``*.key``.

    Forbidden actions default to ``git push``, ``npm publish``,
    ``deploy``, and ``rm -rf`` unless overridden.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Trimmed intent -- only the fields agents need
    intent_summary = {
        "product_name": intent.get("product_name", ""),
        "product_type": intent.get("product_type", ""),
        "entities": intent.get("entities", []),
        "primary_workflows": intent.get("primary_workflows", []),
        "ux_surfaces": intent.get("ux_surfaces", []),
    }

    blueprint_id = blueprint.get("id") if blueprint else None

    # Acceptance criteria from matrix
    acceptance_criteria = acceptance_matrix.get("criteria", [])

    # Validation commands from the profile adapter
    try:
        val_plan = build_validation_plan(repo_root, profile)
        validation_commands = _flatten_validation_commands(val_plan)
    except Exception:
        validation_commands = []

    if forbidden_actions is None:
        forbidden_actions = list(_DEFAULT_FORBIDDEN_ACTIONS)

    return {
        "schema_version": "signalos.agent_packet.v1",
        "run_id": run_id,
        "created_at": now,
        "intent_summary": intent_summary,
        "blueprint_id": blueprint_id,
        "profile": profile,
        "wave": wave,
        "tasks": tasks,
        "acceptance_criteria": acceptance_criteria,
        "allowed_paths": allowed_paths,
        "forbidden_paths": list(_DEFAULT_FORBIDDEN_PATHS),
        "forbidden_actions": forbidden_actions,
        "validation_commands": validation_commands,
        "result_schema": _RESULT_SCHEMA,
    }


def _flatten_validation_commands(plan: dict) -> list[str]:
    """Flatten a validation plan dict into a single list of commands."""
    result: list[str] = []
    for key in ("install", "build", "test", "lint", "qa",
                "e2e", "runtime_smoke", "ux_smoke", "security"):
        cmds = plan.get(key, [])
        if isinstance(cmds, list):
            result.extend(cmds)
    return result


# ---------------------------------------------------------------------------
# write_agent_packet
# ---------------------------------------------------------------------------

def write_agent_packet(
    packet: dict,
    repo_root: Path,
) -> Path:
    """Write packet to ``.signalos/product/agent-runs/<run_id>/``.

    Creates:
    - ``PACKET.md``            -- human-readable summary
    - ``scope.json``           -- full packet data
    - ``files-allowed.txt``    -- one glob per line
    - ``commands-allowed.txt`` -- validation commands
    - ``validation-plan.json`` -- validation plan for the agent to run
    - ``result.schema.json``   -- expected result shape

    Returns the run directory path.
    """
    run_id = packet["run_id"]
    run_dir = repo_root / ".signalos" / "product" / "agent-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # scope.json -- full packet
    (run_dir / "scope.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # files-allowed.txt
    (run_dir / "files-allowed.txt").write_text(
        "\n".join(packet.get("allowed_paths", [])) + "\n",
        encoding="utf-8",
    )

    # commands-allowed.txt
    (run_dir / "commands-allowed.txt").write_text(
        "\n".join(packet.get("validation_commands", [])) + "\n",
        encoding="utf-8",
    )

    # validation-plan.json
    val_plan = {
        "commands": packet.get("validation_commands", []),
        "forbidden_actions": packet.get("forbidden_actions", []),
    }
    (run_dir / "validation-plan.json").write_text(
        json.dumps(val_plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # result.schema.json
    (run_dir / "result.schema.json").write_text(
        json.dumps(packet.get("result_schema", {}), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # PACKET.md -- human-readable summary
    md = _render_packet_md(packet)
    (run_dir / "PACKET.md").write_text(md, encoding="utf-8")

    return run_dir


def _render_packet_md(packet: dict) -> str:
    """Render a human-readable Markdown summary of the packet."""
    lines: list[str] = []
    lines.append(f"# Agent Packet {packet['run_id']}")
    lines.append("")
    lines.append(f"**Created:** {packet.get('created_at', '')}")
    lines.append(f"**Profile:** {packet.get('profile', '')}")
    lines.append(f"**Wave:** {packet.get('wave', '')}")

    intent = packet.get("intent_summary", {})
    lines.append("")
    lines.append("## Intent")
    lines.append("")
    lines.append(f"- **Product:** {intent.get('product_name', '(unnamed)')}")
    lines.append(f"- **Type:** {intent.get('product_type', '')}")
    entities = intent.get("entities", [])
    if entities:
        lines.append(f"- **Entities:** {', '.join(entities)}")
    workflows = intent.get("primary_workflows", [])
    if workflows:
        lines.append(f"- **Workflows:** {', '.join(workflows)}")

    tasks = packet.get("tasks", [])
    if tasks:
        lines.append("")
        lines.append("## Tasks")
        lines.append("")
        for t in tasks:
            title = t.get("title") or t.get("id") or t.get("task", "untitled")
            desc = t.get("description", "")
            lines.append(f"- **{title}**")
            if desc:
                lines.append(f"  {desc}")

    allowed = packet.get("allowed_paths", [])
    if allowed:
        lines.append("")
        lines.append("## Allowed Paths")
        lines.append("")
        for p in allowed:
            lines.append(f"- `{p}`")

    forbidden = packet.get("forbidden_paths", [])
    if forbidden:
        lines.append("")
        lines.append("## Forbidden Paths")
        lines.append("")
        for p in forbidden:
            lines.append(f"- `{p}`")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# validate_agent_result
# ---------------------------------------------------------------------------

def validate_agent_result(
    run_dir: Path,
    repo_root: Path,
    generation_manifest: dict | None,
) -> dict:
    """Validate an agent's ``RESULT.json`` against the packet scope.

    Checks:
    1. ``RESULT.json`` exists and is valid JSON
    2. ``files_written`` are within ``allowed_paths``
    3. No writes to ``forbidden_paths``
    4. No forbidden actions reported
    5. Validation commands pass (optional -- run if requested)

    Returns a validation result dict with ``valid``, ``checks``, and
    ``violations`` keys.
    """
    checks: list[dict[str, Any]] = []
    violations: list[str] = []

    # Load scope
    scope_path = run_dir / "scope.json"
    if not scope_path.is_file():
        return {
            "valid": False,
            "checks": [{"name": "scope_exists", "passed": False,
                         "detail": "scope.json missing from run dir"}],
            "violations": ["scope.json missing"],
        }

    try:
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "valid": False,
            "checks": [{"name": "scope_readable", "passed": False,
                         "detail": f"scope.json unreadable: {exc}"}],
            "violations": [f"scope.json unreadable: {exc}"],
        }

    # 1. RESULT.json exists and is valid JSON
    result_path = run_dir / "RESULT.json"
    if not result_path.is_file():
        checks.append({"name": "result_exists", "passed": False,
                        "detail": "RESULT.json not found"})
        violations.append("RESULT.json missing")
        return {"valid": False, "checks": checks, "violations": violations}

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        checks.append({"name": "result_valid_json", "passed": False,
                        "detail": f"RESULT.json invalid: {exc}"})
        violations.append(f"RESULT.json invalid JSON: {exc}")
        return {"valid": False, "checks": checks, "violations": violations}

    if not isinstance(result, dict):
        checks.append({"name": "result_valid_json", "passed": False,
                        "detail": "RESULT.json is not an object"})
        violations.append("RESULT.json is not a JSON object")
        return {"valid": False, "checks": checks, "violations": violations}

    checks.append({"name": "result_valid_json", "passed": True,
                    "detail": "RESULT.json is valid JSON"})

    # 2. files_written within allowed_paths
    files_written = result.get("files_written", [])
    allowed_paths = scope.get("allowed_paths", [])
    forbidden_paths = scope.get("forbidden_paths", [])

    all_files_allowed = True
    for f in files_written:
        if not _path_matches_any(f, allowed_paths):
            all_files_allowed = False
            violations.append(f"File '{f}' not within allowed paths")

    checks.append({
        "name": "files_within_allowed",
        "passed": all_files_allowed,
        "detail": (
            "All files within allowed paths"
            if all_files_allowed
            else f"{len(violations)} file(s) outside allowed paths"
        ),
    })

    # 3. No writes to forbidden_paths
    no_forbidden_writes = True
    for f in files_written:
        if _path_matches_any_forbidden(f, forbidden_paths):
            no_forbidden_writes = False
            violations.append(f"File '{f}' is in forbidden paths")

    checks.append({
        "name": "no_forbidden_writes",
        "passed": no_forbidden_writes,
        "detail": (
            "No writes to forbidden paths"
            if no_forbidden_writes
            else "Writes to forbidden paths detected"
        ),
    })

    # 4. No forbidden actions reported
    actions_taken = result.get("actions_taken", [])
    forbidden_actions = scope.get("forbidden_actions", [])
    no_forbidden_actions = True
    for action in actions_taken:
        for forbidden in forbidden_actions:
            if forbidden.lower() in action.lower():
                no_forbidden_actions = False
                violations.append(
                    f"Forbidden action detected: '{action}' matches '{forbidden}'"
                )

    checks.append({
        "name": "no_forbidden_actions",
        "passed": no_forbidden_actions,
        "detail": (
            "No forbidden actions"
            if no_forbidden_actions
            else "Forbidden actions detected"
        ),
    })

    valid = len(violations) == 0
    return {"valid": valid, "checks": checks, "violations": violations}


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    """Return True if *path* matches any of the glob *patterns*."""
    normed = path.replace("\\", "/")
    for pattern in patterns:
        pat = pattern.replace("\\", "/")
        if fnmatch.fnmatch(normed, pat):
            return True
        # Also check if the path starts with a directory pattern
        # (e.g. pattern "src/*" should match "src/foo/bar.ts")
        if pat.endswith("/*") or pat.endswith("/**"):
            prefix = pat.rsplit("/", 1)[0]
            if normed.startswith(prefix + "/"):
                return True
    return False


def _path_matches_any_forbidden(path: str, forbidden: list[str]) -> bool:
    """Return True if *path* matches any forbidden path pattern."""
    normed = path.replace("\\", "/")
    for pat in forbidden:
        p = pat.replace("\\", "/").rstrip("/")
        # Directory prefix: ".signalos/" means anything under .signalos
        if pat.endswith("/"):
            if normed.startswith(p + "/") or normed == p:
                return True
        # Glob patterns like "*.pem"
        elif "*" in pat:
            if fnmatch.fnmatch(normed, p):
                return True
            # Also check just the filename
            fname = normed.rsplit("/", 1)[-1] if "/" in normed else normed
            if fnmatch.fnmatch(fname, p):
                return True
        # Exact match (e.g. ".env")
        else:
            if normed == p or normed.endswith("/" + p):
                return True
    return False
