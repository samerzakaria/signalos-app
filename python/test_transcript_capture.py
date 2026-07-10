"""Layer 2 seed -- raw transcript CAPTURE hook + a stub REPLAY.

The panel's highest-ROI point: funded runs (~$0.80 each) throw away the raw
provider payloads that are our best regression corpus. `provider_adapter` now has
an opt-in capture hook (SIGNALOS_CAPTURE_TRANSCRIPTS=1) that appends each call's
raw request + raw response to a cassette under .signalos/transcripts/.

These tests assert:
  1. OFF by default => ZERO behavior change, no filesystem touch.
  2. ON => a cassette with the raw request (incl. offered tools) + raw response
     (incl. the tool call) is written, captured at the REAL litellm boundary.
  3. A captured cassette REPLAYS offline for $0: the captured response is fed
     back through the network seam to a fresh litellm call and the tool call
     round-trips. (This is a STUB of the future replay harness -- not the full
     thing -- just enough to prove the corpus is replayable.)

Deps: httpx (installed) via a monkeypatched httpx.Client.send; respx/vcrpy are
not installed in this env.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_lib.product.provider_adapter as pa  # noqa: E402
from signalos_lib.product.provider_adapter import (  # noqa: E402
    LiteLLMAgentProvider,
    ProviderAdapter,
    capture_transcript,
    iter_cassette,
)

REAL_MODEL = "openrouter/z-ai/glm-5.2"
WIRE_MODEL = "z-ai/glm-5.2"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "write a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    }
]


def _wire_tool_response() -> dict:
    return {
        "id": "chatcmpl-cap",
        "object": "chat.completion",
        "created": 0,
        "model": WIRE_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "a.txt", "content": "x"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class _WireFake:
    """Scripted fake at the network boundary. `transport()` returns a plain
    function for monkeypatch.setattr(httpx.Client, "send", ...)."""

    def __init__(self, script: list[dict]):
        self._script = script
        self.requests: list[dict] = []

    def transport(self):
        wire = self

        def send(client_self, request, **kwargs):  # noqa: ANN001 - httpx.Client.send
            try:
                body = json.loads(request.content.decode("utf-8"))
            except Exception:  # pragma: no cover
                body = {}
            wire.requests.append(body)
            idx = len(wire.requests) - 1
            return httpx.Response(200, json=wire._script[idx], request=request)

        return send


@pytest.fixture(autouse=True)
def _fresh_cassette_file(monkeypatch, tmp_path):
    """Isolate each test: fresh per-process filename + a tmp transcripts dir."""
    monkeypatch.setattr(pa, "_CAPTURE_FILENAME", None, raising=False)
    monkeypatch.setenv(pa.CAPTURE_DIR_ENV, str(tmp_path / "transcripts"))
    yield


def _fake_litellm_module(response: dict):
    """A stub litellm module (no network) for the off-by-default test."""

    def completion(**kwargs):
        return response

    return types.SimpleNamespace(
        completion=completion,
        AuthenticationError=RuntimeError,
        supports_function_calling=lambda model=None: True,
        get_model_info=lambda model=None: {"max_input_tokens": 128000},
        model_list=[],
    )


def test_capture_off_by_default_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv(pa.CAPTURE_ENV, raising=False)
    lm = _fake_litellm_module(_wire_tool_response())
    prov = LiteLLMAgentProvider(litellm_module=lm)

    resp = prov.chat(
        messages=[{"role": "user", "content": "hi"}],
        model="openai/gpt-oss-120b",
        tools=TOOLS,
    )
    # Behavior is completely unchanged (the call still works)...
    assert resp.tool_calls and resp.tool_calls[0].name == "write_file"
    # ...and NOTHING was written to disk.
    tdir = tmp_path / "transcripts"
    assert not tdir.exists() or list(tdir.glob("*.jsonl")) == []


def test_capture_hook_direct_writes_when_enabled(monkeypatch, tmp_path):
    """The capture primitive works in isolation (no litellm needed)."""
    monkeypatch.setenv(pa.CAPTURE_ENV, "1")
    request = {"model": WIRE_MODEL, "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS}
    capture_transcript(request, {"choices": [{"finish_reason": "stop"}]}, streamed=False)

    files = list((tmp_path / "transcripts").glob("*.jsonl"))
    assert len(files) == 1
    records = list(iter_cassette(files[0]))
    assert len(records) == 1
    assert records[0]["request"]["model"] == WIRE_MODEL
    assert records[0]["response"]["choices"][0]["finish_reason"] == "stop"
    assert records[0]["streamed"] is False


def test_capture_at_real_litellm_boundary(monkeypatch, tmp_path):
    """End-to-end: with capture ON, a REAL litellm call (faked socket) leaves a
    cassette carrying the raw request (with offered tools) + raw response (with
    the tool call). This is exactly what a funded run would deposit."""
    monkeypatch.setenv(pa.CAPTURE_ENV, "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-for-tests")
    wire = _WireFake([_wire_tool_response()])
    monkeypatch.setattr(httpx.Client, "send", wire.transport(), raising=True)

    adapter = ProviderAdapter(model=REAL_MODEL)
    resp = adapter.chat(
        messages=[{"role": "user", "content": "write a file"}],
        tools=TOOLS,
    )
    assert resp.tool_calls and resp.tool_calls[0].name == "write_file"

    files = list((tmp_path / "transcripts").glob("*.jsonl"))
    assert len(files) == 1, files
    records = list(iter_cassette(files[0]))
    assert len(records) == 1
    rec = records[0]
    # raw request: captured at the adapter->litellm boundary, so it carries the
    # litellm-level model id (openrouter/ prefix intact) plus the offered tools.
    assert rec["request"]["model"] == REAL_MODEL
    assert rec["request"]["tools"]
    # raw response: the provider's tool call is preserved for replay.
    choice = rec["response"]["choices"][0]
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "write_file"


def test_captured_cassette_is_replayable(monkeypatch, tmp_path):
    """STUB replay (not the full harness): take a captured response and re-serve
    it through the network seam to a FRESH litellm call, proving a funded-run
    payload can be replayed offline for $0 and still parse to the same tool call.
    """
    # --- 1. Capture a real (faked-socket) call into a cassette. ---
    monkeypatch.setenv(pa.CAPTURE_ENV, "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-for-tests")
    wire = _WireFake([_wire_tool_response()])
    monkeypatch.setattr(httpx.Client, "send", wire.transport(), raising=True)
    adapter = ProviderAdapter(model=REAL_MODEL)
    adapter.chat(messages=[{"role": "user", "content": "go"}], tools=TOOLS)

    files = list((tmp_path / "transcripts").glob("*.jsonl"))
    rec = list(iter_cassette(files[0]))[0]
    captured_response = rec["response"]

    # --- 2. Replay: serve the CAPTURED response back to a fresh litellm call. ---
    replay = _WireFake([captured_response])
    monkeypatch.setattr(httpx.Client, "send", replay.transport(), raising=True)
    # Turn capture OFF for the replay leg so we don't append to the cassette.
    monkeypatch.delenv(pa.CAPTURE_ENV, raising=False)
    replay_adapter = ProviderAdapter(model=REAL_MODEL)
    replayed = replay_adapter.chat(
        messages=[{"role": "user", "content": "go"}], tools=TOOLS
    )
    # The replayed run reproduces the same tool call -- no network, no $.
    assert replayed.tool_calls and replayed.tool_calls[0].name == "write_file"
    assert replayed.tool_calls[0].arguments == {"path": "a.txt", "content": "x"}


if __name__ == "__main__":  # pragma: no cover - manual run convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
