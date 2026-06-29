"""Governed product-blueprint authoring workflows.

The app keeps blueprint storage JSON-native, but the behavior mirrors the
SignalOS product-factory concept: custom blueprints are drafted under
`.signalos/product/blueprint-drafts`, reviewed with bounded verdict evidence,
and registered into an adopter-owned overlay registry only after validation.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .registry import (
    load_blueprint,
    load_combined_registry,
    validate_blueprint,
    validate_blueprint_registry,
)

SCHEMA_VERSION = "signalos.blueprint_factory.v1"
DRAFT_COMPONENTS = ("blueprint.json", "api.json", "ui.json", "tests.json", "seed.json", "acceptance.json")
DRAFT_GOVERNANCE_ARTIFACTS = (
    "DOMAIN_LANGUAGE.md",
    "PROVIDER_REQUEST.json",
    "TOOL_SELECTION.json",
    "SKILL_ROUTING.yaml",
    "PRODUCT_PIPELINE.yaml",
    "SUBAGENT_HARNESS.json",
    "GOVERNANCE_GATES.json",
)
_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class BlueprintWorkflowError(ValueError):
    """Raised when a governed blueprint workflow cannot proceed."""


def draft_blueprint(
    repo_root: Path | str,
    blueprint_id: str,
    *,
    from_intent: Path | str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create a custom-blueprint draft and host-agent handoff files."""
    root = Path(repo_root).resolve()
    bp_id = normalize_blueprint_id(blueprint_id)
    _ensure_new_id(root, bp_id)
    draft_dir = _draft_dir(root, bp_id)
    if draft_dir.exists() and not force:
        raise BlueprintWorkflowError(f"blueprint draft already exists: {_display(draft_dir, root)}")
    if draft_dir.exists():
        shutil.rmtree(draft_dir)
    draft_dir.mkdir(parents=True, exist_ok=True)

    intent = _load_intent(from_intent) if from_intent else {}
    files = _draft_files(bp_id, intent)
    written: list[str] = []
    for name, payload in files.items():
        path = draft_dir / name
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(_display(path, root))

    governance_artifacts = _draft_governance_files(bp_id, intent)
    for name, content in governance_artifacts.items():
        path = draft_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(_display(path, root))

    questions = _render_questions(bp_id, intent)
    agent_task = _render_agent_task(bp_id, from_intent)
    plan = {
        "schema_version": "signalos.blueprint_draft_plan.v1",
        "blueprint_id": bp_id,
        "required_files": list(DRAFT_COMPONENTS),
        "status": "awaiting-review",
        "required_governance_artifacts": list(DRAFT_GOVERNANCE_ARTIFACTS),
        "gate_policy": "G1 authoring plan, G2 review, G3 validation, G4 registration, and G5 adoption proof are tracked before a custom blueprint can claim ready lifecycle status.",
        "created_at": _now(),
    }
    for name, text in {
        "QUESTIONS.md": questions,
        "AGENT_TASK.md": agent_task,
        "PLAN.json": json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
    }.items():
        path = draft_dir / name
        path.write_text(text, encoding="utf-8")
        written.append(_display(path, root))

    payload = {
        "schema_version": SCHEMA_VERSION,
        "action": "draft",
        "status": "drafted",
        "blueprint_id": bp_id,
        "draft_dir": _display(draft_dir, root),
        "files": written,
        "governance_artifacts": [
            _display(draft_dir / name, root)
            for name in DRAFT_GOVERNANCE_ARTIFACTS
        ],
        "required_gates": ["G1-authoring", "G2-review", "G3-validation", "G4-registration", "G5-adoption-proof"],
        "next": f"signalos product blueprint review --id {bp_id} --verdict approve",
        "created_at": _now(),
    }
    _write_state(root, "blueprint-draft", bp_id, payload)
    _append_audit(root, "product.blueprint.drafted", bp_id, payload)
    return payload


