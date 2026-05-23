"""Dependency-free loader for SignalOS factory profile manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

PROFILE_SCHEMA_VERSION = 1
COMMAND_NAMES = ("install", "build", "test", "lint", "preview")
PREVIEW_MODES = ("none", "command", "npm-script", "static")
_PROFILE_DIR = Path(__file__).resolve().parent
_FIXTURE_DIR = _PROFILE_DIR / "fixtures"


class ProfileError(ValueError):
    """Raised when a profile manifest is malformed."""


class ProfileNotFoundError(ProfileError):
    """Raised when a requested profile id has no manifest."""


@dataclass(frozen=True)
class ProfileTemplate:
    source: str
    destination: str
    required: bool = True
    group: str = "governance"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str) -> "ProfileTemplate":
        _require_keys(raw, {"source", "destination"}, path)
        _reject_unknown_keys(raw, {"source", "destination", "required", "group"}, path)
        source = _string(raw.get("source"), f"{path}.source")
        destination = _string(raw.get("destination"), f"{path}.destination")
        _validate_relative_path(source, f"{path}.source")
        _validate_relative_path(destination, f"{path}.destination")
        return cls(
            source=source,
            destination=destination,
            required=_bool(raw.get("required", True), f"{path}.required"),
            group=_string(raw.get("group", "governance"), f"{path}.group"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "required": self.required,
            "group": self.group,
        }


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    required: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str) -> "CommandSpec":
        _require_keys(raw, {"name", "argv"}, path)
        _reject_unknown_keys(raw, {"name", "argv", "required"}, path)
        name = _string(raw.get("name"), f"{path}.name")
        argv = _string_tuple(raw.get("argv"), f"{path}.argv")
        if not argv:
            raise ProfileError(f"{path}.argv must contain at least one argument")
        return cls(
            name=name,
            argv=argv,
            required=_bool(raw.get("required", True), f"{path}.required"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "argv": list(self.argv), "required": self.required}


@dataclass(frozen=True)
class CIConfig:
    enabled: bool
    files: tuple[str, ...] = ()
    templates: tuple[ProfileTemplate, ...] = ()
    disabled_reason: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str) -> "CIConfig":
        _require_keys(raw, {"enabled"}, path)
        _reject_unknown_keys(raw, {"enabled", "files", "templates", "disabled_reason"}, path)
        enabled = _bool(raw.get("enabled"), f"{path}.enabled")
        files = _string_tuple(raw.get("files", []), f"{path}.files")
        for index, file_path in enumerate(files):
            _validate_relative_path(file_path, f"{path}.files[{index}]")
        templates = tuple(
            ProfileTemplate.from_dict(item, f"{path}.templates[{index}]")
            for index, item in enumerate(_mapping_list(raw.get("templates", []), f"{path}.templates"))
        )
        disabled_reason = raw.get("disabled_reason")
        if disabled_reason is not None:
            disabled_reason = _string(disabled_reason, f"{path}.disabled_reason")
        if not enabled and not disabled_reason:
            raise ProfileError(f"{path}.disabled_reason is required when CI is disabled")
        return cls(
            enabled=enabled,
            files=files,
            templates=templates,
            disabled_reason=disabled_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "files": list(self.files),
            "templates": [template.to_dict() for template in self.templates],
            "disabled_reason": self.disabled_reason,
        }


@dataclass(frozen=True)
class PreviewConfig:
    mode: str
    command: str | None = None
    url: str | None = None
    requires_install: bool = False
    disabled_reason: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str) -> "PreviewConfig":
        _require_keys(raw, {"mode"}, path)
        _reject_unknown_keys(
            raw,
            {"mode", "command", "url", "requires_install", "disabled_reason"},
            path,
        )
        mode = _string(raw.get("mode"), f"{path}.mode")
        if mode not in PREVIEW_MODES:
            raise ProfileError(f"{path}.mode must be one of {', '.join(PREVIEW_MODES)}")
        command = raw.get("command")
        if command is not None:
            command = _string(command, f"{path}.command")
        url = raw.get("url")
        if url is not None:
            url = _string(url, f"{path}.url")
        disabled_reason = raw.get("disabled_reason")
        if disabled_reason is not None:
            disabled_reason = _string(disabled_reason, f"{path}.disabled_reason")
        if mode == "none" and not disabled_reason:
            raise ProfileError(f"{path}.disabled_reason is required when preview mode is none")
        if mode != "none" and not command:
            raise ProfileError(f"{path}.command is required when preview mode is {mode}")
        return cls(
            mode=mode,
            command=command,
            url=url,
            requires_install=_bool(raw.get("requires_install", False), f"{path}.requires_install"),
            disabled_reason=disabled_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "command": self.command,
            "url": self.url,
            "requires_install": self.requires_install,
            "disabled_reason": self.disabled_reason,
        }


@dataclass(frozen=True)
class Profile:
    schema_version: int
    id: str
    name: str
    description: str
    required_templates: tuple[ProfileTemplate, ...]
    ci: CIConfig
    commands: Mapping[str, CommandSpec | None]
    preview: PreviewConfig
    validator_groups: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str = "<profile>") -> "Profile":
        _require_keys(
            raw,
            {
                "schema_version",
                "id",
                "name",
                "description",
                "required_templates",
                "ci",
                "commands",
                "preview",
            },
            path,
        )
        _reject_unknown_keys(
            raw,
            {
                "schema_version",
                "id",
                "name",
                "description",
                "required_templates",
                "ci",
                "commands",
                "preview",
                "validator_groups",
            },
            path,
        )
        schema_version = _int(raw.get("schema_version"), f"{path}.schema_version")
        if schema_version != PROFILE_SCHEMA_VERSION:
            raise ProfileError(
                f"{path}.schema_version must be {PROFILE_SCHEMA_VERSION}, got {schema_version}"
            )
        profile_id = _profile_id(raw.get("id"), f"{path}.id")
        commands = _commands(raw.get("commands"), f"{path}.commands")
        preview = PreviewConfig.from_dict(_mapping(raw.get("preview"), f"{path}.preview"), f"{path}.preview")
        if preview.command is not None and preview.command not in commands:
            raise ProfileError(f"{path}.preview.command must reference a known command name")
        if preview.command is not None and commands[preview.command] is None:
            raise ProfileError(f"{path}.preview.command references a disabled command")
        return cls(
            schema_version=schema_version,
            id=profile_id,
            name=_string(raw.get("name"), f"{path}.name"),
            description=_string(raw.get("description"), f"{path}.description"),
            required_templates=tuple(
                ProfileTemplate.from_dict(item, f"{path}.required_templates[{index}]")
                for index, item in enumerate(
                    _mapping_list(raw.get("required_templates"), f"{path}.required_templates")
                )
            ),
            ci=CIConfig.from_dict(_mapping(raw.get("ci"), f"{path}.ci"), f"{path}.ci"),
            commands=commands,
            preview=preview,
            validator_groups=_string_tuple(raw.get("validator_groups", []), f"{path}.validator_groups"),
        )

    def command(self, name: str) -> CommandSpec | None:
        if name not in COMMAND_NAMES:
            raise KeyError(f"unknown profile command {name!r}")
        return self.commands[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "required_templates": [template.to_dict() for template in self.required_templates],
            "ci": self.ci.to_dict(),
            "commands": {
                name: command.to_dict() if command is not None else None
                for name, command in self.commands.items()
            },
            "preview": self.preview.to_dict(),
            "validator_groups": list(self.validator_groups),
        }


def list_profile_ids(profile_dir: Path | None = None) -> list[str]:
    """Return available profile ids from the fixture directory."""

    base = profile_dir or _FIXTURE_DIR
    if not base.exists():
        return []
    return sorted(path.stem for path in base.glob("*.json") if path.is_file())


def list_profiles(profile_dir: Path | None = None) -> list[Profile]:
    """Load all available profiles in stable id order."""

    return [load_profile(profile_id, profile_dir=profile_dir) for profile_id in list_profile_ids(profile_dir)]


def profile_exists(profile_id: str, profile_dir: Path | None = None) -> bool:
    """Return whether a profile manifest exists."""

    return _profile_path(profile_id, profile_dir).is_file()


def load_profile(profile_id: str, profile_dir: Path | None = None) -> Profile:
    """Load and validate a profile manifest by id."""

    normalized_id = _profile_id(profile_id, "profile_id")
    path = _profile_path(normalized_id, profile_dir)
    if not path.is_file():
        raise ProfileNotFoundError(f"profile {normalized_id!r} does not exist")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"profile {normalized_id!r} is not valid JSON: {exc}") from exc
    profile = Profile.from_dict(_mapping(raw, str(path)), path=str(path))
    if profile.id != normalized_id:
        raise ProfileError(f"profile file {path.name} declares id {profile.id!r}")
    return profile


def _profile_path(profile_id: str, profile_dir: Path | None = None) -> Path:
    base = profile_dir or _FIXTURE_DIR
    return base / f"{profile_id}.json"


def _commands(raw: Any, path: str) -> dict[str, CommandSpec | None]:
    mapping = _mapping(raw, path)
    missing = [name for name in COMMAND_NAMES if name not in mapping]
    if missing:
        raise ProfileError(f"{path} is missing command entries: {', '.join(missing)}")
    extra = sorted(set(mapping.keys()).difference(COMMAND_NAMES))
    if extra:
        raise ProfileError(f"{path} has unknown command entries: {', '.join(extra)}")
    commands: dict[str, CommandSpec | None] = {}
    for name in COMMAND_NAMES:
        value = mapping[name]
        if value is None:
            commands[name] = None
            continue
        commands[name] = CommandSpec.from_dict(_mapping(value, f"{path}.{name}"), f"{path}.{name}")
    return commands


def _validate_relative_path(value: str, path: str) -> None:
    if "\\" in value:
        raise ProfileError(f"{path} must use forward-slash relative paths")
    if ":" in value:
        raise ProfileError(f"{path} must not contain a drive or URI scheme")
    parsed = PurePosixPath(value)
    if not value or parsed.is_absolute() or ".." in parsed.parts:
        raise ProfileError(f"{path} must be a safe relative path")


def _require_keys(raw: Mapping[str, Any], required: set[str], path: str) -> None:
    missing = sorted(required.difference(raw.keys()))
    if missing:
        raise ProfileError(f"{path} is missing required keys: {', '.join(missing)}")


def _reject_unknown_keys(raw: Mapping[str, Any], allowed: set[str], path: str) -> None:
    extra = sorted(set(raw.keys()).difference(allowed))
    if extra:
        raise ProfileError(f"{path} has unknown keys: {', '.join(extra)}")


def _mapping(raw: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise ProfileError(f"{path} must be an object")
    return raw


def _mapping_list(raw: Any, path: str) -> list[Mapping[str, Any]]:
    if not isinstance(raw, list):
        raise ProfileError(f"{path} must be a list")
    values: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw):
        values.append(_mapping(item, f"{path}[{index}]"))
    return values


def _string_tuple(raw: Any, path: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ProfileError(f"{path} must be a list")
    values: list[str] = []
    for index, value in enumerate(raw):
        values.append(_string(value, f"{path}[{index}]"))
    return tuple(values)


def _string(raw: Any, path: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ProfileError(f"{path} must be a non-empty string")
    return raw


def _bool(raw: Any, path: str) -> bool:
    if not isinstance(raw, bool):
        raise ProfileError(f"{path} must be a boolean")
    return raw


def _int(raw: Any, path: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ProfileError(f"{path} must be an integer")
    return raw


def _profile_id(raw: Any, path: str) -> str:
    value = _string(raw, path)
    if not value.replace("-", "").replace("_", "").isalnum() or value[0] in "-_":
        raise ProfileError(f"{path} must contain only letters, numbers, hyphens, or underscores")
    return value
