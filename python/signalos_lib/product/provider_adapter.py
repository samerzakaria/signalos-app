# signalos_lib/product/provider_adapter.py
# v4 Phase 2.2 + 2.11 — Provider adapter with capability detection.
#
# Architecture Decision Q1: LiteLLM sits behind the AgentProvider protocol
# (harness.AgentProvider). LiteLLMAgentProvider implements that protocol by
# wrapping litellm.completion(); ProviderAdapter wraps the provider and adds
# capability detection (streaming, tool_calls, json_schema, context_length,
# model_list). If supports_tool_calls is False the agent loop falls back to
# text-only mode (INV-7).
#
# litellm is imported lazily so stdlib-only installs and CI (which uses the
# deterministic AgentTestProvider from harness.py) never require it. No silent
# failures (INV-4): a missing litellm raises a clear RuntimeError with an
# install hint when a live provider is actually requested.

from __future__ import annotations

__all__ = [
    "ProviderCapabilities",
    "ProviderAdapter",
    "LiteLLMAgentProvider",
    "detect_capabilities",
    "ProviderAuthError",
]

from dataclasses import dataclass, field
from typing import Any, Iterator

from ..harness import (
    AgentProvider,
    AgentResponse,
    StreamDelta,
    TokenUsage,
    ToolCall,
)


class ProviderAuthError(RuntimeError):
    """Raised when a provider rejects the request for auth reasons.

    Surfaced to the user (INV-4) so the chat can show "connect a provider"
    rather than a silent empty response (test T05).
    """


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------

# Conservative known-context windows for common models. LiteLLM exposes
# get_model_info() at runtime; we only use this table as a fallback when
# litellm is unavailable or has no entry. Values are deliberately modest
# (compression triggers at 80% of context_length, so under-reporting is safe).
_CONTEXT_LENGTHS: dict[str, int] = {
    "claude-sonnet-4-5": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-opus": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "gemini-1.5-pro": 1_000_000,
    "gemini-1.5-flash": 1_000_000,
}

_DEFAULT_CONTEXT_LENGTH = 8_192

# Model-name fragments that are known NOT to support function/tool calling.
# Used as a fallback when litellm.supports_function_calling is unavailable.
_NO_TOOL_FRAGMENTS = (
    "instruct",       # base instruct models without tools
    "embedding",
    "whisper",
)


@dataclass
class ProviderCapabilities:
    """Detected capabilities for one model behind the adapter."""

    model: str
    supports_streaming: bool = True
    supports_tool_calls: bool = True
    supports_json_schema: bool = False
    context_length: int = _DEFAULT_CONTEXT_LENGTH
    model_list: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "supports_streaming": self.supports_streaming,
            "supports_tool_calls": self.supports_tool_calls,
            "supports_json_schema": self.supports_json_schema,
            "context_length": self.context_length,
            "model_list": list(self.model_list),
        }


def _lookup_context_length(model: str) -> int:
    """Best-effort context window for *model* using the static fallback table."""
    key = (model or "").lower()
    for frag, length in _CONTEXT_LENGTHS.items():
        if frag in key:
            return length
    return _DEFAULT_CONTEXT_LENGTH


def detect_capabilities(model: str, litellm_module: Any | None = None) -> ProviderCapabilities:
    """Detect capabilities for *model*.

    When litellm is available (passed in or importable) we ask it directly via
    supports_function_calling()/get_model_info(). Otherwise we fall back to the
    static tables above. This function NEVER does a network call — capability
    detection must be offline-safe for CI (T08).

    *litellm_module* is injectable so tests can pass a fake litellm without
    importing the real one.
    """
    litellm = litellm_module
    if litellm is None:
        try:
            import litellm as _litellm  # type: ignore[import-not-found]

            litellm = _litellm
        except Exception:
            litellm = None

    supports_tool_calls = True
    supports_json_schema = False
    context_length = _lookup_context_length(model)
    model_list: list[str] = []

    if litellm is not None:
        # supports_function_calling — preferred source of truth.
        try:
            supports_tool_calls = bool(litellm.supports_function_calling(model=model))
        except Exception:
            # litellm raises for unknown models; fall back to name heuristic.
            supports_tool_calls = not any(
                frag in (model or "").lower() for frag in _NO_TOOL_FRAGMENTS
            )
        try:
            info = litellm.get_model_info(model=model) or {}
            ctx = info.get("max_input_tokens") or info.get("max_tokens")
            if isinstance(ctx, int) and ctx > 0:
                context_length = ctx
            supports_json_schema = bool(info.get("supports_response_schema", False))
        except Exception:
            pass
        try:
            model_list = sorted(getattr(litellm, "model_list", []) or [])
        except Exception:
            model_list = []
    else:
        supports_tool_calls = not any(
            frag in (model or "").lower() for frag in _NO_TOOL_FRAGMENTS
        )

    return ProviderCapabilities(
        model=model,
        supports_streaming=True,
        supports_tool_calls=supports_tool_calls,
        supports_json_schema=supports_json_schema,
        context_length=context_length,
        model_list=model_list,
    )


