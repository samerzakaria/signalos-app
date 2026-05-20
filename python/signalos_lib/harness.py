# SignalOS Core v2.1 — Headless harness (AMD-CORE-004 + AMD-CORE-007).
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
#
# The harness is the 8th tool-adapter emitter: it executes a PLAN step
# without an attached editor by invoking the LLM provider API and
# emitting the same journal / metrics events as the seven editor
# emitters. External callers touch this module through:
#
#     signalos harness call --step <id> [--prompt <s> | --prompt-file <p>]
#     signalos harness status <call-id>
#     signalos harness abort  <call-id>
#
# Design invariants (cross-ref §C/§D of core/CONSTITUTION.md + AMD-CORE-004):
#
#   1. The harness writes to the journal ONLY through the four hook
#      scripts under core/execution/hooks/<event>/<event>.sh. It does
#      not open journal.jsonl itself. This keeps the redaction and
#      flock path uniform with the editor emitters.
#   2. The harness writes to metrics.jsonl ONLY through
#      core/execution/hooks/_lib/metrics-append.sh, again to share the
#      strict field allowlist with the dashboard renderer.
#   3. The LLM provider is resolved lazily on the first `run_step`
#      call. `signalos session` and `signalos pause` continue to work
#      on a stdlib-only Python 3.11 install.
#   4. `SIGNALOS_HARNESS_TEST=1` replaces the LLM call with a
#      deterministic canned response. The proof scenarios use this so
#      CI does not need a live API key. No network call is made in
#      test mode.
#   5. AMD-CORE-007: LLM provider abstraction. The `LLMProvider`
#      Protocol and five concrete implementations (Anthropic, OpenAI,
#      Gemini, Ollama, Test) decouple the harness from any single SDK.
#      `SIGNALOS_LLM_PROVIDER` env var selects the provider; default
#      is "anthropic". `SIGNALOS_HARNESS_TEST=1` overrides to TestProvider.
#
# Exit-code contract (propagated by commands/harness.py):
#   0 — step.completed event emitted; call state = "completed"
#   1 — user error (bad step-id, missing prompt, bad session)
#   2 — execution error (provider returned an error, hook script
#        missing, IO failure); step.failed event emitted when possible
#   3 — policy refusal (e.g. attempting to resume an aborted call)


from __future__ import annotations

__all__ = ["run_step", "DEFAULT_MODEL", "LLMProvider"]  # W-2: explicit public API

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT_MARKER = ".signalos"

# Default Anthropic model for the harness. Pin-by-family — callers can
# override via --model. The Sonnet 4.5 family is the W1.2 reference;
# the `<1.0` upper bound on `anthropic` in cli/requirements.txt keeps
# the SDK surface stable enough for this default to remain valid.
DEFAULT_MODEL = "claude-sonnet-4-5"

# The `tool` identifier the 8th emitter reports into hook events and
# metrics. Must match the folder name core/tool-adapters/emitters/harness/.
HARNESS_TOOL_NAME = "harness"

# Canned response used in SIGNALOS_HARNESS_TEST=1 mode. Deterministic
# so proof scenarios can diff against a fixed string.
_HARNESS_TEST_CANNED = "SIGNALOS_HARNESS_TEST: canned harness response for proof scenarios."


