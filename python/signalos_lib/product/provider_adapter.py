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
    "classify_error_scenario",
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


# --- provider turn-error accounting -----------------------------------------
# Some providers return finish_reason='error' turns that litellm NORMALIZES to
# 'stop' (logging a warning) -- the turn silently degrades instead of failing.
# A flaky provider can eat a model's fix attempts and read as model weakness in
# comparisons, so the warnings are COUNTED here (a logging handler on litellm's
# logger) and exposed for per-run attribution.

import logging as _logging

_TURN_ERROR_COUNT = 0
_TURN_ERROR_MARKERS = ("Unmapped finish_reason",)


class _TurnErrorHandler(_logging.Handler):
    def emit(self, record: _logging.LogRecord) -> None:  # pragma: no cover - trivial
        global _TURN_ERROR_COUNT
        try:
            msg = record.getMessage()
            if any(m in msg for m in _TURN_ERROR_MARKERS):
                _TURN_ERROR_COUNT += 1
        except Exception:
            pass


def _install_turn_error_handler() -> None:
    logger = _logging.getLogger("LiteLLM")
    if not any(isinstance(h, _TurnErrorHandler) for h in logger.handlers):
        logger.addHandler(_TurnErrorHandler())


_install_turn_error_handler()


def turn_error_count() -> int:
    """Process-wide count of provider turn errors observed so far. Callers
    snapshot before/after a run to attribute errors to that run."""
    return _TURN_ERROR_COUNT


class ProviderAuthError(RuntimeError):
    """Raised when a provider rejects the request for auth reasons.

    Surfaced to the user (INV-4) so the chat can show "connect a provider"
    rather than a silent empty response (test T05).
    """


def classify_error_scenario(exc: Exception) -> str | None:
    """1.10: map a provider exception to an `incidents.py` scenario key, so a
    live provider failure surfaces as a plain-words card instead of a bare
    error string. Returns None when the exception doesn't match a known
    scenario (caller falls back to the generic incident card)."""
    if isinstance(exc, ProviderAuthError):
        return "credential-revoked"
    msg = str(exc).lower()
    if any(t in msg for t in ("api key", "api_key", "unauthorized", "authentication", "401")):
        return "credential-revoked"
    if "rate limit" in msg or "quota" in msg or "429" in msg:
        return "integration-outage"
    if any(t in msg for t in ("connection", "timeout", "timed out", "unreachable", "503", "502")):
        return "integration-outage"
    return None


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

    # Name heuristic: True (tool-capable) unless the model name marks it tool-less
    # (embedding/base/etc. via _NO_TOOL_FRAGMENTS). Used as the fallback below.
    _heuristic_tool_calls = not any(
        frag in (model or "").lower() for frag in _NO_TOOL_FRAGMENTS
    )
    if litellm is not None:
        # supports_function_calling is only trustworthy when it says True. Its
        # static registry FALSE-NEGATIVES on newer models (e.g. z-ai/glm-5.2,
        # qwen/qwen3.7-max, openai/gpt-oss-120b without the openrouter/ prefix)
        # that DO support tool-calling per the provider's own API -- and a False
        # here silently denies a capable model its tools, forcing it to narrate
        # (no build). So trust a True, but treat False/unknown/raises as "unknown"
        # and defer to the name heuristic instead of hard-disabling tools.
        try:
            supports_tool_calls = (
                True if litellm.supports_function_calling(model=model)
                else _heuristic_tool_calls
            )
        except Exception:
            supports_tool_calls = _heuristic_tool_calls
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
                decoded = _json.loads(raw_args)
                # Cross-provider normalization: tool-call arguments MUST end up
                # a dict. Providers variously return a JSON object, a JSON
                # string, or a DOUBLE-encoded JSON string (first decode yields a
                # string) -- decode once more in that case. Anything that still
                # isn't an object becomes an explicit parse error rather than a
                # non-dict ToolCall.arguments that crashes every downstream
                # consumer ("'str' object has no attribute 'items'").
                if isinstance(decoded, str):
                    try:
                        decoded = _json.loads(decoded)
                    except _json.JSONDecodeError:
                        pass
                parsed = decoded if isinstance(decoded, dict) else {
                    "__parse_error__": (
                        f"tool arguments must be a JSON object, got "
                        f"{type(decoded).__name__}"
                    )
                }
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

    def __init__(
        self,
        litellm_module: Any | None = None,
        max_tokens: int = 4096,
        provider_name: str | None = None,
    ) -> None:
        self._litellm = litellm_module
        self._max_tokens = max_tokens
        self._provider_name = provider_name

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
        tool_choice: str | None = None,
    ) -> AgentResponse:
        litellm = self.litellm
        kwargs: dict[str, Any] = {
            "model": _normalize_litellm_model(model, provider_name=self._provider_name),
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            # Default remains "auto"; the agent loop may escalate to "required"
            # on a reprompt turn to force a narration-only model to act. If a
            # provider rejects "required" the loop catches the error and retries
            # without the override (see AgentLoop._run_tool_loop).
            kwargs["tool_choice"] = tool_choice or "auto"
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
            raise RuntimeError(_provider_error_message(exc, provider_name=self._provider_name)) from exc

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


_PROVIDER_PREFIX_BY_NAME: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "gemini",
    "qwen": "dashscope",
    "dashscope": "dashscope",
    "ollama": "ollama_chat",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "mistral": "mistral",
    "groq": "groq",
    "cerebras": "cerebras",
    "together": "together_ai",
    "togetherai": "together_ai",
    "together_ai": "together_ai",
    "xai": "xai",
}