def review_blueprint(
    repo_root: Path | str,
    blueprint_id: str,
    *,
    verdict: str,
    notes: str = "",
) -> dict[str, Any]:
    """Record a bounded review verdict for a draft or registered blueprint."""
    root = Path(repo_root).resolve()
    bp_id = normalize_blueprint_id(blueprint_id)
    normalized_verdict = verdict.strip().lower()
    if normalized_verdict not in {"approve", "request-changes", "reject"}:
        raise BlueprintWorkflowError("verdict must be approve, request-changes, or reject")

    draft_dir = _draft_dir(root, bp_id)
    target_dir = draft_dir if draft_dir.is_dir() else _custom_dir(root, bp_id)
    if not target_dir.is_dir():
        raise BlueprintWorkflowError(f"blueprint draft or custom blueprint not found: {bp_id}")

    validation = _validate_blueprint_dir(root, target_dir, bp_id)
    approved = normalized_verdict == "approve" and validation["ok"]
    status = "approved" if approved else ("rework-required" if normalized_verdict == "request-changes" else "rejected")
    review_path = target_dir / "REVIEW.json"
    previous = _load_json(review_path) if review_path.is_file() else {}
    cycle = int(previous.get("cycle", 0)) + 1 if isinstance(previous, dict) else 1
    payload = {
        "schema_version": "signalos.blueprint_review.v1",
        "blueprint_id": bp_id,
        "verdict": normalized_verdict,
        "status": status,
        "cycle": cycle,
        "notes": notes,
        "validation": validation,
        "reviewed_at": _now(),
    }
    review_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if status == "rework-required":
        packet = target_dir / f"REWORK_{cycle}.md"
        packet.write_text(_render_rework_packet(bp_id, validation, notes), encoding="utf-8")
        legacy_packet = target_dir / "REWORK_PACKET.md"
        legacy_packet.write_text(_render_rework_packet(bp_id, validation, notes), encoding="utf-8")
    _update_gate_state(target_dir, root, "G2-review", status == "approved", {
        "verdict": normalized_verdict,
        "review_path": _display(review_path, root),
    })
    _update_gate_state(target_dir, root, "G3-validation", validation["ok"], {
        "errors": validation.get("errors", []),
    })
    _write_state(root, "blueprint-review", bp_id, payload)
    _append_audit(root, f"product.blueprint.review.{status}", bp_id, payload)
    return {
        **payload,
        "review_path": _display(review_path, root),
        "ok": approved,
    }


