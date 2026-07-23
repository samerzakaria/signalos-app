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
    "classify_provider_failure",
    "classify_error_scenario",
    "normalize_provider_model",
    # Layer 2 seed — opt-in raw transcript capture (OFF by default).
    "capture_transcript",
    "iter_cassette",
    "CAPTURE_ENV",
    "CAPTURE_DIR_ENV",
    # Layer 2 — offline cassette replay (the other half of the capture hook).
    "CassetteTransport",
    "replay_cassette",
]

from dataclasses import dataclass, field, replace
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


def classify_provider_failure(exc: Exception) -> str:
    """Return a stable machine-readable category for a failed provider call.

    AgentLoop calls this only after the provider boundary raised, so the
    fallback is deliberately ``provider-error`` rather than an application or
    generated-product failure.  Benchmark callers can then keep provider
    availability and credentials out of model-quality grades.
    """
    if isinstance(exc, ProviderAuthError):
        return "provider-auth"

    msg = str(exc).lower()
    if any(
        token in msg
        for token in (
            "api key", "api_key", "unauthorized", "authentication",
            "permission denied", "forbidden", "401", "403",
        )
    ):
        return "provider-auth"
    if any(
        token in msg
        for token in (
            "insufficient credit", "insufficient balance", "credit balance",
            "payment required", "billing", "402",
        )
    ):
        return "provider-billing"
    if any(
        token in msg
        for token in (
            "rate limit", "rate-limit", "rate limiting", "rate-limiting",
            "too many requests", "quota", "429",
        )
    ):
        return "provider-rate-limit"
    if any(
        token in msg
        for token in (
            "connection", "timeout", "timed out", "unreachable", "network",
            "service unavailable", "bad gateway", "gateway timeout",
            "502", "503", "504",
        )
    ):
        return "provider-transport"
    if any(
        token in msg
        for token in (
            "model not found", "no endpoints", "no endpoint",
            "unsupported model", "selected model for chat",
            "provider routing", "route not found", "404",
        )
    ):
        return "provider-route"
    return "provider-error"


def classify_error_scenario(exc: Exception) -> str | None:
    """1.10: map a provider exception to an `incidents.py` scenario key, so a
    live provider failure surfaces as a plain-words card instead of a bare
    error string. Returns None when the exception doesn't match a known
    scenario (caller falls back to the generic incident card)."""
    failure = classify_provider_failure(exc)
    if failure == "provider-auth":
        return "credential-revoked"
    if failure in {
        "provider-billing",
        "provider-rate-limit",
        "provider-transport",
        "provider-route",
    }:
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
# Layer 2 seed — raw provider-payload capture (opt-in, OFF by default)
# ---------------------------------------------------------------------------
# Funded builds throw away the raw provider request/response payloads that are
# the single highest-fidelity regression corpus we have (each is ~$0.80 to
# produce). When SIGNALOS_CAPTURE_TRANSCRIPTS=1, every LiteLLMAgentProvider.chat
# call appends its RAW request + RAW response JSON to a per-process cassette
# under .signalos/transcripts/ (override the dir with SIGNALOS_TRANSCRIPTS_DIR).
# A captured cassette can later be replayed offline for $0 to re-drive the real
# litellm parse/normalize path (see test_transcript_capture.py's stub replay).
#
# Contract: OFF by default => ZERO behavior change. Every step is wrapped so a
# capture failure can NEVER break a live provider call (this is diagnostic-only
# telemetry, not a load-bearing path). Streamed calls capture the request only
# (never consume the live stream).

CAPTURE_ENV = "SIGNALOS_CAPTURE_TRANSCRIPTS"
CAPTURE_DIR_ENV = "SIGNALOS_TRANSCRIPTS_DIR"

_CAPTURE_FILENAME: str | None = None


def _capture_enabled() -> bool:
    import os

    return os.environ.get(CAPTURE_ENV) == "1"


def _transcripts_dir() -> "Path":
    import os
    from pathlib import Path

    override = os.environ.get(CAPTURE_DIR_ENV)
    if override:
        return Path(override)
    return Path.cwd() / ".signalos" / "transcripts"


