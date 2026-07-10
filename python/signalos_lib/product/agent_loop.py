# signalos_lib/product/agent_loop.py
# v4 Phase 2.4-2.11 — The governed agent loop.
#
# messages -> adapter.chat() -> tool_calls -> governance check -> execute
#          -> append tool results -> loop until end_turn or budget exhaustion.
#
# Architecture Decisions implemented here:
#   Q1  — uses harness.AgentProvider via ProviderAdapter.
#   Q2  — governance rules are read ONCE at loop start from an
#         EnforcementProvider (Rust is the authority; CI uses a test double).
#         Per-call checks hit the cached rules (fast, no IPC round-trip).
#         File writes ALSO go through a validate_workspace_write hook.
#   Q4  — the loop is stateless re: gates. It runs until end_turn or explicit
#         execution budget exhaustion, then returns control to the orchestrator.
#         It does NOT detect gate boundaries.
#   Q5a — idempotent tool execution via content_sha256.
#   INV-1 no silent skips; INV-4 no silent failures / no except: pass;
#   INV-5 persisted run state; INV-6 deterministic via AgentTestProvider;
#   INV-7 text-only degradation when supports_tool_calls is False.
#
# Tool execution (2.5): typed path allowlists per trust tier, command
# allowlist + denylist, timeouts (30s read, 120s command), cancellation,
# secret redaction on stdout/stderr, no governance-file edits.
#
# Audit ledger (2.6): every tool call (allowed or denied) -> tool-calls.jsonl.
# Security scan (2.9): security_gate.run on write_file content.

from __future__ import annotations

__all__ = [
    "AgentLoop",
    "LoopResult",
    "ToolDefinition",
    "AGENT_TOOLS",
    "ToolPolicyError",
    "build_tool_definitions",
]

import fnmatch
import hashlib
import importlib.util
import json
import logging
import os
import re
import shlex
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..harness import AgentResponse, ToolCall
from .enforcement_state import (
    EnforcementProvider,
    EnforcementState,
    StaticEnforcementProvider,
)
from .budgets import (
    DEFAULT_AGENT_LOOP_TOOL_CALL_BUDGET,
    resolve_agent_loop_tool_budget,
)
from .provider_adapter import ProviderAdapter
from .sandbox import SandboxRunner, select_runner

# ---------------------------------------------------------------------------
# Constants / timeouts
# ---------------------------------------------------------------------------

READ_TIMEOUT_S = 30
# 600s so the agent can `npm install` + build + test within a single tool call
# and iterate to green; 120s routinely killed a cold install mid-run, leaving the
# build unverifiable.
COMMAND_TIMEOUT_S = 600
DEFAULT_TOOL_CALL_BUDGET = DEFAULT_AGENT_LOOP_TOOL_CALL_BUDGET
MAX_READ_BYTES = 2_000_000  # guard against reading huge binaries into context

# Narration/truncation recovery bounds. A capable model that narrates a plan but
# emits NO tool call (observed: 14k-char prose, zero tool calls) would otherwise
# have its bare end_turn accepted as "completed" -- it writes nothing, the task
# deadlocks, and the run reads as success at trivial cost. Instead: re-prompt a
# work-expecting run (up to MAX_NO_TOOL_REPROMPTS) with a firm "perform the work
# now" nudge, escalating tool_choice to "required" on the reprompt turn; and when
# a turn is TRUNCATED (max_tokens), continue it (up to MAX_TRUNCATION_CONTINUES)
# rather than treating the cut-off as a finished turn.
MAX_NO_TOOL_REPROMPTS = 2
MAX_TRUNCATION_CONTINUES = 2
_NO_TOOL_NUDGE = (
    "You did not call any tool and no files have been written yet. Do NOT "
    "describe what you will do -- perform the work NOW by calling write_file / "
    "edit_file / run_command. Emit a tool call, not prose."
)
_CONTINUE_NUDGE = (
    "Your previous message was cut off before it finished (output token limit). "
    "Continue exactly where you stopped and, when ready, emit the tool call -- "
    "do not restart or re-summarize."
)

# Secret-redaction patterns applied to command stdout/stderr (2.5/2.9).
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                  # OpenAI-style
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),           # Anthropic-style
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),              # Google API key
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),                 # GitHub PAT
    re.compile(
        r"(?i)\b[a-z0-9_]*(?:api[_-]?key|secret(?:[_-]?key)?|token|"
        r"password|credential|passphrase)\b\s*[=:]\s*['\"]?[^'\"\s;]+"
    ),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

# Value-aware pattern for BLOCKING write content (distinct from the aggressive
# _SECRET_PATTERNS above, which are fine for redacting logs but false-positive
# when used to deny a write). This matches a secret-named variable assigned a
# QUOTED string LITERAL only -- so a real hardcoded key (`API_KEY = "sk_live_..."`)
# is caught while ordinary generated code that merely mentions the name is not:
#   const token = response.headers.get('x-request-id')   -> value is a call, not a quoted literal
#   let sessionToken: string | null = null               -> value is null
#   const authToken = await getToken()                   -> value is a call
#   password: userEnteredPassword                         -> value is an identifier
# The canonical redactor (redact.py _redact_string) already catches concrete
# token SHAPES (sk-ant-, ghp_, sk_live_, PEM, JWT); this adds the generic
# "<secret-name> = "<literal>"" case with a value/entropy heuristic.
# Letter-lookarounds (not \b): so the secret keyword also matches inside an
# underscore-separated identifier like STRIPE_SECRET_KEY / MY_API_KEY (where \b
# would fail because '_' is a word char), while still not matching a camelCase
# substring like the 'token' in sessionToken.
_WRITE_SECRET_VALUE_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:api[_-]?key|secret(?:[_-]?key)?|token|passwd|password|"
    r"credential|passphrase|access[_-]?key|private[_-]?key)(?![A-Za-z])\s*[=:]\s*"
    r"(['\"])([^'\"\s]{16,})\1"
)
_SECRET_PLACEHOLDER_PREFIXES = ("YOUR", "CHANGE", "EXAMPLE", "PLACEHOLDER", "XXX", "TODO", "DUMMY", "TEST", "FAKE", "SAMPLE")


def _looks_like_secret_literal(val: str) -> bool:
    """Value/entropy heuristic: a quoted literal is a plausible hardcoded secret
    if it mixes letters and digits, isn't a template/env reference, and isn't an
    obvious placeholder. Keeps the write-block from firing on prose or scaffold
    placeholders while still catching real keys."""
    if "${" in val or "process.env" in val or "import.meta" in val:
        return False
    if val.upper().startswith(_SECRET_PLACEHOLDER_PREFIXES):
        return False
    return any(c.isdigit() for c in val) and any(c.isalpha() for c in val)

_REDACTION = "[REDACTED]"
_REDACTOR_MODULE: Any | None = None


class ToolPolicyError(Exception):
    """A tool call was denied by governance. Carries a user-facing reason."""

    def __init__(self, reason: str, rule: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.rule = rule


# ---------------------------------------------------------------------------
# Tool definitions (2.5)
# ---------------------------------------------------------------------------


@dataclass
class ToolDefinition:
    """One provider-agnostic tool (OpenAI function-tool shape)."""

    name: str
    description: str
    parameters: dict[str, Any]

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def build_tool_definitions() -> list[ToolDefinition]:
    """The agent's tool set: read/write/edit files, run command, search."""
    return [
        ToolDefinition(
            name="read_file",
            description="Read a UTF-8 text file from the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path."}
                },
                "required": ["path"],
            },
        ),
        ToolDefinition(
            name="write_file",
            description="Create or overwrite a workspace file with new content.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
        ToolDefinition(
            name="edit_file",
            description="Replace an exact substring in an existing file once.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        ),
        ToolDefinition(
            name="run_command",
            description="Run a shell command from the workspace root (governed).",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        ),
        ToolDefinition(
            name="search_files",
            description="Glob-search the workspace for files matching a pattern.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob, e.g. **/*.tsx"},
                },
                "required": ["pattern"],
            },
        ),
        ToolDefinition(
            name="list_directory",
            description="List files and subdirectories of a workspace directory.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative directory; '' or '.' for root.",
                    }
                },
                "required": ["path"],
            },
        ),
    ]


AGENT_TOOLS: list[ToolDefinition] = build_tool_definitions()
_KNOWN_TOOL_NAMES: frozenset[str] = frozenset(t.name for t in AGENT_TOOLS)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool-call validity (Refinement 1) — reject empty/placeholder tool calls
# ---------------------------------------------------------------------------


def _tool_call_defect(tc: ToolCall) -> str | None:
    """Return a reason string when *tc* is an invalid / placeholder tool call
    that must NOT count as real work; None when the call is well-formed.

    Guards the required-escalation path: a provider forced to emit a tool call
    (tool_choice="required") can satisfy the constraint with empty/placeholder
    args (write_file with empty path/content) that would write garbage while
    looking like success. An unknown tool name is also invalid. Genuine JSON
    parse errors keep their existing dispatch/audit path (they are surfaced to
    the model as an explicit arg-parse error), so they are NOT flagged here.
    """
    name = tc.name
    if name not in _KNOWN_TOOL_NAMES:
        return f"unknown tool '{name}'"
    args = tc.arguments if isinstance(tc.arguments, dict) else {}
    if "__parse_error__" in args:
        return None  # handled by _dispatch_tool's arg-parse path, unchanged
    if name == "write_file":
        if not str(args.get("path", "")).strip():
            return "write_file with empty path"
        if not str(args.get("content", "")).strip():
            return "write_file with empty content"
    elif name == "edit_file":
        if not str(args.get("path", "")).strip():
            return "edit_file with empty path"
        if not str(args.get("old_string", "")).strip():
            return "edit_file with empty old_string"
    elif name == "run_command":
        if not str(args.get("command", "")).strip():
            return "run_command with empty command"
    elif name == "read_file":
        if not str(args.get("path", "")).strip():
            return "read_file with empty path"
    elif name == "search_files":
        if not str(args.get("pattern", "")).strip():
            return "search_files with empty pattern"
    # list_directory: '' / '.' is the workspace root -> always valid.
    return None


