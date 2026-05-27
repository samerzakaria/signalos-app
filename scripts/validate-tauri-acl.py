#!/usr/bin/env python3
"""Validate that registered Tauri commands are permitted by active capabilities."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAURI_DIR = ROOT / "src-tauri"


def fail(message: str) -> None:
    print(f"tauri-acl: {message}", file=sys.stderr)
    raise SystemExit(1)


def registered_commands() -> set[str]:
    main_rs = TAURI_DIR / "src" / "main.rs"
    text = main_rs.read_text(encoding="utf-8")
    match = re.search(r"generate_handler!\s*\[(.*?)\]\s*\)", text, re.S)
    if not match:
        fail(f"could not find generate_handler block in {main_rs}")
    block = re.sub(r"//.*", "", match.group(1))
    return set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*::([a-zA-Z_][a-zA-Z0-9_]*)\b", block))


def active_permission_ids() -> set[str]:
    capability_dir = TAURI_DIR / "capabilities"
    if not capability_dir.exists():
        fail(f"missing capabilities directory: {capability_dir}")

    active: set[str] = set()
    for path in capability_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("permissions", []):
            if isinstance(item, str):
                active.add(item)
            elif isinstance(item, dict) and isinstance(item.get("identifier"), str):
                active.add(item["identifier"])
    return active


def permission_index() -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    permission_dir = TAURI_DIR / "permissions"
    sets: dict[str, list[str]] = {}
    commands: dict[str, set[str]] = {}
    for path in permission_dir.glob("*.toml"):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for item in data.get("set", []):
            identifier = item.get("identifier")
            permissions = item.get("permissions", [])
            if isinstance(identifier, str):
                sets[identifier] = [p for p in permissions if isinstance(p, str)]
        for item in data.get("permission", []):
            identifier = item.get("identifier")
            allow = item.get("commands", {}).get("allow", [])
            if isinstance(identifier, str):
                commands[identifier] = {cmd for cmd in allow if isinstance(cmd, str)}
    return sets, commands


def allowed_commands() -> set[str]:
    active = active_permission_ids()
    sets, commands = permission_index()
    active_permissions: set[str] = set()

    for identifier in active:
        if identifier in sets:
            active_permissions.update(sets[identifier])
        if identifier in commands:
            active_permissions.add(identifier)

    allowed: set[str] = set()
    for identifier in active_permissions:
        allowed.update(commands.get(identifier, set()))
    return allowed


def frontend_invokes() -> set[str]:
    commands: set[str] = set()
    patterns = [
        re.compile(r"\binvoke\(\s*[\"']([A-Za-z0-9_]+)[\"']"),
        re.compile(r"\btauriInvoke(?:<[^>]+>)?\(\s*[\"']([A-Za-z0-9_]+)[\"']"),
    ]
    for base in (ROOT / "src").rglob("*"):
        if base.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        text = base.read_text(encoding="utf-8")
        for pattern in patterns:
            commands.update(pattern.findall(text))
    return commands


def main() -> None:
    registered = registered_commands()
    allowed = allowed_commands()
    invoked = frontend_invokes()

    missing_acl = sorted(registered - allowed)
    if missing_acl:
        fail("registered commands missing active ACL permission: " + ", ".join(missing_acl))

    missing_registration = sorted(invoked - registered)
    if missing_registration:
        fail("frontend invokes missing Tauri registration: " + ", ".join(missing_registration))

    missing_frontend_acl = sorted(invoked - allowed)
    if missing_frontend_acl:
        fail("frontend invokes missing active ACL permission: " + ", ".join(missing_frontend_acl))

    stale_acl = sorted(allowed - registered)
    if stale_acl:
        fail("ACL permits commands not registered by Tauri: " + ", ".join(stale_acl))

    print(f"tauri-acl: {len(registered)} registered commands permitted; {len(invoked)} frontend invokes covered")


if __name__ == "__main__":
    main()
