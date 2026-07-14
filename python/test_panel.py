# test_panel.py
# The War Room engine: unit tests for the cross-vendor second-opinion panel
# (signalos_lib.panel.consult) plus the panel:consult IPC wiring in
# signalos_ipc_server.
#
# Every panel.consult test injects a FAKE opener via panel.ask/panel.total_usage
# (the opener= kwarg) so the REAL request-building and OpenRouter response-shape
# parsing run WITHOUT touching the network. The IPC test asserts the response
# envelope shape AND that a sentinel OpenRouter key never leaks into the wire.

from __future__ import annotations

import functools
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import panel  # noqa: E402
import signalos_ipc_server as srv  # noqa: E402


# ---------------------------------------------------------------------------
# Fake OpenRouter transport (no network)
# ---------------------------------------------------------------------------


class _FakeResp:
    """Minimal urlopen() return: a .read() that yields JSON bytes."""

    def __init__(self, obj: dict):
        self._bytes = json.dumps(obj).encode()

    def read(self) -> bytes:
        return self._bytes


class FakeOpener:
    """Stand-in for urllib.request.urlopen(req, timeout=...).

    Serves the two OpenRouter endpoints panel.py talks to:
      * /credits          -> {"data": {"total_usage": <float>}} (before/after)
      * /chat/completions -> {"choices": [{"message": {"content": <text>}}]}
    Records every chat request so tests can assert which models were queried and
    exactly what prompt each one saw (blindness). Optionally raises for a chosen
    model to simulate a single-vendor outage. Thread-safe: the four chat calls
    run in parallel from consult()'s pool."""

    def __init__(self, *, credits, answer_for, raise_for=None):
        self._credits = list(credits)
        self._answer_for = answer_for
        self._raise_for = set(raise_for or ())
        self._credit_i = 0
        self.chat_requests: list[tuple[str, list]] = []
        self._lock = threading.Lock()

    def __call__(self, req, timeout=None):
        url = req.full_url
        if url.endswith("/credits"):
            with self._lock:
                val = self._credits[min(self._credit_i, len(self._credits) - 1)]
                self._credit_i += 1
            # A real request carries the bearer key; assert it is present but
            # never surface it anywhere.
            assert req.headers.get("Authorization", "").startswith("Bearer ")
            return _FakeResp({"data": {"total_usage": val}})
        if url.endswith("/chat/completions"):
            body = json.loads(req.data.decode())
            model = body["model"]
            with self._lock:
                self.chat_requests.append((model, body["messages"]))
            if model in self._raise_for:
                raise RuntimeError(f"simulated vendor outage for {model}")
            return _FakeResp({"choices": [{"message": {"content": self._answer_for(model)}}]})
        raise AssertionError(f"unexpected url: {url}")