def _obj_get(obj: Any, key: str) -> Any:
    """getattr-or-dict-get, returning None on absence/error (diagnostic use)."""
    if obj is None:
        return None
    try:
        val = getattr(obj, key, None)
        if val is None and isinstance(obj, dict):
            val = obj.get(key)
        return val
    except Exception:
        return None


def _no_tool_diagnostic(resp: Any) -> dict[str, Any]:
    """Compact, defensive snapshot of a no-tool provider turn (Refinement 2).

    Leading hypothesis for the observed 14-16k-char no-tool narrations is a
    "reasoning-channel leak": the model's real output (or even a tool call)
    lands in message.reasoning / reasoning_content / reasoning_details, which
    the adapter does not read, so we see empty content + no tool_calls. This
    makes that OBSERVABLE (it does NOT extract tool calls from reasoning). Every
    field is best-effort: a provider (or the CI double) that omits them yields
    None/False, never a crash.
    """
    content = getattr(resp, "content", None) or ""
    diag: dict[str, Any] = {
        "content_len": len(content),
        "has_tool_calls": bool(getattr(resp, "tool_calls", None)),
        "stop_reason": getattr(resp, "stop_reason", None),
        "finish_reason": None,
        "native_finish_reason": None,
        "provider": None,
    }
    # Always present (default absent) so downstream consumers can rely on them
    # even when the provider/CI double supplies no raw payload.
    for fld in ("reasoning", "reasoning_content", "reasoning_details"):
        diag[f"{fld}_present"] = False
        diag[f"{fld}_len"] = 0
    raw = getattr(resp, "raw", None)
    if raw is None:
        return diag
    choices = _obj_get(raw, "choices") or []
    choice = choices[0] if choices else None
    msg = _obj_get(choice, "message")
    diag["finish_reason"] = _obj_get(choice, "finish_reason")
    diag["native_finish_reason"] = _obj_get(choice, "native_finish_reason")
    diag["provider"] = (
        _obj_get(raw, "provider")
        or _obj_get(raw, "served_by")
        or _obj_get(raw, "model")
    )
    for fld in ("reasoning", "reasoning_content", "reasoning_details"):
        val = _obj_get(msg, fld)
        present = val is not None and val != ""
        diag[f"{fld}_present"] = bool(present)
        try:
            diag[f"{fld}_len"] = len(val) if present else 0
        except Exception:
            diag[f"{fld}_len"] = None
    return diag


# ---------------------------------------------------------------------------
# Path / command policy helpers (2.5)
# ---------------------------------------------------------------------------


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


# ---------------------------------------------------------------------------
# FIX 1 — ONE canonical path pipeline (kills path-form whack-a-mole)
#
# The old policy layer did "OS-level sandboxing by regex": it string-matched
# command prefixes and path globs against whatever FORM the model happened to
# type. That endlessly false-denied, because a single filesystem location has
# many spellings on Windows + Git-Bash:
#   c:\ws\x   c:/ws/x   C:\WS\X   /c/ws/x   ws\..\ws\x   (and absolute vs rel)
# ~90% of observed denials were OUR bug (e.g. `cd /c/tmp/.../proj && npm test`
# where /c/tmp/.../proj IS the workspace root, denied as "outside workspace";
# an absolute write into core/execution/** denied because the allowlist matched
# RELATIVE globs). The fix: funnel EVERY containment + allowlist check through
# ONE normalization that collapses all those spellings to a single canonical
# absolute real-path, then derives a workspace-RELATIVE path for glob matching.
# ---------------------------------------------------------------------------

# Git-Bash / MSYS absolute form: `/c/foo` denotes drive C exactly like `c:\foo`.
# A SINGLE drive letter followed by a separator or end-of-string. A genuine
# POSIX root such as `/etc` is NOT this (two+ leading chars can't be a drive),
# so it is left untouched and still correctly reads as an escape on Windows.
_GITBASH_ABS_RE = re.compile(r"^/([A-Za-z])(?:/|$)")


def _degitbash(token: str) -> str:
    """Collapse a Git-Bash absolute path (`/c/ws`) to the Windows drive form
    (`c:/ws`) so it resolves to the SAME real path as `c:\\ws`. Without this,
    Path('/c/ws').resolve() anchors to the current drive as `C:\\c\\ws` -- the
    exact mis-resolution behind the `cd /c/tmp/... && npm test` false denial."""
    m = _GITBASH_ABS_RE.match(token)
    if not m:
        return token
    return f"{m.group(1)}:/" + token[m.end():]


def _canonical_abs(root: Path, candidate: str) -> Path | None:
    """Resolve *candidate* to ONE canonical absolute real-path (FIX 1).

    Single normalization used by every containment + allowlist check (writes
    AND commands). It:
      * strips surrounding quotes / whitespace,
      * expands a leading `~` (the shell WOULD, since run_command is shell=True,
        so an un-expanded `~/.ssh` must be treated as the home dir it becomes),
      * collapses the Git-Bash `/c/` vs `c:\\` duality (`_degitbash`),
      * treats `\\` and `/` interchangeably and lowercases the drive-letter
        difference implicitly (pathlib + case-insensitive WindowsPath compare),
      * resolves a relative candidate against *root*,
      * returns the resolved absolute Path.

    Returns None for a malformed / unresolvable candidate; every caller treats
    None as "deny / escape" (fail closed).
    """
    tok = candidate.strip().strip('"').strip("'")
    if not tok:
        return None
    if tok.startswith("~"):
        tok = os.path.expanduser(tok)
    else:
        tok = _degitbash(tok)
    try:
        p = Path(tok)
        combined = p if p.is_absolute() else (Path(root) / tok)
        return combined.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _workspace_relative(root: Path, candidate: str) -> str | None:
    """Canonical workspace-RELATIVE POSIX path for *candidate*, or None when it
    resolves OUTSIDE *root* (a true escape). The workspace root ITSELF maps to
    "" (empty relative) -- a `cd` to the workspace root must ALWAYS be allowed.

    This is the single funnel: an ABSOLUTE path into an allowlisted dir
    (`c:\\ws\\core\\execution\\X`) becomes the relative `core/execution/X`, so it
    matches the RELATIVE glob `core/execution/**`. Containment is decided here,
    not by string-matching a prefix.
    """
    target = _canonical_abs(root, candidate)
    if target is None:
        return None
    try:
        root_abs = Path(root).resolve()
    except (OSError, RuntimeError):
        root_abs = Path(root)
    # WindowsPath == / parents are case-insensitive; parts slicing is structural
    # (no re-comparison), so the drive-case and separator forms already agree.
    if target == root_abs:
        return ""
    if root_abs not in target.parents:
        return None
    return "/".join(target.parts[len(root_abs.parts):])


def _matches_glob(path: str, patterns: list[str]) -> bool:
    normed = _norm(path)
    for pat in patterns:
        p = _norm(pat)
        if p == "**":
            return True
        if fnmatch.fnmatch(normed, p):
            return True
        # directory-prefix globs: "src/**" should match "src/a/b.ts"
        if p.endswith("/**") or p.endswith("/*"):
            prefix = p.rsplit("/", 1)[0]
            if normed == prefix or normed.startswith(prefix + "/"):
                return True
    return False


def _matches_forbidden_path(path: str, forbidden: list[str]) -> bool:
    """Forbidden paths are TYPED (explicit), not globs — except *.pem/*.key."""
    normed = _norm(path)
    fname = normed.rsplit("/", 1)[-1]
    for pat in forbidden:
        p = _norm(pat)
        if p.endswith("/"):
            base = p.rstrip("/")
            if normed == base or normed.startswith(base + "/"):
                return True
        elif "*" in p:
            if fnmatch.fnmatch(normed, p) or fnmatch.fnmatch(fname, p):
                return True
        else:
            # exact path OR exact filename match (covers .env at any depth)
            if normed == p or fname == p or normed.endswith("/" + p):
                return True
    return False


def _is_plan_test_path(path: str) -> bool:
    """True for a plan-authored acceptance test (the signed spec). These live
    under the plan's test tree (core/execution/tests/**) and are read-only to
    the build agent -- see the 'spec-immutable' rule."""
    normed = _norm(path)
    return (normed.startswith("core/execution/tests/")
            and (".test." in normed or ".spec." in normed
                 or "/test_" in normed or normed.endswith("_test.py")))


def _is_governance_path(path: str) -> bool:
    """No direct governance-file edits (2.5): anything under .signalos/."""
    normed = _norm(path)
    return normed == ".signalos" or normed.startswith(".signalos/")


# WAVE-ENGINE-DESIGN §3.2 — the three canonical subtrees the gate-artifact
# manifest (gate_artifacts.json) addresses. For a NON-default project these
# rel_paths physically rebase under projects.project_governance_dir(...) at
# resolution time so artifact GENERATION lands exactly where the readers
# (sign.py / wave_engine.inspect / status) resolve them. Everything else —
# product source (src/**, tests/**, ...) and the whole default project — keeps
# the historical repo-root resolution byte-identical.
_GATE_ARTIFACT_SUBTREES: tuple[str, ...] = (
    "core/governance",
    "core/strategy",
    "core/execution",
)


def _is_gate_artifact_rel_path(path: str) -> bool:
    """True when *path* addresses a canonical gate-artifact subtree (§3.2).

    Only clean relative paths qualify: anything containing a ``..`` segment is
    NOT rebased and falls through to the ordinary workspace resolution, whose
    containment guard already handles escapes — the rebase can never be
    steered by traversal tricks.
    """
    normed = _norm(path)
    if any(part == ".." for part in normed.split("/")):
        return False
    return any(
        normed == subtree or normed.startswith(subtree + "/")
        for subtree in _GATE_ARTIFACT_SUBTREES
    )