# ---------------------------------------------------------------------------
# LiteLLMAgentProvider — implements harness.AgentProvider
# ---------------------------------------------------------------------------


def _import_litellm() -> Any:
    try:
        import litellm  # type: ignore[import-not-found]

        return litellm
    except ImportError as exc:  # INV-4: clear error, no silent fallback
        raise RuntimeError(
            "signalos agent: the `litellm` package is not installed. "
            "Run `pip install 'litellm>=1.40,<2'` (it is bundled into the "
            "sidecar by scripts/bundle-sidecar.*) and retry."
        ) from exc


def _normalize_tool_calls(message: Any) -> list[ToolCall]:
    """Parse provider tool_calls (OpenAI shape) into our ToolCall list."""
    import json as _json

    raw_calls = getattr(message, "tool_calls", None)
    if raw_calls is None and isinstance(message, dict):
        raw_calls = message.get("tool_calls")
    out: list[ToolCall] = []
    for rc in raw_calls or []:
        # rc may be an object or a dict depending on the provider.
        rc_id = getattr(rc, "id", None) or (rc.get("id") if isinstance(rc, dict) else None)
        fn = getattr(rc, "function", None) or (
            rc.get("function") if isinstance(rc, dict) else None
        )
        name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)
        raw_args = getattr(fn, "arguments", None) or (
            fn.get("arguments") if isinstance(fn, dict) else None
        )
        parsed: dict[str, Any]
        if isinstance(raw_args, dict):
            parsed = raw_args
        elif isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = _json.loads(raw_args)
            except _json.JSONDecodeError as exc:
                # INV-4: do not silently drop — surface as an explicit error arg
                parsed = {"__parse_error__": f"invalid tool arguments JSON: {exc}"}
        else:
            parsed = {}
        out.append(
            ToolCall(
                id=str(rc_id or f"call_{len(out)}"),
                name=str(name or ""),
                arguments=parsed,
            )
        )
    return out


def _normalize_stop_reason(finish_reason: str | None, has_tool_calls: bool) -> str:
    if has_tool_calls:
        return "tool_use"
    fr = (finish_reason or "").lower()
    if fr in ("tool_calls", "function_call"):
        return "tool_use"
    if fr in ("length", "max_tokens"):
        return "max_tokens"
    if fr in ("stop", "end_turn", "eos", ""):
        return "end_turn"
    return fr


class LiteLLMAgentProvider:
    """AgentProvider implementation wrapping litellm.completion().

    Translates our provider-agnostic tool definitions (already in OpenAI
    function-tool shape) straight through to litellm and normalizes the
    response into a harness.AgentResponse. Auth failures raise
    ProviderAuthError (INV-4 / T05). Other failures raise RuntimeError —
    never an empty success.
    """

    def __init__(self, litellm_module: Any | None = None, max_tokens: int = 4096) -> None:
        self._litellm = litellm_module
        self._max_tokens = max_tokens

    @property
    def litellm(self) -> Any:
        if self._litellm is None:
            self._litellm = _import_litellm()
        return self._litellm

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> AgentResponse:
        litellm = self.litellm
        kwargs: dict[str, Any] = {
            "model": _normalize_litellm_model(model),
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if stream:
            kwargs["stream"] = True

        try:
            resp = litellm.completion(**kwargs)
        except Exception as exc:  # noqa: BLE001 — must classify, not swallow
            if _is_auth_error(litellm, exc):
                raise ProviderAuthError(
                    "Provider authentication failed. Connect a provider in "
                    "Settings (set the API key) and retry."
                ) from exc
            raise RuntimeError(f"Provider call failed: {type(exc).__name__}: {exc}") from exc

        if stream:
            return AgentResponse(
                content=None,
                tool_calls=None,
                stop_reason="end_turn",
                usage=TokenUsage(),
                stream=self._wrap_stream(resp),
            )

        return self._normalize_response(resp)

    def _normalize_response(self, resp: Any) -> AgentResponse:
        choices = getattr(resp, "choices", None) or (
            resp.get("choices") if isinstance(resp, dict) else None
        )
        if not choices:
            raise RuntimeError("Provider returned no choices in response.")
        choice = choices[0]
        message = getattr(choice, "message", None) or (
            choice.get("message") if isinstance(choice, dict) else None
        )
        finish_reason = getattr(choice, "finish_reason", None) or (
            choice.get("finish_reason") if isinstance(choice, dict) else None
        )
        content = getattr(message, "content", None) or (
            message.get("content") if isinstance(message, dict) else None
        )
        tool_calls = _normalize_tool_calls(message)
        usage_obj = getattr(resp, "usage", None) or (
            resp.get("usage") if isinstance(resp, dict) else None
        )
        usage = TokenUsage()
        if usage_obj is not None:
            usage = TokenUsage(
                input_tokens=getattr(usage_obj, "prompt_tokens", None)
                or (usage_obj.get("prompt_tokens") if isinstance(usage_obj, dict) else None),
                output_tokens=getattr(usage_obj, "completion_tokens", None)
                or (
                    usage_obj.get("completion_tokens")
                    if isinstance(usage_obj, dict)
                    else None
                ),
            )

        return AgentResponse(
            content=content,
            tool_calls=tool_calls or None,
            stop_reason=_normalize_stop_reason(finish_reason, bool(tool_calls)),
            usage=usage,
            raw=resp,
        )

    def _wrap_stream(self, stream_resp: Any) -> Iterator[StreamDelta]:
        for chunk in stream_resp:
            try:
                choices = getattr(chunk, "choices", None) or (
                    chunk.get("choices") if isinstance(chunk, dict) else None
                )
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None) or (
                    choices[0].get("delta") if isinstance(choices[0], dict) else None
                )
                text = getattr(delta, "content", None) or (
                    delta.get("content") if isinstance(delta, dict) else None
                )
                if text:
                    yield StreamDelta(kind="text", text=text)
            except Exception as exc:  # noqa: BLE001
                # INV-4: surface a stream error rather than ending silently.
                yield StreamDelta(kind="text", text=f"\n[stream error: {exc}]")
                return


