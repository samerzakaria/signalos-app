# test_voice_transcribe.py
# Voice transcription (#21): signalos_lib/voice_transcribe.py + the
# voice:transcribe IPC handler.
#
# All HTTP is mocked at the urllib boundary (urllib.request.urlopen inside the
# module) — no real network. conftest.py strips provider keys, so the no-key
# baseline is hermetic; key-dependent tests set fakes via monkeypatch.

from __future__ import annotations

import base64
import io
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_ipc_server as srv  # noqa: E402
from signalos_lib import voice_transcribe as vt  # noqa: E402


AUDIO_BYTES = b"\x1aE\xdf\xa3fake-webm-opus-payload" * 4
AUDIO_B64 = base64.b64encode(AUDIO_BYTES).decode("ascii")


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mock_urlopen(monkeypatch, responder):
    """Install a fake urlopen and capture every (request, timeout) call."""
    calls: list[dict] = []

    def fake_urlopen(request, timeout=None):
        calls.append({"request": request, "timeout": timeout})
        return responder(request)

    monkeypatch.setattr(vt.urllib_request, "urlopen", fake_urlopen)
    return calls


# ---------------------------------------------------------------------------
# provider selection
# ---------------------------------------------------------------------------


def test_pick_provider_none_without_keys():
    assert vt.pick_provider() is None