# ---------------------------------------------------------------------------
# LLM Provider Protocol (AMD-CORE-007)
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM provider implementations.

    All concrete providers must implement `call()` and return a 3-tuple
    of (response_text, tokens_in, tokens_out). Token counts may be None
    if the provider does not report them.
    """

    def call(
        self,
        prompt: str,
        model: str,
    ) -> tuple[str, int | None, int | None]:
        """Invoke the LLM and return (response_text, tokens_in, tokens_out)."""
        ...


class AnthropicProvider:
    """LLM provider wrapping the `anthropic` SDK.

    The anthropic package is imported lazily so that stdlib-only installs
    (e.g. `signalos session`, `signalos pause`) continue to work without it.
    """

    def call(
        self,
        prompt: str,
        model: str,
    ) -> tuple[str, int | None, int | None]:
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "signalos harness: the `anthropic` package is not installed. "
                "Run `pip install -r cli/requirements.txt` "
                "(adds anthropic>=0.39,<1.0) and retry."
            ) from exc

        client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY from env
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        # Response shape: resp.content is a list of blocks; collect text blocks.
        text_parts = []
        for block in getattr(resp, "content", []) or []:
            t = getattr(block, "text", None)
            if t is None and isinstance(block, dict):
                t = block.get("text")
            if t:
                text_parts.append(t)
        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "input_tokens", None) if usage else None
        tokens_out = getattr(usage, "output_tokens", None) if usage else None
        return "\n".join(text_parts), tokens_in, tokens_out


class OpenAIProvider:
    """LLM provider wrapping the `openai` SDK.

    The openai package is imported lazily. Raises RuntimeError with an
    install hint if the package is not available.
    """

    def call(
        self,
        prompt: str,
        model: str,
    ) -> tuple[str, int | None, int | None]:
        try:
            import openai  # noqa: F401  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "signalos harness: the `openai` package is not installed. "
                "Run `pip install openai>=1.0` and retry."
            ) from exc

        import openai as _openai  # type: ignore[import-not-found]
        client = _openai.OpenAI()  # picks up OPENAI_API_KEY from env
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        if resp.choices:
            text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", None) if usage else None
        tokens_out = getattr(usage, "completion_tokens", None) if usage else None
        return text, tokens_in, tokens_out


class GeminiProvider:
    """LLM provider wrapping the `google.generativeai` SDK.

    The google-generativeai package is imported lazily. Raises RuntimeError
    with an install hint if the package is not available.
    """

    def call(
        self,
        prompt: str,
        model: str,
    ) -> tuple[str, int | None, int | None]:
        try:
            import google.generativeai  # noqa: F401  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "signalos harness: the `google-generativeai` package is not installed. "
                "Run `pip install google-generativeai>=0.5` and retry."
            ) from exc

        import google.generativeai as genai  # type: ignore[import-not-found]
        # GOOGLE_API_KEY picked up from env automatically when using configure()
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        _model = genai.GenerativeModel(model)
        resp = _model.generate_content(prompt)
        text = ""
        if hasattr(resp, "text"):
            text = resp.text or ""
        # Gemini SDK does not always expose per-call token counts in the
        # generate_content response; use None for now.
        tokens_in: int | None = None
        tokens_out: int | None = None
        usage_meta = getattr(resp, "usage_metadata", None)
        if usage_meta:
            tokens_in = getattr(usage_meta, "prompt_token_count", None)
            tokens_out = getattr(usage_meta, "candidates_token_count", None)
        return text, tokens_in, tokens_out


class OllamaProvider:
    """LLM provider using the local Ollama inference server.

    Uses only `urllib.request` from the standard library — no third-party
    packages required. Calls http://localhost:11434/api/generate.
    """

    OLLAMA_URL = "http://localhost:11434/api/generate"

    def call(
        self,
        prompt: str,
        model: str,
    ) -> tuple[str, int | None, int | None]:
        import urllib.request

        payload = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8")
        except Exception as exc:
            raise RuntimeError(
                f"signalos harness: Ollama request failed: {exc}. "
                "Is Ollama running at http://localhost:11434/?"
            ) from exc

        data = json.loads(body)
        text = data.get("response", "")
        tokens_in: int | None = data.get("prompt_eval_count")
        tokens_out: int | None = data.get("eval_count")
        return text, tokens_in, tokens_out


class TestProvider:
    """LLM provider that returns a deterministic canned response.

    No network call, no SDK required. Used by `SIGNALOS_HARNESS_TEST=1`
    and returned by `_resolve_provider()` when that flag is set.
    Token counts are the byte length of the prompt / response — non-zero
    for dashboard assertions without reporting garbage.
    """

    def call(
        self,
        prompt: str,
        model: str,
    ) -> tuple[str, int | None, int | None]:
        return (
            _HARNESS_TEST_CANNED,
            len(prompt.encode("utf-8")),
            len(_HARNESS_TEST_CANNED.encode("utf-8")),
        )


# ---------------------------------------------------------------------------
# Provider resolution (AMD-CORE-007)
# ---------------------------------------------------------------------------

def _resolve_provider(name: str | None = None) -> LLMProvider:
    """Return the appropriate LLMProvider instance.

    Resolution order:
    1. If SIGNALOS_HARNESS_TEST=1 is set → TestProvider (no SDK, no network).
    2. Explicit `name` argument (passed by --provider CLI flag).
    3. SIGNALOS_LLM_PROVIDER env var.
    4. Default: "anthropic".
    """
    if os.environ.get("SIGNALOS_HARNESS_TEST") == "1":
        return TestProvider()

    provider_name = name or os.environ.get("SIGNALOS_LLM_PROVIDER", "anthropic")
    provider_name = provider_name.lower().strip()

    if provider_name == "anthropic":
        return AnthropicProvider()
    if provider_name in {"openai", "open_ai"}:
        return OpenAIProvider()
    if provider_name in {"gemini", "google", "google-generativeai"}:
        return GeminiProvider()
    if provider_name == "ollama":
        return OllamaProvider()
    if provider_name in {"test", "mock"}:
        return TestProvider()

    raise RuntimeError(
        f"signalos harness: unknown provider '{provider_name}'. "
        "Valid values: anthropic, openai, gemini, ollama, test. "
        "Set SIGNALOS_LLM_PROVIDER or pass --provider."
    )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (or cwd) until .signalos/ is found, or raise."""
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / REPO_ROOT_MARKER).is_dir():
            return cand
    raise RuntimeError(
        f"signalos harness: no {REPO_ROOT_MARKER}/ ancestor of {p}. "
        "Run `signalos init` or cd into a repo that already has .signalos/."
    )