def _consult(opener: FakeOpener, question: str, **kwargs) -> dict:
    """Run panel.consult with the fake opener threaded through the REAL
    ask/total_usage (so their request build + response parse are exercised)."""
    return panel.consult(
        question,
        _ask=functools.partial(panel.ask, opener=opener),
        _usage=functools.partial(panel.total_usage, opener=opener),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# panel.consult unit tests
# ---------------------------------------------------------------------------


def test_every_selected_model_is_queried():
    opener = FakeOpener(credits=[1.0, 1.0], answer_for=lambda m: f"answer from {m}")
    result = _consult(opener, "Should we ship on Friday?", key="sk-unit")

    selected = {m for m, _ in panel.DEFAULT_MODELS}
    queried = {m for m, _ in opener.chat_requests}
    assert queried == selected
    assert [a["model"] for a in result["answers"]] == [m for m, _ in panel.DEFAULT_MODELS]
    assert result["models"] == [m for m, _ in panel.DEFAULT_MODELS]


def test_each_ask_is_blind_only_the_question():
    """Each model must see ONLY the shared question -- never another model's
    answer (that would herd the panel)."""
    question = "Is this architecture over-engineered?"
    opener = FakeOpener(credits=[0.0, 0.0], answer_for=lambda m: f"answer from {m}")
    _consult(opener, question, key="sk-unit")

    assert len(opener.chat_requests) == len(panel.DEFAULT_MODELS)
    for model, messages in opener.chat_requests:
        user_turns = [m for m in messages if m["role"] == "user"]
        assert len(user_turns) == 1
        assert user_turns[0]["content"] == question
        # No other model's answer (or any answer) was threaded into the prompt.
        assert "answer from" not in json.dumps(messages)


def test_one_model_failure_is_soft_others_survive():
    victim = "openai/gpt-5.6-sol"
    opener = FakeOpener(
        credits=[2.0, 2.0],
        answer_for=lambda m: f"answer from {m}",
        raise_for={victim},
    )
    result = _consult(opener, "What breaks first at scale?", key="sk-unit")

    by_model = {a["model"]: a for a in result["answers"]}
    assert by_model[victim]["ok"] is False
    assert by_model[victim]["error"]
    assert "RuntimeError" in by_model[victim]["error"]
    assert by_model[victim]["text"] == ""
    for model, ans in by_model.items():
        if model == victim:
            continue
        assert ans["ok"] is True
        assert ans["error"] is None
        assert ans["text"] == f"answer from {model}"


def test_cost_is_after_minus_before():
    opener = FakeOpener(credits=[2.0, 2.75], answer_for=lambda m: "ok")
    result = _consult(opener, "Should we adopt this stack?", key="sk-unit")
    assert result["cost_usd"] == pytest.approx(0.75)
    # /credits queried exactly twice: once before, once after.
    assert opener._credit_i == 2


def test_empty_question_raises_value_error():
    opener = FakeOpener(credits=[0.0, 0.0], answer_for=lambda m: "ok")
    with pytest.raises(ValueError):
        _consult(opener, "   ", key="sk-unit")
    with pytest.raises(ValueError):
        _consult(opener, "", key="sk-unit")


def test_missing_key_raises_value_error(monkeypatch):
    # No injected key AND no ambient/keyfile key -> ValueError before any call.
    monkeypatch.setattr(panel, "load_key", lambda: "")
    opener = FakeOpener(credits=[0.0, 0.0], answer_for=lambda m: "ok")
    with pytest.raises(ValueError):
        _consult(opener, "Should we ship?", key=None)
    # Nothing was queried.
    assert opener.chat_requests == []


def test_custom_models_string_is_normalised():
    opener = FakeOpener(credits=[0.0, 0.0], answer_for=lambda m: f"a:{m}")
    result = _consult(opener, "pick", key="sk-unit", models="vendor/one, vendor/two")
    assert result["models"] == ["vendor/one", "vendor/two"]
    assert {m for m, _ in opener.chat_requests} == {"vendor/one", "vendor/two"}


# ---------------------------------------------------------------------------
# panel:consult IPC wiring
# ---------------------------------------------------------------------------


def test_ipc_panel_consult_envelope_and_no_key_leak(tmp_path, monkeypatch):
    """The IPC handler returns the {ok, data.answers, data.cost_usd} envelope,
    resolves the OpenRouter key server-side, and NEVER lets that key appear in
    the serialized response."""
    monkeypatch.chdir(tmp_path)
    # Secret-shaped sentinel; if it ever reached the wire the assertion trips.
    sentinel = "sk-or-" + ("Z" * 48)
    monkeypatch.setenv("OPENROUTER_API_KEY", sentinel)

    captured: dict = {}

    def fake_consult(question, *, models=None, system=None, key=None, **kw):
        captured["key"] = key
        captured["question"] = question
        return {
            "answers": [
                {
                    "model": "anthropic/claude-sonnet-5",
                    "name": "Sonnet-5",
                    "text": "Ship it -- the plan is sound.",
                    "ok": True,
                    "error": None,
                },
                {
                    "model": "openai/gpt-5.6-sol",
                    "name": "GPT-5.6-Sol",
                    "text": "Hold: add a rollback path first.",
                    "ok": True,
                    "error": None,
                },
            ],
            "cost_usd": 0.0123,
            "models": ["anthropic/claude-sonnet-5", "openai/gpt-5.6-sol"],
            "system": "candid second opinion",
        }

    monkeypatch.setattr(panel, "consult", fake_consult)

    resp = srv.panel_consult("req-panel-1", [json.dumps({"question": "Ship on Friday?"})])

    assert resp["ok"] is True
    assert resp["id"] == "req-panel-1"
    data = resp["data"]
    assert len(data["answers"]) == 2
    assert data["answers"][0]["text"] == "Ship it -- the plan is sound."
    assert data["cost_usd"] == 0.0123
    # The handler resolved the env key and forwarded it to the engine...
    assert captured["key"] == sentinel
    assert captured["question"] == "Ship on Friday?"
    # ...but the key never appears ANYWHERE in the serialized response.
    assert sentinel not in json.dumps(resp)


def test_ipc_panel_consult_reads_workspace_env_local(tmp_path, monkeypatch):
    """When no key is in the process env, the handler reads the workspace
    .env.local (the desktop Secrets vault target) -- and still never leaks it."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    vault_key = "sk-or-" + ("v" * 40)
    (tmp_path / ".env.local").write_text(
        f"OPENROUTER_API_KEY={vault_key}\n", encoding="utf-8"
    )

    captured: dict = {}

    def fake_consult(question, *, models=None, system=None, key=None, **kw):
        captured["key"] = key
        return {"answers": [], "cost_usd": None, "models": [], "system": ""}

    monkeypatch.setattr(panel, "consult", fake_consult)

    resp = srv.panel_consult("req-panel-2", [json.dumps({"question": "Q?"})])
    assert resp["ok"] is True
    assert captured["key"] == vault_key
    assert vault_key not in json.dumps(resp)


def test_ipc_panel_consult_missing_key_is_error_without_leak(tmp_path, monkeypatch):
    """No key anywhere -> transport error carrying panel.consult's message; the
    handler must not invent or echo a key."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(panel, "load_key", lambda: "")

    resp = srv.panel_consult("req-panel-3", [json.dumps({"question": "Q?"})])
    assert resp["ok"] is False
    assert "OpenRouter key" in resp["error"]


def test_ipc_panel_consult_empty_question_is_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-present")
    resp = srv.panel_consult("req-panel-4", [json.dumps({"question": "   "})])
    assert resp["ok"] is False
    assert resp["error"]


def test_ipc_panel_consult_is_advertised_in_capabilities():
    assert "panel:consult" in srv.ROUTED_COMMANDS
    assert "panel:consult" in srv._capabilities_payload()["commands"]


def test_ipc_panel_consult_routes_through_handle(tmp_path, monkeypatch):
    """End-to-end through handle(): the command must dispatch to panel_consult
    (proving the wiring in the routed dispatch, not just the function)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-present")

    seen: dict = {}

    def fake_consult(question, *, models=None, system=None, key=None, **kw):
        seen["question"] = question
        seen["system"] = system
        seen["models"] = models
        return {"answers": [], "cost_usd": 0.0, "models": [], "system": system or ""}

    monkeypatch.setattr(panel, "consult", fake_consult)

    resp = srv.handle({
        "id": "req-panel-5",
        "command": "panel:consult",
        "args": [json.dumps({
            "question": "Is the plan sound?",
            "system": "be terse",
            "models": ["vendor/x", "vendor/y"],
        })],
        "cwd": str(tmp_path),
    })
    assert resp["ok"] is True
    assert seen["question"] == "Is the plan sound?"
    assert seen["system"] == "be terse"
    assert seen["models"] == ["vendor/x", "vendor/y"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
