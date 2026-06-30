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
#   5. AMD-CORE-007 / 007.1: LLM provider abstraction. The `LLMProvider`
#      Protocol plus native Anthropic/Gemini wrappers and one
#      OpenAICompatibleProvider (covering OpenAI + Groq, Mistral, DeepSeek,
#      OpenRouter, xAI, Together, Cerebras, DashScope + local Ollama)
#      decouple the harness from any single SDK. A single provider table
#      (`PROVIDERS`) is the one source of truth. The active provider is
#      auto-detected from whichever provider key is set, overridable via
#      `SIGNALOS_LLM_PROVIDER`. The model is DISCOVERED from the provider's
#      API (no hardcoded id), overridable via SIGNALOS_LLM_MODEL / --model.
#      `SIGNALOS_HARNESS_TEST=1` overrides to TestProvider (no network).
#
# Exit-code contract (propagated by commands/harness.py):
#   0 — step.completed event emitted; call state = "completed"
#   1 — user error (bad step-id, missing prompt, bad session)
#   2 — execution error (provider returned an error, hook script
#        missing, IO failure); step.failed event emitted when possible
#   3 — policy refusal (e.g. attempting to resume an aborted call)


from __future__ import annotations

__all__ = [
    "run_step",
    "DEFAULT_MODEL",
    "LLMProvider",
    # AMD-CORE-007.1 — multi-provider single source of truth + discovery
    "ProviderSpec",
    "PROVIDERS",
    "PROVIDER_NAMES",
    "PROVIDER_ENV_VARS",
    "OpenAICompatibleProvider",
    "discover_model",
    "list_models",
    "pick_best_model",
    "resolve_model",
    "clear_model_cache",
    # v4 Phase 2.3 — new agent-loop protocol (alongside the frozen LLMProvider)
    "AgentProvider",
    "AgentResponse",
    "ToolCall",
    "TokenUsage",
    "StreamDelta",
    "AgentTestProvider",
]  # W-2: explicit public API

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT_MARKER = ".signalos"

# DEPRECATED back-compat alias. It is NO LONGER a silent default: the
# harness discovers the flagship model from the active provider's API
# (see `discover_model` / `resolve_model`). This constant is retained
# only so that older importers keep importing something; it must NOT be
# passed as a silent default into provider.call() anymore. Model
# resolution order is: explicit --model → SIGNALOS_LLM_MODEL → discovery.
DEFAULT_MODEL = None  # type: ignore[assignment]

# The `tool` identifier the 8th emitter reports into hook events and
# metrics. Must match the folder name core/tool-adapters/emitters/harness/.
HARNESS_TOOL_NAME = "harness"

# Canned response used in SIGNALOS_HARNESS_TEST=1 mode. Deterministic
# so proof scenarios can diff against a fixed string.
_HARNESS_TEST_CANNED = "SIGNALOS_HARNESS_TEST: canned harness response for proof scenarios."


# ---------------------------------------------------------------------------
# Provider table — SINGLE SOURCE OF TRUTH (AMD-CORE-007.1)
#
# Every provider the harness can route lives here exactly once. Both the
# CLI/auto-detect path (this module) AND product/llm_provider.py derive
# their notion of "which providers exist / which env vars to check" from
# this table, so the two lists can never drift again.
#
#   kind:
#     "anthropic"          → native AnthropicProvider (anthropic SDK)
#     "gemini"             → native GeminiProvider (google-generativeai SDK)
#     "openai-compatible"  → OpenAICompatibleProvider (openai SDK; base_url
#                             None means the canonical OpenAI endpoint)
#     "ollama"             → OpenAI-compatible local server (openai SDK,
#                             base_url http://localhost:11434/v1)
#     "test"               → deterministic TestProvider (no SDK, no network)
#
#   env_var: provider API key env var, or None for ollama/test.
#   base_url: OpenAI-compatible base URL, or None for native SDKs/OpenAI.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderSpec:
    """Static description of one routable LLM provider."""

    name: str
    kind: str  # anthropic | gemini | openai-compatible | ollama | test
    env_var: str | None
    base_url: str | None = None
    aliases: tuple[str, ...] = ()


