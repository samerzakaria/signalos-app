"""Secret redaction helpers for SignalOS desktop IPC.

The desktop app follows the same product rule as hosted secret stores:
secret values live outside project files and are exposed only to the runtime
that needs them. Model prompts, notes, audit logs, and UI output get redacted
values or variable names only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


REDACTED = "<redacted>"

SECRET_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".npmrc",
    ".pypirc",
    ".netrc",
}

SECRET_FILE_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
}

SECRET_NAME_RE = re.compile(
    r"(secret|token|password|passwd|pwd|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|database[_-]?url|db[_-]?url|redis[_-]?url|jwt|session|"
    r"stripe|openai|anthropic|gemini|qwen|dashscope)",
    re.IGNORECASE,
)

ENV_ASSIGNMENT_RE = re.compile(
    r"(?m)^\ufeff?([A-Z_][A-Z0-9_]{1,80})\s*=\s*([^\r\n]*)$",
    re.IGNORECASE,
)

SECRET_FIELD_PATTERN = (
    r"[A-Z0-9_.-]*(?:SECRET|TOKEN|PASSWORD|PASSWD|PWD|API[_-]?KEY|ACCESS[_-]?KEY|"
    r"PRIVATE[_-]?KEY|CLIENT[_-]?SECRET|DATABASE[_-]?URL|DB[_-]?URL|REDIS[_-]?URL|"
    r"AUTHORIZATION)[A-Z0-9_.-]*"
)

QUOTED_SECRET_FIELD_RE = re.compile(
    rf"([\"']{SECRET_FIELD_PATTERN}[\"']\s*:\s*[\"'])([^\"'\r\n]*)([\"'])",
    re.IGNORECASE,
)

PLAIN_SECRET_FIELD_RE = re.compile(
    rf"\b({SECRET_FIELD_PATTERN})\s*:\s*([^\s,;]+)",
    re.IGNORECASE,
)

INLINE_ASSIGNMENT_RE = re.compile(
    rf"\b({SECRET_FIELD_PATTERN})\s*=\s*([^\s,;]+)",
    re.IGNORECASE,
)

HIGH_CONFIDENCE_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_\-]{18,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{20,}\b"),
    re.compile(r"(?i)(postgres|postgresql|mysql|mongodb|redis)://[^:\s/@]+:[^@\s]+@"),
]


def is_secret_path(path: str | os.PathLike[str]) -> bool:
    p = Path(path)
    name = p.name.lower()
    if name in SECRET_FILE_NAMES:
        return True
    if name.startswith(".env."):
        return True
    if p.suffix.lower() in SECRET_FILE_SUFFIXES:
        return True
    return False


def redact_text(text: str) -> str:
    if not text:
        return text

    redacted = ENV_ASSIGNMENT_RE.sub(_redact_env_match, text)
    redacted = QUOTED_SECRET_FIELD_RE.sub(
        lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}",
        redacted,
    )
    redacted = PLAIN_SECRET_FIELD_RE.sub(lambda m: f"{m.group(1)}: {REDACTED}", redacted)
    redacted = INLINE_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", redacted)
    for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_for_model(text: str, source_path: str | None = None) -> str:
    if source_path and is_secret_path(source_path):
        return summarize_env_text(text)
    return redact_text(text)


def summarize_env_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.lstrip("\ufeff").strip()
        if not stripped or stripped.startswith("#"):
            continue
        key = stripped.split("=", 1)[0].strip()
        if re.match(r"^[A-Z_][A-Z0-9_]*$", key, re.IGNORECASE):
            lines.append(f"{key}={REDACTED}")
    return "\n".join(lines)


def redact_response(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_response(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_response(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_response(item) for key, item in value.items()}
    return value


def redact_arg_list(args: list[str]) -> list[str]:
    return [redact_text(str(arg)) for arg in args]


def scan_secret_files(root: str, limit: int = 100) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    root_path = Path(root)
    if not root_path.exists() or not root_path.is_dir():
        return found

    ignored_dirs = {
        ".git",
        "node_modules",
        "target",
        "dist",
        "build",
        ".venv",
        "venv",
        ".sidecar-venv",
        "__pycache__",
    }
    for current, dirs, files in os.walk(root_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for filename in files:
            path = Path(current) / filename
            if not is_secret_path(path):
                continue
            try:
                rel = path.relative_to(root_path).as_posix()
            except ValueError:
                rel = path.name
            entry: dict[str, Any] = {"path": rel, "kind": "secret-file"}
            if filename.lower().startswith(".env"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    entry["variables"] = [
                        line.split("=", 1)[0]
                        for line in summarize_env_text(text).splitlines()
                        if "=" in line
                    ]
                except OSError:
                    entry["variables"] = []
            found.append(entry)
            if len(found) >= limit:
                return found
    return found


def _redact_env_match(match: re.Match[str]) -> str:
    key = match.group(1)
    value = match.group(2).strip()
    if value and (SECRET_NAME_RE.search(key) or _looks_like_secret_value(value)):
        return f"{key}={REDACTED}"
    return match.group(0)


def _looks_like_secret_value(value: str) -> bool:
    clean = value.strip().strip("\"'")
    if len(clean) >= 32 and re.fullmatch(r"[A-Za-z0-9_\-\.=/+]+", clean):
        return True
    return any(pattern.search(clean) for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS)
