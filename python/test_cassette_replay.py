"""Layer 2 -- OFFLINE cassette REPLAY harness ($0, no network, runs in seconds).

The capture hook in `provider_adapter` (SIGNALOS_CAPTURE_TRANSCRIPTS=1) records
each funded provider call's raw request + raw response to a cassette. This file
proves the *other half*: a recorded cassette is REPLAYED through the SAME network
seam the wire-level golden path uses -- a monkeypatched `httpx.Client.send` --
so litellm's request construction/routing and our ProviderAdapter normalize path
(`_normalize_response`/`_normalize_tool_calls`/`_wrap_stream`) run UNMODIFIED
against the recorded REAL payloads. No adapter-level fake short-circuits the
exact code that caused our worst real-provider bugs.

Each test names the real-provider bug class it guards:

  * tool-call parse   -- `choices[].message.tool_calls` -> ToolCall(args=dict);
  * reasoning leak     -- a reasoning channel must NOT bleed into user content,
                          and a reasoning turn (content=null + tool_call) must
                          not be lost;
  * delta assembly     -- streamed `delta.content` fragments reassemble in order
                          with empty/role-only chunks dropped (no drop/dup);
  * graceful handling  -- a malformed/truncated cassette surfaces a clean error
                          (or an empty read), never an unhandled crash.

Round-trip: capture (ON) a synthetic call, then replay the captured cassette and
assert the parsed response is identical -- proving the corpus is faithfully
replayable. Deps: httpx (installed); respx/vcrpy are not in this env, so we use
the same stdlib monkeypatch seam the panel allowed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_lib.product.provider_adapter as pa  # noqa: E402
from signalos_lib.product.provider_adapter import (  # noqa: E402
    ProviderAdapter,
    replay_cassette,
)

# The litellm-level model id (openrouter/ prefix) and the provider-native id
# litellm should place on the wire (prefix stripped) -- matches the golden path.
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

CALC_SRC = "def add(a, b):\n    return a + b\n"


# --- realistic recorded OpenRouter payloads (the fixture cassettes) ----------
# Shapes match a real OpenRouter/OpenAI-compatible completion: an id/provider,
# `choices[].message` (tool_calls or content, plus an optional reasoning
# channel), `finish_reason`, and `usage`.

def _completion(message: dict, finish_reason: str) -> dict:
    return {
        "id": "gen-1a2b3c",
        "provider": "z-ai",
        "model": WIRE_MODEL,
        "object": "chat.completion",
        "created": 1_720_000_000,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 812, "completion_tokens": 41, "total_tokens": 853},
    }


TOOL_CALL_RESPONSE = _completion(
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc123",
                "index": 0,
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "src/calc.py", "content": CALC_SRC}),
                },
            }
        ],
    },
    "tool_calls",
)

# A reasoning model that returns its private chain-of-thought on a separate
# `reasoning` channel plus the actual answer on `content`.
SECRET_COT = "The user asked 6*7. 6*7=42. <hidden chain-of-thought the user must never see>"
REASONING_FINAL_RESPONSE = _completion(
    {"role": "assistant", "content": "6 x 7 = 42.", "reasoning": SECRET_COT},
    "stop",
)

# A reasoning turn whose *visible* content is null -- everything is in the
# reasoning channel and a tool call. Losing this turn = a real funded-run bug.
REASONING_TOOLCALL_RESPONSE = _completion(
    {
        "role": "assistant",
        "content": None,
        "reasoning": "I should write the module now via write_file.",
        "tool_calls": [
            {
                "id": "call_r1",
                "index": 0,
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "src/calc.py", "content": CALC_SRC}),
                },
            }
        ],
    },
    "tool_calls",
)


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {
        "id": "gen-stream-1",
        "provider": "z-ai",
        "model": WIRE_MODEL,
        "object": "chat.completion.chunk",
        "created": 1_720_000_000,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


# A streamed turn: a role-only opener, three content deltas, and an empty
# closing delta with finish_reason. The opener + closer carry no text and MUST
# be dropped by _wrap_stream (no phantom empty deltas).
STREAM_CHUNKS = [
    _chunk({"role": "assistant", "content": ""}),
    _chunk({"content": "Created "}),
    _chunk({"content": "src/calc.py "}),
    _chunk({"content": "and verified it."}),
    _chunk({}, finish="stop"),
]
STREAM_TEXT = "Created src/calc.py and verified it."


def _record(response, *, streamed: bool = False, request: dict | None = None) -> dict:
    """One cassette record in the capture-hook format."""
    return {
        "ts": "2026-07-10T00:00:00Z",
        "streamed": streamed,
        "request": request
        or {
            "model": REAL_MODEL,
            "messages": [{"role": "user", "content": "go"}],
            "tools": TOOLS,
            "tool_choice": "auto",
            "max_tokens": 4096,
        },
        "response": response,
    }


def _write_cassette(tmp_path: Path, records: list[dict], name: str = "cassette.jsonl") -> Path:
    """Materialize a fixture cassette to disk as JSONL (as the capture hook does)."""
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch):
    """Every replay test runs offline & $0: a fake key so litellm proceeds to the
    (faked) socket, capture OFF so replay writes nothing, and a fresh cassette
    filename so nothing leaks between tests."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-for-tests")
    monkeypatch.delenv(pa.CAPTURE_ENV, raising=False)
    monkeypatch.setattr(pa, "_CAPTURE_FILENAME", None, raising=False)
    yield