def _capture_cassette_path() -> "Path":
    """One rolling cassette file per process (stable across calls in a run)."""
    global _CAPTURE_FILENAME
    import os
    from datetime import datetime, timezone

    if _CAPTURE_FILENAME is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        _CAPTURE_FILENAME = f"cassette-{ts}-{os.getpid()}.jsonl"
    return _transcripts_dir() / _CAPTURE_FILENAME


def _jsonable(obj: Any) -> Any:
    """Best-effort convert a litellm request/response into a JSON-able value.

    Handles pydantic ModelResponse (model_dump/dict/json), plain
    dict/list/scalars, and falls back to a bounded repr — it NEVER raises.
    """
    import json as _json

    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    for meth in ("model_dump", "dict"):
        fn = getattr(obj, meth, None)
        if callable(fn):
            try:
                return _jsonable(fn())
            except Exception:
                pass
    json_fn = getattr(obj, "json", None)
    if callable(json_fn):
        try:
            return _json.loads(json_fn())
        except Exception:
            pass
    try:
        _json.dumps(obj)
        return obj
    except Exception:
        return {"__repr__": str(obj)[:20000]}


def capture_transcript(
    request: dict[str, Any], response: Any, *, streamed: bool = False
) -> None:
    """Append one raw request/response record to the process cassette IFF
    SIGNALOS_CAPTURE_TRANSCRIPTS=1. No-op (and no filesystem touch) when off.

    Defensive by contract: any error is swallowed so a capture problem can
    never affect the real provider call. Not INV-4 relevant — this is opt-in
    telemetry, not a user-visible result path.
    """
    if not _capture_enabled():
        return
    try:
        import json as _json
        from datetime import datetime, timezone

        path = _capture_cassette_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "streamed": bool(streamed),
            "request": _jsonable(request),
            # A live stream must not be consumed here; capture request-only.
            "response": None if streamed else _jsonable(response),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        # Best-effort telemetry only — never surface to the caller.
        pass


def iter_cassette(path: Any) -> Iterator[dict[str, Any]]:
    """Yield {'request','response',...} records from a cassette file.

    The seam the future replay harness plugs into: a saved funded-run payload
    can be fed back through the adapter's parse/normalize path offline for $0.
    Missing file / malformed lines yield nothing rather than raising.
    """
    import json as _json
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = _json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            yield rec


# ---------------------------------------------------------------------------
# Layer 2 — offline cassette REPLAY (the other half of the capture hook)
# ---------------------------------------------------------------------------
# The replay harness turns a recorded cassette (see capture_transcript) into a
# $0 OFFLINE regression: it re-serves each recorded provider response, IN ORDER,
# through the SAME network seam the wire-level golden path uses (a monkeypatched
# httpx.Client.send). litellm's real request construction/routing and our real
# normalize path (_normalize_response / _normalize_tool_calls / _wrap_stream)
# therefore run UNMODIFIED against the recorded REAL payloads -- so a whole
# real-provider bug class (tool-call parsing, reasoning-channel leak, streamed
# delta assembly) is regression-tested without ever touching the network.
#
# The transport is URL-aware ON PURPOSE: only /chat/completions traffic consumes
# a recorded turn. litellm's model-cost-map GET (which ALSO travels through
# httpx.Client.send once the seam is patched) is refused so litellm falls back
# to its bundled local map -- otherwise that GET would silently eat the first
# recorded turn and desync the whole cassette.


def _sse_stream_bytes(chunks: list[Any]) -> bytes:
    """Serialize chunk dicts as an OpenAI-style ``text/event-stream`` body: one
    ``data: {json}`` line per chunk, terminated by ``data: [DONE]``. litellm's
    streaming parser then reassembles them exactly as it would a live SSE feed,
    so the adapter's _wrap_stream delta assembly runs against real chunk shapes.
    """
    import json as _json

    lines = [f"data: {_json.dumps(c, ensure_ascii=False, default=str)}" for c in chunks]
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


