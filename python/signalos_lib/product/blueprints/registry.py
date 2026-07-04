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
import re
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

# Minimum overlapping tokens a fuzzy pass (entity / keyword) must clear before
# it is allowed to commit the intent to a blueprint's whole domain.  A single
# incidental coincidence (e.g. the word "dashboard" appearing in an expense
# tracker's UI surface) is NOT enough: below this floor ``match_blueprint``
# returns ``None`` so generation builds for the founder's actual stated domain
# rather than snapping onto the nearest wrong-domain blueprint.  Deterministic
# exact ``product_type`` matches bypass the floor; LLM-refined exact matches
# must be corroborated when concrete domain evidence is present.
_MIN_FUZZY_OVERLAP = 2


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
    1. Exact product_type match (deterministic exact matches bypass the floor;
       LLM-refined exact matches must be corroborated)
    2. Entity overlap scoring (must clear ``_MIN_FUZZY_OVERLAP``)
    3. Keyword overlap scoring (must clear ``_MIN_FUZZY_OVERLAP``)

    The fuzzy passes require a minimum-confidence floor so a single incidental
    token coincidence does not force a wrong-domain blueprint (e.g. a personal
    expense tracker snapping onto the revenue/metrics dashboard on the lone
    shared word "dashboard").  LLM-refined exact type labels also need
    corroboration when entities/workflows/surfaces are present, so a model's
    label cannot override the extracted domain contract.  When no pass clears
    its bar the intent is considered domain-unmatched and generation builds for
    the founder's actual stated domain instead.

    Returns the blueprint id or ``None`` if no match.
    """
    registry = load_combined_registry(repo_root)
    blueprints = _load_all_blueprints(registry, repo_root)

    if not blueprints:
        return None

    intent_type = intent.get("product_type", "")
    # Fix #9: fuzzy entity overlap must score against the founder's INDEPENDENT
    # domain contract.  When an LLM refinement rewrote ``entities`` (and stashed
    # the pre-refinement snapshot in ``_deterministic_entities``) the rewritten
    # finance entities must NOT be what we score a blueprint against -- otherwise
    # a co-mislabelled intent snaps onto the wrong domain in Pass 2 even after
    # Pass 1's corroboration check has rejected it.  Prefer the deterministic
    # snapshot; fall back to current entities only when no snapshot exists.
    det_entities = intent.get("_deterministic_entities")
    if isinstance(det_entities, list) and det_entities:
        source_entities = det_entities
    else:
        source_entities = intent.get("entities", [])
    intent_entities = {_normalise_match_token(e) for e in source_entities}
    intent_entities.discard("")
    # Also build keyword tokens from all intent string fields for keyword matching
    intent_keywords = _extract_intent_keywords(intent)

    # --- Pass 1: exact product_type match ---
    for bp in blueprints:
        match_spec = bp.get("intent_match", {})
        if intent_type in match_spec.get("product_type", []):
            if not _exact_product_type_is_trustworthy(
                intent,
                match_spec,
                intent_entities,
                intent_keywords,
            ):
                continue
            return bp["id"]

    # --- Pass 2: entity overlap (must clear the confidence floor) ---
    best_id: str | None = None
    best_score = 0
    for bp in blueprints:
        match_spec = bp.get("intent_match", {})
        bp_entities = {
            _normalise_match_token(e) for e in match_spec.get("entities", [])
        }
        bp_entities.discard("")
        overlap = len(intent_entities & bp_entities)
        if overlap > best_score:
            best_score = overlap
            best_id = bp["id"]
    if best_score >= _MIN_FUZZY_OVERLAP:
        return best_id

    # --- Pass 3: keyword overlap (must clear the confidence floor) ---
    best_id = None
    best_score = 0
    for bp in blueprints:
        match_spec = bp.get("intent_match", {})
        bp_keywords = {
            _normalise_match_token(k) for k in match_spec.get("keywords", [])
        }
        bp_keywords.discard("")
        overlap = len(intent_keywords & bp_keywords)
        if overlap > best_score:
            best_score = overlap
            best_id = bp["id"]
    if best_score >= _MIN_FUZZY_OVERLAP:
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
    """Build a set of lowercase keyword tokens from intent fields.

    Fix #9: when a deterministic entity snapshot exists (LLM refinement rewrote
    ``entities``), the keyword tokens are drawn from the INDEPENDENT snapshot so
    an LLM-injected domain cannot corroborate its own product_type label via the
    keyword pass either.
    """
    tokens: set[str] = set()
    # #35: do NOT draw keywords from `product_type` -- an LLM's label
    # corroborating itself is circular -- nor from `ux_surfaces`. UX surfaces
    # (`dashboard`, `chart`, `form`, `list`) are generic UI words present in
    # almost every app and are LLM-injected in the same refinement that sets the
    # label; they let a personal expense tracker (ux_surfaces=[form,dashboard,
    # chart]) snap onto the financial-dashboard blueprint on the lone shared
    # word "dashboard" even though NO entity or workflow is finance-related.
    val = intent.get("product_name", "")
    if isinstance(val, str):
        tokens.update(_normalise_match_token(part) for part in val.split())

    det_entities = intent.get("_deterministic_entities")
    entities_source = (
        det_entities
        if isinstance(det_entities, list) and det_entities
        else intent.get("entities", [])
    )
    list_sources: list[tuple[str, Any]] = [
        ("primary_workflows", intent.get("primary_workflows", [])),
        ("entities", entities_source),
        ("target_users", intent.get("target_users", [])),
        ("integrations", intent.get("integrations", [])),
    ]
    for _key, val in list_sources:
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    tokens.update(
                        _normalise_match_token(part) for part in item.split()
                    )
    tokens.discard("")
    return tokens


def _normalise_match_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _exact_product_type_is_trustworthy(
    intent: dict[str, Any],
    match_spec: dict[str, Any],
    intent_entities: set[str],
    intent_keywords: set[str],
) -> bool:
    """Return whether an exact type label can select this blueprint.

    An exact ``product_type`` label is trusted ONLY when it does not contradict
    the founder's concrete, independently-extracted domain contract.  Fix #9
    closes two escape hatches the confidence-floor fix left open:

    * **Self-fulfilling LLM corroboration.** When ``refine_intent_with_llm``
      changes ``product_type`` it can also overwrite ``entities`` in the SAME
      call, so ``intent['entities']`` is no longer independent of the label.
      We therefore corroborate against ``_deterministic_entities`` (the
      pre-refinement snapshot) whenever it is present -- not against the
      possibly-LLM-rewritten current entities.

    * **Missing provenance marker.** The label may reach here without the
      ``_product_type_source == 'llm'`` marker (e.g. reloaded from INTENT.json,
      or caller-injected).  When concrete domain entities are present they must
      still corroborate the blueprint; an exact label that disagrees with the
      extracted entities cannot silently override them.

    A bare label with no concrete domain entities (the classic
    deterministic/caller case) stays trusted for backwards compatibility.
    """
    bp_entities = {
        _normalise_match_token(e) for e in match_spec.get("entities", [])
    }
    bp_keywords = {
        _normalise_match_token(k) for k in match_spec.get("keywords", [])
    }
    bp_entities.discard("")
    bp_keywords.discard("")

    # Independent corroboration source: prefer the deterministic entity snapshot
    # captured before any LLM refinement; fall back to the current entities only
    # when no snapshot exists (deterministic-only pipelines never rewrite them).
    det_entities_raw = intent.get("_deterministic_entities")
    if isinstance(det_entities_raw, list) and det_entities_raw:
        corroborating_entities = {
            _normalise_match_token(e) for e in det_entities_raw
        }
        corroborating_entities.discard("")
    else:
        corroborating_entities = set(intent_entities)

    # Is there a concrete, independent domain contract to protect?
    has_concrete_entities = bool(corroborating_entities)

    if not has_concrete_entities:
        # No independent entity contract to contradict -> trust the label.
        return True

    # Concrete entities exist.  The exact label is trustworthy only if that
    # independent evidence actually corroborates this blueprint's domain.
    if len(corroborating_entities & bp_entities) >= _MIN_FUZZY_OVERLAP:
        return True
    if len(intent_keywords & bp_keywords) >= _MIN_FUZZY_OVERLAP:
        return True
    return False