def test_pick_provider_prefers_openai_over_groq(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-groq")
    provider = vt.pick_provider()
    assert provider is not None and provider.name == "openai"
    assert provider.model == "whisper-1"


def test_pick_provider_groq_when_only_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-groq")
    provider = vt.pick_provider()
    assert provider is not None and provider.name == "groq"
    assert provider.model == "whisper-large-v3"
    assert "api.groq.com" in provider.url


def test_pick_provider_ignores_blank_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert vt.pick_provider() is None


# ---------------------------------------------------------------------------
# transcribe(): domain outcomes
# ---------------------------------------------------------------------------


def test_transcribe_no_capable_provider(monkeypatch):
    calls = _mock_urlopen(monkeypatch, lambda req: _FakeResponse(b"{}"))
    result = vt.transcribe(AUDIO_B64)
    assert result == {
        "status": "no-capable-provider",
        "error": "Voice transcription needs an OpenAI or Groq key.",
    }
    assert calls == []  # gated before any network call


def test_transcribe_success_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    calls = _mock_urlopen(
        monkeypatch,
        lambda req: _FakeResponse(json.dumps({"text": "  hello world  "}).encode()),
    )
    result = vt.transcribe(AUDIO_B64, "audio/webm;codecs=opus")
    assert result["status"] == "ok"
    assert result["text"] == "hello world"
    assert result["provider"] == "openai"
    assert result["model"] == "whisper-1"

    assert len(calls) == 1
    request = calls[0]["request"]
    assert request.full_url == "https://api.openai.com/v1/audio/transcriptions"
    assert request.get_header("Authorization") == "Bearer sk-test-openai"
    body = request.data
    assert AUDIO_BYTES in body                # audio uploaded verbatim
    assert b'name="model"' in body and b"whisper-1" in body
    assert b'filename="recording.webm"' in body
    assert b"Content-Type: audio/webm\r\n" in body  # codecs param stripped


def test_transcribe_success_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-groq")
    calls = _mock_urlopen(
        monkeypatch, lambda req: _FakeResponse(json.dumps({"text": "groq says hi"}).encode())
    )
    result = vt.transcribe(AUDIO_B64)
    assert result["status"] == "ok"
    assert result["text"] == "groq says hi"
    assert result["provider"] == "groq"
    request = calls[0]["request"]
    assert request.full_url == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert b"whisper-large-v3" in request.data


def test_transcribe_accepts_data_url_prefix(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _mock_urlopen(
        monkeypatch, lambda req: _FakeResponse(json.dumps({"text": "ok"}).encode())
    )
    result = vt.transcribe("data:audio/webm;base64," + AUDIO_B64)
    assert result["status"] == "ok"
    assert AUDIO_BYTES in calls[0]["request"].data


def test_transcribe_invalid_base64(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _mock_urlopen(monkeypatch, lambda req: _FakeResponse(b"{}"))
    result = vt.transcribe("!!!not-base64!!!")
    assert result["status"] == "invalid-audio"
    assert calls == []


def test_transcribe_empty_audio(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _mock_urlopen(monkeypatch, lambda req: _FakeResponse(b"{}"))
    result = vt.transcribe("")
    assert result["status"] == "invalid-audio"
    assert calls == []


def test_transcribe_oversize_never_hits_network(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _mock_urlopen(monkeypatch, lambda req: _FakeResponse(b"{}"))
    big = base64.b64encode(b"a" * (vt.MAX_AUDIO_BYTES + 1)).decode("ascii")
    result = vt.transcribe(big)
    assert result["status"] == "too-large"
    assert "10 MB" in result["error"]
    assert calls == []


def test_transcribe_provider_http_error_passthrough(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def raise_http_error(request):
        raise urllib.error.HTTPError(
            request.full_url, 429,
            "Too Many Requests", hdrs=None,
            fp=io.BytesIO(json.dumps(
                {"error": {"message": "Rate limit reached for whisper-1"}}
            ).encode()),
        )

    _mock_urlopen(monkeypatch, raise_http_error)
    result = vt.transcribe(AUDIO_B64)
    assert result["status"] == "provider-error"
    assert result["provider"] == "openai"
    assert "HTTP 429" in result["error"]
    assert "Rate limit reached for whisper-1" in result["error"]
    # Privacy: the audio payload never appears in the error surface.
    assert AUDIO_B64 not in result["error"]


def test_transcribe_network_failure(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def raise_url_error(request):
        raise urllib.error.URLError("connection refused")

    _mock_urlopen(monkeypatch, raise_url_error)
    result = vt.transcribe(AUDIO_B64)
    assert result["status"] == "provider-error"
    assert "groq" in result["error"]
    assert "connection refused" in result["error"]


def test_transcribe_unparseable_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _mock_urlopen(monkeypatch, lambda req: _FakeResponse(b"<html>gateway</html>"))
    result = vt.transcribe(AUDIO_B64)
    assert result["status"] == "provider-error"
    assert "unreadable" in result["error"]


def test_transcribe_response_missing_text(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _mock_urlopen(monkeypatch, lambda req: _FakeResponse(b'{"task": "transcribe"}'))
    result = vt.transcribe(AUDIO_B64)
    assert result["status"] == "provider-error"
    assert "no transcript text" in result["error"]


# ---------------------------------------------------------------------------
# voice:transcribe IPC handler
# ---------------------------------------------------------------------------


def _handle(command: str, args: list | None = None) -> dict:
    return srv.handle({
        "id": "test-req",
        "command": command,
        "args": args or [],
        "cwd": os.getcwd(),
    })


def test_ipc_voice_transcribe_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _mock_urlopen(
        monkeypatch, lambda req: _FakeResponse(json.dumps({"text": "build a todo app"}).encode())
    )
    resp = _handle("voice:transcribe", [json.dumps({
        "audio_b64": AUDIO_B64, "mime": "audio/webm;codecs=opus",
    })])
    assert resp["ok"], resp
    assert resp["data"]["status"] == "ok"
    assert resp["data"]["text"] == "build a todo app"
    assert resp["data"]["provider"] == "openai"


def test_ipc_voice_transcribe_no_capable_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = _mock_urlopen(monkeypatch, lambda req: _FakeResponse(b"{}"))
    resp = _handle("voice:transcribe", [json.dumps({"audio_b64": AUDIO_B64})])
    assert resp["ok"], resp
    assert resp["data"] == {
        "status": "no-capable-provider",
        "error": "Voice transcription needs an OpenAI or Groq key.",
    }
    assert calls == []


def test_ipc_voice_transcribe_oversize(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _mock_urlopen(monkeypatch, lambda req: _FakeResponse(b"{}"))
    big = base64.b64encode(b"a" * (vt.MAX_AUDIO_BYTES + 1)).decode("ascii")
    resp = _handle("voice:transcribe", [json.dumps({"audio_b64": big})])
    assert resp["ok"], resp
    assert resp["data"]["status"] == "too-large"
    assert calls == []


def test_ipc_voice_transcribe_provider_error_passthrough(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def raise_http_error(request):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", hdrs=None,
            fp=io.BytesIO(b'{"error": {"message": "audio format not supported"}}'),
        )

    _mock_urlopen(monkeypatch, raise_http_error)
    resp = _handle("voice:transcribe", [json.dumps({"audio_b64": AUDIO_B64})])
    assert resp["ok"], resp
    assert resp["data"]["status"] == "provider-error"
    assert "audio format not supported" in resp["data"]["error"]


def test_ipc_voice_transcribe_missing_audio_is_transport_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for bad_args in ([], [json.dumps({})], [json.dumps({"audio_b64": "  "})]):
        resp = _handle("voice:transcribe", bad_args)
        assert not resp["ok"], bad_args
        assert "voice:transcribe" in resp["error"]


def test_ipc_voice_transcribe_malformed_payload_is_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = _handle("voice:transcribe", ["{not json"])
    assert not resp["ok"]
    assert "voice:transcribe" in resp["error"]


def test_ipc_voice_transcribe_never_persists_audio(tmp_path, monkeypatch):
    """No file in the workspace ends up holding the audio payload."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _mock_urlopen(
        monkeypatch, lambda req: _FakeResponse(json.dumps({"text": "ok"}).encode())
    )
    resp = _handle("voice:transcribe", [json.dumps({"audio_b64": AUDIO_B64})])
    assert resp["ok"], resp
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