class CassetteTransport:
    """Re-serves a cassette's recorded provider responses in order at the
    ``httpx.Client.send`` seam.

    ``transport()`` returns a PLAIN function suitable for
    ``monkeypatch.setattr(httpx.Client, "send", ...)`` (a bound method would
    swallow the client ``self``), mirroring the wire-level golden path's fake.
    Prefer the :func:`replay_cassette` context manager, which does the
    patch/restore for you.

    Each record's ``response`` is served according to its shape:

      * ``dict``  -> a normal JSON ``chat.completion`` (a non-streamed turn);
      * ``list``  -> an SSE stream of delta chunks (a streamed turn); also used
                     when the record's ``streamed`` flag is set;
      * ``str``   -> a raw body served verbatim, so a truncated/garbled payload
                     can be replayed to exercise the adapter's graceful path.

    A non-chat request (e.g. litellm's cost-map GET) and an exhausted cassette
    both raise ``httpx.ConnectError``; the adapter surfaces that as a clean
    RuntimeError instead of crashing.
    """

    def __init__(self, records: list[dict[str, Any]]):
        self._records: list[dict[str, Any]] = [r for r in records if isinstance(r, dict)]
        self._cursor = 0
        self.requests: list[dict[str, Any]] = []
        self.exhausted = False

    @classmethod
    def from_cassette(cls, path: Any) -> "CassetteTransport":
        """Build a transport from a cassette file (malformed lines are skipped
        by :func:`iter_cassette`)."""
        return cls(list(iter_cassette(path)))

    def transport(self):
        """Return the ``httpx.Client.send`` replacement (a plain function)."""
        shim = self

        def send(client_self, request, **kwargs):  # noqa: ANN001 - httpx.Client.send
            return shim._serve(request)

        return send

    def _serve(self, request: Any):
        import httpx  # local import: keep module import stdlib-light

        url = str(getattr(request, "url", ""))
        if "chat/completions" not in url:
            # Refuse non-chat traffic (cost-map GET, etc.): it must neither reach
            # the network nor consume a recorded turn. litellm falls back to its
            # bundled local cost map on this error.
            raise httpx.ConnectError(
                f"offline replay: blocked non-chat request {url}", request=request
            )

        import json as _json

        try:
            body = _json.loads(request.content.decode("utf-8"))
        except Exception:  # pragma: no cover - defensive
            body = {}
        self.requests.append(body)

        if self._cursor >= len(self._records):
            self.exhausted = True
            raise httpx.ConnectError(
                "offline replay: cassette exhausted (no recorded turn left)",
                request=request,
            )
        rec = self._records[self._cursor]
        self._cursor += 1
        response = rec.get("response")
        streamed = bool(rec.get("streamed"))

        if streamed or isinstance(response, list):
            chunks = response if isinstance(response, list) else []
            return httpx.Response(
                200,
                content=_sse_stream_bytes(chunks),
                headers={"content-type": "text/event-stream"},
                request=request,
            )
        if isinstance(response, str):
            # Replay a raw/truncated body verbatim (graceful-degradation probe).
            return httpx.Response(
                200,
                content=response.encode("utf-8"),
                headers={"content-type": "application/json"},
                request=request,
            )
        # Normal recorded chat.completion (dict); a captured stream leg stores
        # response=None -> serve an empty object so litellm still parses cleanly.
        return httpx.Response(
            200, json=response if response is not None else {}, request=request
        )


import contextlib as _contextlib


