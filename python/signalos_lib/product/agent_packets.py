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
    "build_applicable_skills",
    "build_agent_packet",
    "build_skills_catalog",
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

_DEFAULT_SUCCESS_CRITERIA: list[str] = [
    "All assigned tasks are completed inside the approved scope.",
    "Every generated or modified file is non-empty, syntactically valid, and "
    "listed in the packet file specs or allowed paths.",
    "Relevant tests are written before implementation for new behavior, or "
    "the packet records the exact blocker that prevented TDD.",
    "Validation commands pass, or the result records an exact tooling, "
    "environment, or human-approval blocker.",
    "No forbidden path, forbidden action, gate bypass, secret write, or "
    "fabricated evidence occurs.",
]

_DEFAULT_EVIDENCE_REQUIRED: list[str] = [
    "RESULT.json records status, files_written, actions_taken, and validation_results.",
    "Each changed file traces to an assigned task or acceptance criterion.",
    "Validation output, skipped-command reason, or blocker evidence is recorded.",
    "Known limitations are explicit instead of being hidden behind a success status.",
]

_DEFAULT_FORBIDDEN_RULES: list[str] = [
    "Never write outside allowed_paths.",
    "Never write to forbidden_paths, including .signalos/, .git/, env files, "
    "private keys, or generated secrets.",
    "Never edit gate signatures, governance records, or audit evidence directly.",
    "Never remove, weaken, or fabricate tests or validation evidence to make "
    "the result pass.",
    "Never perform live deploy, package publish, git push, destructive deletion, "
    "or network release actions unless the packet explicitly authorizes them.",
]

_DEFAULT_REPAIR_POLICY: dict[str, Any] = {
    "quality_failure": (
        "Create a repair packet and rework within the same approved scope."
    ),
    "forbidden_violation": (
        "Reject the output, do not accept or merge it, and regenerate from a "
        "clean packet with the violation recorded."
    ),
    "human_authority_required": (
        "Pause autonomous action and request approval; do not guess or forge "
        "authorization."
    ),
    "missing_tooling_or_environment": (
        "Record the exact blocker and keep delivery open instead of claiming "
        "success."
    ),
}

_DEFAULT_ESCALATION_POLICY: list[str] = [
    "Escalate when requirements, authority, safety, secrets, live deployment, "
    "or signed artifacts are ambiguous.",
    "Escalate when the requested output cannot be produced without violating "
    "a forbidden rule.",
    "Escalate when max repair cycles are reached with validation still failing.",
]

_DEFAULT_SOURCE_POLICY: dict[str, str] = {
    "repo_facts": "Use repository files and packet artifacts as the source of truth.",
    "current_external_claims": (
        "Cite dated sources or record that external verification is required; "
        "do not invent current market, legal, finance, pricing, or security facts."
    ),
    "inference": "Label inferred conclusions separately from observed facts.",
}

_DEFAULT_TEAM_CONTRACT: dict[str, Any] = {
    "agents_are_signalos_team": True,
    "user_manages_agents": False,
    "signalos_orchestrates_team": True,
    "user_visible_name": "SignalOS team",
    "scope_authority": (
        "SignalOS owns the approved packet, allowed paths, forbidden rules, "
        "validation commands, repair policy, and release evidence."
    ),
    "team_authority": (
        "The assigned team member may produce product code only inside the "
        "packet scope and must report blockers instead of inventing authority."
    ),
}

_DEFAULT_AGENT_ROLE = "SignalOS Build agent"

_DEFAULT_EXPERTISE_FRAME = (
    "Act as the highest-level software engineer ever for the selected stack "
    "and product domain. "
    "SignalOS owns scope, governance, and validation; you own implementation "
    "quality inside the allowed files. Apply domain judgment for real user "
    "workflows, architecture, maintainability, security, accessibility, and "
    "tests. Stop and report a blocker instead of guessing when requirements, "
    "safety, or architecture are ambiguous."
)

_SKILL_CONTENT_BUDGET_CHARS = 6_000

_DEFAULT_PRODUCT_AGENT_SKILLS = [
    "test-driven-development",
    "test-generation",
    "verification-before-completion",
]

_REQUIRED_PACKET_CONTRACT_FIELDS = (
    "quality_bar",
    "success_criteria",
    "evidence_required",
    "forbidden_rules",
    "repair_policy",
    "escalation_policy",
    "source_policy",
    "team_contract",
)

_UI_HINTS = {
    "ui", "ux", "screen", "view", "page", "dashboard", "chart", "form",
    "frontend", "component", "tsx", "html", "css", "accessibility",
}

