"""Canonical SignalOS skill catalog helpers.

The orchestrator's `_SKILL_KEY_TO_PATH` mapping is the runtime source of
truth for routable skills. This module exposes that catalog in a structured
form so generators and tests can keep adapter registries from drifting.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


BUNDLE_ROOT = Path(__file__).resolve().parent / "_bundle"
ORCHESTRATOR_PATH = Path(__file__).resolve().parent / "orchestrator.py"
TOOL_ADAPTER_SKILLS_JSON = (
    BUNDLE_ROOT / "core" / "tool-adapters" / "_shared" / "skills.json"
)


@dataclass(frozen=True)
class SkillCatalogEntry:
    """One routable skill from the orchestrator catalog."""

    key: str
    name: str
    path: str


def _orchestrator_catalog() -> dict[str, tuple[str, str]]:
    from .orchestrator import _SKILL_KEY_TO_PATH

    return _SKILL_KEY_TO_PATH


def canonical_skill_catalog() -> list[SkillCatalogEntry]:
    """Return every skill key/display-name/path from `_SKILL_KEY_TO_PATH`."""

    return [
        SkillCatalogEntry(key=key, name=name, path=path)
        for key, (name, path) in _orchestrator_catalog().items()
    ]


def canonical_skill_paths() -> dict[str, str]:
    """Return `{skill_key: bundle_relative_skill_md_path}`."""

    return {entry.key: entry.path for entry in canonical_skill_catalog()}


def bundle_skill_path(path: str, *, bundle_root: Path = BUNDLE_ROOT) -> Path:
    """Resolve a catalog path and ensure it stays inside the bundle root."""

    root = bundle_root.resolve()
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Skill path escapes bundle root: {path}") from exc
    return resolved


def validate_catalog_paths_exist(
    catalog: Sequence[SkillCatalogEntry] | None = None,
    *,
    bundle_root: Path = BUNDLE_ROOT,
) -> list[str]:
    """Return validation errors for missing or out-of-bundle skill paths."""

    errors: list[str] = []
    for entry in catalog or canonical_skill_catalog():
        try:
            full_path = bundle_skill_path(entry.path, bundle_root=bundle_root)
        except ValueError as exc:
            errors.append(f"{entry.key}: {exc}")
            continue
        if not full_path.is_file():
            errors.append(f"{entry.key}: missing {entry.path}")
    return errors


def load_tool_adapter_skills(
    registry_path: Path = TOOL_ADAPTER_SKILLS_JSON,
) -> list[dict[str, Any]]:
    """Load the current tool-adapter skills registry."""

    data = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{registry_path} must contain a JSON list")
    return data


def _registry_name_to_source(
    entries: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    names: list[str] = []
    by_name: dict[str, str] = {}
    for entry in entries:
        name = entry.get("name")
        source = entry.get("source")
        if not isinstance(name, str) or not isinstance(source, str):
            continue
        names.append(name)
        by_name[name] = source

    duplicates = sorted(
        name for name, count in Counter(names).items()
        if count > 1
    )
    return by_name, duplicates


def validate_tool_adapter_registry_sync(
    registry_entries: Sequence[Mapping[str, Any]] | None = None,
    *,
    registry_path: Path = TOOL_ADAPTER_SKILLS_JSON,
) -> list[str]:
    """Return errors if the adapter registry differs from the catalog.

    The sync contract is intentionally narrow: `skills.json` must expose
    exactly the same skill keys and bundle-relative SKILL.md paths as the
    orchestrator catalog.
    """

    entries = (
        load_tool_adapter_skills(registry_path)
        if registry_entries is None else registry_entries
    )
    canonical = canonical_skill_paths()
    registry, duplicates = _registry_name_to_source(entries)

    errors: list[str] = []
    if duplicates:
        errors.append(f"duplicate registry names: {duplicates}")

    canonical_keys = set(canonical)
    registry_keys = set(registry)
    missing = sorted(canonical_keys - registry_keys)
    extra = sorted(registry_keys - canonical_keys)
    if missing:
        errors.append(f"missing registry skills: {missing}")
    if extra:
        errors.append(f"unknown registry skills: {extra}")

    path_mismatches = {
        key: {"canonical": canonical[key], "registry": registry[key]}
        for key in sorted(canonical_keys & registry_keys)
        if canonical[key] != registry[key]
    }
    if path_mismatches:
        errors.append(f"registry path mismatches: {path_mismatches}")

    return errors


def default_description(entry: SkillCatalogEntry) -> str:
    """Return a fallback description for generated adapter entries."""

    return f"SignalOS skill guidance for {entry.name}; see the bundled SKILL.md."


def render_tool_adapter_skills(
    existing_entries: Sequence[Mapping[str, Any]] | None = None,
    catalog: Sequence[SkillCatalogEntry] | None = None,
) -> list[dict[str, Any]]:
    """Render JSON-compatible entries for `_shared/skills.json`.

    Existing descriptions and extra metadata are preserved by skill key.
    Missing descriptions get a deterministic fallback.
    """

    skills = list(catalog or canonical_skill_catalog())
    existing_by_name: dict[str, Mapping[str, Any]] = {}
    for entry in existing_entries or ():
        name = entry.get("name")
        if isinstance(name, str):
            existing_by_name[name] = entry

    rendered: list[dict[str, Any]] = []
    for skill in skills:
        existing = existing_by_name.get(skill.key, {})
        description = existing.get("description")
        if not isinstance(description, str) or not description.strip():
            description = default_description(skill)

        rendered_entry: dict[str, Any] = {
            "name": skill.key,
            "source": skill.path,
            "description": description,
        }
        for key, value in existing.items():
            if key not in rendered_entry:
                rendered_entry[key] = value
        rendered.append(rendered_entry)

    return rendered


def render_tool_adapter_skills_json(
    existing_entries: Sequence[Mapping[str, Any]] | None = None,
    catalog: Sequence[SkillCatalogEntry] | None = None,
) -> str:
    """Render the adapter registry as stable pretty JSON text."""

    return json.dumps(
        render_tool_adapter_skills(existing_entries, catalog),
        indent=2,
        ensure_ascii=True,
    ) + "\n"


def orchestrator_skill_key_literals(
    orchestrator_path: Path = ORCHESTRATOR_PATH,
) -> list[str]:
    """Return skill keys as written in the orchestrator source literal.

    This catches duplicate literal keys before Python collapses them into the
    imported dict.
    """

    tree = ast.parse(orchestrator_path.read_text(encoding="utf-8"))
    for node in tree.body:
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "_SKILL_KEY_TO_PATH":
                value = node.value
        elif isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "_SKILL_KEY_TO_PATH"
                for target in node.targets
            ):
                value = node.value

        if isinstance(value, ast.Dict):
            keys: list[str] = []
            for key_node in value.keys:
                if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                    keys.append(key_node.value)
            return keys

    raise ValueError("_SKILL_KEY_TO_PATH literal not found")


def duplicate_orchestrator_skill_keys(
    orchestrator_path: Path = ORCHESTRATOR_PATH,
) -> list[str]:
    """Return duplicate skill keys in source order."""

    seen: set[str] = set()
    duplicates: list[str] = []
    for key in orchestrator_skill_key_literals(orchestrator_path):
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    return duplicates