@_contextlib.contextmanager
def replay_cassette(path: Any = None, *, records: list[dict[str, Any]] | None = None):
    """Patch ``httpx.Client.send`` to re-serve *path*'s recorded provider
    responses in order (offline, $0), then restore it on exit.

    Yields the :class:`CassetteTransport` so callers can inspect ``.requests`` /
    ``.exhausted``. Build the ProviderAdapter/AgentLoop INSIDE the ``with`` block
    so litellm's capability lookups also stay offline. litellm + ProviderAdapter
    run UNMODIFIED against the recorded payloads -- the same ``httpx.Client.send``
    seam the wire-level golden path uses::

        with replay_cassette(".signalos/transcripts/cassette-....jsonl") as tape:
            resp = ProviderAdapter(model="openrouter/z-ai/glm-5.2").chat(...)
        assert tape.requests  # the recorded turn was actually replayed
    """
    import httpx

    recs = list(records) if records is not None else list(iter_cassette(path))
    shim = CassetteTransport(recs)
    original_send = httpx.Client.send
    httpx.Client.send = shim.transport()  # type: ignore[assignment]
    try:
        yield shim
    finally:
        httpx.Client.send = original_send  # type: ignore[assignment]


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
        num_retries: int | None = None,
        retry_base_seconds: float | None = None,
        request_timeout: float | None = None,
    ) -> None:
        self._litellm = litellm_module
        self._max_tokens = max_tokens
        self._provider_name = provider_name
        self._num_retries = _resolve_provider_num_retries(num_retries)
        self._retry_base_seconds = _resolve_provider_retry_base(retry_base_seconds)
        self._request_timeout = _resolve_provider_request_timeout(request_timeout)

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
            # OA-57: bounded per-request timeout -- an unresponsive upstream must
            # fail in minutes and reach the terminal error chain, never hang on
            # litellm's ~600s default through every retry (heartbeat starvation).
            "timeout": self._request_timeout,
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

        # Bounded retry over TRANSIENT provider faults (empty/non-JSON gateway
        # body, 429/5xx, dropped connection, empty `choices`). The completion AND
        # its normalization are inside the loop so an empty-`choices` transient
        # (raised by _normalize_response) is retried too. Auth / bad-request /
        # context-window errors are NOT retried -- they fail fast and clearly.
        attempt = 0
        while True:
            try:
                resp = litellm.completion(**kwargs)
                if stream:
                    # Layer 2 seed: capture the request only — never consume it.
                    capture_transcript(kwargs, None, streamed=True)
                    return AgentResponse(
                        content=None,
                        tool_calls=None,
                        stop_reason="end_turn",
                        usage=TokenUsage(),
                        stream=self._wrap_stream(resp),
                    )
                # Layer 2 seed: capture the raw request + raw response (opt-in,
                # OFF by default) before we normalize it, to seed a replay corpus.
                capture_transcript(kwargs, resp, streamed=False)
                return self._normalize_response(resp)
            except ProviderAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 — must classify, not swallow
                if _is_auth_error(litellm, exc):
                    raise ProviderAuthError(
                        "Provider authentication failed. Connect a provider in "
                        "Settings (set the API key) and retry."
                    ) from exc
                if attempt >= self._num_retries or not _is_retryable_provider_error(
                    litellm, exc
                ):
                    raise RuntimeError(
                        _provider_error_message(exc, provider_name=self._provider_name)
                    ) from exc
                _sleep_provider_backoff(attempt, self._retry_base_seconds)
                attempt += 1

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


def normalize_provider_model(model: str, provider_name: str | None = None) -> str:
    """Return the exact LiteLLM route used for a provider/model pair."""

    return _normalize_litellm_model(model, provider_name=provider_name)


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


# --- transient provider-fault retry -----------------------------------------
# A transient gateway fault (OpenRouter returning an empty/whitespace body that
# litellm cannot parse to JSON -> `APIError: Unable to get json response`, a
# 429/5xx, a dropped connection, a read timeout, or an empty `choices` list) is
# INFRASTRUCTURE, not a model or governance outcome. A single one must never
# kill a multi-gate funded run (it did: a G3 whitespace-only response ended a
# run whose G0-G2 were already signed). The provider call is retried with capped
# exponential backoff before it surfaces as a RuntimeError. Auth and genuine
# request errors (bad-request / context-window / not-found) are NOT retried --
# retrying them only burns tokens and delays a real, actionable failure. This is
# the same "don't let provider flakiness read as model weakness" intent as the
# turn-error accounting at the top of this module.
_PROVIDER_NUM_RETRIES_ENV = "SIGNALOS_PROVIDER_NUM_RETRIES"
_PROVIDER_RETRY_BASE_ENV = "SIGNALOS_PROVIDER_RETRY_BASE_SECONDS"
_DEFAULT_PROVIDER_NUM_RETRIES = 4
_DEFAULT_PROVIDER_RETRY_BASE_SECONDS = 2.0
_PROVIDER_RETRY_BACKOFF_CAP_SECONDS = 30.0