# ---------------------------------------------------------------------------
# 1. Tool-call cassette -> correct ToolCall / content / stop_reason, offline.
# ---------------------------------------------------------------------------
def test_replay_tool_call_cassette_parses_offline(tmp_path):
    """GUARDS: tool-call parse. A recorded OpenRouter tool-call completion is
    replayed through the real litellm + adapter parse path and must yield the
    exact ToolCall with a *dict* arguments (the '\'str\' has no attribute items'
    class of bug), content=None, stop_reason=tool_use -- with no network."""
    cassette = _write_cassette(tmp_path, [_record(TOOL_CALL_RESPONSE)])

    with replay_cassette(cassette) as tape:
        adapter = ProviderAdapter(model=REAL_MODEL)
        resp = adapter.chat(messages=[{"role": "user", "content": "build it"}], tools=TOOLS)

    assert resp.stop_reason == "tool_use"
    assert resp.content is None
    assert resp.tool_calls and len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.name == "write_file"
    assert isinstance(call.arguments, dict)
    assert call.arguments == {"path": "src/calc.py", "content": CALC_SRC}
    # usage survived the round trip through litellm's normalizer.
    assert resp.usage.input_tokens == 812 and resp.usage.output_tokens == 41

    # URL-aware seam: exactly ONE chat turn was consumed (litellm's cost-map GET
    # was refused, not counted), and the wire body carried the offered tools and
    # the provider-native model id (openrouter/ prefix stripped by litellm).
    assert len(tape.requests) == 1, tape.requests
    body = tape.requests[0]
    assert body["model"] == WIRE_MODEL
    assert body["tools"] and body["tools"][0]["function"]["name"] == "write_file"
    assert tape.exhausted is False


# ---------------------------------------------------------------------------
# 2. Reasoning-channel cassette -> turn not lost, reasoning not leaked.
# ---------------------------------------------------------------------------
def test_replay_reasoning_final_answer_does_not_leak(tmp_path):
    """GUARDS: reasoning leak. A reasoning model returns its chain-of-thought on
    a separate `reasoning` channel + the answer on `content`. The adapter must
    surface the ANSWER as content and never the hidden reasoning, while the
    reasoning stays recoverable on the raw response (turn not lost)."""
    cassette = _write_cassette(tmp_path, [_record(REASONING_FINAL_RESPONSE)])

    with replay_cassette(cassette):
        adapter = ProviderAdapter(model=REAL_MODEL)
        resp = adapter.chat(messages=[{"role": "user", "content": "what is 6*7?"}])

    assert resp.content == "6 x 7 = 42."
    assert resp.stop_reason == "end_turn"
    # The hidden chain-of-thought must NOT bleed into user-visible content.
    assert "chain-of-thought" not in (resp.content or "")
    assert SECRET_COT not in (resp.content or "")
    # ...but litellm kept it on its own channel, so the turn wasn't dropped.
    raw_msg = resp.raw.choices[0].message
    assert getattr(raw_msg, "reasoning_content", None) == SECRET_COT


