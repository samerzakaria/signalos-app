"""Unit coverage for scripts/v4-live-smoke.py helpers.

The live smoke harness is manual by design, but its model-selection helpers
must remain deterministic and no-guessing under unit tests.
"""
from __future__ import annotations

import importlib.util
import json
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_smoke_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "v4-live-smoke.py"
    spec = importlib.util.spec_from_file_location("v4_live_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_resolve_ollama_model_returns_explicit_litellm_path(monkeypatch) -> None:
    mod = _load_smoke_module()

    def fake_urlopen(url: str, timeout: int = 0) -> _Response:
        assert url == "http://localhost:11434/api/tags"
        assert timeout == 10
        return _Response({"models": [{"name": "qwen2.5-coder:14b"}]})

    monkeypatch.delenv("SIGNALOS_MODEL", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert mod._resolve_ollama_model() == "ollama/qwen2.5-coder:14b"


def test_resolve_ollama_model_accepts_prefixed_override(monkeypatch) -> None:
    mod = _load_smoke_module()

    def fake_urlopen(_url: str, timeout: int = 0) -> _Response:
        return _Response({"models": [{"name": "qwen2.5-coder:14b"}]})

    monkeypatch.setenv("SIGNALOS_MODEL", "ollama/qwen2.5-coder:14b")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert mod._resolve_ollama_model() == "ollama/qwen2.5-coder:14b"