_SECURITY_HINTS = {
    "auth", "login", "permission", "role", "security", "secret", "token",
    "password", "audit", "tenant", "xss", "csrf", "injection",
}


# ---------------------------------------------------------------------------
# Result schema the agent is expected to write as RESULT.json
# ---------------------------------------------------------------------------

_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "run_id",
        "status",
        "files_written",
        "actions_taken",
        "validation_results",
    ],
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
    generation_packet: dict | None = None,
    ownership_map: dict | None = None,
) -> dict:
    """Build a scoped agent execution packet.

    The packet contains everything an external agent needs to execute a
    bounded set of tasks: intent context, acceptance criteria, allowed
    and forbidden file paths, validation commands, and the expected
    result schema.

    When *generation_packet* is provided, it is included under the
    ``generation`` key -- this gives the agent the full file specs,
    design constraints, and entity definitions needed to build the
    product.

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

    skills_catalog = build_skills_catalog()
    applicable_skills = build_applicable_skills(
        repo_root=repo_root,
        intent=intent,
        tasks=tasks,
        generation_packet=generation_packet,
    )

    packet: dict[str, Any] = {
        "schema_version": "signalos.agent_packet.v1",
        "run_id": run_id,
        "created_at": now,
        "agent_role": _DEFAULT_AGENT_ROLE,
        "expertise_frame": _DEFAULT_EXPERTISE_FRAME,
        "quality_bar": _build_quality_bar(profile),
        "success_criteria": _build_success_criteria(tasks, acceptance_criteria),
        "evidence_required": list(_DEFAULT_EVIDENCE_REQUIRED),
        "forbidden_rules": list(_DEFAULT_FORBIDDEN_RULES),
        "repair_policy": dict(_DEFAULT_REPAIR_POLICY),
        "escalation_policy": list(_DEFAULT_ESCALATION_POLICY),
        "source_policy": dict(_DEFAULT_SOURCE_POLICY),
        "team_contract": dict(_DEFAULT_TEAM_CONTRACT),
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
        "skills_catalog": skills_catalog,
        "applicable_skills": applicable_skills,
        "result_schema": _RESULT_SCHEMA,
    }

    # Include generation packet data when available
    if generation_packet:
        packet["generation"] = generation_packet
    if ownership_map:
        packet["delivery_ownership"] = ownership_map

    return packet


def _build_quality_bar(profile: str) -> dict[str, Any]:
    return {
        "standard": (
            "Production-quality implementation inside the packet scope, with "
            "governance, tests, evidence, and handoff honesty preserved."
        ),
        "profile": profile,
        "completion_rule": (
            "Done means success criteria passed and no forbidden violation is "
            "present. Partial output must remain partial."
        ),
    }


def _build_success_criteria(
    tasks: list[dict],
    acceptance_criteria: list[dict],
) -> list[str]:
    criteria = list(_DEFAULT_SUCCESS_CRITERIA)

    for task in tasks:
        label = task.get("id") or task.get("title") or task.get("task")
        title = task.get("title") or task.get("description") or ""
        if label:
            criteria.append(f"Task {label}: {title or 'complete assigned work'}")

    for ac in acceptance_criteria:
        ac_id = ac.get("id")
        desc = ac.get("description") or ac.get("title") or ""
        if ac_id:
            criteria.append(f"Acceptance {ac_id}: {desc or 'satisfied'}")

    return criteria


def build_skills_catalog() -> list[dict[str, str]]:
    """Return the routable SignalOS skill catalog shared with orchestrator."""
    from signalos_lib.orchestrator import _SKILL_KEY_TO_PATH

    return [
        {"key": key, "name": label, "path": path}
        for key, (label, path) in _SKILL_KEY_TO_PATH.items()
    ]


def build_applicable_skills(
    repo_root: Path,
    intent: dict,
    tasks: list[dict],
    generation_packet: dict | None,
) -> list[dict[str, str]]:
    """Select and load the skills a product build agent should read first.

    The full catalog is always present in ``skills_catalog``. This function
    loads the smaller working set directly into the packet so packet-only and
    live LLM dispatch modes do not depend on the agent discovering files.
    """
    catalog_by_key = {entry["key"]: entry for entry in build_skills_catalog()}
    keys = _select_applicable_skill_keys(intent, tasks, generation_packet)

    out: list[dict[str, str]] = []
    for key in keys:
        entry = catalog_by_key.get(key)
        if entry is None:
            continue
        content = _load_skill_content(repo_root, entry["path"])
        if not content:
            continue
        out.append({
            "key": key,
            "name": entry["name"],
            "path": entry["path"],
            "content": _trim_skill_content(content),
        })
    return out


def _select_applicable_skill_keys(
    intent: dict,
    tasks: list[dict],
    generation_packet: dict | None,
) -> list[str]:
    keys: list[str] = list(_DEFAULT_PRODUCT_AGENT_SKILLS)

    for task in tasks:
        for raw in task.get("skills") or []:
            if isinstance(raw, str):
                keys.append(raw.strip().lower())

    haystack = _skill_hint_text(intent, tasks, generation_packet)
    if any(hint in haystack for hint in _UI_HINTS):
        keys.extend(["design", "e2e-testing"])
    if any(hint in haystack for hint in _SECURITY_HINTS):
        keys.append("security-audit")
    if any(hint in haystack for hint in ("debug", "bug", "failure", "regression")):
        keys.append("systematic-debugging")
    if any(hint in haystack for hint in ("existing repo", "brownfield", "legacy", "adopt")):
        keys.extend(["existing-product-kit", "product-surface-mapping"])

    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _skill_hint_text(
    intent: dict,
    tasks: list[dict],
    generation_packet: dict | None,
) -> str:
    parts: list[str] = [json.dumps(intent, ensure_ascii=False, sort_keys=True)]
    parts.append(json.dumps(tasks, ensure_ascii=False, sort_keys=True))
    if generation_packet:
        parts.append(json.dumps(generation_packet, ensure_ascii=False, sort_keys=True))
    return " ".join(parts).lower()


def _load_skill_content(repo_root: Path, relative_path: str) -> str:
    """Load skill content from the product repo, falling back to app bundle."""
    candidates = [
        repo_root / relative_path,
        Path(__file__).resolve().parents[1] / "_bundle" / relative_path,
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _trim_skill_content(text: str) -> str:
    if len(text) <= _SKILL_CONTENT_BUDGET_CHARS:
        return text
    return (
        text[:_SKILL_CONTENT_BUDGET_CHARS]
        + "\n\n[...skill trimmed for product agent packet budget...]\n"
    )


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
    - ``skills-catalog.json``  -- every routable SignalOS skill
    - ``applicable-skills.md`` -- loaded skill docs for this run
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

    # skills-catalog.json -- all routable skills exposed to agents
    (run_dir / "skills-catalog.json").write_text(
        json.dumps(packet.get("skills_catalog", []), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # applicable-skills.md -- loaded skill docs selected for this run
    (run_dir / "applicable-skills.md").write_text(
        _render_applicable_skills_md(packet),
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
        "success_criteria": packet.get("success_criteria", []),
        "evidence_required": packet.get("evidence_required", []),
        "forbidden_rules": packet.get("forbidden_rules", []),
        "repair_policy": packet.get("repair_policy", {}),
        "escalation_policy": packet.get("escalation_policy", []),
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

    quality_bar = packet.get("quality_bar", {})
    if quality_bar:
        lines.append("")
        lines.append("## Quality Bar")
        lines.append("")
        if quality_bar.get("standard"):
            lines.append(quality_bar["standard"])
        if quality_bar.get("completion_rule"):
            lines.append("")
            lines.append(f"Completion rule: {quality_bar['completion_rule']}")

    lines.append("")
    lines.append("## Agent Role")
    lines.append("")
    lines.append(f"**Role:** {packet.get('agent_role', _DEFAULT_AGENT_ROLE)}")
    lines.append(
        f"**Expertise frame:** "
        f"{packet.get('expertise_frame', _DEFAULT_EXPERTISE_FRAME)}"
    )

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

    for heading, key in (
        ("Success Criteria", "success_criteria"),
        ("Evidence Required", "evidence_required"),
        ("Forbidden Rules", "forbidden_rules"),
        ("Escalation Policy", "escalation_policy"),
    ):
        values = packet.get(key, [])
        if values:
            lines.append("")
            lines.append(f"## {heading}")
            lines.append("")
            for item in values:
                lines.append(f"- {item}")

    repair_policy = packet.get("repair_policy", {})
    if repair_policy:
        lines.append("")
        lines.append("## Repair/Rework Policy")
        lines.append("")
        for key, value in repair_policy.items():
            lines.append(f"- **{key}:** {value}")

    source_policy = packet.get("source_policy", {})
    if source_policy:
        lines.append("")
        lines.append("## Source Policy")
        lines.append("")
        for key, value in source_policy.items():
            lines.append(f"- **{key}:** {value}")

    team_contract = packet.get("team_contract", {})
    if team_contract:
        lines.append("")
        lines.append("## SignalOS Team Contract")
        lines.append("")
        lines.append(
            "Agents are SignalOS team members managed by SignalOS, not a "
            "separate user-managed system."
        )
        lines.append("")
        for key, value in team_contract.items():
            lines.append(f"- **{key}:** {value}")

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

    catalog = packet.get("skills_catalog", [])
    if catalog:
        lines.append("")
        lines.append("## SignalOS Skills Catalog")
        lines.append("")
        lines.append(
            "All listed skills are available in this product repo. Use the "
            "applicable skills below first; consult other catalog entries "
            "when the task needs that domain guidance."
        )
        lines.append("")
        for skill in catalog:
            lines.append(
                f"- `{skill.get('key', '')}` -- {skill.get('name', '')} "
                f"({skill.get('path', '')})"
            )

    applicable_md = _render_applicable_skills_md(packet)
    if applicable_md.strip():
        lines.append("")
        lines.append(applicable_md.rstrip())

    lines.append("")
    return "\n".join(lines)


def _render_applicable_skills_md(packet: dict) -> str:
    skills = packet.get("applicable_skills", [])
    if not skills:
        return ""

    lines: list[str] = [
        "## Applicable SignalOS Skills",
        "",
        "Read these before producing files. They are selected from the full "
        "catalog for this product agent run.",
        "",
    ]
    for skill in skills:
        lines.append(
            f"### {skill.get('name', skill.get('key', 'unknown'))} "
            f"(`{skill.get('key', '')}`)"
        )
        path = skill.get("path", "")
        if path:
            lines.append("")
            lines.append(f"Source: `{path}`")
        content = str(skill.get("content", "")).strip()
        if content:
            lines.append("")
            lines.append(content)
        lines.append("")
        lines.append("---")
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

    contract_violations = _packet_contract_violations(scope)
    if contract_violations:
        checks.extend(
            {
                "name": "packet_contract",
                "passed": False,
                "detail": violation,
            }
            for violation in contract_violations
        )
        return _validation_result(checks, contract_violations)

    # 1. RESULT.json exists and is valid JSON
    result_path = run_dir / "RESULT.json"
    if not result_path.is_file():
        checks.append({"name": "result_exists", "passed": False,
                        "detail": "RESULT.json not found"})
        violations.append("RESULT.json missing")
        return _validation_result(checks, violations)

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        checks.append({"name": "result_valid_json", "passed": False,
                        "detail": f"RESULT.json invalid: {exc}"})
        violations.append(f"RESULT.json invalid JSON: {exc}")
        return _validation_result(checks, violations)

    if not isinstance(result, dict):
        checks.append({"name": "result_valid_json", "passed": False,
                        "detail": "RESULT.json is not an object"})
        violations.append("RESULT.json is not a JSON object")
        return _validation_result(checks, violations)

    checks.append({"name": "result_valid_json", "passed": True,
                    "detail": "RESULT.json is valid JSON"})

    required_result_fields = scope.get("result_schema", {}).get("required", [])
    missing_result_fields = [
        field for field in required_result_fields
        if field not in result
    ]
    if missing_result_fields:
        checks.append({
            "name": "result_required_fields",
            "passed": False,
            "detail": (
                "RESULT.json missing required fields: "
                + ", ".join(missing_result_fields)
            ),
        })
        violations.append(
            "RESULT.json missing required fields: "
            + ", ".join(missing_result_fields)
        )
        return _validation_result(checks, violations)
    checks.append({
        "name": "result_required_fields",
        "passed": True,
        "detail": "RESULT.json contains required fields",
    })

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

    return _validation_result(checks, violations)


def _packet_contract_violations(scope: dict) -> list[str]:
    violations: list[str] = []
    for field in _REQUIRED_PACKET_CONTRACT_FIELDS:
        value = scope.get(field)
        if value is None or value == [] or value == {} or value == "":
            violations.append(f"scope.json missing required packet contract field: {field}")
    return violations


def _validation_result(
    checks: list[dict[str, Any]],
    violations: list[str],
) -> dict[str, Any]:
    forbidden_violated = _has_forbidden_violation(violations)
    valid = len(violations) == 0
    return {
        "valid": valid,
        "checks": checks,
        "violations": violations,
        "forbidden_violated": forbidden_violated,
        "acceptance": "accepted" if valid else "rejected",
        "next_action": (
            "none"
            if valid
            else (
                "regenerate_from_clean_packet"
                if forbidden_violated
                else "repair_packet"
            )
        ),
    }


def _has_forbidden_violation(violations: list[str]) -> bool:
    markers = (
        "forbidden path",
        "forbidden action",
        "in forbidden paths",
        "not within allowed paths",
        "outside allowed paths",
        ".signalos",
        ".git",
        ".env",
        ".pem",
        ".key",
    )
    for violation in violations:
        low = violation.lower()
        if any(marker in low for marker in markers):
            return True
    return False


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