def test_replay_reasoning_toolcall_turn_not_lost(tmp_path):
    """GUARDS: reasoning leak / lost turn. When a reasoning turn has content=null
    and puts the action in a tool call, we must still parse the tool call (the
    turn is NOT lost to the empty content) and not leak the reasoning."""
    cassette = _write_cassette(tmp_path, [_record(REASONING_TOOLCALL_RESPONSE)])

    with replay_cassette(cassette):
        adapter = ProviderAdapter(model=REAL_MODEL)
        resp = adapter.chat(messages=[{"role": "user", "content": "write it"}], tools=TOOLS)

    assert resp.stop_reason == "tool_use"
    assert resp.content is None  # reasoning did not leak into content
    assert resp.tool_calls and resp.tool_calls[0].name == "write_file"
    assert resp.tool_calls[0].arguments == {"path": "src/calc.py", "content": CALC_SRC}


# ---------------------------------------------------------------------------
# 3. Streamed-delta cassette -> deltas reassemble in order (no drop/dup).
# ---------------------------------------------------------------------------
def test_replay_streamed_delta_cassette_reassembles(tmp_path):
    """GUARDS: delta assembly. A recorded SSE turn is re-served as a real
    text/event-stream body; litellm reassembles the chunks and our _wrap_stream
    must yield exactly the text deltas in order, dropping the role-only opener
    and the empty finish chunk."""
    cassette = _write_cassette(tmp_path, [_record(STREAM_CHUNKS, streamed=True)])

    with replay_cassette(cassette):
        adapter = ProviderAdapter(model=REAL_MODEL)
        resp = adapter.chat(messages=[{"role": "user", "content": "stream it"}], stream=True)
        assert resp.stream is not None
        deltas = [d.text for d in resp.stream if d.kind == "text"]

    assert deltas == ["Created ", "src/calc.py ", "and verified it."]
    assert "".join(deltas) == STREAM_TEXT


# ---------------------------------------------------------------------------
# 4. Malformed / truncated cassette -> graceful, never a crash.
# ---------------------------------------------------------------------------
def test_malformed_cassette_lines_are_skipped(tmp_path):
    """GUARDS: graceful handling. A cassette with a truncated/garbled JSONL line
    (a process killed mid-write) must be read without raising -- iter_cassette
    yields only the intact record, which still replays."""
    path = tmp_path / "torn.jsonl"
    good = json.dumps(_record(TOOL_CALL_RESPONSE))
    torn = '{"ts": "x", "streamed": false, "request": {"model": "openrou'  # truncated
    path.write_text(good + "\n" + torn + "\n{ not json at all }\n", encoding="utf-8")

    records = list(pa.iter_cassette(path))
    assert len(records) == 1  # the two broken lines were skipped, no exception

    with replay_cassette(path) as tape:
        adapter = ProviderAdapter(model=REAL_MODEL)
        resp = adapter.chat(messages=[{"role": "user", "content": "go"}], tools=TOOLS)
    assert resp.tool_calls and resp.tool_calls[0].name == "write_file"
    assert len(tape.requests) == 1


def test_replay_truncated_response_body_is_graceful(tmp_path):
    """GUARDS: graceful handling. A recorded response that is itself a truncated
    JSON body (the provider hung up mid-stream) must surface as a clean
    RuntimeError from the adapter -- never an unhandled parse crash."""
    truncated_body = '{"id":"gen-x","object":"chat.completion","choices":[{"index":0,"message":{"role":"assi'
    cassette = _write_cassette(tmp_path, [_record(truncated_body)])

    with replay_cassette(cassette):
        adapter = ProviderAdapter(model=REAL_MODEL)
        with pytest.raises(RuntimeError):
            adapter.chat(messages=[{"role": "user", "content": "go"}])


def test_replay_empty_choices_is_graceful(tmp_path):
    """GUARDS: graceful handling. A well-formed but empty completion (no choices)
    surfaces the adapter's explicit 'no choices' RuntimeError, not a crash."""
    cassette = _write_cassette(tmp_path, [_record(_no_choices())])

    with replay_cassette(cassette):
        adapter = ProviderAdapter(model=REAL_MODEL)
        with pytest.raises(RuntimeError):
            adapter.chat(messages=[{"role": "user", "content": "go"}])


def _no_choices() -> dict:
    return {
        "id": "gen-empty",
        "object": "chat.completion",
        "created": 1_720_000_000,
        "model": WIRE_MODEL,
        "choices": [],
        "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
    }