# Priority order is intentional: the auto-detect path walks this dict in
# insertion order and picks the first provider whose env_var is present.
# Anthropic, OpenAI, Gemini lead; the OpenAI-compatible fleet follows;
# local Ollama is the last network-free fallback. test is never auto-picked.
_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec("anthropic", "anthropic", "ANTHROPIC_API_KEY"),
    ProviderSpec(
        "openai", "openai-compatible", "OPENAI_API_KEY", None,
        aliases=("open_ai",),
    ),
    ProviderSpec(
        "gemini", "gemini", "GEMINI_API_KEY", None,
        aliases=("google", "google-generativeai"),
    ),
    ProviderSpec(
        "groq", "openai-compatible", "GROQ_API_KEY",
        "https://api.groq.com/openai/v1",
    ),
    ProviderSpec(
        "mistral", "openai-compatible", "MISTRAL_API_KEY",
        "https://api.mistral.ai/v1",
    ),
    ProviderSpec(
        "deepseek", "openai-compatible", "DEEPSEEK_API_KEY",
        "https://api.deepseek.com",
    ),
    ProviderSpec(
        "openrouter", "openai-compatible", "OPENROUTER_API_KEY",
        "https://openrouter.ai/api/v1",
    ),
    ProviderSpec(
        "xai", "openai-compatible", "XAI_API_KEY",
        "https://api.x.ai/v1", aliases=("grok",),
    ),
    ProviderSpec(
        "together", "openai-compatible", "TOGETHER_API_KEY",
        "https://api.together.xyz/v1", aliases=("togetherai",),
    ),
    ProviderSpec(
        "cerebras", "openai-compatible", "CEREBRAS_API_KEY",
        "https://api.cerebras.ai/v1",
    ),
    ProviderSpec(
        "dashscope", "openai-compatible", "DASHSCOPE_API_KEY",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        aliases=("qwen", "alibaba"),
    ),
    ProviderSpec(
        "ollama", "ollama", None,
        "http://localhost:11434/v1", aliases=("local",),
    ),
    ProviderSpec("test", "test", None, None, aliases=("mock",)),
)

# Canonical-name → spec, plus alias → spec, for O(1) lookup.
PROVIDERS: dict[str, ProviderSpec] = {}
for _spec in _PROVIDER_SPECS:
    PROVIDERS[_spec.name] = _spec
    for _alias in _spec.aliases:
        PROVIDERS[_alias] = _spec
del _spec

# Public, ordered list of canonical provider names (auto-detect order).
PROVIDER_NAMES: list[str] = [s.name for s in _PROVIDER_SPECS]

# Public, ordered list of provider *key* env vars (None entries dropped).
# product/llm_provider.py derives _PROVIDER_ENV_VARS from this so the two
# modules cannot disagree about which keys signal "an LLM is available".
PROVIDER_ENV_VARS: list[str] = [
    s.env_var for s in _PROVIDER_SPECS if s.env_var is not None
]


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