def _normalize_litellm_model(model: str, provider_name: str | None = None) -> str:
    """Route the selected provider/model pair to LiteLLM."""
    import os

    m = (model or "").strip()
    provider_key = (provider_name or "").strip().lower()
    prefix = _PROVIDER_PREFIX_BY_NAME.get(provider_key)
    if prefix:
        return m if m.startswith(f"{prefix}/") else f"{prefix}/{m}"
    if "/" in m:
        return m
    if m.lower().startswith("gemini") and (
        os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    ):
        return f"gemini/{m}"
    return m


def _provider_error_message(exc: Exception, provider_name: str | None = None) -> str:
    provider = (provider_name or "AI provider").strip() or "AI provider"
    raw = str(exc)
    extracted = raw
    marker = '"message":"'
    if marker in raw:
        extracted = raw.split(marker, 1)[1].split('"', 1)[0]
    lower = extracted.lower()
    if "credit balance is too low" in lower or "purchase credits" in lower:
        return (
            f"{provider} account credit is too low. Add credits with that "
            "provider or choose another provider/model in Settings."
        )
    if "llm provider not provided" in lower or "unmapped llm provider" in lower:
        return (
            f"Foundry could not route the selected model to {provider}. "
            "Re-select the provider and model in Settings, then retry."
        )
    if "404" in lower or "not found" in lower or "does not exist" in lower:
        return (
            f"{provider} rejected the selected model for chat. Pick a "
            "text/chat model in Settings, test it, then retry."
        )
    if any(token in lower for token in ("api key", "api_key", "unauthorized", "authentication", "401")):
        return f"{provider} rejected the API key. Replace the key in Settings, then retry."
    if "rate limit" in lower or "quota" in lower:
        return f"{provider} is rate-limiting this request. Wait a bit or choose another provider/model."
    return f"Provider call failed: {extracted}"


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
        provider_name: str | None = None,
    ) -> None:
        self.model = model
        self.provider_name = provider_name
        self._provider: AgentProvider = provider or LiteLLMAgentProvider(
            litellm_module=litellm_module,
            provider_name=provider_name,
        )
        capability_model = _normalize_litellm_model(model, provider_name=provider_name)
        self._caps = capabilities or detect_capabilities(capability_model, litellm_module=litellm_module)

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
        self._caps = detect_capabilities(_normalize_litellm_model(target, provider_name=self.provider_name))
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
        tool_choice: str | None = None,
    ) -> AgentResponse:
        """Delegate to the wrapped provider.

        If the provider cannot do tool calls, tools are dropped before the
        call (INV-7: no fake tool calls — the loop handles text-only mode).

        `tool_choice` is an optional override the agent loop uses to escalate a
        reprompt turn to "required" (force a tool call). It is only forwarded
        when tools are actually in play. A wrapped provider that predates the
        `tool_choice` keyword (e.g. the CI AgentTestProvider) raises TypeError
        on the extra kwarg; we catch that and retry without it so the escalation
        never crashes the run — the loop's firm text nudge still drives the
        reprompt.
        """
        effective_tools = tools if self.supports_tool_calls else None
        stream = stream and self.supports_streaming
        if tool_choice is not None and effective_tools is not None:
            try:
                return self._provider.chat(
                    messages=messages,
                    model=model or self.model,
                    tools=effective_tools,
                    stream=stream,
                    tool_choice=tool_choice,
                )
            except TypeError:
                pass  # provider has no tool_choice kwarg -> fall through
        return self._provider.chat(
            messages=messages,
            model=model or self.model,
            tools=effective_tools,
            stream=stream,
        )