def register_blueprint(
    repo_root: Path | str,
    blueprint_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Register an approved custom-blueprint draft into the overlay registry."""
    root = Path(repo_root).resolve()
    bp_id = normalize_blueprint_id(blueprint_id)
    draft_dir = _draft_dir(root, bp_id)
    if not draft_dir.is_dir():
        raise BlueprintWorkflowError(f"blueprint draft not found: {_display(draft_dir, root)}")
    review = _load_json(draft_dir / "REVIEW.json") if (draft_dir / "REVIEW.json").is_file() else {}
    if review.get("status") != "approved":
        raise BlueprintWorkflowError("blueprint draft must have an approved REVIEW.json before registration")

    validation = _validate_blueprint_dir(root, draft_dir, bp_id)
    if not validation["ok"]:
        raise BlueprintWorkflowError("blueprint draft failed validation: " + "; ".join(validation["errors"]))

    custom_dir = _custom_dir(root, bp_id)
    if custom_dir.exists() and not force:
        raise BlueprintWorkflowError(f"custom blueprint already exists: {_display(custom_dir, root)}")
    if custom_dir.exists():
        shutil.rmtree(custom_dir)
    custom_dir.mkdir(parents=True, exist_ok=True)
    for name in DRAFT_COMPONENTS:
        shutil.copy2(draft_dir / name, custom_dir / name)
    for name in DRAFT_GOVERNANCE_ARTIFACTS:
        shutil.copy2(draft_dir / name, custom_dir / name)
    shutil.copy2(draft_dir / "REVIEW.json", custom_dir / "REVIEW.json")

    registry_path = _custom_registry_path(root)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry = _load_json(registry_path) if registry_path.is_file() else {
        "schema_version": 1,
        "origin": "custom",
        "blueprints": [],
    }
    entries = [entry for entry in registry.get("blueprints", []) if entry.get("id") != bp_id]
    entries.append({
        "id": bp_id,
        "path": f".signalos/product/blueprints/custom/{bp_id}/blueprint.json",
        "origin": "custom",
    })
    registry["blueprints"] = sorted(entries, key=lambda item: item["id"])
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    report = validate_blueprint_registry(root, bp_id)
    payload = {
        "schema_version": "signalos.blueprint_registration.v1",
        "status": "registered" if report["ok"] else "invalid",
        "ok": report["ok"],
        "blueprint_id": bp_id,
        "custom_dir": _display(custom_dir, root),
        "registry_path": _display(registry_path, root),
        "validation": report,
        "governance_artifacts": [
            _display(custom_dir / name, root)
            for name in DRAFT_GOVERNANCE_ARTIFACTS
        ],
        "registered_at": _now(),
    }
    _update_gate_state(custom_dir, root, "G4-registration", report["ok"], {
        "registry_path": _display(registry_path, root),
    })
    _update_gate_state(custom_dir, root, "G5-adoption-proof", report["ok"], {
        "validation_status": report.get("status"),
    })
    _write_state(root, "blueprint-register", bp_id, payload)
    _append_audit(root, "product.blueprint.registered", bp_id, payload)
    return payload


def inspect_blueprint(repo_root: Path | str | None, blueprint_id: str) -> dict[str, Any]:
    root = Path(repo_root).resolve() if repo_root is not None else None
    bp = load_blueprint(blueprint_id, root)
    if bp is None:
        raise BlueprintWorkflowError(f"unknown blueprint id: {blueprint_id}")
    return {
        "schema_version": "signalos.blueprint_inspect.v1",
        "id": bp.get("id"),
        "display_name": bp.get("display_name"),
        "origin": bp.get("origin", "builtin"),
        "profile_support": bp.get("profile_support", []),
        "entities": bp.get("entities", []),
        "workflows": bp.get("workflows", []),
        "component_keys": sorted(key for key in bp if key.endswith("_detail")),
        "validation_errors": validate_blueprint(bp),
    }


def normalize_blueprint_id(value: str) -> str:
    bp_id = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(bp_id):
        raise BlueprintWorkflowError("blueprint id must be lowercase kebab-case")
    return bp_id


def _draft_files(bp_id: str, intent: dict[str, Any]) -> dict[str, dict[str, Any]]:
    product_type = str(intent.get("product_type") or bp_id)
    display = str(intent.get("product_name") or _display_name(bp_id))
    entity_names = _entity_names(intent) or [_display_name(bp_id).replace(" ", "")]
    workflow_names = [str(item) for item in intent.get("primary_workflows", []) if str(item).strip()]
    if not workflow_names:
        workflow_names = [f"manage {display.lower()}"]
    api_surfaces = [str(item) for item in intent.get("api_surfaces", []) if str(item).strip()]
    if not api_surfaces:
        api_surfaces = [f"/api/{bp_id}"]
    ux_surfaces = [str(item) for item in intent.get("ux_surfaces", []) if str(item).strip()]
    if not ux_surfaces:
        ux_surfaces = [f"{display} workspace"]

    entities = [{"name": name, "fields": ["name", "status"]} for name in entity_names]
    workflows = [{"name": _slug(name), "description": name} for name in workflow_names]
    return {
        "blueprint.json": {
            "id": bp_id,
            "display_name": display,
            "intent_match": {
                "product_type": [product_type, bp_id],
                "entities": entity_names,
                "keywords": _keywords(display, product_type, entity_names, workflow_names),
            },
            "required_intent_fields": ["product_name", "entities", "primary_workflows"],
            "entities": entities,
            "workflows": workflows,
            "api": api_surfaces,
            "ui": ux_surfaces,
            "tests": ["unit", "acceptance"],
            "seed_data": [{"entity": entity_names[0], "records": 1}],
            "security": {"auth_required": True, "audit": ["create", "update"]},
            "quality_profile": "custom-product",
            "default_deferrals": [],
            "profile_support": ["react-vite", "node-api", "fastapi-api", "go-api", "generic"],
            "intent_defaults": {
                "product_type": product_type,
                "product_name": display,
                "entities": entity_names,
                "primary_workflows": workflow_names,
                "api_surfaces": api_surfaces,
                "ux_surfaces": ux_surfaces,
            },
        },
        "api.json": {"resources": [{"path": path, "entity": entity_names[0]} for path in api_surfaces]},
        "ui.json": {"surfaces": [{"name": surface, "entity": entity_names[0]} for surface in ux_surfaces]},
        "tests.json": {"suites": ["unit", "acceptance"], "critical_paths": workflow_names},
        "seed.json": {"records": [{"entity": entity_names[0], "name": f"Sample {entity_names[0]}"}]},
        "acceptance.json": {"criteria": [{"id": "A1", "description": workflow_names[0]}]},
    }


def _draft_governance_files(bp_id: str, intent: dict[str, Any]) -> dict[str, str]:
    display = str(intent.get("product_name") or _display_name(bp_id))
    product_type = str(intent.get("product_type") or bp_id)
    entities = _entity_names(intent) or [_display_name(bp_id).replace(" ", "")]
    workflows = [str(item) for item in intent.get("primary_workflows", []) if str(item).strip()]
    if not workflows:
        workflows = [f"manage {display.lower()}"]
    gate_state = {
        "schema_version": "signalos.blueprint_lifecycle_gates.v1",
        "blueprint_id": bp_id,
        "gates": [
            {
                "id": "G1-authoring",
                "status": "pass",
                "evidence": ["PLAN.json", "DOMAIN_LANGUAGE.md", "PROVIDER_REQUEST.json"],
                "description": "Authoring plan and source domain language captured.",
            },
            {
                "id": "G2-review",
                "status": "pending",
                "evidence": ["REVIEW.json"],
                "description": "Product owner/reviewer verdict recorded.",
            },
            {
                "id": "G3-validation",
                "status": "pending",
                "evidence": list(DRAFT_COMPONENTS),
                "description": "Six-file blueprint contract validates.",
            },
            {
                "id": "G4-registration",
                "status": "pending",
                "evidence": ["registry.custom.json"],
                "description": "Adopter-owned overlay registry updated.",
            },
            {
                "id": "G5-adoption-proof",
                "status": "pending",
                "evidence": ["blueprint-register evidence"],
                "description": "Registered blueprint can be loaded and validated by the app.",
            },
        ],
    }
    return {
        "DOMAIN_LANGUAGE.md": (
            "# Domain Language\n\n"
            f"blueprint_id: {bp_id}\n"
            f"product: {display}\n"
            f"product_type: {product_type}\n\n"
            "## Entities\n"
            + "\n".join(f"- {entity}" for entity in entities)
            + "\n\n## Workflows\n"
            + "\n".join(f"- {workflow}" for workflow in workflows)
            + "\n"
        ),
        "PROVIDER_REQUEST.json": json.dumps({
            "schema_version": "signalos.blueprint_provider_request.v1",
            "blueprint_id": bp_id,
            "goal": "Create or refine a technology-neutral product blueprint.",
            "inputs": {
                "product_name": display,
                "product_type": product_type,
                "entities": entities,
                "workflows": workflows,
            },
            "required_outputs": list(DRAFT_COMPONENTS),
            "must_preserve": list(DRAFT_GOVERNANCE_ARTIFACTS),
        }, indent=2, ensure_ascii=False) + "\n",
        "TOOL_SELECTION.json": json.dumps({
            "schema_version": "signalos.blueprint_tool_selection.v1",
            "blueprint_id": bp_id,
            "allowed_tools": [
                "signalos product blueprint validate",
                "signalos product blueprint review",
                "signalos product blueprint register",
            ],
            "forbidden_tools": [
                "raw registry edits without validation",
                "technology-specific generator lock-in",
            ],
        }, indent=2, ensure_ascii=False) + "\n",
        "SKILL_ROUTING.yaml": (
            "schema_version: signalos.blueprint_skill_routing.v1\n"
            f"blueprint_id: {bp_id}\n"
            "routes:\n"
            "  - skill: product-surface-mapping\n"
            "    when: domain entities or workflows change\n"
            "  - skill: test-generation\n"
            "    when: acceptance or tests.json changes\n"
            "  - skill: verification-before-completion\n"
            "    when: asking for review or registration\n"
        ),
        "PRODUCT_PIPELINE.yaml": (
            "schema_version: signalos.blueprint_pipeline.v1\n"
            f"blueprint_id: {bp_id}\n"
            "stages:\n"
            "  - G1-authoring\n"
            "  - G2-review\n"
            "  - G3-validation\n"
            "  - G4-registration\n"
            "  - G5-adoption-proof\n"
        ),
        "SUBAGENT_HARNESS.json": json.dumps({
            "schema_version": "signalos.blueprint_subagent_harness.v1",
            "blueprint_id": bp_id,
            "packet_scope": {
                "allowed_paths": [*DRAFT_COMPONENTS, *DRAFT_GOVERNANCE_ARTIFACTS],
                "forbidden_paths": [".signalos/AUDIT_TRAIL.jsonl", ".git/"],
            },
            "result_contract": {
                "required": ["status", "files_written", "validation_results"],
            },
        }, indent=2, ensure_ascii=False) + "\n",
        "GOVERNANCE_GATES.json": json.dumps(gate_state, indent=2, ensure_ascii=False) + "\n",
    }


def _validate_blueprint_dir(root: Path, bp_dir: Path, bp_id: str) -> dict[str, Any]:
    errors: list[str] = []
    for name in DRAFT_COMPONENTS:
        path = bp_dir / name
        if not path.is_file():
            errors.append(f"missing required component file {name}")
            continue
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{name} is not valid JSON: {exc}")
            continue
        if _contains_placeholder(data):
            errors.append(f"{name} contains placeholder content")
    for name in DRAFT_GOVERNANCE_ARTIFACTS:
        path = bp_dir / name
        if not path.is_file():
            errors.append(f"missing required governance artifact {name}")
            continue
        if path.suffix == ".json":
            try:
                _load_json(path)
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{name} is not valid JSON: {exc}")
    try:
        blueprint = _load_json(bp_dir / "blueprint.json")
        if blueprint.get("id") != bp_id:
            errors.append(f"blueprint.json declares id {blueprint.get('id')!r}")
        errors.extend(validate_blueprint(blueprint))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"blueprint.json could not be read: {exc}")
    return {
        "ok": not errors,
        "errors": errors,
        "blueprint_id": bp_id,
        "path": _display(bp_dir, root),
    }


def _update_gate_state(
    bp_dir: Path,
    root: Path,
    gate_id: str,
    passed: bool,
    evidence: dict[str, Any],
) -> None:
    path = bp_dir / "GOVERNANCE_GATES.json"
    if not path.is_file():
        return
    try:
        data = _load_json(path)
    except (OSError, json.JSONDecodeError):
        return
    gates = data.get("gates", [])
    if not isinstance(gates, list):
        return
    for gate in gates:
        if isinstance(gate, dict) and gate.get("id") == gate_id:
            gate["status"] = "pass" if passed else "blocked"
            gate["updated_at"] = _now()
            gate["latest_evidence"] = evidence
            break
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _ensure_new_id(root: Path, bp_id: str) -> None:
    registry = load_combined_registry(root)
    if any(entry.get("id") == bp_id for entry in registry.get("blueprints", [])):
        raise BlueprintWorkflowError(f"blueprint id already registered: {bp_id}")


def _load_intent(path: Path | str | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = _load_json(Path(path))
    return data.get("intent", data) if isinstance(data, dict) else {}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _entity_names(intent: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in intent.get("entities", []):
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
        else:
            name = str(item).strip()
        if name:
            result.append(_pascal(name))
    return result


def _keywords(*values: Any) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if isinstance(value, list):
            tokens.extend(_keywords(*value))
            continue
        for token in re.split(r"[^A-Za-z0-9]+", str(value).lower()):
            if len(token) > 2 and token not in tokens:
                tokens.append(token)
    return tokens[:20]


def _contains_placeholder(value: Any) -> bool:
    text = json.dumps(value, sort_keys=True).lower()
    return any(token in text for token in ("todo", "lorem ipsum", "placeholder", "fake"))


def _render_questions(bp_id: str, intent: dict[str, Any]) -> str:
    product = intent.get("product_name") or _display_name(bp_id)
    return (
        "# Blueprint Authoring Questions\n\n"
        f"blueprint_id: {bp_id}\n"
        f"product: {product}\n\n"
        "- Which domain entities must be first-class?\n"
        "- Which workflows must be acceptance-tested?\n"
        "- Which profiles must the blueprint support?\n"
    )


def _render_agent_task(bp_id: str, from_intent: Path | str | None) -> str:
    intent_line = f"- Source intent: `{from_intent}`\n" if from_intent else ""
    return (
        "# SignalOS Custom Blueprint Authoring Task\n\n"
        f"blueprint_id: {bp_id}\n\n"
        f"{intent_line}"
        "- Produce and refine the six JSON component files.\n"
        "- Keep the blueprint technology-neutral unless profile support is explicit.\n"
        "- Run `signalos product blueprint validate --id "
        f"{bp_id}` before asking for review.\n"
    )


def _render_rework_packet(bp_id: str, validation: dict[str, Any], notes: str) -> str:
    errors = "\n".join(f"- {error}" for error in validation.get("errors", [])) or "- No validator errors."
    return (
        "# Blueprint Rework Packet\n\n"
        f"blueprint_id: {bp_id}\n\n"
        f"notes: {notes or 'n/a'}\n\n"
        "## Validator Findings\n\n"
        f"{errors}\n"
    )


def _write_state(root: Path, action: str, bp_id: str, payload: dict[str, Any]) -> None:
    evidence_dir = root / ".signalos" / "product" / "blueprint-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / f"{action}-{bp_id}.json"
    payload["evidence_path"] = _display(path, root)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_audit(root: Path, action: str, bp_id: str, payload: dict[str, Any]) -> None:
    audit = root / ".signalos" / "product" / "BLUEPRINT_AUDIT.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": _now(),
        "action": action,
        "blueprint_id": bp_id,
        "status": payload.get("status"),
        "evidence_path": payload.get("evidence_path"),
    }
    audit.open("a", encoding="utf-8").write(json.dumps(row, ensure_ascii=False) + "\n")


def _draft_dir(root: Path, bp_id: str) -> Path:
    return root / ".signalos" / "product" / "blueprint-drafts" / bp_id


def _custom_dir(root: Path, bp_id: str) -> Path:
    return root / ".signalos" / "product" / "blueprints" / "custom" / bp_id


def _custom_registry_path(root: Path) -> Path:
    return root / ".signalos" / "product" / "blueprints" / "registry.custom.json"


def _display(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _display_name(bp_id: str) -> str:
    return " ".join(part.capitalize() for part in bp_id.split("-"))


def _pascal(value: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", value) if part)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "workflow"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