class OpenAICompatibleProvider:
    """LLM provider for OpenAI and every OpenAI-compatible endpoint.

    Powers OpenAI itself plus the eight compatible providers (Groq,
    Mistral, DeepSeek, OpenRouter, xAI, Together, Cerebras, DashScope) and
    the local Ollama server — they all speak the OpenAI chat-completions
    wire format, so one `openai.OpenAI(base_url=..., api_key=...)` client
    covers them all.

    `base_url=None` targets the canonical OpenAI endpoint. `api_key_env`
    is the env var holding the credential; it is None only for Ollama,
    whose local server ignores the key (a placeholder is sent so the SDK
    does not refuse to construct a client).

    The `openai` package is imported lazily so stdlib-only installs keep
    working until an autonomous call actually needs it.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key_env: str | None = "OPENAI_API_KEY",
    ) -> None:
        self.base_url = base_url
        self.api_key_env = api_key_env

    def _client(self):
        try:
            import openai as _openai  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "signalos harness: the `openai` package is not installed. "
                "Run `pip install openai>=1.0` and retry."
            ) from exc

        kwargs: dict[str, Any] = {}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        if self.api_key_env:
            api_key = os.environ.get(self.api_key_env)
            if not api_key:
                raise RuntimeError(
                    f"signalos harness: {self.api_key_env} is not set. "
                    "Set the provider API key or pick a different provider."
                )
            kwargs["api_key"] = api_key
        elif self.base_url:
            # Ollama (no key) — the SDK still requires a non-empty key string.
            kwargs["api_key"] = "ollama"
        return _openai.OpenAI(**kwargs)

    def call(
        self,
        prompt: str,
        model: str,
    ) -> tuple[str, int | None, int | None]:
        client = self._client()
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


class OpenAIProvider(OpenAICompatibleProvider):
    """Back-compat alias — canonical OpenAI endpoint via the openai SDK."""

    def __init__(self) -> None:
        super().__init__(base_url=None, api_key_env="OPENAI_API_KEY")


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
# v4 Phase 2.3 — AgentProvider protocol (the tool-calling agent loop path)
#
# This protocol is NEW and lives ALONGSIDE the frozen `LLMProvider` above.
# `LLMProvider.call(prompt, model) -> (text, in, out)` is untouched and keeps
# serving the legacy one-shot harness/orchestrator path. The agent loop
# (agent_loop.py) talks to the multi-turn, tool-aware `AgentProvider` instead.
#
# Architecture Decision Q1: LiteLLMAgentProvider (in product/provider_adapter.py)
# implements this protocol by wrapping litellm.completion(). The ProviderAdapter
# wraps it and adds capability detection. AgentTestProvider below is the
# deterministic CI double (INV-6) — no network, scriptable tool-call responses.
# ---------------------------------------------------------------------------


@dataclass
class TokenUsage:
    """Normalized token accounting for one AgentProvider.chat() turn."""

    input_tokens: int | None = None
    output_tokens: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class ToolCall:
    """A single tool-use request emitted by the model.

    `id` is the provider's correlation id — it MUST be echoed back in the
    tool-result message so the provider can match result to request.
    `arguments` is the already-parsed argument dict (the adapter parses the
    provider's JSON string form before constructing this).
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class StreamDelta:
    """One incremental chunk during a streamed chat turn.

    `kind` is "text" for a token delta or "tool_call" for an assembling
    tool-call fragment. The streaming path (Q5 / Phase 3.5) consumes these.
    """

    kind: str
    text: str | None = None
    tool_call: ToolCall | None = None


@dataclass
class AgentResponse:
    """Normalized result of one AgentProvider.chat() turn.

    `stop_reason` is normalized across providers to one of:
    "end_turn" | "tool_use" | "max_tokens" | "error".
    When `stop_reason == "tool_use"`, `tool_calls` is non-empty.
    `stream` is only set when chat(stream=True) was requested.
    """

    content: str | None
    tool_calls: list[ToolCall] | None
    stop_reason: str
    usage: TokenUsage
    stream: Iterator[StreamDelta] | None = None
    raw: Any = None  # provider-native response object, for debugging only

    def as_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [tc.as_dict() for tc in (self.tool_calls or [])],
            "stop_reason": self.stop_reason,
            "usage": self.usage.as_dict(),
        }


@runtime_checkable
class AgentProvider(Protocol):
    """Protocol for the tool-calling agent-loop provider (v4 Q1).

    Distinct from the frozen `LLMProvider`. Implementations take an
    OpenAI-format messages array plus optional provider-agnostic tool
    definitions and return a normalized `AgentResponse`.
    """

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> AgentResponse:
        """Run one multi-turn completion and return a normalized response."""
        ...


class AgentTestProvider:
    """Deterministic AgentProvider double for CI (INV-6).

    No network, no SDK. The agent loop and its governance tests drive this
    instead of a live provider. A `script` of canned `AgentResponse` objects
    is replayed one per `chat()` call; once exhausted it returns a plain
    end_turn text response. This lets a test stage a sequence like
    "ask for a write_file tool call, then end the turn".

    If no script is provided it always returns a fixed end_turn message,
    mirroring the spirit of the frozen `TestProvider`.
    """

    DEFAULT_TEXT = "AGENT_TEST: deterministic end_turn response."

    def __init__(self, script: list[AgentResponse] | None = None) -> None:
        self._script: list[AgentResponse] = list(script or [])
        self._calls: list[dict[str, Any]] = []

    @property
    def calls(self) -> list[dict[str, Any]]:
        """Record of every chat() invocation, for test assertions."""
        return self._calls

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> AgentResponse:
        self._calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": tools,
                "stream": stream,
            }
        )
        if self._script:
            return self._script.pop(0)
        return AgentResponse(
            content=self.DEFAULT_TEXT,
            tool_calls=None,
            stop_reason="end_turn",
            usage=TokenUsage(
                input_tokens=sum(len(str(m).encode("utf-8")) for m in messages),
                output_tokens=len(self.DEFAULT_TEXT.encode("utf-8")),
            ),
        )