# litellm exception class NAMES, matched via getattr so any litellm version and a
# fake litellm in tests both work. Non-retryable is checked FIRST because in the
# openai/litellm hierarchy BadRequestError/ContextWindowExceededError ARE
# instances of APIError -- order makes the terminal classes win.
_NON_RETRYABLE_LITELLM_EXC = (
    "AuthenticationError", "PermissionDeniedError", "BadRequestError",
    "ContextWindowExceededError", "NotFoundError", "UnprocessableEntityError",
    "ContentPolicyViolationError",
)
_RETRYABLE_LITELLM_EXC = (
    "APIError", "APIConnectionError", "APIResponseValidationError", "Timeout",
    "RateLimitError", "ServiceUnavailableError", "InternalServerError",
)
# Message signatures for transient faults that arrive as a bare APIError or a
# plain RuntimeError (our own "no choices" normalize guard, gateway errors).
_RETRYABLE_MESSAGE_SIGNATURES = (
    "unable to get json response", "expecting value", "no choices",
    "overloaded", "temporarily unavailable", "service unavailable",
    "bad gateway", "gateway time-out", "gateway timeout", "connection reset",
    "connection aborted", "connection error", "remotely closed",
    "read timed out", "timed out", "502", "503", "504", "429",
)
# Hard, user-ACTIONABLE billing / spending-limit errors. These surface as a
# provider APIError (a retryable CLASS) but must NEVER be retried -- retrying a
# key that is out of budget only wastes calls and buries the real message. A
# funded run died at G4 on OpenRouter "Key limit exceeded (total limit)"; the
# fix is the user raising the key limit, not a retry. Checked FIRST, before the
# retryable-class match, so the message wins over the class. OA-57 adds the
# litellm unmapped-provider routing error ("LLM Provider NOT provided" + its
# "Provider List:" banner): a mis-routed model id is a CONFIGURATION error --
# retrying it can only fail identically while printing the banner each attempt.
_NON_RETRYABLE_MESSAGE_SIGNATURES = (
    "key limit exceeded", "insufficient credit", "insufficient_quota",
    "credit balance is too low", "purchase credits", "payment required",
    "exceeded your current quota", "billing", "402",
    "llm provider not provided", "unmapped llm provider",
    "provider list: https://docs.litellm.ai",
)


def _is_retryable_provider_error(litellm: Any, exc: Exception) -> bool:
    """True for transient infra faults worth retrying; False for terminal ones."""
    msg = str(exc).lower()
    # Hard billing/limit errors fail fast even when the class looks retryable.
    if any(sig in msg for sig in _NON_RETRYABLE_MESSAGE_SIGNATURES):
        return False
    for name in _NON_RETRYABLE_LITELLM_EXC:
        cls = getattr(litellm, name, None)
        if isinstance(cls, type) and isinstance(exc, cls):
            return False
    for name in _RETRYABLE_LITELLM_EXC:
        cls = getattr(litellm, name, None)
        if isinstance(cls, type) and isinstance(exc, cls):
            return True
    return any(sig in msg for sig in _RETRYABLE_MESSAGE_SIGNATURES)


def _resolve_provider_num_retries(explicit: int | None) -> int:
    if explicit is not None:
        return max(0, int(explicit))
    import os
    raw = os.environ.get(_PROVIDER_NUM_RETRIES_ENV)
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return _DEFAULT_PROVIDER_NUM_RETRIES