def _hooks_dir(root: Path) -> Path:
    return root / "core" / "execution" / "hooks"


def _lib_dir(root: Path) -> Path:
    return root / "core" / "execution" / "hooks" / "_lib"


def _session_dir(root: Path, session_id: str) -> Path:
    return root / REPO_ROOT_MARKER / "sessions" / session_id


def _harness_dir(root: Path, session_id: str) -> Path:
    return _session_dir(root, session_id) / "harness"


def _call_dir(root: Path, session_id: str, call_id: str) -> Path:
    return _harness_dir(root, session_id) / call_id


def _state_path(root: Path, session_id: str, call_id: str) -> Path:
    return _call_dir(root, session_id, call_id) / "state.json"


def _abort_flag_path(root: Path, session_id: str, call_id: str) -> Path:
    return _call_dir(root, session_id, call_id) / "abort.flag"


# ---------------------------------------------------------------------------
# Time + ids
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """UTC ISO-8601 with Z suffix — matches the shell helpers' format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_call_id() -> str:
    """Opaque call id, sortable by time.

    Format: harness-YYYYMMDDTHHMMSSZ-<hex8>.
    Examples: harness-20260423T014200Z-1a2b3c4d
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"harness-{ts}-{suffix}"


# ---------------------------------------------------------------------------
# Hook + metrics shell-out
# ---------------------------------------------------------------------------

