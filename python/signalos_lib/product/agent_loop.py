# signalos_lib/product/agent_loop.py
# v4 Phase 2.4-2.11 — The governed agent loop.
#
# messages -> adapter.chat() -> tool_calls -> governance check -> execute
#          -> append tool results -> loop until end_turn or tool-call limit.
#
# Architecture Decisions implemented here:
#   Q1  — uses harness.AgentProvider via ProviderAdapter.
#   Q2  — governance rules are read ONCE at loop start from an
#         EnforcementProvider (Rust is the authority; CI uses a test double).
#         Per-call checks hit the cached rules (fast, no IPC round-trip).
#         File writes ALSO go through a validate_workspace_write hook.
#   Q4  — the loop is stateless re: gates. It runs until end_turn or the
#         tool-call limit, then returns control to the orchestrator. It does
#         NOT detect gate boundaries.
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
import json
import re
import shlex
import subprocess
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
from .provider_adapter import ProviderAdapter

# ---------------------------------------------------------------------------
# Constants / timeouts
# ---------------------------------------------------------------------------

READ_TIMEOUT_S = 30
COMMAND_TIMEOUT_S = 120
DEFAULT_TOOL_CALL_LIMIT = 50
MAX_READ_BYTES = 2_000_000  # guard against reading huge binaries into context

# Secret-redaction patterns applied to command stdout/stderr (2.5/2.9).
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                  # OpenAI-style
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),           # Anthropic-style
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),              # Google API key
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),                 # GitHub PAT
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

_REDACTION = "[REDACTED]"


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


# ---------------------------------------------------------------------------
# Path / command policy helpers (2.5)
# ---------------------------------------------------------------------------


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


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


def _is_governance_path(path: str) -> bool:
    """No direct governance-file edits (2.5): anything under .signalos/."""
    normed = _norm(path)
    return normed == ".signalos" or normed.startswith(".signalos/")


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


def _command_matches(command: str, patterns: list[str]) -> bool:
    cmd = command.strip()
    root = _command_root(cmd)
    for pat in patterns:
        p = pat.strip()
        if p == "**":
            return True
        if cmd == p or cmd.startswith(p + " ") or root == p:
            return True
    return False


def _command_denied(command: str, denylist: list[str]) -> str | None:
    """Return the matched denylist entry if *command* hits the denylist."""
    cmd = command.strip().lower()
    for entry in denylist:
        e = entry.strip().lower()
        if e and e in cmd:
            return entry
    return None