# OA-57: a BOUNDED per-request timeout passed to litellm.completion. Without
# it, litellm's default (~600s) applies: when an upstream endpoint goes
# unresponsive, each attempt hangs ~10 minutes and the OA-51 retry loop turns
# that into 60-90 minutes of SILENCE -- no stdout agent-events -- which starved
# the driver's inactivity heartbeat and killed a funded run whose build seats
# had all been finishing cleanly. A generous-but-bounded ceiling: a real
# completion (even a long G2 plan) streams well within it, while a hung
# endpoint now fails in minutes and reaches the terminal error chain.
_PROVIDER_REQUEST_TIMEOUT_ENV = "SIGNALOS_PROVIDER_REQUEST_TIMEOUT"
# OA-59: 180s killed LEGITIMATE long generations on slow models (kimi-k3's big
# plan/implementer outputs take 200-400s) -- each kill was BILLED then retried,
# burning ~$24 in one run and masquerading as provider degradation; fast models
# (deepseek) masked it. 600s tolerates a slow 16k-token completion while still
# bounding a truly hung request (the infinite-hang class is dead via OA-58's
# process-tree kill, not this ceiling).
_DEFAULT_PROVIDER_REQUEST_TIMEOUT_SECONDS = 600.0


def _resolve_provider_request_timeout(explicit: float | None) -> float:
    if explicit is not None:
        return max(1.0, float(explicit))
    import os
    raw = os.environ.get(_PROVIDER_REQUEST_TIMEOUT_ENV)
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_PROVIDER_REQUEST_TIMEOUT_SECONDS


def _resolve_provider_retry_base(explicit: float | None) -> float:
    if explicit is not None:
        return max(0.0, float(explicit))
    import os
    raw = os.environ.get(_PROVIDER_RETRY_BASE_ENV)
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_PROVIDER_RETRY_BASE_SECONDS


def _sleep_provider_backoff(attempt: int, base_seconds: float) -> None:
    if base_seconds <= 0:
        return
    import time
    delay = min(base_seconds * (2 ** attempt), _PROVIDER_RETRY_BACKOFF_CAP_SECONDS)
    time.sleep(delay)


# ---------------------------------------------------------------------------
# ProviderAdapter — capability-aware wrapper (the public entry point)
# ---------------------------------------------------------------------------


# Per-model completion-token ceilings for the agent-loop provider. The old
# hardcoded 4096 default truncated a thorough model's governance artifact:
# deepseek-v4-pro's G2 plan blew past 4096 even across truncation-continues and
# the gate blocked with `max_tokens` -- a harness cap, not a model limit. Large
# models support far more, and providers clamp an over-large request DOWN to the
# model's real cap, so a generous ceiling never 400s. Mirrors
# agent_dispatch._MODEL_MAX_OUTPUT_TOKENS; unknown models take the vetted
# 16384 default (4x the old cap) "that no mainstream chat model 400s on".
_OUTPUT_CEILINGS: tuple[tuple[str, int], ...] = (
    ("gpt-4-turbo", 4096),
    ("gpt-3.5", 4096),
    ("gpt-4.1", 32768),
    ("gpt-4o", 16384),
    ("o1", 32768),
    ("o3", 32768),
    ("claude", 64000),
    ("gemini", 32768),
)
_DEFAULT_OUTPUT_CEILING = 16384


def _output_ceiling(model: str | None) -> int:
    """The completion-token budget for *model* -- model-aware, never the old
    fixed 4096 that truncated a thorough model's governance turn."""
    low = (model or "").lower()
    for prefix, cap in _OUTPUT_CEILINGS:
        if prefix in low:
            return cap
    return _DEFAULT_OUTPUT_CEILING


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
        context_length: int | None = None,
    ) -> None:
        self.model = model
        self.provider_name = provider_name
        self._provider: AgentProvider = provider or LiteLLMAgentProvider(
            litellm_module=litellm_module,
            provider_name=provider_name,
            max_tokens=_output_ceiling(model),
        )
        capability_model = _normalize_litellm_model(model, provider_name=provider_name)
        detected = capabilities or detect_capabilities(
            capability_model, litellm_module=litellm_module
        )
        if context_length is not None:
            if isinstance(context_length, bool) or not isinstance(context_length, int):
                raise ValueError("provider context_length must be an integer")
            if not 4_096 <= context_length <= 10_000_000:
                raise ValueError("provider context_length is outside the supported range")
            detected = replace(detected, context_length=context_length)
        self._caps = detected
        self.routed_model = capability_model

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