# ---------------------------------------------------------------------------
# Provider resolution (AMD-CORE-007)
# ---------------------------------------------------------------------------

def _spec_for(name: str) -> ProviderSpec:
    """Resolve a (possibly aliased) provider name to its ProviderSpec.

    Raises RuntimeError listing all valid provider names on an unknown name.
    """
    key = name.lower().strip()
    spec = PROVIDERS.get(key)
    if spec is None:
        raise RuntimeError(
            f"signalos harness: unknown provider '{name}'. "
            f"Valid values: {', '.join(PROVIDER_NAMES)}. "
            "Set SIGNALOS_LLM_PROVIDER or pass --provider."
        )
    return spec


def _auto_detect_provider() -> str:
    """Return the first provider (in table order) whose env_var is set.

    Falls back to 'anthropic' when no provider key is present at all, so
    behaviour stays deterministic and the resulting error (no key) is
    clear rather than silent.
    """
    for spec in _PROVIDER_SPECS:
        if spec.kind == "test":
            continue
        if spec.env_var and os.environ.get(spec.env_var):
            return spec.name
    return "anthropic"


def _build_provider(spec: ProviderSpec) -> LLMProvider:
    """Instantiate the concrete provider for a spec."""
    if spec.kind == "anthropic":
        return AnthropicProvider()
    if spec.kind == "gemini":
        return GeminiProvider()
    if spec.kind == "test":
        return TestProvider()
    # "openai-compatible" and "ollama" both ride the OpenAI wire format.
    return OpenAICompatibleProvider(
        base_url=spec.base_url,
        api_key_env=spec.env_var,
    )


def _resolve_provider_name(name: str | None = None) -> str:
    """Return the canonical provider name to use (no instantiation).

    Order: explicit `name` → SIGNALOS_LLM_PROVIDER → auto-detect from the
    first present provider key → fall back to "anthropic".
    """
    chosen = name or os.environ.get("SIGNALOS_LLM_PROVIDER") or _auto_detect_provider()
    return _spec_for(chosen).name


def _resolve_provider(name: str | None = None) -> LLMProvider:
    """Return the appropriate LLMProvider instance.

    Resolution order:
    1. SIGNALOS_HARNESS_TEST=1 → TestProvider (no SDK, no network).
    2. Explicit `name` argument (the --provider CLI flag).
    3. SIGNALOS_LLM_PROVIDER env var.
    4. Auto-detect: first provider (priority order: anthropic, openai,
       gemini, then the OpenAI-compatible fleet, then ollama) whose API
       key env var is set in the environment.
    5. Fall back to "anthropic".

    Unknown provider name → RuntimeError listing all valid names.
    """
    if os.environ.get("SIGNALOS_HARNESS_TEST") == "1":
        return TestProvider()
    return _build_provider(_spec_for(_resolve_provider_name(name)))


# ---------------------------------------------------------------------------
# Model discovery (AMD-CORE-007.1) — NO hardcoded model ids.
#
# Each provider exposes a "list models" API; we filter out small / cheap /
# non-chat variants with a generic heuristic and pick the newest flagship.
# Discovery is cached per (provider_name, base_url) for the process lifetime
# so repeat calls don't re-hit the API.
# ---------------------------------------------------------------------------

# Generic substrings that mark a model as small / cheap / non-chat. Provider
# agnostic on purpose — we never encode exact model ids (they go stale).
_NON_FLAGSHIP_SUBSTRINGS = (
    "mini", "nano", "lite", "flash", "small", "instant", "tiny", "haiku",
    "embed", "embedding", "whisper", "tts", "dall", "image", "rerank",
    "guard", "moderation",
)