def _is_test_path(path: str) -> bool:
    normed = _norm(path)
    name = normed.rsplit("/", 1)[-1].lower()
    return (
        normed.startswith("tests/")
        or ".test." in name
        or ".spec." in name
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _is_implementation_path(path: str) -> bool:
    normed = _norm(path)
    if _is_test_path(normed):
        return False
    if normed.startswith(("src/", "public/", "app/", "pages/", "components/")):
        return True
    if normed in ("index.html", "package.json", "tsconfig.json", "vite.config.ts"):
        return True
    return bool(re.search(r"\.(tsx?|jsx?|html|css|scss|py|rs|go|java|cs)$", normed))


def _required_prior_gate_numbers(gate: str | None) -> set[int]:
    if gate != "G4":
        return set()
    return {0, 1, 2, 3}


# Per-stream cap on command output returned INTO the conversation. Tool-call
# budgets bound the NUMBER of calls, not their SIZE -- one un-capped test-runner
# dump (50-100k chars of failures + stack traces) repeated a few times blew a
# provider's context ceiling mid-build (observed at 111k/163k tokens). Head+tail
# truncation keeps both the leading error summary and the trailing totals.
COMMAND_OUTPUT_CAP = 10_000
_CAP_HEAD = 3_000


def _cap_command_output(text: str) -> str:
    if len(text) <= COMMAND_OUTPUT_CAP:
        return text
    dropped = len(text) - COMMAND_OUTPUT_CAP
    tail = COMMAND_OUTPUT_CAP - _CAP_HEAD
    return (text[:_CAP_HEAD]
            + f"\n... [{dropped} chars truncated -- output capped; re-run a "
              "NARROWER command (e.g. a single test file) for full detail] ...\n"
            + text[-tail:])


def _command_root(command: str) -> str:
    """Return a normalized leading token-pair for allowlist matching."""
    try:
        toks = shlex.split(command)
    except ValueError:
        toks = command.split()
    if not toks:
        return ""
    # Match up to two leading tokens (e.g. "npm test", "git status").
    return " ".join(toks[:2]).strip()


# Shell separators that chain independent commands. A compound command
# (`cd frontend && npm test`) is allowed only when EVERY segment is itself
# allowed -- the old first-two-token check denied the whole thing because
# "cd frontend" matched nothing (observed: ~25 false denials tanking a model's
# governance score), and `npm run build && npm test` passed only by accident.
_COMMAND_SEPARATORS = re.compile(r"&&|\|\||;")


def _split_command_segments(command: str) -> list[str]:
    """Split *command* on shell separators (&&, ||, ;) into stripped,
    non-empty segments. Empty segments (e.g. a trailing `;`) are dropped."""
    return [seg.strip() for seg in _COMMAND_SEPARATORS.split(command) if seg.strip()]


def _cd_target(segment: str) -> str | None:
    """Return the target of a bare `cd <path>` segment, or None if the segment
    is not a `cd`. A bare `cd` with no argument returns "" (home; harmless)."""
    try:
        toks = shlex.split(segment, posix=False)
    except ValueError:
        toks = segment.split()
    if not toks or toks[0] != "cd":
        return None
    if len(toks) == 1:
        return ""
    return toks[1].strip().strip('"').strip("'")


# FIX 2 — jail cwd to the workspace root. A leading `cd <target>` in a compound
# command is peeled off and turned into the child process's cwd, so the model
# never NEEDS an absolute `cd <root> && x`: `cd frontend && npm test` runs with
# cwd=<root>/frontend and command="npm test". This is shell-agnostic (cmd.exe
# won't `cd /c/ws/...`, Git-Bash will -- peeling sidesteps that entirely) and
# keeps the compound-segment governance validation on the ORIGINAL command.
_LEADING_CD_RE = re.compile(
    r"""^\s*cd\s+(?P<target>'[^']*'|"[^"]*"|\S+)
        \s*(?:(?:&&|;|\|\|)\s*(?P<rest>.+))?\s*$""",
    re.VERBOSE | re.DOTALL,
)


def _peel_leading_cd(command: str) -> tuple[str | None, str]:
    """If *command* starts with `cd <target>` (optionally `&& <rest>`), return
    (target, rest); otherwise (None, command). `rest` is "" for a bare
    `cd <target>` with nothing after it."""
    m = _LEADING_CD_RE.match(command)
    if not m:
        return None, command
    target = m.group("target").strip().strip('"').strip("'")
    rest = (m.group("rest") or "").strip()
    return target, rest


# FIX 3 — a principled VERIFICATION command CLASS, instead of enumerating one
# command at a time in the trust-tier config (the whack-a-mole we are killing).
# These are read-only / analysis / integrity commands the build gate routinely
# tells the agent to run (observed gaps that got false-denied: `node -e`,
# `sha256sum`). They are permitted IN ADDITION to the trust-tier execute
# allowlist. Two notes on why this is safe:
#   * Path containment still holds: cwd is jailed to the workspace (FIX 2) and
#     any path ARGUMENT that escapes is still rejected by _command_escapes_
#     workspace (FIX 1). So a verification command cannot reach outside the repo
#     by a path token.
#   * Arbitrary-code evaluators (`node -e`, `python -c`) are in the SAME threat
#     class the policy ALREADY accepts via `npm test` / `pytest` / `node --test`
#     (a test file runs arbitrary project code). They are not a new capability;
#     the real containment boundary for what such code does at runtime is a
#     sandbox (container/WSL) -- see the report. Genuinely destructive/exfil
#     verbs (rm -rf, git push --force, ...) remain on the always-forbidden
#     denylist and are checked BEFORE this class.
# Matched on the first token OR the first two tokens, so both `sha256sum FILE`
# (single verb) and `node -e '...'` (verb + subcommand/flag) are recognized.
_VERIFICATION_COMMANDS: frozenset[str] = frozenset({
    # JS/TS one-off eval + syntax/type checks (no watch, no serve).
    "node -e", "node -p", "node --eval", "node --print",
    "node --check", "node --test",
    "tsc", "npx tsc", "npx vitest", "npx vite",
    # Python one-off eval + byte-compile / syntax check.
    "python -c", "python -m py_compile", "python -m compileall",
    "python3 -c", "python3 -m py_compile", "python3 -m compileall",
    # Integrity / hashing (BUILD_EVIDENCE checksums were denied as a gap).
    "sha256sum", "sha1sum", "sha512sum", "md5sum", "shasum", "cksum", "b2sum",
    # Read-only inspection idioms.
    "cat", "head", "tail", "wc", "ls", "dir", "pwd", "echo", "printf",
    "type", "find", "grep", "rg", "stat", "file", "true", "false", "which",
    # Read-only VCS queries (mutating subcommands are deliberately excluded).
    "git status", "git diff", "git log", "git rev-parse", "git show",
    "git branch", "git ls-files",
})


def _is_verification_command(segment: str) -> bool:
    """True when *segment*'s leading verb is in the read-only verification class
    (FIX 3). Recognizes the single-verb (`sha256sum`), verb+flag (`node -e`),
    and verb+module (`python -m compileall`) forms by matching the first 1, 2,
    or 3 leading tokens against the class."""
    seg = segment.strip()
    if not seg:
        return False
    try:
        toks = shlex.split(seg, posix=False)
    except ValueError:
        toks = seg.split()
    if not toks:
        return False
    return any(
        " ".join(toks[:n]) in _VERIFICATION_COMMANDS
        for n in (1, 2, 3)
    )


def _segment_matches(segment: str, patterns: list[str]) -> bool:
    """True when a single (non-compound) command segment matches the allowlist.
    This is the historical single-command matching logic, plus the FIX-3
    verification class (a principled read-only set permitted on top of the
    trust-tier allowlist so we stop growing the list one command at a time)."""
    seg = segment.strip()
    if not seg:
        return True
    root = _command_root(seg)
    for pat in patterns:
        p = pat.strip()
        if p == "**":
            return True
        if seg == p or seg.startswith(p + " ") or root == p:
            return True
    return _is_verification_command(seg)


def _command_matches(
    command: str, patterns: list[str], repo_root: Path | None = None
) -> bool:
    """True when EVERY segment of *command* is allowed.

    A compound command is split on &&/||/; and each segment must independently
    match an allowlisted pattern. A `cd <path>` segment is permitted as long as
    <path> stays inside the workspace -- validated with the same
    `_command_escapes_workspace` containment guard the caller applies to the
    whole command; a `cd` that escapes fails the match (and is independently
    rejected upstream). A non-compound command behaves exactly as before.
    """
    segments = _split_command_segments(command.strip())
    if not segments:
        return False
    for seg in segments:
        target = _cd_target(seg)
        if target is not None:
            # A `cd` inside the workspace is fine; an escaping `cd` is not.
            if (repo_root is not None and target
                    and _command_escapes_workspace(seg, repo_root)):
                return False
            continue
        if not _segment_matches(seg, patterns):
            return False
    return True


def _command_denied(command: str, denylist: list[str]) -> str | None:
    """Return the matched denylist entry if *command* hits the denylist."""
    cmd = command.strip().lower()
    for entry in denylist:
        e = entry.strip().lower()
        if e and e in cmd:
            return entry
    return None


def _command_escapes_workspace(command: str, repo_root: Path) -> str | None:
    """Return the first command token that names a filesystem path OUTSIDE
    *repo_root*, or None when every path stays within the workspace.

    run_command executes with shell=True and cwd=repo_root and allows read
    utilities (cat/type/ls/head/tail/npx vitest/npx tsc ...). Without this guard
    a model could read files outside the repo -- e.g. `cat ../../gold/x`,
    `type ..\\..\\gold\\x`, `ls c:/tmp/prove-a`, or
    `npx vitest run ../../gold/x.test.ts` -- and exfiltrate a hidden gold test
    suite stored outside the workspace.

    To minimize false positives only genuine path tokens are inspected: flags
    (leading '-') are skipped, and a bare word with no separator, that is not
    absolute and has no '..' segment (npx, vitest, run, cat, src, *.ts globs) is
    left alone. Candidates run through the ONE canonical pipeline (FIX 1), which
    collapses the Git-Bash `/c/`, drive-case, and mixed-separator spellings to a
    single real-path before deciding containment -- so `cd /c/ws/... && x` where
    /c/ws/... IS the workspace root is correctly recognized as in-workspace
    (the previous version mis-resolved it to C:\\c\\ws\\... and false-denied).
    """
    if not command or not command.strip():
        return None
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        tokens = command.split()
    for raw in tokens:
        token = raw.strip().strip('"').strip("'")
        if not token or token.startswith("-"):
            continue
        has_sep = "/" in token or "\\" in token
        is_abs = os.path.isabs(token)
        has_dotdot = ".." in re.split(r"[\\/]", token)
        is_home = token.startswith("~")
        # Bare words / globs without a separator-escape are always in-workspace.
        if not (has_sep or is_abs or has_dotdot or is_home):
            continue
        # Canonical containment: None means it resolves outside the workspace
        # (or is unresolvable -> fail closed) and is reported as escaping.
        if _workspace_relative(repo_root, token) is None:
            return token
    return None


def redact_secrets(text: str) -> str:
    """Redact secret-looking substrings from command output (2.5/2.9)."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(_REDACTION, out)
    return out


def _resolve_redactor_path() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        frozen = (
            Path(sys._MEIPASS)
            / "signalos_lib"
            / "_bundle"
            / "core"
            / "execution"
            / "hooks"
            / "_lib"
            / "redact.py"
        )
        if frozen.is_file():
            return frozen
    return (
        Path(__file__).resolve().parent.parent
        / "_bundle"
        / "core"
        / "execution"
        / "hooks"
        / "_lib"
        / "redact.py"
    )


def _load_redactor_module() -> Any:
    global _REDACTOR_MODULE
    if _REDACTOR_MODULE is not None:
        return _REDACTOR_MODULE

    path = _resolve_redactor_path()
    spec = importlib.util.spec_from_file_location(
        "signalos_runtime_redactor",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load redactor module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _REDACTOR_MODULE = module
    return module


def _scan_write_secrets(rel_path: str, content: str) -> list[str]:
    """Mirror redact.py --scan-diff for to-be-written content."""
    redactor = _load_redactor_module()
    findings: list[str] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        _redacted, rules = redactor._redact_string(line)
        if rules:
            findings.append(
                f"{rel_path}:{lineno}: redaction rule(s) {rules} "
                "matched generated write content"
            )
            continue
        m = _WRITE_SECRET_VALUE_RE.search(line)
        if m and _looks_like_secret_literal(m.group(2)):
            findings.append(
                f"{rel_path}:{lineno}: hardcoded secret literal assigned to a "
                "secret-named variable in generated write content"
            )
    return findings


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Loop result
# ---------------------------------------------------------------------------


@dataclass
class LoopResult:
    """Outcome of one agent-loop run, returned to the orchestrator (Q4)."""

    run_id: str
    # "completed" | "budget_exhausted" | "text_only" | "cancelled" | "error"
    # | "stalled_no_tool" (gave up after reprompts with no tool work)
    # | "max_tokens" (truncated and could not finish within the continue budget)
    status: str
    final_text: str | None
    tool_calls_made: int
    messages: list[dict[str, Any]]
    error: str | None = None
    text_only: bool = False
    # True when the run produced NO landed write (write_file/edit_file) -- lets a
    # caller distinguish a "narrated, wrote nothing" run from real work even when
    # status is "completed" (stamped by _finalize from _wrote_file_this_run).
    wrote_no_files: bool = False
    # Token accounting summed across every provider turn in this run (None when
    # the provider reports no usage). Feeds cost tracking (commands/cost.py) and
    # the per-model 360 comparison.
    tokens_in: int | None = None
    tokens_out: int | None = None
    # Provider turn errors observed during this run (finish_reason='error'
    # turns a provider normalized away): transport noise vs model weakness.
    provider_turn_errors: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "final_text": self.final_text,
            "tool_calls_made": self.tool_calls_made,
            "error": self.error,
            "text_only": self.text_only,
            "wrote_no_files": self.wrote_no_files,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "provider_turn_errors": self.provider_turn_errors,
        }


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------


class AgentLoop:
    """The governed agent runtime (Q2/Q4).

    Construct with a ProviderAdapter, a repo root, and an EnforcementProvider.
    `run()` executes one bounded conversation and returns a LoopResult. The
    loop reads the enforcement state ONCE at the start of run() and caches it.

    Cancellation (2.5): pass a `cancel_check` callable returning True to abort
    between tool calls; the loop stops and persists state.
    """

    def __init__(
        self,
        adapter: ProviderAdapter,
        repo_root: Path,
        enforcement_provider: EnforcementProvider | None = None,
        run_id: str | None = None,
        tool_call_limit: int | None = None,
        cancel_check: Callable[[], bool] | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
        execution_context: str = "delivery",
        active_gate: str | None = None,
        signed_gates: list[int] | None = None,
        project_id: str = "default",
    ) -> None:
        self.adapter = adapter
        self.repo_root = Path(repo_root)
        self.enforcement_provider = enforcement_provider or StaticEnforcementProvider()
        self.run_id = run_id or f"agent-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        self.tool_call_limit = resolve_agent_loop_tool_budget(tool_call_limit)
        self._cancel_check = cancel_check or (lambda: False)
        self._emit = emit or (lambda _e: None)
        self._enforcement: EnforcementState | None = None
        self.execution_context = execution_context
        self.active_gate = active_gate
        # §3.2: the delivery's project namespace. Rebases the PHYSICAL target
        # of gate-artifact rel_paths (see _artifact_base); governance/policy
        # checks keep operating on the canonical rel_path the model addressed.
        self.project_id = project_id
        self._context_signed_gates = set(signed_gates or [])
        self._test_written_this_run = False
        # Whether ANY write (write_file/edit_file) actually LANDED this run.
        # Feeds LoopResult.wrote_no_files so a narration-only run is honest.
        self._wrote_file_this_run = False
        self._seq = 0
        # Token accounting summed across every provider turn (None until the
        # provider reports usage). Stamped onto the LoopResult by _finalize.
        self._tokens_in: int | None = None
        self._tokens_out: int | None = None
        try:
            from .provider_adapter import turn_error_count
            self._turn_errors_at_start = turn_error_count()
        except Exception:
            self._turn_errors_at_start = None

    def _track_usage(self, resp: Any) -> None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        ti = getattr(usage, "input_tokens", None)
        to = getattr(usage, "output_tokens", None)
        if ti is not None:
            self._tokens_in = (self._tokens_in or 0) + int(ti)
        if to is not None:
            self._tokens_out = (self._tokens_out or 0) + int(to)

    def _finalize(self, result: "LoopResult") -> "LoopResult":
        result.tokens_in = self._tokens_in
        result.tokens_out = self._tokens_out
        result.wrote_no_files = not self._wrote_file_this_run
        if self._turn_errors_at_start is not None:
            try:
                from .provider_adapter import turn_error_count
                result.provider_turn_errors = (
                    turn_error_count() - self._turn_errors_at_start)
            except Exception:
                pass
        return result

    # --- run-state paths (INV-5) --------------------------------------------

    @property
    def run_dir(self) -> Path:
        return self.repo_root / ".signalos" / "agent-runs" / self.run_id

    @property
    def ledger_path(self) -> Path:
        return self.run_dir / "tool-calls.jsonl"

    @property
    def state_path(self) -> Path:
        return self.run_dir / "state.json"

    @property
    def conversation_path(self) -> Path:
        return self.run_dir / "conversation.jsonl"

    def _ensure_run_dir(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def _load_enforcement(self) -> str | None:
        """Load and cache enforcement once per run/resume.

        Returns a user-visible error string on failure. Keeping this in one
        place prevents resume from using a weaker policy path than run().
        """
        try:
            self._enforcement = self.enforcement_provider.get_enforcement_state(
                self.repo_root
            )
        except Exception as exc:  # INV-4: surface, do not swallow
            err = f"Failed to load enforcement state: {exc}"
            self._emit({"type": "error", "error": err})
            return err
        return None

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            raise FileNotFoundError(f"no persisted state for run {self.run_id}")
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"agent run state unreadable: {exc}") from exc
        if not isinstance(state, dict):
            raise RuntimeError("agent run state must be a JSON object")
        return state

    def _load_conversation(self) -> list[dict[str, Any]]:
        if not self.conversation_path.is_file():
            raise FileNotFoundError(
                f"no persisted conversation for run {self.run_id}"
            )
        messages: list[dict[str, Any]] = []
        try:
            for raw in self.conversation_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    messages.append(item)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"agent conversation unreadable: {exc}") from exc
        if not messages:
            raise RuntimeError("agent conversation is empty")
        return messages

    # --- main entry ----------------------------------------------------------

    def run(
        self,
        system_prompt: str,
        user_message: str,
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> LoopResult:
        """Run one bounded agent conversation and return control (Q4).

        The loop does NOT detect gates. It runs until end_turn or the explicit
        execution budget is exhausted, then hands back to the orchestrator.
        """
        self._ensure_run_dir()
        # Q2: read governance rules ONCE, cache for this run.
        enforcement_error = self._load_enforcement()
        if enforcement_error:
            return LoopResult(
                run_id=self.run_id,
                status="error",
                final_text=None,
                tool_calls_made=0,
                messages=[],
                error=enforcement_error,
            )

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if prior_messages:
            messages.extend(prior_messages)
        messages.append({"role": "user", "content": user_message})

        # INV-7: text-only degradation when the provider cannot do tools.
        if not self.adapter.supports_tool_calls:
            return self._finalize(self._run_text_only(messages))

        return self._finalize(self._run_tool_loop(messages, tool_calls_made=0))

    def resume(self) -> LoopResult:
        """Resume a persisted agent conversation without adding a new prompt.

        This is the durable P3 resume path: state.json provides the prior tool
        count and conversation.jsonl provides the provider context. Terminal
        runs are returned as-is. Non-terminal runs continue through the same
        governed tool loop as a fresh run.
        """
        self._ensure_run_dir()
        try:
            state = self._load_state()
            messages = self._load_conversation()
        except Exception as exc:
            err = str(exc)
            self._emit({"type": "error", "error": err})
            return LoopResult(
                run_id=self.run_id,
                status="error",
                final_text=None,
                tool_calls_made=0,
                messages=[],
                error=err,
            )

        tool_calls_made = int(state.get("tool_calls_made") or 0)
        status = str(state.get("status") or "running")
        if status in {"completed", "text_only", "cancelled"}:
            return LoopResult(
                run_id=self.run_id,
                status=status,
                final_text=None,
                tool_calls_made=tool_calls_made,
                messages=messages,
                text_only=status == "text_only",
                error="cancelled by user" if status == "cancelled" else None,
            )

        enforcement_error = self._load_enforcement()
        if enforcement_error:
            return LoopResult(
                run_id=self.run_id,
                status="error",
                final_text=None,
                tool_calls_made=tool_calls_made,
                messages=messages,
                error=enforcement_error,
            )

        if not self.adapter.supports_tool_calls:
            return self._finalize(self._run_text_only(messages))

        return self._finalize(
            self._run_tool_loop(messages, tool_calls_made=tool_calls_made))

    def _run_tool_loop(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_calls_made: int,
    ) -> LoopResult:
        tools = [t.as_openai_tool() for t in AGENT_TOOLS]
        final_text: str | None = None
        # Narration/truncation recovery counters (see MAX_* constants).
        reprompts_used = 0
        continues_used = 0
        escalate_next = False  # request tool_choice="required" next turn

        self._persist_state(messages, tool_calls_made, status="running")

        while True:
            if self._cancel_check():
                self._persist_state(messages, tool_calls_made, status="cancelled")
                self._emit({"type": "cancelled", "run_id": self.run_id})
                return LoopResult(
                    run_id=self.run_id,
                    status="cancelled",
                    final_text=final_text,
                    tool_calls_made=tool_calls_made,
                    messages=messages,
                    error="cancelled by user",
                )

            if tool_calls_made >= self.tool_call_limit:
                return self._budget_exhausted_result(
                    messages,
                    tool_calls_made,
                    final_text,
                )

            escalate = escalate_next
            escalate_next = False
            resp: AgentResponse | None = None
            if escalate:
                # Reprompt turn: force a tool call if the provider supports it.
                # GUARD: some providers reject tool_choice="required"; never let
                # the escalation crash the run -- fall back to the default "auto"
                # plus the firm text nudge already sitting in the transcript.
                try:
                    resp = self.adapter.chat(
                        messages=[dict(m) for m in messages],
                        tools=tools,
                        tool_choice="required",
                    )
                    self._track_usage(resp)
                except Exception:
                    self._emit(
                        {"type": "tool_choice_fallback", "run_id": self.run_id})
                    resp = None
            if resp is None:
                try:
                    resp = self.adapter.chat(
                        messages=[dict(m) for m in messages],
                        tools=tools,
                    )
                    self._track_usage(resp)
                except Exception as exc:  # INV-4
                    err = f"Provider call failed: {exc}"
                    self._emit({"type": "error", "error": err})
                    # 1.10: also surface a plain-words incident card with recovery
                    # options -- a founder should never see a bare error string.
                    try:
                        from .provider_adapter import classify_error_scenario
                        from .incidents import build_incident_card
                        scenario = classify_error_scenario(exc) or "unclassified-provider-error"
                        self._emit(build_incident_card(scenario, detail=str(exc)).to_dict())
                    except Exception:
                        pass
                    self._persist_state(messages, tool_calls_made, status="error")
                    return LoopResult(
                        run_id=self.run_id,
                        status="error",
                        final_text=final_text,
                        tool_calls_made=tool_calls_made,
                        messages=messages,
                        error=err,
                    )

            if resp.content:
                final_text = resp.content
                self._emit({"type": "text", "text": resp.content})

            # TRUNCATION (max_tokens) is NOT a finished turn: the model was cut
            # off mid-thought / before its tool call. Continue it (bounded) so it
            # can finish, rather than accepting the cut-off as "completed".
            if resp.stop_reason == "max_tokens":
                messages.append({"role": "assistant", "content": resp.content or ""})
                if continues_used < MAX_TRUNCATION_CONTINUES:
                    continues_used += 1
                    messages.append({"role": "user", "content": _CONTINUE_NUDGE})
                    self._emit({"type": "truncated_continue",
                                "run_id": self.run_id, "attempt": continues_used})
                    self._persist_state(messages, tool_calls_made, status="running")
                    continue
                # Out of continue budget -> hand back honestly as truncated.
                self._persist_state(messages, tool_calls_made, status="max_tokens")
                self._emit({"type": "max_tokens", "run_id": self.run_id})
                return LoopResult(
                    run_id=self.run_id,
                    status="max_tokens",
                    final_text=final_text,
                    tool_calls_made=tool_calls_made,
                    messages=messages,
                    text_only=tool_calls_made == 0,
                    error="model response was truncated (max_tokens) and did not "
                          "finish within the continue budget",
                )

            # Refinement 1: split tool calls into well-formed vs invalid /
            # placeholder (empty write/edit args, unknown tool). A provider
            # forced by tool_choice="required" may emit a placeholder just to
            # satisfy the constraint -- never execute that garbage, and if the
            # turn has NO well-formed call, treat it as a no-tool turn below.
            valid_tool_calls: list[ToolCall] = []
            rejected: list[tuple[ToolCall, str]] = []
            for tc in (resp.tool_calls or []):
                defect = _tool_call_defect(tc)
                if defect is None:
                    valid_tool_calls.append(tc)
                else:
                    rejected.append((tc, defect))
            for tc, why in rejected:
                self._emit({"type": "tool_call_rejected", "run_id": self.run_id,
                            "tool": tc.name, "reason": why})

            has_real_tool_call = (
                resp.stop_reason == "tool_use" and bool(valid_tool_calls))

            if not has_real_tool_call:
                # No well-formed tool call this turn (bare narration, or only
                # placeholder/invalid calls). Record the narration either way.
                messages.append({"role": "assistant", "content": resp.content or ""})
                work_expecting = self.execution_context != "conversation"
                did_tool_work = tool_calls_made > 0
                if work_expecting:
                    # Refinement 2: log/emit a compact diagnostic of the raw
                    # provider turn so a no-tool narration (suspected reasoning-
                    # channel leak) is diagnosable. Logging only; never crashes.
                    self._emit_no_tool_diagnostic(resp, rejected)
                # "Narrated a plan, wrote nothing": in a work-expecting run where
                # the model has done NO tool work yet, re-prompt (bounded) to
                # force real action instead of accepting the bare turn as done.
                if (work_expecting and not did_tool_work
                        and reprompts_used < MAX_NO_TOOL_REPROMPTS):
                    reprompts_used += 1
                    messages.append({"role": "user", "content": _NO_TOOL_NUDGE})
                    self._emit({"type": "reprompt", "run_id": self.run_id,
                                "reason": "no_tool_call", "attempt": reprompts_used})
                    self._persist_state(messages, tool_calls_made, status="running")
                    escalate_next = True
                    continue
                if work_expecting and not did_tool_work:
                    # Gave up after the reprompt budget with zero tool work: this
                    # is NOT a success -- do not report "completed".
                    self._persist_state(
                        messages, tool_calls_made, status="stalled_no_tool")
                    self._emit({"type": "stalled_no_tool", "run_id": self.run_id})
                    return LoopResult(
                        run_id=self.run_id,
                        status="stalled_no_tool",
                        final_text=final_text,
                        tool_calls_made=tool_calls_made,
                        messages=messages,
                        text_only=True,
                        error="model narrated but never called a tool; no files "
                              "were written",
                    )
                # Legitimate end_turn (conversation Q&A, or the model already did
                # tool work and is signing off) — hand back (Q4/2.7).
                self._persist_state(messages, tool_calls_made, status="completed")
                self._emit({"type": "end_turn", "run_id": self.run_id})
                return LoopResult(
                    run_id=self.run_id,
                    status="completed",
                    final_text=final_text,
                    tool_calls_made=tool_calls_made,
                    messages=messages,
                )

            # Record the assistant turn carrying the WELL-FORMED tool calls
            # (placeholder/invalid calls are dropped -- never executed).
            messages.append(self._assistant_tool_msg(resp.content, valid_tool_calls))

            for tc in valid_tool_calls:
                if tool_calls_made >= self.tool_call_limit:
                    return self._budget_exhausted_result(
                        messages,
                        tool_calls_made,
                        final_text=final_text,
                    )

                tool_calls_made += 1
                result_text = self._dispatch_tool(tc)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result_text,
                    }
                )

            self._persist_state(messages, tool_calls_made, status="running")

    def _emit_no_tool_diagnostic(
        self, resp: Any, rejected: list[tuple[ToolCall, str]]
    ) -> None:
        """Emit + log a compact diagnostic of a no-tool provider turn
        (Refinement 2). Best-effort: diagnostics must never affect the run."""
        try:
            diag = _no_tool_diagnostic(resp)
            diag["type"] = "no_tool_diagnostic"
            diag["run_id"] = self.run_id
            if rejected:
                diag["rejected_tool_calls"] = [
                    {"tool": tc.name, "reason": why} for tc, why in rejected]
            self._emit(diag)
            _LOGGER.info("agent-loop no-tool turn diagnostic: %s", diag)
        except Exception:  # pragma: no cover - diagnostics never break the run
            pass

    def _budget_exhausted_result(
        self,
        messages: list[dict[str, Any]],
        tool_calls_made: int,
        final_text: str | None,
    ) -> LoopResult:
        self._persist_state(
            messages,
            tool_calls_made,
            status="budget_exhausted",
        )
        self._emit(
            {
                "type": "budget_exhausted",
                "tool_call_budget": self.tool_call_limit,
            }
        )
        return LoopResult(
            run_id=self.run_id,
            status="budget_exhausted",
            final_text=final_text,
            tool_calls_made=tool_calls_made,
            messages=messages,
            error=(
                "agent loop tool-call budget "
                f"({self.tool_call_limit}) exhausted"
            ),
        )

    # --- text-only mode (INV-7 / 2.11) --------------------------------------

    def _run_text_only(self, messages: list[dict[str, Any]]) -> LoopResult:
        try:
            resp = self.adapter.chat(messages=messages, tools=None)
            self._track_usage(resp)
        except Exception as exc:  # INV-4
            err = f"Provider call failed (text-only): {exc}"
            self._emit({"type": "error", "error": err})
            return LoopResult(
                run_id=self.run_id,
                status="error",
                final_text=None,
                tool_calls_made=0,
                messages=messages,
                error=err,
                text_only=True,
            )
        text = resp.content or ""
        messages.append({"role": "assistant", "content": text})
        self._persist_state(messages, 0, status="text_only")
        self._emit({"type": "text", "text": text})
        self._emit({"type": "text_only", "run_id": self.run_id})
        return LoopResult(
            run_id=self.run_id,
            status="text_only",
            final_text=text,
            tool_calls_made=0,
            messages=messages,
            text_only=True,
        )

    # --- tool dispatch + governance (2.5/2.6/2.9) ---------------------------

    def _dispatch_tool(self, tc: ToolCall) -> str:
        """Govern, execute, audit one tool call. Always returns result text."""
        t0 = time.perf_counter()
        args = tc.arguments or {}
        # Cross-provider robustness (defense in depth behind the adapter's own
        # normalization): tool-call arguments must be a dict, but a provider may
        # hand back a raw JSON string. Everything downstream (governance check,
        # _redact_args) assumes a dict, so normalize here too. Unparseable args
        # become an explicit parse error the model is told to fix, never a hard
        # crash.
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except (ValueError, TypeError):
                args = {"__parse_error__": f"tool arguments were not valid JSON: {args[:200]}"}
        if not isinstance(args, dict):
            args = {"__parse_error__": f"tool arguments must be a JSON object, got {type(args).__name__}"}
        content_for_hash = self._content_for_hash(tc.name, args)
        content_sha = _sha256(content_for_hash) if content_for_hash is not None else None

        if "__parse_error__" in args:
            reason = str(args["__parse_error__"])
            self._audit(tc, "denied", reason, t0, content_sha, rule="arg-parse")
            return f"ERROR: {reason}"

        try:
            self._check_governance(tc.name, args)
        except ToolPolicyError as exc:
            self._audit(tc, "denied", exc.reason, t0, content_sha, rule=exc.rule)
            self._emit(
                {"type": "tool_denied", "tool": tc.name, "reason": exc.reason}
            )
            # INV-4: the denial is surfaced to the model AND the user.
            return f"DENIED: {exc.reason}"

        # Idempotency (Q5a): a write whose target already matches the hash is
        # a no-op on resume.
        idempotent = self._idempotent_skip(tc.name, args, content_sha)
        if idempotent:
            self._audit(
                tc, "skipped-idempotent", "content already present", t0, content_sha
            )
            self._emit({"type": "tool_done", "tool": tc.name, "idempotent": True})
            return f"OK (idempotent): {args.get('path', '')} already has this content."

        try:
            result_text = self._execute_tool(tc.name, args)
        except ToolPolicyError as exc:
            self._audit(tc, "denied", exc.reason, t0, content_sha, rule=exc.rule)
            self._emit(
                {"type": "tool_denied", "tool": tc.name, "reason": exc.reason}
            )
            return f"DENIED: {exc.reason}"
        except Exception as exc:  # INV-4: never except: pass
            reason = f"{type(exc).__name__}: {exc}"
            self._audit(tc, "error", reason, t0, content_sha)
            self._emit({"type": "tool_error", "tool": tc.name, "error": reason})
            return f"ERROR: {reason}"

        # audit-append (#16 Edit 2.6): the completed-audit append is a hard
        # post-condition, not best-effort. If it fails while the rule is enabled
        # (audit-append is a core invariant, so always enabled unless overridden),
        # the write must NOT be surfaced as success — otherwise an un-audited
        # write would look "done". Convert it to a hard ERROR.
        try:
            self._audit(tc, "completed", "ok", t0, content_sha)
        except Exception as exc:  # INV-4: never swallow
            enf = self._enforcement
            if enf is not None and enf.rule_enabled("audit-append"):
                reason = f"audit-append failed: {type(exc).__name__}: {exc}"
                self._emit(
                    {"type": "tool_error", "tool": tc.name, "error": reason}
                )
                return f"ERROR: {reason} (rule audit-append)"
            raise
        self._emit({"type": "tool_done", "tool": tc.name})
        return result_text

    def _content_for_hash(self, name: str, args: dict[str, Any]) -> str | None:
        if name == "write_file":
            return str(args.get("content", ""))
        if name == "edit_file":
            return str(args.get("new_string", ""))
        return None

    def _check_governance(self, name: str, args: dict[str, Any]) -> None:
        """Cached-rule governance check (Q2). Raises ToolPolicyError if denied."""
        enf = self._enforcement
        assert enf is not None  # set in run()

        if name in ("write_file", "edit_file"):
            raw_path = str(args.get("path", "")).strip()
            if not raw_path:
                raise ToolPolicyError("write requires a non-empty path")
            # FIX 1: canonicalize to a workspace-RELATIVE path BEFORE any
            # containment / allowlist / classification check, so every check
            # sees ONE form. This is what lets an ABSOLUTE path into an
            # allowlisted dir (c:\ws\core\execution\X, or /c/ws/core/execution/X)
            # match the RELATIVE glob core/execution/** instead of false-denying.
            rel = _workspace_relative(self.repo_root, raw_path)
            if rel is None:
                raise ToolPolicyError(
                    f"Write path '{raw_path}' resolves outside the workspace.",
                    rule="trust-tier",
                )
            if rel == "":
                raise ToolPolicyError(
                    "Cannot write to the workspace root itself; give a file path.",
                    rule="trust-tier",
                )
            path = rel
            # Spec immutability: the plan-authored acceptance tests are the
            # SIGNED SPEC the build is graded against. At the BUILD gate the
            # model must never edit them (import paths are repaired
            # deterministically before the run), so a weak model cannot corrupt
            # the exam it is measured on. The earlier gates that AUTHOR these
            # tests (plan/design) are unaffected -- the guard is build-scoped.
            if self.active_gate == "G4" and _is_plan_test_path(path):
                raise ToolPolicyError(
                    f"'{path}' is a plan-authored acceptance test (the signed "
                    "spec) and is read-only during the build; make it pass by "
                    "implementing the product, not by editing the test.",
                    rule="spec-immutable",
                )
            is_impl_write = _is_implementation_path(path)
            if self.execution_context == "conversation":
                raise ToolPolicyError(
                    "Product file writes are only allowed inside governed delivery.",
                    rule="gate-gating",
                )
            if self.active_gate and self.active_gate != "G4" and is_impl_write:
                raise ToolPolicyError(
                    f"Implementation write '{path}' is not allowed during {self.active_gate}; "
                    "complete the delivery gates before build work.",
                    rule="gate-gating",
                )
            # plan-gating (#16 Edit 2.1): inside a governed delivery, an
            # implementation write requires the plan gate (G2 Expectation Map)
            # to be signed — the plan-signed signal. Evaluated before the broader
            # gate-gating required-gate set so that a missing G2 is cited under
            # the specific plan-gating rule; gate-gating still enforces the rest
            # of the required set (G0/G1/G3) below and is NOT weakened. Only fires
            # when a delivery gate is active (matching gate-gating's scoping) so
            # the trust-tier-only write path is unaffected. plan-gating is a core
            # invariant (can be warn, never off) → rule_enabled True unless a
            # governed override is in effect.
            required_gates = _required_prior_gate_numbers(self.active_gate)
            if enf.rule_enabled("plan-gating") and is_impl_write and self.active_gate:
                signed = set(enf.signed_gates) | self._context_signed_gates
                if 2 not in signed:
                    raise ToolPolicyError(
                        f"Implementation write '{path}' is blocked until the plan "
                        "gate (G2 Expectation Map) is signed.",
                        rule="plan-gating",
                    )
            if required_gates and is_impl_write:
                signed = set(enf.signed_gates) | self._context_signed_gates
                missing = sorted(required_gates - signed)
                if missing:
                    labels = ", ".join(f"G{n}" for n in missing)
                    raise ToolPolicyError(
                        f"Implementation write '{path}' is blocked until {labels} "
                        "are signed or explicitly waived.",
                        rule="gate-gating",
                    )
                if not self._test_written_this_run and not self._has_existing_test_for(path):
                    raise ToolPolicyError(
                        f"Implementation write '{path}' is blocked by test-first "
                        "policy; write or update a matching test first.",
                        rule="test-first",
                    )
            if _is_governance_path(path):
                raise ToolPolicyError(
                    f"Writing to governance path '{path}' is forbidden (.signalos/).",
                    rule="secret-block",
                )
            if _matches_forbidden_path(path, enf.forbidden_paths):
                raise ToolPolicyError(
                    f"Path '{path}' is in the always-forbidden list.",
                    rule="secret-block",
                )
            # trust-tier write allowlist (only enforced if rule enabled)
            if enf.rule_enabled("trust-tier"):
                allow = enf.tier_paths("write")
                if not _matches_glob(path, allow):
                    raise ToolPolicyError(
                        f"Path '{path}' is not in the {enf.trust_tier} write "
                        f"allowlist.",
                        rule="trust-tier",
                    )

        elif name == "run_command":
            command = str(args.get("command", "")).strip()
            if not command:
                raise ToolPolicyError("run_command requires a non-empty command")
            if self.execution_context == "conversation":
                raise ToolPolicyError(
                    "Commands that can change product state are only allowed inside governed delivery.",
                    rule="gate-gating",
                )
            denied = _command_denied(command, enf.forbidden_actions)
            if denied:
                raise ToolPolicyError(
                    f"Command matches forbidden action '{denied}'.",
                    rule="secret-block",
                )
            # Path containment: the execute allowlist (cat/type/ls/head/tail/
            # npx vitest/npx tsc ...) must never reach files outside the
            # workspace via `../` traversal or an absolute path -- otherwise a
            # model could read a hidden gold suite stored beside the repo.
            escaping = _command_escapes_workspace(command, self.repo_root)
            if escaping:
                raise ToolPolicyError(
                    f"command references a path outside the workspace: {escaping}",
                    rule="path-escape",
                )
            if enf.rule_enabled("trust-tier"):
                allow = enf.tier_paths("execute")
                if not _command_matches(command, allow, self.repo_root):
                    raise ToolPolicyError(
                        f"Command '{command}' is not in the {enf.trust_tier} "
                        f"execute allowlist.",
                        rule="trust-tier",
                    )

        elif name == "read_file":
            raw_path = str(args.get("path", "")).strip()
            if not raw_path:
                raise ToolPolicyError("read_file requires a non-empty path")
            # FIX 1: canonicalize first -> a read that escapes the workspace is
            # denied here (not just at execution), and an absolute in-workspace
            # path matches the relative read allowlist.
            rel = _workspace_relative(self.repo_root, raw_path)
            if rel is None:
                raise ToolPolicyError(
                    f"read_file path '{raw_path}' resolves outside the workspace.",
                    rule="trust-tier",
                )
            path = rel or "."
            if enf.rule_enabled("trust-tier"):
                allow = enf.tier_paths("read")
                if not _matches_glob(path, allow):
                    raise ToolPolicyError(
                        f"Path '{path}' is not in the {enf.trust_tier} read "
                        f"allowlist.",
                        rule="trust-tier",
                    )
        elif name == "list_directory":
            raw_path = str(args.get("path", "")).strip()
            # Root ('' or '.') is always listable; sub-paths honor the read allowlist.
            if raw_path in ("", "."):
                path = "."
            else:
                rel = _workspace_relative(self.repo_root, raw_path)
                if rel is None:
                    raise ToolPolicyError(
                        f"list_directory path '{raw_path}' resolves outside the "
                        "workspace.",
                        rule="trust-tier",
                    )
                path = rel or "."
            if enf.rule_enabled("trust-tier") and path not in ("", "."):
                allow = enf.tier_paths("read")
                if not _matches_glob(path, allow):
                    raise ToolPolicyError(
                        f"Path '{path}' is not in the {enf.trust_tier} read "
                        f"allowlist.",
                        rule="trust-tier",
                    )
        # search_files has no governance restriction (read-only metadata).

    def _idempotent_skip(
        self, name: str, args: dict[str, Any], content_sha: str | None
    ) -> bool:
        if name != "write_file" or content_sha is None:
            return False
        target = self._resolve_in_workspace(str(args.get("path", "")))
        if target is None or not target.is_file():
            return False
        try:
            existing = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        return _sha256(existing) == content_sha

    def _has_existing_test_for(self, rel_path: str) -> bool:
        """Return True when a plausible test already exists for rel_path."""
        normed = _norm(rel_path)
        stem = normed.rsplit("/", 1)[-1].split(".", 1)[0]
        candidates = [
            f"tests/test_{stem}.py",
            f"tests/{stem}_test.py",
            f"src/{stem}.test.ts",
            f"src/{stem}.test.tsx",
            f"src/{stem}.spec.ts",
            f"src/{stem}.spec.tsx",
        ]
        if normed.startswith("src/"):
            base = normed.rsplit(".", 1)[0]
            candidates.extend([
                f"{base}.test.ts",
                f"{base}.test.tsx",
                f"{base}.spec.ts",
                f"{base}.spec.tsx",
            ])
        if any((self.repo_root / c).is_file() for c in candidates):
            return True
        return self._plan_authored_test_for(stem)

    # Where the PLAN gate authors its acceptance-test skeletons. A test there
    # that references the module satisfies test-first for that module: the
    # failing test EXISTS (authored upstream, test-first-at-plan-time), so
    # demanding the implementer write ANOTHER test before implementing forces
    # exactly the duplicate/parallel tests the build forbids (observed: 20
    # test-first denials + skeleton copies in one G4 walk).
    _PLAN_TEST_DIRS = ("core/execution/tests", "tests")
    _PLAN_TEST_SCAN_CAP = 200

    def _plan_authored_test_for(self, stem: str) -> bool:
        """True when a plan-authored test references the module *stem*."""
        if not stem or len(stem) < 3:
            return False
        scanned = 0
        for d in self._PLAN_TEST_DIRS:
            root = self.repo_root / d
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                if scanned >= self._PLAN_TEST_SCAN_CAP:
                    return False
                if not p.is_file():
                    continue
                n = p.name
                if not (".test." in n or ".spec." in n
                        or n.startswith("test_") or n.endswith("_test.py")):
                    continue
                scanned += 1
                try:
                    if stem in p.read_text(encoding="utf-8", errors="replace"):
                        return True
                except OSError:
                    continue
        return False

    # --- execution (2.5) -----------------------------------------------------

    def _artifact_base(self, rel_path: str) -> Path:
        """Base dir under which *rel_path* physically resolves (§3.2).

        Gate-artifact rel_paths (core/governance/**, core/strategy/**,
        core/execution/**) of a NON-default project rebase under the
        project's governance base — projects.project_governance_dir — the
        SAME single resolver sign.py / wave_engine.inspect / status read
        through, so a gate agent's generated artifact is signable where it
        was written. Product-source paths and the default project resolve
        under the repo root, byte-identical to the historical behaviour.

        Enforcement note: governance checks (_check_governance) run BEFORE
        resolution, on the canonical rel_path — which the trust-tier write
        allowlist already covers (core/** entries) — and direct `.signalos/…`
        paths remain forbidden regardless of project, so this rebase neither
        needs nor grants any allowlist loosening.
        """
        if self.project_id != "default" and _is_gate_artifact_rel_path(rel_path):
            from ..projects import project_governance_dir

            return project_governance_dir(self.repo_root, self.project_id)
        return self.repo_root

    def _resolve_in_workspace(self, rel_path: str) -> Path | None:
        """Resolve *rel_path* under the workspace; None if it escapes (guard).

        Routed through the ONE canonical pipeline (FIX 1) so an absolute /
        Git-Bash `/c/` / mixed-separator path that points back INSIDE the
        workspace resolves correctly instead of being mis-anchored (the bug
        that denied absolute in-workspace writes). The gate-artifact rebase
        (non-default project) is preserved: the physical base may be the
        project governance dir, which is itself under repo_root.
        """
        if not rel_path:
            return None
        base = self._artifact_base(rel_path)
        target = _canonical_abs(base, rel_path)
        if target is None:
            return None
        try:
            root = self.repo_root.resolve()
        except (OSError, RuntimeError):
            root = self.repo_root
        if target == root or root in target.parents:
            return target
        return None

    def _validate_workspace_write(self, target: Path) -> None:
        """Rust-safety-net analogue (Q2 step 5).

        The canonical check is Rust ipc::validate_workspace_write. In the
        sidecar process we re-assert the same invariant in Python (path is
        inside the workspace). Phase 3 wires the actual Rust round-trip. Uses
        the same case-insensitive containment (==/parents) the canonical
        pipeline uses, so it never spuriously trips on a drive-case difference.
        """
        try:
            root = self.repo_root.resolve()
            resolved = target.resolve()
        except (OSError, RuntimeError) as exc:
            raise ToolPolicyError(
                f"Write denied: {target} could not be resolved ({exc}).",
                rule="trust-tier",
            ) from exc
        if resolved != root and root not in resolved.parents:
            raise ToolPolicyError(
                f"Write denied: {target} is outside the workspace boundary.",
                rule="trust-tier",
            )

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "read_file":
            return self._tool_read_file(str(args.get("path", "")))
        if name == "write_file":
            return self._tool_write_file(
                str(args.get("path", "")), str(args.get("content", ""))
            )
        if name == "edit_file":
            return self._tool_edit_file(
                str(args.get("path", "")),
                str(args.get("old_string", "")),
                str(args.get("new_string", "")),
            )
        if name == "run_command":
            return self._tool_run_command(str(args.get("command", "")))
        if name == "search_files":
            return self._tool_search_files(str(args.get("pattern", "")))
        if name == "list_directory":
            return self._tool_list_directory(str(args.get("path", "")))
        raise ToolPolicyError(f"Unknown tool '{name}'.")

    def _tool_read_file(self, rel_path: str) -> str:
        target = self._resolve_in_workspace(rel_path)
        if target is None:
            raise ToolPolicyError(f"read_file path escapes workspace: {rel_path}")
        if not target.is_file():
            return f"ERROR: file not found: {rel_path}"
        data = target.read_bytes()
        if len(data) > MAX_READ_BYTES:
            return f"ERROR: file too large to read ({len(data)} bytes): {rel_path}"
        # READ_TIMEOUT_S is a contract for slow filesystems; local reads are
        # synchronous and fast. We still record it in the tool definition.
        return data.decode("utf-8", errors="replace")

    def _tool_write_file(self, rel_path: str, content: str) -> str:
        target = self._resolve_in_workspace(rel_path)
        if target is None:
            raise ToolPolicyError(f"write_file path escapes workspace: {rel_path}")
        self._validate_workspace_write(target)
        # Security scan on write content (2.9) — reuse security_gate.
        warnings = self._scan_write_content(rel_path, content)
        before = ""
        if target.is_file():
            try:
                before = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                before = ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._wrote_file_this_run = True
        if _is_test_path(rel_path):
            self._test_written_this_run = True
        # 3.6: emit a diff event so the UI can render a FileDiffBubble.
        self._emit({"type": "diff", "path": rel_path, "before": before, "after": content})
        msg = f"OK: wrote {len(content)} bytes to {rel_path}"
        if warnings:
            msg += "\nSECURITY WARNINGS:\n" + "\n".join(f"- {w}" for w in warnings)
        return msg

    def _tool_edit_file(self, rel_path: str, old: str, new: str) -> str:
        target = self._resolve_in_workspace(rel_path)
        if target is None:
            raise ToolPolicyError(f"edit_file path escapes workspace: {rel_path}")
        if not target.is_file():
            return f"ERROR: file not found: {rel_path}"
        self._validate_workspace_write(target)
        original = target.read_text(encoding="utf-8")
        count = original.count(old)
        if count == 0:
            return f"ERROR: old_string not found in {rel_path}"
        if count > 1:
            return (
                f"ERROR: old_string is not unique in {rel_path} "
                f"({count} matches); provide more context."
            )
        updated = original.replace(old, new, 1)
        warnings = self._scan_write_content(rel_path, updated)
        target.write_text(updated, encoding="utf-8")
        self._wrote_file_this_run = True
        if _is_test_path(rel_path):
            self._test_written_this_run = True
        # 3.6: emit a diff event so the UI can render a FileDiffBubble.
        self._emit({"type": "diff", "path": rel_path, "before": original, "after": updated})
        msg = f"OK: edited {rel_path}"
        if warnings:
            msg += "\nSECURITY WARNINGS:\n" + "\n".join(f"- {w}" for w in warnings)
        return msg

    def _resolve_run_cwd(self, command: str) -> tuple[Path, str]:
        """FIX 2: jail the child cwd to the workspace root, honoring a leading
        `cd <subdir>`.

        A leading `cd <target>` is resolved via the canonical pipeline (FIX 1).
        When it stays IN the workspace (relative OR absolute-but-contained,
        including the workspace root itself) it becomes the child's cwd and the
        `cd` segment is stripped -- so the model never needs `cd <abs> && x`,
        and it works regardless of the underlying shell. An escaping / absolute
        cd target (rel is None) is left in the command as-is; the containment
        guard in _check_governance has already denied it, so this path is only
        reached for contained targets, but leaving it untouched fails closed.
        A non-existent subdir is left to the shell so its error reaches the
        model verbatim.
        """
        root = self.repo_root
        target, rest = _peel_leading_cd(command)
        if target is None:
            return root, command
        rel = _workspace_relative(root, target)
        if rel is None:
            return root, command  # escaping cd: leave it (already governed)
        sub = (root / rel) if rel else root
        try:
            if not sub.is_dir():
                return root, command  # nonexistent subdir -> let the shell say so
        except OSError:
            return root, command
        # `cd frontend` alone (no following command) is a no-op at the new cwd.
        return sub, (rest or "cd .")

    def _get_sandbox_runner(self) -> SandboxRunner:
        """The runtime CONTAINMENT backend for run_command, selected once per
        loop from SIGNALOS_SANDBOX (default = in-process = today's behavior).

        This is the "boundary endgame": when a container backend is selected the
        command runs inside a bounded OS/process boundary (workspace-only mount,
        network off) so the in-code allowlist becomes a backstop rather than the
        primary defense. The default keeps the current subprocess path
        byte-identical, so nothing changes unless a caller opts in. See
        sandbox.py. The in-code path/allowlist policy already ran in
        _check_governance and stays in force regardless of backend.
        """
        runner = getattr(self, "_sandbox_runner", None)
        if runner is None:
            runner = select_runner(self.repo_root, emit=self._emit)
            self._sandbox_runner = runner
        return runner

    def _tool_run_command(self, command: str) -> str:
        # Cancellation: a long command honors the loop-level timeout. We do not
        # poll cancel_check mid-process; the COMMAND_TIMEOUT_S bound applies.
        #
        # CI=1: interactive/watch modes never terminate (bare `npx vitest` is
        # WATCH mode; dev servers wait forever). Test runners and CLIs almost
        # universally honor CI to run once and exit -- without this, one watch
        # command hung a build for hours. Passed as an environment OVERLAY: the
        # in-process runner merges it onto os.environ (byte-identical to before);
        # the container runner forwards ONLY these keys as -e so no host env
        # leaks past the boundary.
        #
        # FIX 2: cwd is jailed to the workspace root (or a contained subdir a
        # leading `cd` names), so a compound `cd <abs> && x` is never required.
        run_cwd, command = self._resolve_run_cwd(command)
        env = {"CI": "1", "FORCE_COLOR": "0"}
        runner = self._get_sandbox_runner()
        exit_code, output = runner.run(command, run_cwd, COMMAND_TIMEOUT_S, env)
        if output.timed_out:
            return (
                f"ERROR: command timed out after {COMMAND_TIMEOUT_S}s and was "
                f"killed: {command}. If this was a watch/serve mode command, "
                f"use its run-once form instead."
            )
        # Secret redaction + output-cap are POLICY that applies regardless of
        # backend, so they stay here (a backstop layer over containment).
        stdout = redact_secrets(output.stdout or "")
        stderr = redact_secrets(output.stderr or "")
        parts = [f"exit_code: {exit_code}"]
        if stdout.strip():
            parts.append("stdout:\n" + _cap_command_output(stdout))
        if stderr.strip():
            parts.append("stderr:\n" + _cap_command_output(stderr))
        return "\n".join(parts)

    def _tool_search_files(self, pattern: str) -> str:
        if not pattern:
            return "ERROR: empty pattern"
        root = self.repo_root.resolve()
        matches: list[str] = []
        seen: set[str] = set()
        for p in root.glob(pattern):
            if ".signalos" in p.parts or "node_modules" in p.parts or ".git" in p.parts:
                continue
            try:
                rel = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if rel not in seen:
                seen.add(rel)
                matches.append(rel)
            if len(matches) >= 500:
                break
        # A non-default project's gate artifacts physically live under the
        # governance base (see _artifact_base), which the .signalos skip above
        # would hide — glob that base too and report canonical rel_paths, so
        # search_files agrees with read_file/list_directory about what exists.
        if self.project_id != "default" and len(matches) < 500:
            from ..projects import project_governance_dir

            gov = project_governance_dir(self.repo_root, self.project_id).resolve()
            if gov != root and gov.is_dir():
                for p in gov.glob(pattern):
                    if "node_modules" in p.parts or ".git" in p.parts:
                        continue
                    try:
                        rel = str(p.relative_to(gov)).replace("\\", "/")
                    except ValueError:
                        continue
                    if _is_gate_artifact_rel_path(rel) and rel not in seen:
                        seen.add(rel)
                        matches.append(rel)
                    if len(matches) >= 500:
                        break
        if not matches:
            return f"No files match: {pattern}"
        return "\n".join(sorted(matches))

    def _tool_list_directory(self, rel_path: str) -> str:
        rel = (rel_path or "").strip()
        lookup = "." if rel in ("", ".") else rel
        target = self._resolve_in_workspace(lookup)
        if target is None:
            raise ToolPolicyError(
                f"list_directory path escapes workspace: {rel_path}"
            )
        if not target.is_dir():
            return f"ERROR: not a directory: {rel or '.'}"
        entries: list[str] = []
        # Directories first, then files; both alphabetical. Skip VCS/build noise.
        for p in sorted(
            target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())
        ):
            if p.name in (".git", "node_modules", ".signalos"):
                continue
            entries.append(f"[{'dir' if p.is_dir() else 'file'}] {p.name}")
            if len(entries) >= 500:
                break
        if not entries:
            return f"(empty directory: {rel or '.'})"
        return "\n".join(entries)

    def _scan_write_content(self, rel_path: str, content: str) -> list[str]:
        """Run secret and injection scans on write content (2.9)."""
        warnings: list[str] = []
        secret_block_enabled = (
            self._enforcement is None
            or self._enforcement.rule_enabled("secret-block")
        )
        try:
            secret_findings = _scan_write_secrets(rel_path, content)
        except Exception as exc:
            if secret_block_enabled:
                raise ToolPolicyError(
                    f"Write denied: secret scan unavailable for '{rel_path}' "
                    f"({type(exc).__name__}: {exc}).",
                    rule="secret-block",
                ) from exc
            warnings.append(f"secret scan error: {exc}")
        else:
            if secret_findings and secret_block_enabled:
                preview = "; ".join(secret_findings[:3])
                more = "" if len(secret_findings) <= 3 else " ..."
                raise ToolPolicyError(
                    f"Write denied: secret-like content detected in '{rel_path}' "
                    f"({preview}{more}). Rule: secret-block.",
                    rule="secret-block",
                )
            warnings.extend(secret_findings)

        try:
            from .security_gate import _JS_INJECTION_PATTERNS, _JS_EXTENSIONS
            from ..security import scan_injection_risks

            # Write to a temp scan by reusing scan_injection_risks against the
            # to-be-written content: write a sibling temp is overkill, so we
            # apply the JS patterns directly and call scan_injection_risks on a
            # transient file only when the file already exists.
            suffix = Path(rel_path).suffix.lower()
            if suffix in _JS_EXTENSIONS:
                for lineno, line in enumerate(content.splitlines(), start=1):
                    for compiled, risk in _JS_INJECTION_PATTERNS:
                        if compiled.search(line):
                            warnings.append(f"{rel_path}:{lineno}: {risk}")
            # Generic injection patterns (eval, exec, etc.) via security.py.
            generic = re.compile(r"\b(eval|exec)\s*\(")
            for lineno, line in enumerate(content.splitlines(), start=1):
                if generic.search(line):
                    warnings.append(
                        f"{rel_path}:{lineno}: dynamic code execution "
                        f"(eval/exec) — injection risk"
                    )
        except Exception as exc:  # INV-4: surface the scan failure, do not hide
            warnings.append(f"security scan error: {exc}")
        return warnings

    # --- audit ledger (2.6) --------------------------------------------------

    def _audit(
        self,
        tc: ToolCall,
        status: str,
        detail: str,
        t0: float,
        content_sha: str | None,
        rule: str | None = None,
    ) -> None:
        self._seq += 1
        duration_ms = int((time.perf_counter() - t0) * 1000)
        # Redact secret-looking args before persisting to the ledger.
        safe_args = self._redact_args(tc.arguments or {})
        entry = {
            "seq": self._seq,
            "run_id": self.run_id,
            "ts": _now_iso(),
            "tool": tc.name,
            "tool_call_id": tc.id,
            "args": safe_args,
            "content_sha256": content_sha,
            "status": status,
            "detail": detail,
            "rule": rule,
            "duration_ms": duration_ms,
        }
        self._ensure_run_dir()
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        # Safety net: never crash the audit on a non-dict argument payload (a
        # provider that returns tool arguments as a raw/typed JSON value). The
        # source normalizes to a dict; this guards the audit path regardless.
        if not isinstance(args, dict):
            return {"_raw": str(args)[:2000]}
        out: dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, str):
                # Truncate large content blobs; redact secrets.
                redacted = redact_secrets(v)
                out[k] = redacted if len(redacted) <= 2000 else redacted[:2000] + "…"
            else:
                out[k] = v
        return out

    # --- run state persistence (INV-5 / 2.10) -------------------------------

    def _persist_state(
        self, messages: list[dict[str, Any]], tool_calls_made: int, status: str
    ) -> None:
        self._ensure_run_dir()
        state = {
            "run_id": self.run_id,
            "status": status,
            "tool_calls_made": tool_calls_made,
            "trust_tier": self._enforcement.trust_tier if self._enforcement else None,
            "updated_at": _now_iso(),
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)
        # Append the conversation tail for resume (full transcript log).
        with open(self.conversation_path, "w", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    @staticmethod
    def _assistant_tool_msg(
        content: str | None, tool_calls: list[ToolCall]
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments or {}),
                    },
                }
                for tc in tool_calls
            ],
        }