def _normalize_litellm_model(model: str) -> str:
    """Route a bare model name to the correct LiteLLM provider path.

    A bare ``gemini-*`` name is ambiguous to LiteLLM and falls through to its
    Vertex AI path, which requires a Google Cloud service account (the
    ``ModuleNotFoundError: No module named 'google'`` failure). A ``GEMINI_API_KEY``
    is a Google *AI Studio* key, which LiteLLM only routes when the model is
    explicitly prefixed ``gemini/``. We add that prefix when an AI Studio key is
    present and the caller has not already chosen a provider path (no ``/``).

    Models that already carry a provider prefix (``gemini/``, ``vertex_ai/``,
    ``openai/`` ...) are left untouched, as are anthropic/openai bare names that
    LiteLLM resolves correctly on their own.
    """
    import os

    m = (model or "").strip()
    if "/" in m:
        return m  # caller chose an explicit provider path — respect it
    if m.lower().startswith("gemini") and (
        os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    ):
        return f"gemini/{m}"
    return m


def _is_auth_error(litellm: Any, exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if "auth" in name or "permission" in name:
        return True
    # litellm exposes AuthenticationError; match by class if present.
    auth_cls = getattr(litellm, "AuthenticationError", None)
    if auth_cls is not None and isinstance(exc, auth_cls):
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("api key", "api_key", "unauthorized", "authentication", "401")
    )


# ---------------------------------------------------------------------------
# ProviderAdapter — capability-aware wrapper (the public entry point)
# ---------------------------------------------------------------------------


class ProviderAdapter:
    """Capability-detecting wrapper around an AgentProvider (Q1).

    Exposes capability flags as attributes (supports_streaming,
    supports_tool_calls, supports_json_schema, context_length, model_list)
    and delegates chat() to the wrapped provider. The agent loop reads
    `supports_tool_calls` to decide whether to run the tool loop or fall back
    to text-only mode (INV-7 / Phase 2.11).

    The wrapped provider defaults to LiteLLMAgentProvider, but any
    AgentProvider can be injected — CI injects harness.AgentTestProvider so
    no network/litellm is required (INV-6).
    """

    def __init__(
        self,
        model: str,
        provider: AgentProvider | None = None,
        capabilities: ProviderCapabilities | None = None,
        litellm_module: Any | None = None,
    ) -> None:
        self.model = model
        self._provider: AgentProvider = provider or LiteLLMAgentProvider(
            litellm_module=litellm_module
        )
        self._caps = capabilities or detect_capabilities(model, litellm_module=litellm_module)

    # --- capability surface --------------------------------------------------

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    @property
    def supports_streaming(self) -> bool:
        return self._caps.supports_streaming

    @property
    def supports_tool_calls(self) -> bool:
        return self._caps.supports_tool_calls

    @property
    def supports_json_schema(self) -> bool:
        return self._caps.supports_json_schema

    @property
    def context_length(self) -> int:
        return self._caps.context_length

    @property
    def model_list(self) -> list[str]:
        return list(self._caps.model_list)

    def detect_capabilities(self, model: str | None = None) -> ProviderCapabilities:
        """Re-run capability detection (optionally for a different model)."""
        target = model or self.model
        self._caps = detect_capabilities(target)
        if model:
            self.model = model
        return self._caps

    # --- delegation ----------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> AgentResponse:
        """Delegate to the wrapped provider.

        If the provider cannot do tool calls, tools are dropped before the
        call (INV-7: no fake tool calls — the loop handles text-only mode).
        """
        effective_tools = tools if self.supports_tool_calls else None
        return self._provider.chat(
            messages=messages,
            model=model or self.model,
            tools=effective_tools,
            stream=stream and self.supports_streaming,
        )