def _fire_hook(
    root: Path,
    event: str,
    session_id: str,
    step_id: str,
    *,
    actor: str = HARNESS_TOOL_NAME,
    extra_args: list[str] | None = None,
) -> int:
    """Invoke core/execution/hooks/<event>/<event>.sh as a subprocess.

    Returns the hook script's exit code. A missing hook script is a
    soft warning (returns 0 per the dispatcher's fail-open contract in
    W1.1) — this matches how `session-hook-dispatch.sh` treats the
    step-* events.
    """
    hook_script = _hooks_dir(root) / event / f"{event}.sh"
    if not hook_script.is_file():
        sys.stderr.write(
            f"signalos harness: hook script missing, fail-open: {hook_script}\n"
        )
        return 0

    # Only step-started accepts --actor / --intent. step-completed and
    # step-failed take outcome/duration/token fields instead; pre-session-
    # compress takes its own shape. Pass --tool to every event (every
    # hook script accepts it) so the journal rows are uniformly tagged.
    #
    # Path form: relative-from-root in POSIX form, with cwd=str(root).
    # On Windows, subprocess.run(["bash", ...]) resolves to WSL bash
    # (C:\Windows\System32\bash.exe wins via CreateProcess System32
    # priority, regardless of user PATH or shutil.which). WSL bash does
    # not understand drive-letter paths (C:/Users/...); it expects
    # /mnt/c/Users/... A relative path against cwd works for both WSL
    # bash and git-bash because Python translates the cwd argument
    # correctly when launching the child process.
    argv = [
        "bash", hook_script.relative_to(root).as_posix(),
        "--session-id", session_id,
        "--step-id", step_id,
        "--tool", HARNESS_TOOL_NAME,
    ]
    if event == "step-started":
        argv.extend(["--actor", actor])
    if extra_args:
        argv.extend(extra_args)
    # Sandbox wrap: when .signalos/sandbox.json has enabled=true AND
    # Docker is reachable, the hook script runs in a container with the
    # workspace mounted at /workspace. Trusted bundle code, but wrapping
    # it completes the sandbox-toggle promise (every subprocess routes
    # through the same gate -- defense in depth).
    from signalos_lib.sandbox import maybe_wrap_for_sandbox
    argv, _ = maybe_wrap_for_sandbox(root, argv)
    proc = subprocess.run(argv, check=False, cwd=str(root))
    return proc.returncode


def _append_metric(
    root: Path,
    session_id: str,
    step_id: str,
    *,
    duration_ms: int,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    hook: str | None = None,
) -> int:
    """Invoke metrics-append.sh with a harness-origin metric row.

    Field allowlist is enforced by metrics-append.sh; we only emit the
    keys we are certain are allowed (see core/execution/hooks/_lib/
    metrics-append.sh header comment for the authoritative list).
    """
    metric: dict[str, Any] = {
        "ts": _now_iso(),
        "schema_version": 1,
        "session_id": session_id,
        "step_id": step_id,
        "tool": HARNESS_TOOL_NAME,
        "duration_ms": int(duration_ms),
        "actor": HARNESS_TOOL_NAME,
    }
    if hook:
        metric["hook"] = hook
    if tokens_in is not None:
        metric["tokens_in"] = int(tokens_in)
    if tokens_out is not None:
        metric["tokens_out"] = int(tokens_out)

    helper = _lib_dir(root) / "metrics-append.sh"
    if not helper.is_file():
        sys.stderr.write(
            f"signalos harness: metrics-append.sh missing at {helper} — "
            "metrics row not written (fail-open)\n"
        )
        return 0
    # Relative-from-root POSIX path + cwd=str(root). See _fire_hook for
    # the full rationale (WSL bash vs git-bash path-form portability).
    # Sandbox wrap: same rationale as _fire_hook -- bundle script,
    # workspace-scoped writes only.
    from signalos_lib.sandbox import maybe_wrap_for_sandbox
    argv, _ = maybe_wrap_for_sandbox(
        root,
        ["bash", helper.relative_to(root).as_posix(),
         "--session-id", session_id,
         "--metric", json.dumps(metric, separators=(",", ":"))],
    )
    proc = subprocess.run(
        argv,
        check=False,
        cwd=str(root),
    )
    return proc.returncode


# ---------------------------------------------------------------------------
# Per-call state file
# ---------------------------------------------------------------------------

