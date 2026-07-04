# Tests for the per-call token budget on every LLM provider (Foundry gen fix).
#
# STEP 1 of the Foundry generation pipeline plan: every provider's call()
# gains a keyword-only max_tokens (default 1024 -> zero behavior change for
# legacy callers). Anthropic streams above a large threshold (the non-stream
# SDK rejects very large max_tokens). A capped/truncated response is surfaced
# as a retryable TruncatedResponseError so the worker pool can act on it.
#
# NO real network: the SDK surface is stubbed per test.

from __future__ import annotations

import sys
import types

import pytest

import signalos_lib.harness as h


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def _install_fake_anthropic(monkeypatch, *, create_impl=None, stream_impl=None):
    """Install a fake `anthropic` module whose Anthropic() client records the
    kwargs passed to messages.create / messages.stream."""
    captured: dict = {"create_kwargs": None, "stream_kwargs": None, "used": None}

    class _Msg:
        def __init__(self, text="ok", stop_reason="end_turn"):
            self.content = [types.SimpleNamespace(text=text)]
            self.stop_reason = stop_reason
            self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=20)

    class _Messages:
        def create(self, **kwargs):
            captured["create_kwargs"] = kwargs
            captured["used"] = "create"
            if create_impl is not None:
                return create_impl(kwargs)
            return _Msg()

        def stream(self, **kwargs):
            captured["stream_kwargs"] = kwargs
            captured["used"] = "stream"

            class _Ctx:
                def __enter__(_self):
                    class _Stream:
                        def get_final_message(_s):
                            if stream_impl is not None:
                                return stream_impl(kwargs)
                            return _Msg()
                    return _Stream()

                def __exit__(_self, *a):
                    return False

            return _Ctx()

    class _Client:
        def __init__(self, **kwargs):
            self.messages = _Messages()

    fake = types.SimpleNamespace(Anthropic=_Client)
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return captured


def test_anthropic_call_passes_max_tokens(monkeypatch):
    captured = _install_fake_anthropic(monkeypatch)
    provider = h.AnthropicProvider()
    text, ti, to = provider.call("hi", "claude-opus-4-8", max_tokens=8192)
    assert captured["used"] == "create"
    assert captured["create_kwargs"]["max_tokens"] == 8192
    assert text == "ok"


def test_default_max_tokens_unchanged(monkeypatch):
    captured = _install_fake_anthropic(monkeypatch)
    provider = h.AnthropicProvider()
    provider.call("hi", "claude-opus-4-8")
    # Legacy callers (no kwarg) must still send the historical 1024 cap.
    assert captured["create_kwargs"]["max_tokens"] == 1024


def test_anthropic_streams_above_threshold(monkeypatch):
    captured = _install_fake_anthropic(monkeypatch)
    provider = h.AnthropicProvider()
    text, ti, to = provider.call("hi", "claude-opus-4-8", max_tokens=64000)
    assert captured["used"] == "stream"
    assert captured["stream_kwargs"]["max_tokens"] == 64000
    assert text == "ok"
    # streaming path still reports usage
    assert ti == 10 and to == 20


def test_anthropic_truncation_raises(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        create_impl=lambda kw: _truncated_msg(),
    )
    provider = h.AnthropicProvider()
    with pytest.raises(h.TruncatedResponseError):
        provider.call("hi", "claude-opus-4-8", max_tokens=1024)


def _truncated_msg():
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(text="partial")],
        stop_reason="max_tokens",
        usage=types.SimpleNamespace(input_tokens=5, output_tokens=1024),
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------

def _install_fake_openai(monkeypatch, *, finish_reason="stop"):
    captured: dict = {"kwargs": None}

    class _Msg:
        content = "hello"

    class _Choice:
        def __init__(self):
            self.message = _Msg()
            self.finish_reason = finish_reason

    class _Resp:
        def __init__(self):
            self.choices = [_Choice()]
            self.usage = types.SimpleNamespace(prompt_tokens=3, completion_tokens=4)

    class _Completions:
        def create(self, **kwargs):
            captured["kwargs"] = kwargs
            return _Resp()

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class _Client:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    fake = types.SimpleNamespace(OpenAI=_Client)
    monkeypatch.setitem(sys.modules, "openai", fake)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    return captured


def test_openai_compat_forwards_max_tokens(monkeypatch):
    captured = _install_fake_openai(monkeypatch)
    provider = h.OpenAICompatibleProvider()
    provider.call("hi", "gpt-4o", max_tokens=12000)
    assert captured["kwargs"]["max_tokens"] == 12000


def test_openai_compat_default_unchanged(monkeypatch):
    captured = _install_fake_openai(monkeypatch)
    provider = h.OpenAICompatibleProvider()
    provider.call("hi", "gpt-4o")
    assert captured["kwargs"]["max_tokens"] == 1024


def test_openai_compat_truncation_raises(monkeypatch):
    _install_fake_openai(monkeypatch, finish_reason="length")
    provider = h.OpenAICompatibleProvider()
    with pytest.raises(h.TruncatedResponseError):
        provider.call("hi", "gpt-4o", max_tokens=1024)


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def test_gemini_max_output_tokens(monkeypatch):
    captured: dict = {"gen_config": None}

    class _Resp:
        text = "g"
        usage_metadata = types.SimpleNamespace(
            prompt_token_count=1, candidates_token_count=2,
        )

    class _Model:
        def __init__(self, model):
            pass

        def generate_content(self, prompt, **kwargs):
            captured["gen_config"] = kwargs.get("generation_config")
            return _Resp()

    fake = types.SimpleNamespace(
        configure=lambda **k: None,
        GenerativeModel=_Model,
    )
    google_pkg = types.ModuleType("google")
    google_pkg.generativeai = fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-x")

    provider = h.GeminiProvider()
    provider.call("hi", "gemini-2.5-pro", max_tokens=9000)
    assert captured["gen_config"] == {"max_output_tokens": 9000}


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def test_ollama_num_predict(monkeypatch):
    import urllib.request

    captured: dict = {"payload": None}

    class _FakeResp:
        def read(self):
            import json as _json
            return _json.dumps({
                "response": "o",
                "prompt_eval_count": 1,
                "eval_count": 2,
                "done_reason": "stop",
            }).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=0):
        import json as _json
        captured["payload"] = _json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    provider = h.OllamaProvider()
    provider.call("hi", "llama3", max_tokens=7000)
    assert captured["payload"]["options"]["num_predict"] == 7000


# ---------------------------------------------------------------------------
# Protocol / test provider accept the kwarg (no break)
# ---------------------------------------------------------------------------

def test_test_provider_accepts_max_tokens(monkeypatch):
    provider = h.TestProvider()
    text, ti, to = provider.call("hi", "any-model", max_tokens=50000)
    assert isinstance(text, str) and text