# Cache: (provider_name, base_url) → discovered model id (or None).
_MODEL_CACHE: dict[tuple[str, str | None], str | None] = {}


def clear_model_cache() -> None:
    """Drop the per-process model-discovery cache (used by tests)."""
    _MODEL_CACHE.clear()


def _version_key(model_id: str) -> tuple:
    """Version-aware sort key — newest first when sorted descending.

    Extracts all numeric runs from the id into a tuple (so 4.8 > 4.5 > 3.5),
    then appends the lowercased id as a lexicographic tiebreaker.
    """
    import re as _re

    nums = tuple(int(n) for n in _re.findall(r"\d+", model_id))
    return (nums, model_id.lower())


def _is_non_flagship(model_id: str) -> bool:
    """True if the id names a small/cheap/non-chat variant.

    Matching is boundary-aware on purpose: a banned term only counts when
    it is NOT embedded inside a larger alphabetic word. This is what stops
    "mini" from wrongly matching "geMINI" (Gemini's flagship) while still
    catching "gpt-4o-mini", "claude-haiku-4-5", "text-embedding-3", etc.
    A boundary is the string edge or any non-letter (digit, '-', '_', '.',
    '/').
    """
    import re as _re

    low = model_id.lower()
    for sub in _NON_FLAGSHIP_SUBSTRINGS:
        # (?<![a-z]) — not preceded by a letter; (?![a-z]) — not followed by one.
        if _re.search(rf"(?<![a-z]){_re.escape(sub)}(?![a-z])", low):
            return True
    return False


def pick_best_model(model_ids: list[str]) -> str | None:
    """Pick the best-fit flagship from a list of model ids — generic.

    Heuristic (no exact-id encoding): drop obvious small/cheap/non-chat
    variants, then from the remainder pick the NEWEST by a version-aware
    key (numeric tuples desc, lexicographic fallback). If filtering empties
    the pool, pick the newest of the full list instead.
    """
    if not model_ids:
        return None

    filtered = [m for m in model_ids if not _is_non_flagship(m)]
    pool = filtered or list(model_ids)
    return max(pool, key=_version_key)


def list_models(provider_name: str | None = None) -> list[str]:
    """Return the available model ids for a provider via its API.

    Network call (except for ollama, which is local). Raises on SDK/HTTP
    errors; callers wrap this. Returns [] when the provider reports none.
    """
    spec = _spec_for(_resolve_provider_name(provider_name))

    if spec.kind == "anthropic":
        import anthropic  # type: ignore[import-not-found]

        client = anthropic.Anthropic()
        return [m.id for m in client.models.list()]

    if spec.kind == "gemini":
        import google.generativeai as genai  # type: ignore[import-not-found]

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        out: list[str] = []
        for m in genai.list_models():
            methods = getattr(m, "supported_generation_methods", None) or []
            if "generateContent" in methods:
                out.append(m.name)
        return out

    if spec.kind == "ollama":
        import urllib.request

        url = "http://localhost:11434/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m["name"] for m in data.get("models", []) if m.get("name")]

    # openai-compatible (openai + the eight compatible endpoints)
    import openai as _openai  # type: ignore[import-not-found]

    kwargs: dict[str, Any] = {}
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    if spec.env_var:
        api_key = os.environ.get(spec.env_var)
        if api_key:
            kwargs["api_key"] = api_key
    client = _openai.OpenAI(**kwargs)
    return [m.id for m in client.models.list()]


def discover_model(provider_name: str | None = None) -> str | None:
    """Discover the best-fit flagship model for a provider.

    Caches the result per (provider, base_url) for the process. Returns
    None when discovery fails (network/SDK error) or the provider reports
    no usable models — callers turn None into a clear, fail-closed error.
    """
    spec = _spec_for(_resolve_provider_name(provider_name))
    cache_key = (spec.name, spec.base_url)
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    try:
        ids = list_models(spec.name)
        best = pick_best_model(ids)
    except Exception:
        best = None

    _MODEL_CACHE[cache_key] = best
    return best