def redact_secrets(text: str) -> str:
    """Redact secret-looking substrings from command output (2.5/2.9)."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(_REDACTION, out)
    return out


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
    status: str  # "completed" | "tool_limit" | "text_only" | "cancelled" | "error"
    final_text: str | None
    tool_calls_made: int
    messages: list[dict[str, Any]]
    error: str | None = None
    text_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "final_text": self.final_text,
            "tool_calls_made": self.tool_calls_made,
            "error": self.error,
            "text_only": self.text_only,
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
        tool_call_limit: int = DEFAULT_TOOL_CALL_LIMIT,
        cancel_check: Callable[[], bool] | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.adapter = adapter
        self.repo_root = Path(repo_root)
        self.enforcement_provider = enforcement_provider or StaticEnforcementProvider()
        self.run_id = run_id or f"agent-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        self.tool_call_limit = tool_call_limit
        self._cancel_check = cancel_check or (lambda: False)
        self._emit = emit or (lambda _e: None)
        self._enforcement: EnforcementState | None = None
        self._seq = 0

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

        The loop does NOT detect gates. It runs until end_turn or the
        tool-call limit, then hands back to the orchestrator.
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
            return self._run_text_only(messages)

        return self._run_tool_loop(messages, tool_calls_made=0)

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
            return self._run_text_only(messages)

        return self._run_tool_loop(messages, tool_calls_made=tool_calls_made)

    def _run_tool_loop(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_calls_made: int,
    ) -> LoopResult:
        tools = [t.as_openai_tool() for t in AGENT_TOOLS]
        final_text: str | None = None

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

            try:
                resp: AgentResponse = self.adapter.chat(
                    messages=[dict(m) for m in messages],
                    tools=tools,
                )
            except Exception as exc:  # INV-4
                err = f"Provider call failed: {exc}"
                self._emit({"type": "error", "error": err})
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

            if resp.stop_reason != "tool_use" or not resp.tool_calls:
                # end_turn / max_tokens — hand back to orchestrator (Q4/2.7).
                messages.append(
                    {"role": "assistant", "content": resp.content or ""}
                )
                self._persist_state(messages, tool_calls_made, status="completed")
                self._emit({"type": "end_turn", "run_id": self.run_id})
                return LoopResult(
                    run_id=self.run_id,
                    status="completed",
                    final_text=final_text,
                    tool_calls_made=tool_calls_made,
                    messages=messages,
                )

            # Record the assistant turn carrying the tool calls.
            messages.append(self._assistant_tool_msg(resp.content, resp.tool_calls))

            for tc in resp.tool_calls:
                if tool_calls_made >= self.tool_call_limit:
                    self._persist_state(messages, tool_calls_made, status="tool_limit")
                    self._emit(
                        {"type": "tool_limit", "limit": self.tool_call_limit}
                    )
                    return LoopResult(
                        run_id=self.run_id,
                        status="tool_limit",
                        final_text=final_text,
                        tool_calls_made=tool_calls_made,
                        messages=messages,
                        error=f"tool-call limit ({self.tool_call_limit}) reached",
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

    # --- text-only mode (INV-7 / 2.11) --------------------------------------

    def _run_text_only(self, messages: list[dict[str, Any]]) -> LoopResult:
        try:
            resp = self.adapter.chat(messages=messages, tools=None)
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
        except Exception as exc:  # INV-4: never except: pass
            reason = f"{type(exc).__name__}: {exc}"
            self._audit(tc, "error", reason, t0, content_sha)
            self._emit({"type": "tool_error", "tool": tc.name, "error": reason})
            return f"ERROR: {reason}"

        self._audit(tc, "completed", "ok", t0, content_sha)
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
            path = str(args.get("path", "")).strip()
            if not path:
                raise ToolPolicyError("write requires a non-empty path")
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
            denied = _command_denied(command, enf.forbidden_actions)
            if denied:
                raise ToolPolicyError(
                    f"Command matches forbidden action '{denied}'.",
                    rule="secret-block",
                )
            if enf.rule_enabled("trust-tier"):
                allow = enf.tier_paths("execute")
                if not _command_matches(command, allow):
                    raise ToolPolicyError(
                        f"Command '{command}' is not in the {enf.trust_tier} "
                        f"execute allowlist.",
                        rule="trust-tier",
                    )

        elif name == "read_file":
            path = str(args.get("path", "")).strip()
            if not path:
                raise ToolPolicyError("read_file requires a non-empty path")
            if enf.rule_enabled("trust-tier"):
                allow = enf.tier_paths("read")
                if not _matches_glob(path, allow):
                    raise ToolPolicyError(
                        f"Path '{path}' is not in the {enf.trust_tier} read "
                        f"allowlist.",
                        rule="trust-tier",
                    )
        elif name == "list_directory":
            path = str(args.get("path", "")).strip()
            # Root ('' or '.') is always listable; sub-paths honor the read allowlist.
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

    # --- execution (2.5) -----------------------------------------------------

    def _resolve_in_workspace(self, rel_path: str) -> Path | None:
        """Resolve *rel_path* under the workspace; None if it escapes (guard)."""
        if not rel_path:
            return None
        candidate = (self.repo_root / rel_path).resolve()
        root = self.repo_root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def _validate_workspace_write(self, target: Path) -> None:
        """Rust-safety-net analogue (Q2 step 5).

        The canonical check is Rust ipc::validate_workspace_write. In the
        sidecar process we re-assert the same invariant in Python (path is
        inside the workspace). Phase 3 wires the actual Rust round-trip.
        """
        root = self.repo_root.resolve()
        try:
            target.resolve().relative_to(root)
        except ValueError as exc:
            raise ToolPolicyError(
                f"Write denied: {target} is outside the workspace boundary.",
                rule="trust-tier",
            ) from exc

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
        # 3.6: emit a diff event so the UI can render a FileDiffBubble.
        self._emit({"type": "diff", "path": rel_path, "before": original, "after": updated})
        msg = f"OK: edited {rel_path}"
        if warnings:
            msg += "\nSECURITY WARNINGS:\n" + "\n".join(f"- {w}" for w in warnings)
        return msg

    def _tool_run_command(self, command: str) -> str:
        # Cancellation: a long command honors the loop-level timeout. We do not
        # poll cancel_check mid-process; the COMMAND_TIMEOUT_S bound applies.
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return (
                f"ERROR: command timed out after {COMMAND_TIMEOUT_S}s and was "
                f"killed: {command}"
            )
        stdout = redact_secrets(proc.stdout or "")
        stderr = redact_secrets(proc.stderr or "")
        parts = [f"exit_code: {proc.returncode}"]
        if stdout.strip():
            parts.append("stdout:\n" + stdout)
        if stderr.strip():
            parts.append("stderr:\n" + stderr)
        return "\n".join(parts)

    def _tool_search_files(self, pattern: str) -> str:
        if not pattern:
            return "ERROR: empty pattern"
        root = self.repo_root.resolve()
        matches: list[str] = []
        for p in root.glob(pattern):
            if ".signalos" in p.parts or "node_modules" in p.parts or ".git" in p.parts:
                continue
            try:
                matches.append(str(p.relative_to(root)).replace("\\", "/"))
            except ValueError:
                continue
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
        """Run the security gate's injection scan on write content (2.9)."""
        warnings: list[str] = []
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
