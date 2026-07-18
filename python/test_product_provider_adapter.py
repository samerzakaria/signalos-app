"""Gap 8: real litellm call-path test for provider_adapter.py.

These exercise LiteLLMAgentProvider.chat() / ProviderAdapter.chat() through a
*fake* litellm module (no network, no real provider) to prove the call path
and response normalization actually work — not just offline capability
detection. Covers text response, tool-call response, and auth-error mapping.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.provider_adapter import (  # noqa: E402
    LiteLLMAgentProvider,
    ProviderAdapter,
    ProviderAuthError,
    ProviderCapabilities,
    classify_error_scenario,
    classify_provider_failure,
    _normalize_litellm_model,
    _normalize_tool_calls,
)


def test_provider_failures_have_stable_machine_categories():
    assert classify_provider_failure(ProviderAuthError("bad key")) == "provider-auth"
    assert classify_provider_failure(RuntimeError("HTTP 402 payment required")) == "provider-billing"
    assert classify_provider_failure(RuntimeError("429 rate-limiting")) == "provider-rate-limit"
    assert classify_provider_failure(TimeoutError("gateway timeout")) == "provider-transport"
    assert classify_provider_failure(RuntimeError("no endpoints for model")) == "provider-route"
    assert classify_provider_failure(RuntimeError("unexpected provider fault")) == "provider-error"
    assert classify_error_scenario(RuntimeError("429 rate-limiting")) == "integration-outage"


def _msg(arguments):
    return {"tool_calls": [{"id": "c1", "function": {"name": "write_file", "arguments": arguments}}]}


class TestToolCallArgumentsAlwaysDict:
    """Regression: ToolCall.arguments MUST always be a dict. A provider (e.g.
    DeepSeek via OpenRouter) that returns arguments as a raw/typed/double-encoded
    JSON value must not yield a non-dict payload that crashes every downstream
    consumer with "'str' object has no attribute 'items'"."""

    def test_dict_arguments(self):
        tc = _normalize_tool_calls(_msg({"path": "a.txt", "content": "x"}))[0]
        assert tc.arguments == {"path": "a.txt", "content": "x"}

    def test_json_object_string(self):
        tc = _normalize_tool_calls(_msg('{"path": "a.txt", "content": "x"}'))[0]
        assert tc.arguments == {"path": "a.txt", "content": "x"}

    def test_double_encoded_object_string(self):
        # first json.loads yields a STRING, which must be decoded once more
        tc = _normalize_tool_calls(_msg('"{\\"path\\": \\"a.txt\\"}"'))[0]
        assert isinstance(tc.arguments, dict)
        assert tc.arguments == {"path": "a.txt"}

    def test_non_dict_json_becomes_parse_error_not_crash(self):
        for bad in ('"just a string"', "123", "[1, 2, 3]", "true"):
            tc = _normalize_tool_calls(_msg(bad))[0]
            assert isinstance(tc.arguments, dict), f"{bad!r} -> non-dict"
            assert "__parse_error__" in tc.arguments

    def test_invalid_json_becomes_parse_error(self):
        tc = _normalize_tool_calls(_msg("{not valid json"))[0]
        assert isinstance(tc.arguments, dict)
        assert "__parse_error__" in tc.arguments

    def test_empty_arguments(self):
        tc = _normalize_tool_calls(_msg(""))[0]
        assert tc.arguments == {}


class _FakeAuthError(Exception):
    pass


def _fake_litellm(*, response=None, raise_exc=None):
    """A minimal stand-in for the litellm module."""
    captured = {}

    def completion(**kwargs):
        captured.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        return response

    mod = types.SimpleNamespace(
        completion=completion,
        AuthenticationError=_FakeAuthError,
        supports_function_calling=lambda model=None: True,
        get_model_info=lambda model=None: {"max_input_tokens": 128000},
        model_list=["gpt-4o", "claude-sonnet-4-5"],
        _captured=captured,
    )
    return mod


def _text_response(text):
    # OpenAI-shaped dict — _normalize_response handles dict or object.
    return {
        "choices": [
            {"message": {"content": text, "tool_calls": None}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _tool_response(name, args_json):
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": name, "arguments": args_json}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
    }


class TestLiteLLMCallPath:
    def test_text_response_normalized(self):
        lm = _fake_litellm(response=_text_response("Hello from the model"))
        prov = LiteLLMAgentProvider(litellm_module=lm)
        resp = prov.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4o")
        assert resp.content == "Hello from the model"
        assert resp.tool_calls is None
        assert resp.stop_reason == "end_turn"
        assert resp.usage.input_tokens == 10
        assert resp.usage.output_tokens == 5
        # the call path actually invoked litellm.completion with our messages
        assert lm._captured["model"] == "gpt-4o"
        assert lm._captured["messages"][0]["content"] == "hi"

    def test_tool_call_response_normalized(self):
        lm = _fake_litellm(response=_tool_response("write_file", '{"path":"a.txt","content":"x"}'))
        prov = LiteLLMAgentProvider(litellm_module=lm)
        resp = prov.chat(
            messages=[{"role": "user", "content": "write a file"}],
            model="gpt-4o",
            tools=[{"type": "function", "function": {"name": "write_file"}}],
        )
        assert resp.stop_reason == "tool_use"
        assert resp.tool_calls is not None and len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert tc.name == "write_file"
        assert tc.arguments == {"path": "a.txt", "content": "x"}
        # tools + tool_choice were forwarded to the provider
        assert lm._captured["tools"]
        assert lm._captured["tool_choice"] == "auto"

    def test_auth_error_mapped(self):
        lm = _fake_litellm(raise_exc=_FakeAuthError("invalid api key"))
        prov = LiteLLMAgentProvider(litellm_module=lm)
        try:
            prov.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4o")
            assert False, "expected ProviderAuthError"
        except ProviderAuthError:
            pass

    def test_gemini_routed_to_ai_studio_with_api_key(self, monkeypatch):
        # A GEMINI_API_KEY is a Google AI Studio key; LiteLLM only routes it
        # when the model carries the `gemini/` prefix. A bare name falls through
        # to Vertex AI (needs a service account) and fails. The chat() call must
        # forward the prefixed model.
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "fake-ai-studio-key")
        assert _normalize_litellm_model("gemini-2.0-flash") == "gemini/gemini-2.0-flash"
        # explicit provider paths are respected, never double-prefixed
        assert _normalize_litellm_model("vertex_ai/gemini-2.0-flash") == "vertex_ai/gemini-2.0-flash"
        assert _normalize_litellm_model("gemini/gemini-2.0-flash") == "gemini/gemini-2.0-flash"
        # other providers untouched
        assert _normalize_litellm_model("claude-sonnet-4-5") == "claude-sonnet-4-5"
        assert _normalize_litellm_model("gpt-4o") == "gpt-4o"
        # end-to-end through chat(): the prefixed model reaches litellm.completion
        lm = _fake_litellm(response=_text_response("ready"))
        prov = LiteLLMAgentProvider(litellm_module=lm)
        prov.chat(messages=[{"role": "user", "content": "hi"}], model="gemini-2.0-flash")
        assert lm._captured["model"] == "gemini/gemini-2.0-flash"

    def test_gemini_not_prefixed_without_key(self, monkeypatch):
        # No AI Studio key set -> leave the model alone (the caller may have
        # Vertex creds and expect the bare/explicit path).
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert _normalize_litellm_model("gemini-2.0-flash") == "gemini-2.0-flash"

    def test_ollama_explicit_prefix_is_preserved(self, monkeypatch):
        # Ollama must be selected by an explicit LiteLLM provider path so one
        # process can safely test cloud and local providers without env coupling.
        monkeypatch.setenv("SIGNALOS_LLM_PROVIDER", "ollama")
        assert _normalize_litellm_model("qwen2.5-coder:14b") == "qwen2.5-coder:14b"
        assert _normalize_litellm_model("ollama/qwen2.5-coder:14b") == "ollama/qwen2.5-coder:14b"

        lm = _fake_litellm(response=_text_response("ready"))
        prov = LiteLLMAgentProvider(litellm_module=lm)
        prov.chat(messages=[{"role": "user", "content": "hi"}], model="ollama/qwen2.5-coder:14b")
        assert lm._captured["model"] == "ollama/qwen2.5-coder:14b"

    def test_selected_provider_prefixes_model_for_litellm(self):
        assert (
            _normalize_litellm_model("openai/gpt-4o", provider_name="openrouter")
            == "openrouter/openai/gpt-4o"
        )
        assert _normalize_litellm_model("qwen-plus", provider_name="qwen") == "dashscope/qwen-plus"
        assert _normalize_litellm_model("llama3", provider_name="ollama") == "ollama_chat/llama3"
        assert _normalize_litellm_model("gpt-4o", provider_name="openai") == "openai/gpt-4o"

    def test_chat_uses_selected_provider_for_prefixed_model_ids(self):
        lm = _fake_litellm(response=_text_response("ready"))
        prov = LiteLLMAgentProvider(litellm_module=lm, provider_name="openrouter")
        prov.chat(messages=[{"role": "user", "content": "hi"}], model="openai/gpt-4o")
        assert lm._captured["model"] == "openrouter/openai/gpt-4o"

    def test_adapter_drops_tools_when_unsupported(self):
        # ProviderAdapter must not forward tools if the provider can't use them.
        lm = _fake_litellm(response=_text_response("text only"))
        caps = ProviderCapabilities(model="some-instruct", supports_tool_calls=False)
        adapter = ProviderAdapter(model="some-instruct", litellm_module=lm, capabilities=caps)
        resp = adapter.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="some-instruct",
            tools=[{"type": "function", "function": {"name": "write_file"}}],
        )
        assert resp.content == "text only"
        assert "tools" not in lm._captured  # tools dropped (text-only path)

    def test_catalog_context_override_replaces_unknown_model_fallback(self):
        lm = _fake_litellm(response=_text_response("ready"))
        adapter = ProviderAdapter(
            model="qwen/qwen3.7-max",
            provider_name="openrouter",
            litellm_module=lm,
            context_length=262_144,
        )

        assert adapter.routed_model == "openrouter/qwen/qwen3.7-max"
        assert adapter.context_length == 262_144


def test_output_ceiling_is_model_aware_not_the_old_fixed_4096():
    # Regression (deepseekv4pro run 1): the gate agent's output was capped at a
    # fixed 4096, so a thorough model's G2 plan truncated (max_tokens) and the
    # gate blocked. The ceiling is now model-aware; unknown models take the
    # vetted 16384 default (4x the old cap), never 4096.
    from signalos_lib.product.provider_adapter import _output_ceiling

    assert _output_ceiling("deepseek/deepseek-v4-pro") == 16384   # unknown -> default
    assert _output_ceiling("anthropic/claude-fable-5") == 64000
    assert _output_ceiling("openai/gpt-4.1") == 32768
    assert _output_ceiling("gpt-3.5-turbo") == 4096              # genuinely-4096 models kept
    assert _output_ceiling(None) == 16384


def test_adapter_wires_model_aware_output_ceiling_into_the_wrapped_provider():
    # The gate agent's ProviderAdapter built its LiteLLMAgentProvider without a
    # max_tokens -> the 4096 default. It now gets the model-aware ceiling, so a
    # thorough model can finish its governance artifact.
    lm = _fake_litellm(response=_text_response("ok"))
    caps = ProviderCapabilities(model="deepseek/deepseek-v4-pro", supports_tool_calls=True)
    adapter = ProviderAdapter(
        model="deepseek/deepseek-v4-pro", litellm_module=lm, capabilities=caps
    )
    assert adapter._provider._max_tokens == 16384  # not the old 4096