def resolve_model(
    model: str | None,
    provider_name: str | None = None,
) -> str:
    """Resolve the model id to use, fail-closed.

    Order: explicit `model` → SIGNALOS_LLM_MODEL env → discover_model().
    Raises RuntimeError when discovery yields nothing, so the harness never
    silently substitutes a stale hardcoded id.
    """
    if model:
        return model
    env_model = os.environ.get("SIGNALOS_LLM_MODEL")
    if env_model:
        return env_model
    canonical = _resolve_provider_name(provider_name)
    discovered = discover_model(canonical)
    if not discovered:
        raise RuntimeError(
            f"signalos harness: could not discover a model for provider "
            f"'{canonical}'; set SIGNALOS_LLM_MODEL or pass --model."
        )
    return discovered


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
    """Invoke metrics-append.sh with a harness-origin metric row."""
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
    """Upsert state.json for a harness call."""
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
    """Return a usable session_id. If none provided, create a new one."""
    if session_id:
        return session_id

    sid = datetime.now(timezone.utc).strftime("harness-session-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
    _session_dir(root, sid).mkdir(parents=True, exist_ok=True)

    session_start = _hooks_dir(root) / "session-start"
    if session_start.is_dir():
        script = session_start / "session-start.sh"
        if script.is_file():
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
    """Return a sanitized error string for user-facing stderr output."""
    if os.environ.get("SIGNALOS_DEBUG"):
        return str(exc)
    msg = str(exc)
    import re as _re
    msg = _re.sub(r"(/[a-zA-Z0-9_.\-]+){3,}", "[path]", msg)
    msg = _re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\){2,}[^\\\s]+", "[path]", msg)
    msg = _re.sub(r"[A-Z][A-Z0-9_]{4,}=[^\s]+", "[env]", msg)
    return msg or "[internal error — set SIGNALOS_DEBUG=1 for full trace]"

def run_step(
    step_id: str,
    *,
    prompt: str | None = None,
    prompt_file: Path | None = None,
    model: str | None = None,
    session_id: str | None = None,
    parent_step_id: str | None = None,
    cwd: Path | None = None,
    intent: str | None = None,
    provider: LLMProvider | None = None,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Execute one PLAN step headlessly and emit the four W1.1 events.

    Model resolution (when *model* is None): SIGNALOS_LLM_MODEL env →
    discovery from the resolved provider's API. There is no hardcoded
    silent default. Test mode and an explicitly injected TestProvider
    skip discovery entirely (no network), keeping CI offline.
    """
    if not step_id or not isinstance(step_id, str):
        raise ValueError("signalos harness: --step is required and must be a string")
    resolved_prompt = _resolve_prompt(prompt, prompt_file)
    if not resolved_prompt.strip():
        raise ValueError(
            "signalos harness: prompt is empty — pass --prompt '<text>' or --prompt-file <path>"
        )

    active_provider = provider if provider is not None else _resolve_provider(provider_name)

    # Resolve the model, fail-closed, NO hardcoded silent default. Test mode
    # or an injected TestProvider needs no real model and must not hit the
    # network — feed those a deterministic placeholder.
    if model is None:
        test_mode = (
            os.environ.get("SIGNALOS_HARNESS_TEST") == "1"
            or isinstance(active_provider, TestProvider)
        )
        if test_mode:
            model = "test"
        else:
            model = resolve_model(None, provider_name)

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

    t0 = time.perf_counter()
    response_text: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    failure: str | None = None

    try:
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

    _append_metric(
        root, sid, step_id,
        duration_ms=duration_ms,
        tokens_in=tokens_in, tokens_out=tokens_out,
        hook=None,
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
    """Return the state.json contents for a given call."""
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
    """Write the abort.flag for a running call and update state.json."""
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
    """Write a truncated, redacted preview beside state.json."""
    if not response_text:
        return
    cdir = _call_dir(root, session_id, call_id)
    cdir.mkdir(parents=True, exist_ok=True)

    preview_path = cdir / "response.preview.txt"
    redacted = _redact_text(root, response_text[:4000])
    preview_path.write_text(redacted, encoding="utf-8")


def _redact_text(root: Path, text: str) -> str:
    """Run text through core/execution/hooks/_lib/redact.py --filter."""
    helper = _lib_dir(root) / "redact.py"
    if not helper.is_file():
        return text
    wrapped = json.dumps({"t": text})
    try:
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
