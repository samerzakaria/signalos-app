"""Technology-neutral product capability choices.

This module keeps user/agent technology preferences separate from the
SignalOS governance pipeline. A stack adapter is only one implementation
choice; databases, caches, language, frontend, backend, and deployment targets
remain portable capabilities that can be honored by any capable adapter.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

SCHEMA_VERSION = "signalos.product_capabilities.v1"

_AUTO_VALUES = {"", "auto", "agent", "agent-decides", "agent_decides"}
_NONE_VALUES = {"none", "no", "off", "false", "disabled"}

_ALIASES = {
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "postgre": "postgresql",
    "postegres": "postgresql",
    "mssql": "sql-server",
    "sqlserver": "sql-server",
    "sql-server": "sql-server",
    "sql server": "sql-server",
    "mysql": "mysql",
    "maria": "mariadb",
    "mariadb": "mariadb",
    "sqlite": "sqlite",
    "redis": "redis",
    "redia": "redis",
    "memcached": "memcached",
    "memcache": "memcached",
    "nodejs": "node",
    "node.js": "node",
    "expressjs": "express",
    "express.js": "express",
    "go-api": "go-api",
    "golang": "go",
    "go-lang": "go",
    "fast-api": "fastapi",
    "fastapi-api": "fastapi",
    "react-vite": "react-vite",
    "vite-react": "react-vite",
    "angular": "angular",
    "ng": "angular",
    "next": "nextjs-app",
    "nextjs": "nextjs-app",
    "next.js": "nextjs-app",
    "nextjs-app": "nextjs-app",
    "vue": "vue-vite",
    "vuejs": "vue-vite",
    "vue.js": "vue-vite",
    "vue-vite": "vue-vite",
    "flutter": "flutter-app",
    "flutter-app": "flutter-app",
    "dart": "flutter-app",
    "react-native": "expo-react-native",
    "react native": "expo-react-native",
    "expo": "expo-react-native",
    "expo-react-native": "expo-react-native",
    "mobile": "mobile",
    "mobile-app": "mobile",
    "django-api": "django-api",
    "flask": "flask-api",
    "flask-api": "flask-api",
    "nestjs": "nestjs-api",
    "nestjs-api": "nestjs-api",
    "nest": "nestjs-api",
    "nest.js": "nestjs-api",
    "spring": "spring-boot-api",
    "spring-boot": "spring-boot-api",
    "springboot": "spring-boot-api",
    "spring-boot-api": "spring-boot-api",
    "java-api": "java-api",
    "rust-api": "rust-api",
    "dotnet": ".net",
    "dotnet-minimal-api": "dotnet-minimal-api",
    "minimal-api": "dotnet-minimal-api",
    "minimal api": "dotnet-minimal-api",
    "aspnet": "dotnet-minimal-api",
    "asp.net": "dotnet-minimal-api",
    "aspnetcore": "dotnet-minimal-api",
    "asp.net-core": "dotnet-minimal-api",
    "net": ".net",
    "csharp": "csharp",
    "c#": "csharp",
}

_DATABASES = {"postgresql", "sql-server", "mysql", "mariadb", "sqlite", "mongodb"}
_CACHES = {"redis", "memcached"}
_FRONTENDS = {
    "react",
    "react-vite",
    "angular",
    "vue",
    "vue-vite",
    "svelte",
    "next",
    "nextjs-app",
    "blazor",
}
_MOBILE = {"mobile", "flutter-app", "expo-react-native", "android", "ios"}
_BACKENDS = {
    "node",
    "express",
    "nestjs-api",
    "python",
    "fastapi",
    "django",
    "django-api",
    "flask",
    "flask-api",
    "go",
    "go-api",
    "rust",
    "rust-api",
    ".net",
    "dotnet-minimal-api",
    "csharp",
    "java",
    "java-api",
    "spring-boot-api",
}


def split_choice_values(values: Iterable[str] | None) -> list[str]:
    """Split repeated/comma-delimited CLI choice values."""
    result: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            normalized = normalize_choice(part)
            if normalized not in {"", "auto"} and normalized not in result:
                result.append(normalized)
    return result


def normalize_choice(value: Any) -> str:
    """Normalize one technology/capability token."""
    raw = str(value or "").strip().lower()
    if raw in _AUTO_VALUES:
        return "auto"
    if raw in _NONE_VALUES:
        return "none"
    raw = raw.replace("_", "-")
    return _ALIASES.get(raw, raw)


def apply_capability_choices(
    intent: dict[str, Any],
    *,
    technologies: Iterable[str] | None = None,
    frontend: str | None = None,
    database: str | None = None,
    cache: str | None = None,
    language: str | None = None,
    deployment_target: str | None = None,
    adapter_profile: str | None = None,
    source: str = "user-or-agent",
) -> dict[str, Any]:
    """Return an intent enriched with portable capability preferences."""
    enriched = deepcopy(intent)
    techs = split_choice_values(technologies)
    frontend_choice = normalize_choice(frontend)
    database_choice = normalize_choice(database)
    cache_choice = normalize_choice(cache)
    language_choice = normalize_choice(language)
    deploy_choice = normalize_choice(deployment_target)

    explicit = any(
        choice not in {"", "auto"}
        for choice in (
            *techs,
            frontend_choice,
            database_choice,
            cache_choice,
            language_choice,
            deploy_choice,
        )
    )

    preferences = {
        "schema_version": SCHEMA_VERSION,
        "source": source if explicit else "agent-or-auto",
        "adapter_profile": adapter_profile or "auto",
        "technologies": techs,
        "frontend": frontend_choice,
        "database": database_choice,
        "cache": cache_choice,
        "language": language_choice,
        "deployment_target": deploy_choice,
        "selection_rule": (
            "Honor explicit user technology choices when an adapter can prove "
            "them. If no adapter can prove them, keep delivery partial with a "
            "clear blocker instead of silently switching stacks."
        ),
    }
    enriched["capability_preferences"] = preferences

    stack_preferences = _ensure_list(enriched, "stack_preferences")
    data_sources = _ensure_list(enriched, "data_sources")

    for token in techs:
        _append_unique(stack_preferences, token)
        if token in _DATABASES:
            _append_unique(data_sources, "database")
            _append_unique(data_sources, token)
        if token in _CACHES:
            _append_unique(data_sources, "cache")
            _append_unique(data_sources, token)

    if frontend_choice not in {"", "auto", "none"}:
        _append_unique(stack_preferences, frontend_choice)
    elif frontend_choice == "none":
        enriched["ux_surfaces"] = []
        out_of_scope = _ensure_list(enriched, "out_of_scope")
        _append_unique(out_of_scope, "browser user interface")
    if database_choice not in {"", "auto", "none"}:
        _append_unique(stack_preferences, database_choice)
        _append_unique(data_sources, "database")
        _append_unique(data_sources, database_choice)
    if cache_choice not in {"", "auto", "none"}:
        _append_unique(stack_preferences, cache_choice)
        _append_unique(data_sources, "cache")
        _append_unique(data_sources, cache_choice)
    if language_choice not in {"", "auto", "none"}:
        _append_unique(stack_preferences, language_choice)
    if deploy_choice not in {"", "auto", "none"}:
        enriched["deployment_intent"] = deploy_choice

    return enriched


def build_capability_profile(
    intent: dict[str, Any],
    *,
    adapter_profile: str,
) -> dict[str, Any]:
    """Build the capability profile persisted into packets and evidence."""
    preferences = intent.get("capability_preferences")
    if not isinstance(preferences, dict):
        preferences = {
            "schema_version": SCHEMA_VERSION,
            "source": "agent-or-auto",
            "adapter_profile": adapter_profile,
            "technologies": [],
            "frontend": "auto",
            "database": "auto",
            "cache": "auto",
            "language": "auto",
            "deployment_target": normalize_choice(intent.get("deployment_intent")),
            "selection_rule": "Agent or adapter may choose any provable technology.",
        }

    stack_preferences = [
        normalize_choice(item)
        for item in intent.get("stack_preferences", [])
        if normalize_choice(item) not in {"", "auto"}
    ]
    data_sources = [
        normalize_choice(item)
        for item in intent.get("data_sources", [])
        if normalize_choice(item) not in {"", "auto"}
    ]

    databases = _ordered_unique(
        [
            preferences.get("database"),
            *(item for item in stack_preferences + data_sources if item in _DATABASES),
        ]
    )
    caches = _ordered_unique(
        [
            preferences.get("cache"),
            *(item for item in stack_preferences + data_sources if item in _CACHES),
        ]
    )
    frontends = _ordered_unique(
        [
            preferences.get("frontend"),
            *(item for item in stack_preferences if item in _FRONTENDS),
        ]
    )
    mobile = _ordered_unique(
        [
            *(item for item in stack_preferences if item in _MOBILE),
        ]
    )
    backends = _ordered_unique(
        [
            preferences.get("language"),
            *(item for item in stack_preferences if item in _BACKENDS),
        ]
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "adapter_profile": adapter_profile,
        "user_or_agent_preferences": preferences,
        "technology_preferences": stack_preferences,
        "infrastructure": {
            "databases": databases,
            "caches": caches,
            "data_sources": data_sources,
        },
        "application_layers": {
            "frontend": frontends,
            "mobile": mobile,
            "backend": backends,
            "api_surfaces": list(intent.get("api_surfaces", [])),
        },
        "language": preferences.get("language", "auto"),
        "deployment_target": preferences.get(
            "deployment_target",
            normalize_choice(intent.get("deployment_intent")),
        ),
        "portability_contract": [
            "SignalOS behavior is independent of ABP, .NET, or any one framework.",
            "Adapters may use Redis, SQL Server, PostgreSQL, MySQL, or other normal infrastructure when selected.",
            "An unproved technology choice is a blocker or partial closeout, not a fake success.",
        ],
    }


def _ensure_list(target: dict[str, Any], key: str) -> list[str]:
    value = target.get(key)
    if not isinstance(value, list):
        value = []
        target[key] = value
    return value


def _append_unique(target: list[str], value: str) -> None:
    if not value or value == "auto":
        return
    existing = {str(item).lower() for item in target}
    if value.lower() not in existing:
        target.append(value)


def _ordered_unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_choice(value)
        if normalized in {"", "auto", "none"} or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result