def _write_state(
    root: Path,
    session_id: str,
    call_id: str,
    **fields: Any,
) -> None:
    """Upsert state.json for a harness call.

    This is NOT the journal. state.json is a mutable per-call file the
    harness uses to communicate with `signalos harness status` and
    `signalos harness abort`. The append-only truth record is still the
    journal, written through the hook scripts.
    """
    cdir = _call_dir(root, session_id, call_id)
    cdir.mkdir(parents=True, exist_ok=True)
    path = _state_path(root, session_id, call_id)

    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    existing.update(fields)
    existing.setdefault("call_id", call_id)
    existing.setdefault("session_id", session_id)
    existing["updated_at"] = _now_iso()

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_state(root: Path, session_id: str, call_id: str) -> dict[str, Any]:
    path = _state_path(root, session_id, call_id)
    if not path.exists():
        raise FileNotFoundError(
            f"signalos harness: call state not found: "
            f".signalos/sessions/{session_id}/harness/{call_id}/state.json"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _is_aborted(root: Path, session_id: str, call_id: str) -> bool:
    return _abort_flag_path(root, session_id, call_id).exists()


# ---------------------------------------------------------------------------
# Session-id resolution
# ---------------------------------------------------------------------------

def _resolve_or_create_session(root: Path, session_id: str | None) -> str:
    """Return a usable session_id. If none provided, create a new one.

    The harness does not itself emit a `session.start` event (that is
    the session-start hook's job). If we create a new session dir here
    it's only so the hook scripts downstream have a place to write.
    """
    if session_id:
        return session_id

    sid = datetime.now(timezone.utc).strftime("harness-session-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
    _session_dir(root, sid).mkdir(parents=True, exist_ok=True)

    # Fire session-start hook if present. Fail-open if missing.
    session_start = _hooks_dir(root) / "session-start"
    if session_start.is_dir():
        # session-start is a FILE script in v1.0.3+ at
        # core/execution/hooks/session-start/session-start.sh (W1.1
        # convention). Use it if present; otherwise skip.
        script = session_start / "session-start.sh"
        if script.is_file():
            # Relative-from-root POSIX path + cwd=str(root). See
            # _fire_hook for the full rationale.
            from signalos_lib.sandbox import maybe_wrap_for_sandbox
            argv, _ = maybe_wrap_for_sandbox(
                root,
                ["bash", script.relative_to(root).as_posix(),
                 "--session-id", sid, "--actor", HARNESS_TOOL_NAME],
            )
            subprocess.run(
                argv,
                check=False,
                cwd=str(root),
            )
    return sid


# ---------------------------------------------------------------------------
# Public API — run_step / get_status / abort_call
# ---------------------------------------------------------------------------



def _safe_error(exc: BaseException) -> str:
    """Return a sanitized error string for user-facing stderr output.

    Strips internal file paths and env var names from exception messages
    to prevent accidental disclosure of filesystem layout or secrets.
    Set SIGNALOS_DEBUG=1 to get the full trace instead.
    """
    if os.environ.get("SIGNALOS_DEBUG"):
        return str(exc)
    msg = str(exc)
    # Strip absolute paths (Unix and Windows)
    import re as _re
    msg = _re.sub(r"(/[a-zA-Z0-9_.\-]+){3,}", "[path]", msg)
    msg = _re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\){2,}[^\\\s]+", "[path]", msg)
    # Strip env var values that look like secrets
    msg = _re.sub(r"[A-Z][A-Z0-9_]{4,}=[^\s]+", "[env]", msg)
    return msg or "[internal error — set SIGNALOS_DEBUG=1 for full trace]"

def run_step(
    step_id: str,
    *,
    prompt: str | None = None,
    prompt_file: Path | None = None,
    model: str = DEFAULT_MODEL,
    session_id: str | None = None,
    parent_step_id: str | None = None,
    cwd: Path | None = None,
    intent: str | None = None,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Execute one PLAN step headlessly and emit the four W1.1 events.

    Returns a dict with at least:
        call_id, session_id, step_id, status ("completed"|"failed"|"aborted"),
        duration_ms, response_preview, tokens_in, tokens_out, exit_code.

    The `provider` parameter (AMD-CORE-007) selects the LLM backend.
    If None, `_resolve_provider()` is called to pick via env vars.

    Never raises for an LLM provider failure — the failure is captured
    in the `status = "failed"` return and the `step.failed` event.
    """
    # ---- Input validation ----
    if not step_id or not isinstance(step_id, str):
        raise ValueError("signalos harness: --step is required and must be a string")
    resolved_prompt = _resolve_prompt(prompt, prompt_file)
    if not resolved_prompt.strip():
        raise ValueError(
            "signalos harness: prompt is empty — pass --prompt '<text>' or --prompt-file <path>"
        )

    # ---- Provider resolution (AMD-CORE-007) ----
    active_provider = provider if provider is not None else _resolve_provider()

    root = _repo_root(cwd)
    sid = _resolve_or_create_session(root, session_id)
    call_id = _generate_call_id()
    started_at = _now_iso()

    _call_dir(root, sid, call_id).mkdir(parents=True, exist_ok=True)
    _write_state(
        root, sid, call_id,
        step_id=step_id,
        status="running",
        started_at=started_at,
        model=model,
        parent_step_id=parent_step_id,
        intent=intent or f"headless harness call for step {step_id}",
    )

    # ---- Emit step.started via the step-started hook ----
    step_started_extra = [
        "--intent", intent or f"headless harness call for step {step_id}",
    ]
    if parent_step_id:
        step_started_extra.extend(["--parent-step-id", parent_step_id])
    _fire_hook(
        root, "step-started",
        session_id=sid, step_id=step_id,
        extra_args=step_started_extra,
    )

    # ---- Do the work ----
    t0 = time.perf_counter()
    response_text: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    failure: str | None = None

    try:
        # AMD-CORE-011: sanitize prompt before any LLM transmission
        resolved_prompt = _redact_text(root, resolved_prompt)
        if _is_aborted(root, sid, call_id):
            failure = "aborted before LLM call"
        else:
            response_text, tokens_in, tokens_out = active_provider.call(
                prompt=resolved_prompt,
                model=model,
            )
    except Exception as exc:  # defensive — never let an SDK hiccup leak
        failure = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.perf_counter() - t0) * 1000)
    # Persist a redacted preview next to state.json. Trimmed aggressively —
    # full response bodies are out-of-scope for the append-only journal
    # per AMD-CORE-001 §invariants.
    _persist_response_preview(root, sid, call_id, response_text)

    final_status: str
    if _is_aborted(root, sid, call_id):
        final_status = "aborted"
    elif failure is not None:
        final_status = "failed"
    else:
        final_status = "completed"

    _write_state(
        root, sid, call_id,
        status=final_status,
        ended_at=_now_iso(),
        duration_ms=duration_ms,
        failure=failure,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )

    # ---- Emit step.{completed|failed} via the matching hook ----
    if final_status == "completed":
        completed_extra = [
            "--outcome", "ok",
            "--duration-ms", str(duration_ms),
        ]
        if tokens_in is not None:
            completed_extra.extend(["--tokens-in", str(tokens_in)])
        if tokens_out is not None:
            completed_extra.extend(["--tokens-out", str(tokens_out)])
        _fire_hook(
            root, "step-completed",
            session_id=sid, step_id=step_id,
            extra_args=completed_extra,
        )
    else:
        reason = (failure or final_status).strip() or "aborted"
        _fire_hook(
            root, "step-failed",
            session_id=sid, step_id=step_id,
            extra_args=[
                "--reason", reason,
                "--exit-code", "2",
            ],
        )

    # ---- Emit metrics row (one per call; hook-level metrics belong to
    # the per-hook scripts) ----
    _append_metric(
        root, sid, step_id,
        duration_ms=duration_ms,
        tokens_in=tokens_in, tokens_out=tokens_out,
        hook=None,  # this is a tool-level row, not a hook-fired row
    )

    exit_code = 0 if final_status == "completed" else 2
    return {
        "call_id": call_id,
        "session_id": sid,
        "step_id": step_id,
        "status": final_status,
        "duration_ms": duration_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "response_preview": response_text[:200] if response_text else "",
        "failure": failure,
        "exit_code": exit_code,
    }


def get_status(call_id: str, *, session_id: str | None = None, cwd: Path | None = None) -> dict[str, Any]:
    """Return the state.json contents for a given call.

    When `session_id` is None, scan every session in .signalos/sessions/
    for a matching call. Raises FileNotFoundError if no match.
    """
    root = _repo_root(cwd)

    candidates: list[Path]
    if session_id:
        candidates = [_state_path(root, session_id, call_id)]
    else:
        sessions_root = root / REPO_ROOT_MARKER / "sessions"
        candidates = []
        if sessions_root.is_dir():
            for sdir in sessions_root.iterdir():
                if sdir.is_dir():
                    candidates.append(sdir / "harness" / call_id / "state.json")

    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    raise FileNotFoundError(
        f"signalos harness: no state.json found for call {call_id}"
    )


def abort_call(call_id: str, *, session_id: str | None = None, cwd: Path | None = None) -> dict[str, Any]:
    """Write the abort.flag for a running call and update state.json.

    Idempotent: calling abort on an already-completed call is a no-op
    that returns the current state. This function does NOT emit a
    step.aborted hook — the running harness process observes the flag
    and emits its own step.failed event with reason "aborted".
    """
    root = _repo_root(cwd)
    state = get_status(call_id, session_id=session_id, cwd=cwd)
    sid = state["session_id"]
    current = state.get("status", "unknown")

    if current in {"completed", "failed", "aborted"}:
        return {**state, "abort_requested": False, "reason": f"status={current}; nothing to abort"}

    flag = _abort_flag_path(root, sid, call_id)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(_now_iso() + "\n", encoding="utf-8")

    _write_state(root, sid, call_id, abort_requested=True)
    return {**state, "abort_requested": True}


# ---------------------------------------------------------------------------
# Small internals
# ---------------------------------------------------------------------------

def _resolve_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    if prompt and prompt_file:
        raise ValueError(
            "signalos harness: pass --prompt or --prompt-file, not both"
        )
    if prompt:
        return prompt
    if prompt_file:
        p = Path(prompt_file)
        if not p.is_file():
            raise ValueError(f"signalos harness: --prompt-file not found: {p}")
        return p.read_text(encoding="utf-8")
    return ""


def _persist_response_preview(
    root: Path,
    session_id: str,
    call_id: str,
    response_text: str,
) -> None:
    """Write a truncated, redacted preview beside state.json.

    Full response bodies are deliberately not journaled (AMD-CORE-001
    invariant). This file is a human-readable sample for debugging.
    """
    if not response_text:
        return
    cdir = _call_dir(root, session_id, call_id)
    cdir.mkdir(parents=True, exist_ok=True)

    preview_path = cdir / "response.preview.txt"
    # Redact via the shared Python filter.
    redacted = _redact_text(root, response_text[:4000])
    preview_path.write_text(redacted, encoding="utf-8")


def _redact_text(root: Path, text: str) -> str:
    """Run text through core/execution/hooks/_lib/redact.py --filter.

    The redactor reads JSON by default, so we wrap the text as a
    single-field JSON and unwrap after. On any redactor error, return
    the text unchanged — failure-to-redact must not crash the harness,
    only the journal write path is required to fail hard on redaction.
    """
    helper = _lib_dir(root) / "redact.py"
    if not helper.is_file():
        return text
    wrapped = json.dumps({"t": text})
    try:
        # Sandbox wrap: redact.py is a pure stdin/stdout filter; safe to
        # run in a container. Use relative-from-root path so the helper
        # resolves the same way inside the container (workspace at
        # /workspace) as on host (cwd=root). The image classifier picks
        # python:3.11-slim from the `python3` cmd[0].
        from signalos_lib.sandbox import maybe_wrap_for_sandbox
        argv, _ = maybe_wrap_for_sandbox(
            root,
            ["python3", helper.relative_to(root).as_posix(), "--filter"],
        )
        proc = subprocess.run(
            argv,
            input=wrapped,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(root),
        )
        if proc.returncode != 0:
            return text
        out = json.loads(proc.stdout.strip() or wrapped)
        return str(out.get("t", text))
    except Exception:
        return text
