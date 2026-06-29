# signalos_lib/product/blueprints/registry.py
# Phase P2 - Blueprint Registry
#
# Data-driven blueprint loader, validator, and intent matcher.
# Adding a new product type means adding a blueprint directory and
# an entry in registry.json - no code changes required.

from __future__ import annotations

__all__ = [
    "apply_blueprint_intent_defaults",
    "load_combined_registry",
    "load_registry",
    "load_blueprint",
    "list_blueprints",
    "match_blueprint",
    "validate_blueprint_registry",
    "validate_blueprint",
]

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Internal paths
# ---------------------------------------------------------------------------

_BLUEPRINTS_DIR = Path(__file__).resolve().parent
_REGISTRY_PATH = _BLUEPRINTS_DIR / "registry.json"
_CUSTOM_REGISTRY_REL = Path(".signalos") / "product" / "blueprints" / "registry.custom.json"

# Sub-files that are merged into a loaded blueprint
_SUB_FILES = ("api", "ui", "tests", "seed", "acceptance")

# Required top-level keys in a valid blueprint
_REQUIRED_KEYS = frozenset({
    "id",
    "display_name",
    "intent_match",
    "required_intent_fields",
    "entities",
    "workflows",
    "api",
    "ui",
    "tests",
    "seed_data",
    "security",
    "quality_profile",
    "default_deferrals",
    "profile_support",
})

# Valid profile_support values (must match stack adapter ids)
_VALID_PROFILES = frozenset({
    "react-vite",
    "node-api",
    "fastapi-api",
    "go-api",
    "agent-selected",
    "generic",
    "existing-repo",
})

_DEFAULTABLE_LIST_FIELDS = frozenset({
    "target_users",
    "primary_workflows",
    "entities",
    "entity_relationships",
    "ux_surfaces",
    "api_surfaces",
    "data_sources",
    "integrations",
    "auth_requirements",
    "permissions",
    "audit_requirements",
    "security_constraints",
    "performance_expectations",
    "stack_preferences",
    "unknowns",
    "assumptions",
    "out_of_scope",
})

