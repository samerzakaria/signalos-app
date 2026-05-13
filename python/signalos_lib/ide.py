# SignalOS Core — IDE detection (AMD-CORE-022, W5.1).
#
# Detects the active IDE/agent host via environment variables only.
# pgrep is never called from Python — that is the shell dispatcher's fallback.
#
# Public API:
#   detect_ide() -> str
#       Returns one of: "claude-code", "cursor", "github-copilot", "vs-code",
#       "windsurf", "codex", "antigravity", or "" (headless / unknown).
#
# Priority order mirrors session-hook-dispatch.sh:
#   1. SIGNALOS_TOOL          — explicit override (set by install.sh --tool)
#   2. CLAUDE_CODE_SESSION_ID — claude-code (primary, W5.1)
#   3. CLAUDE_CODE            — claude-code (legacy, back-compat)
#   4. CURSOR_TRACE_ID        — cursor
#   5. GITHUB_COPILOT_SESSION — github-copilot
#   6. VSCODE_PID             — vs-code
#   7. WINDSURF_SESSION       — windsurf
#   8. CODEX_SESSION          — codex
#   9. ANTIGRAVITY_SESSION    — antigravity
#  10. (none)                 — "" (headless)
#
# Stdlib only — no third-party imports.

from __future__ import annotations

__all__ = ["detect_ide", "IDE_ENV_MAP"]

import os

# Ordered list of (env_var, ide_name) pairs checked in priority order.
# SIGNALOS_TOOL is handled separately (it's a direct override, not a detector).
IDE_ENV_MAP: list[tuple[str, str]] = [
    ("CLAUDE_CODE_SESSION_ID", "claude-code"),
    ("CLAUDE_CODE",            "claude-code"),
    ("CURSOR_TRACE_ID",        "cursor"),
    ("GITHUB_COPILOT_SESSION", "github-copilot"),
    ("VSCODE_PID",             "vs-code"),
    ("WINDSURF_SESSION",       "windsurf"),
    ("CODEX_SESSION",          "codex"),
    ("ANTIGRAVITY_SESSION",    "antigravity"),
]


def detect_ide() -> str:
    """Return the detected IDE name, or '' for headless/unknown.

    Checks environment variables only — no process inspection, no filesystem
    probing, no network calls.  The function is intentionally pure and
    side-effect-free so it can be called at import time or in tests without
    monkeypatching anything beyond os.environ.

    If SIGNALOS_TOOL is set, it is returned verbatim (mirrors the shell
    dispatcher's explicit-override behaviour).

    Returns
    -------
    str
        One of "claude-code", "cursor", "github-copilot", "vs-code",
        "windsurf", "codex", "antigravity", or "" (headless/unknown).
    """
    # Explicit override — highest priority, verbatim passthrough
    override = os.environ.get("SIGNALOS_TOOL", "").strip()
    if override:
        return override

    # Env-var detection in priority order
    for env_var, ide_name in IDE_ENV_MAP:
        if os.environ.get(env_var, "").strip():
            return ide_name

    return ""