def test_replay_exhausted_cassette_is_graceful(tmp_path):
    """GUARDS: graceful handling. Asking for more turns than the cassette
    recorded raises a clean provider error, never an IndexError off the end."""
    cassette = _write_cassette(tmp_path, [_record(TOOL_CALL_RESPONSE)])

    with replay_cassette(cassette) as tape:
        adapter = ProviderAdapter(model=REAL_MODEL)
        adapter.chat(messages=[{"role": "user", "content": "go"}], tools=TOOLS)  # consumes the only turn
        with pytest.raises(RuntimeError):
            adapter.chat(messages=[{"role": "user", "content": "again"}], tools=TOOLS)
    assert tape.exhausted is True


# ---------------------------------------------------------------------------
# 5. Round-trip: capture (ON) a synthetic call, replay it, assert equality.
# ---------------------------------------------------------------------------
def _chat_wire(script: list[dict]):
    """A URL-aware wire fake for the CAPTURE leg: serves scripted chat responses,
    refuses litellm's cost-map GET so it can't eat a turn."""
    state = {"i": 0}

    def send(client_self, request, **kwargs):  # noqa: ANN001
        url = str(request.url)
        if "chat/completions" not in url:
            raise httpx.ConnectError(f"blocked non-chat: {url}", request=request)
        idx = state["i"]
        state["i"] += 1
        return httpx.Response(200, json=script[idx], request=request)

    return send


def test_capture_then_replay_round_trip_is_equal(monkeypatch, tmp_path):
    """The corpus is faithfully replayable: capture a (faked-socket) call to a
    cassette, then replay that exact cassette and assert the parsed response is
    identical -- same tool call, content, stop_reason and usage, for $0."""
    monkeypatch.setenv(pa.CAPTURE_ENV, "1")
    monkeypatch.setenv(pa.CAPTURE_DIR_ENV, str(tmp_path / "transcripts"))
    monkeypatch.setattr(httpx.Client, "send", _chat_wire([TOOL_CALL_RESPONSE]), raising=True)

    adapter = ProviderAdapter(model=REAL_MODEL)
    original = adapter.chat(messages=[{"role": "user", "content": "go"}], tools=TOOLS)

    # The capture hook deposited exactly one cassette with the parsed-able record.
    files = list((tmp_path / "transcripts").glob("*.jsonl"))
    assert len(files) == 1, files
    monkeypatch.delenv(pa.CAPTURE_ENV, raising=False)  # capture OFF for the replay leg

    with replay_cassette(files[0]) as tape:
        replay_adapter = ProviderAdapter(model=REAL_MODEL)
        replayed = replay_adapter.chat(messages=[{"role": "user", "content": "go"}], tools=TOOLS)

    assert replayed.as_dict() == original.as_dict()
    assert replayed.tool_calls[0].arguments == {"path": "src/calc.py", "content": CALC_SRC}
    assert tape.requests and tape.requests[0]["model"] == WIRE_MODEL
    # Replay wrote nothing new (capture stayed off).
    assert len(list((tmp_path / "transcripts").glob("*.jsonl"))) == 1


# ---------------------------------------------------------------------------
# 6. Transport unit: URL-aware, order-preserving, restores the seam.
# ---------------------------------------------------------------------------
def test_replay_cassette_restores_httpx_send(tmp_path):
    """The context manager must leave httpx.Client.send exactly as it found it."""
    cassette = _write_cassette(tmp_path, [_record(TOOL_CALL_RESPONSE)])
    before = httpx.Client.send
    with replay_cassette(cassette):
        assert httpx.Client.send is not before
    assert httpx.Client.send is before


def test_cassette_transport_from_cassette_serves_in_order(tmp_path):
    """Records are served in recorded order across successive chat turns."""
    records = [
        _record(TOOL_CALL_RESPONSE),
        _record(_completion({"role": "assistant", "content": "done."}, "stop")),
    ]
    cassette = _write_cassette(tmp_path, records)

    with replay_cassette(cassette):
        adapter = ProviderAdapter(model=REAL_MODEL)
        first = adapter.chat(messages=[{"role": "user", "content": "go"}], tools=TOOLS)
        second = adapter.chat(messages=[{"role": "user", "content": "and?"}], tools=TOOLS)

    assert first.tool_calls and first.tool_calls[0].name == "write_file"
    assert second.tool_calls is None and second.content == "done."


if __name__ == "__main__":  # pragma: no cover - manual run convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