_DEFAULTABLE_STRING_FIELDS = frozenset({
    "product_name",
    "product_type",
    "deployment_intent",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_registry() -> dict[str, Any]:
    """Load and return the parsed registry.json."""
    text = _REGISTRY_PATH.read_text(encoding="utf-8")
    return json.loads(text)


def load_combined_registry(repo_root: Path | str | None = None) -> dict[str, Any]:
    """Load built-in plus adopter-owned custom blueprint registry entries."""
    registry = load_registry()
    combined = {
        "schema_version": registry.get("schema_version", 1),
        "blueprints": [
            {**entry, "origin": entry.get("origin", "builtin")}
            for entry in registry.get("blueprints", [])
            if isinstance(entry, dict)
        ],
    }
    root = Path(repo_root).resolve() if repo_root is not None else None
    if root is None:
        return combined

    custom_path = root / _CUSTOM_REGISTRY_REL
    if not custom_path.is_file():
        return combined
    try:
        custom = _load_json(custom_path)
    except (OSError, json.JSONDecodeError):
        return combined
    for entry in custom.get("blueprints", []):
        if not isinstance(entry, dict):
            continue
        combined["blueprints"].append({
            **entry,
            "origin": entry.get("origin", "custom"),
            "registry_path": str(custom_path),
        })
    return combined


def load_blueprint(
    blueprint_id: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Load a blueprint by id, merging its sub-files.

    Returns the merged blueprint dict, or ``None`` if the id is not
    found in the registry.
    """
    registry = load_combined_registry(repo_root)
    entry = _find_entry(registry, blueprint_id)
    if entry is None:
        return None

    bp_path = _resolve_entry_path(entry, repo_root)
    if not bp_path.is_file():
        return None

    blueprint = _load_json(bp_path)
    blueprint.setdefault("origin", entry.get("origin", "builtin"))
    blueprint.setdefault("registry_path", entry.get("registry_path"))
    bp_dir = bp_path.parent

    # Merge sub-files
    for sub in _SUB_FILES:
        sub_path = bp_dir / f"{sub}.json"
        if sub_path.is_file():
            blueprint[f"{sub}_detail"] = _load_json(sub_path)

    return blueprint


def list_blueprints(repo_root: Path | str | None = None) -> list[dict[str, str]]:
    """Return lightweight metadata for every registered blueprint."""
    registry = load_combined_registry(repo_root)
    result: list[dict[str, str]] = []
    for entry in registry.get("blueprints", []):
        bp_path = _resolve_entry_path(entry, repo_root)
        if bp_path.is_file():
            bp = _load_json(bp_path)
            result.append({
                "id": bp["id"],
                "display_name": bp.get("display_name", bp["id"]),
                "origin": entry.get("origin", "builtin"),
            })
        else:
            result.append({
                "id": entry["id"],
                "display_name": entry["id"],
                "origin": entry.get("origin", "builtin"),
            })
    return result


def match_blueprint(
    intent: dict[str, Any],
    repo_root: Path | str | None = None,
) -> str | None:
    """Match a product intent to the best blueprint id.

    Match priority:
    1. Exact product_type match
    2. Entity overlap scoring
    3. Keyword overlap scoring (prompt text scanned against keywords)

    Returns the blueprint id or ``None`` if no match.
    """
    registry = load_combined_registry(repo_root)
    blueprints = _load_all_blueprints(registry, repo_root)

    if not blueprints:
        return None

    intent_type = intent.get("product_type", "")
    intent_entities = {e.lower() for e in intent.get("entities", [])}
    intent_prompt = intent.get("_prompt", "").lower()

    # Also build keyword tokens from all intent string fields for keyword matching
    intent_keywords = _extract_intent_keywords(intent)

    # --- Pass 1: exact product_type match ---
    for bp in blueprints:
        match_spec = bp.get("intent_match", {})
        if intent_type in match_spec.get("product_type", []):
            return bp["id"]

    # --- Pass 2: entity overlap ---
    best_id: str | None = None
    best_score = 0
    for bp in blueprints:
        match_spec = bp.get("intent_match", {})
        bp_entities = {e.lower() for e in match_spec.get("entities", [])}
        overlap = len(intent_entities & bp_entities)
        if overlap > best_score:
            best_score = overlap
            best_id = bp["id"]
    if best_score > 0:
        return best_id

    # --- Pass 3: keyword overlap ---
    best_id = None
    best_score = 0
    for bp in blueprints:
        match_spec = bp.get("intent_match", {})
        bp_keywords = {k.lower() for k in match_spec.get("keywords", [])}
        overlap = len(intent_keywords & bp_keywords)
        if overlap > best_score:
            best_score = overlap
            best_id = bp["id"]
    if best_score > 0:
        return best_id

    return None


def validate_blueprint_registry(
    repo_root: Path | str | None = None,
    blueprint_id: str | None = None,
) -> dict[str, Any]:
    """Validate built-in and optional custom blueprint registry entries."""
    registry = load_combined_registry(repo_root)
    entries = registry.get("blueprints", [])
    results: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            issues.append({
                "severity": "error",
                "scope": "registry",
                "message": "registry entry must be an object",
            })
            continue
        entry_id = str(entry.get("id", ""))
        if blueprint_id and entry_id != blueprint_id:
            continue
        if entry_id in seen:
            issues.append({
                "severity": "error",
                "scope": f"registry:{entry_id}",
                "message": f"duplicate blueprint id: {entry_id}",
            })
        seen.add(entry_id)

        bp_path = _resolve_entry_path(entry, repo_root)
        bp = load_blueprint(entry_id, repo_root)
        if bp is None:
            bp_issues = [f"blueprint file not found: {bp_path}"]
        else:
            bp_issues = validate_blueprint(bp)
            bp_issues.extend(_validate_component_files(bp_path.parent))
        for message in bp_issues:
            issues.append({
                "severity": "error",
                "scope": entry_id or "unknown",
                "message": message,
            })
        results.append({
            "id": entry_id,
            "origin": entry.get("origin", "builtin"),
            "path": str(bp_path),
            "valid": not bp_issues,
            "issues": bp_issues,
        })

    if blueprint_id and not results:
        issues.append({
            "severity": "error",
            "scope": blueprint_id,
            "message": f"unknown blueprint id: {blueprint_id}",
        })

    ok = not any(issue["severity"] == "error" for issue in issues)
    return {
        "schema_version": "signalos.blueprint_registry_validation.v1",
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "repo_root": str(Path(repo_root).resolve()) if repo_root is not None else None,
        "blueprint_id": blueprint_id,
        "blueprints": results,
        "issues": issues,
        "summary": {
            "total": len(results),
            "valid": sum(1 for result in results if result["valid"]),
            "invalid": sum(1 for result in results if not result["valid"]),
            "issues": len(issues),
        },
    }


def validate_blueprint(blueprint: dict[str, Any]) -> list[str]:
    """Validate a blueprint dict and return a list of errors (empty = valid)."""
    errors: list[str] = []

    # Check required keys
    missing = _REQUIRED_KEYS - set(blueprint.keys())
    for key in sorted(missing):
        errors.append(f"missing required field: {key}")

    # Validate id
    bp_id = blueprint.get("id", "")
    if not bp_id or not isinstance(bp_id, str):
        errors.append("id must be a non-empty string")

    # Validate intent_match structure
    intent_match = blueprint.get("intent_match")
    if intent_match is not None:
        if not isinstance(intent_match, dict):
            errors.append("intent_match must be a dict")
        else:
            for key in ("product_type", "entities", "keywords"):
                val = intent_match.get(key)
                if val is not None and not isinstance(val, list):
                    errors.append(f"intent_match.{key} must be a list")

    # Validate entities
    entities = blueprint.get("entities")
    if entities is not None:
        if not isinstance(entities, list):
            errors.append("entities must be a list")
        else:
            for i, entity in enumerate(entities):
                if not isinstance(entity, dict):
                    errors.append(f"entities[{i}] must be a dict")
                elif "name" not in entity or "fields" not in entity:
                    errors.append(f"entities[{i}] must have name and fields")

    # Validate workflows
    workflows = blueprint.get("workflows")
    if workflows is not None:
        if not isinstance(workflows, list):
            errors.append("workflows must be a list")
        else:
            for i, wf in enumerate(workflows):
                if not isinstance(wf, dict):
                    errors.append(f"workflows[{i}] must be a dict")
                elif "name" not in wf or "description" not in wf:
                    errors.append(f"workflows[{i}] must have name and description")

    # Validate profile_support values
    profile_support = blueprint.get("profile_support")
    if profile_support is not None:
        if not isinstance(profile_support, list):
            errors.append("profile_support must be a list")
        else:
            for ps in profile_support:
                if ps not in _VALID_PROFILES:
                    errors.append(f"invalid profile_support value: {ps}")

    # Validate optional intent defaults.  Domain defaults belong in blueprint
    # data so adding a product type does not require core delivery code edits.
    defaults = blueprint.get("intent_defaults")
    if defaults is not None:
        if not isinstance(defaults, dict):
            errors.append("intent_defaults must be a dict")
        else:
            allowed = _DEFAULTABLE_LIST_FIELDS | _DEFAULTABLE_STRING_FIELDS
            for key, val in defaults.items():
                if key not in allowed:
                    errors.append(f"intent_defaults.{key} is not supported")
                    continue
                if key in _DEFAULTABLE_LIST_FIELDS and not isinstance(val, list):
                    errors.append(f"intent_defaults.{key} must be a list")
                if key in _DEFAULTABLE_STRING_FIELDS and not isinstance(val, str):
                    errors.append(f"intent_defaults.{key} must be a string")

    return errors


def apply_blueprint_intent_defaults(
    intent: dict[str, Any],
    blueprint: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply data-driven intent defaults from a matched blueprint.

    This is intentionally generic.  The core extractor may identify a product
    type, but product-specific enterprise scope, roles, workflow defaults,
    surfaces, security posture, and assumptions must live in blueprint data.
    """
    enriched = _clone_intent(intent)
    defaults = blueprint.get("intent_defaults") if blueprint else None
    if not isinstance(defaults, dict):
        return enriched

    for key in _DEFAULTABLE_LIST_FIELDS:
        current = enriched.setdefault(key, [])
        if not isinstance(current, list):
            current = []
            enriched[key] = current
        values = defaults.get(key)
        if isinstance(values, list):
            _append_missing(current, [str(value) for value in values])

    for key in _DEFAULTABLE_STRING_FIELDS:
        value = defaults.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        current = enriched.get(key)
        if key == "deployment_intent":
            if not current or current == "none":
                enriched[key] = value
        elif key == "product_type":
            if not current or current == "custom":
                enriched[key] = value
        elif not current:
            enriched[key] = value

    return enriched


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _clone_intent(intent: dict[str, Any]) -> dict[str, Any]:
    cloned: dict[str, Any] = {}
    for key, value in intent.items():
        cloned[key] = list(value) if isinstance(value, list) else value
    return cloned


def _append_missing(target: list[str], values: list[str]) -> None:
    existing = {str(item).lower() for item in target}
    for value in values:
        key = value.lower()
        if key not in existing:
            target.append(value)
            existing.add(key)


def _find_entry(registry: dict[str, Any], blueprint_id: str) -> dict[str, Any] | None:
    for entry in registry.get("blueprints", []):
        if entry.get("id") == blueprint_id:
            return entry
    return None


def _load_all_blueprints(
    registry: dict[str, Any],
    repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in registry.get("blueprints", []):
        bp_path = _resolve_entry_path(entry, repo_root)
        if bp_path.is_file():
            result.append(_load_json(bp_path))
    return result


def _resolve_entry_path(entry: dict[str, Any], repo_root: Path | str | None) -> Path:
    path = Path(str(entry.get("path", "")))
    if path.is_absolute():
        return path
    if entry.get("origin") == "custom" and repo_root is not None:
        return Path(repo_root).resolve() / path
    return _BLUEPRINTS_DIR / path


def _validate_component_files(bp_dir: Path) -> list[str]:
    errors: list[str] = []
    for sub in _SUB_FILES:
        path = bp_dir / f"{sub}.json"
        if not path.is_file():
            errors.append(f"missing component file: {sub}.json")
            continue
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"component file {sub}.json is not valid JSON: {exc}")
            continue
        if not isinstance(data, dict) or not data:
            errors.append(f"component file {sub}.json must be a non-empty object")
    return errors


def _extract_intent_keywords(intent: dict[str, Any]) -> set[str]:
    """Build a set of lowercase keyword tokens from intent fields."""
    tokens: set[str] = set()
    for key in ("product_name", "product_type"):
        val = intent.get(key, "")
        if isinstance(val, str):
            tokens.update(val.lower().split())
    for key in ("primary_workflows", "entities", "ux_surfaces",
                "target_users", "integrations"):
        val = intent.get(key, [])
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    tokens.update(item.lower().split())
    return tokens
