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
)


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
