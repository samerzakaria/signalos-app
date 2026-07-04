# Tests for dispatch_build_agent_chunked (Foundry gen fix, STEP 3).
#
# The core rewrite: replace the single monolithic LLM call (which truncated at
# 1024 tokens and lost 16 of 18 files) with concurrent PER-FILE LLM calls
# through the git-free worker pool -- each with an adequate max_tokens budget,
# parsed + path-validated + retried per file, then aggregated with a TRUTHFUL
# RESULT.json status (dead-letter/truncation -> failed, never a false
# "completed"). NO real network: a fake provider records calls and returns
# canned fenced blocks.

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_lib.product.agent_dispatch as ad
from signalos_lib.harness import TruncatedResponseError


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeProvider:
    """Records every call and returns a fenced block for the target file.

    The prompt names exactly one output file (## File To Create -> ### `path`),
    so the fake infers the path from the prompt and returns a valid block for
    it. Behavior can be overridden per-path via `responses` (a callable or a
    string), and `failures` can force a raise/empty for a given path N times.
    """

    def __init__(self, responses=None, min_tokens_seen=None):
        self.calls = []  # list of (prompt, model, max_tokens)
        self.responses = responses or {}
        self._lock = threading.Lock()
        self._attempts = {}  # path -> count

    def _target_path(self, prompt: str) -> str:
        marker = "## File To Create"
        tail = prompt.split(marker, 1)[1] if marker in prompt else prompt
        # first ### `path` header in the output region
        for line in tail.splitlines():
            line = line.strip()
            if line.startswith("### `") and line.endswith("`"):
                return line[len("### `"):-1]
        return "src/unknown.tsx"

    def call(self, prompt, model, *, max_tokens=1024):
        path = self._target_path(prompt)
        with self._lock:
            self.calls.append((prompt, model, max_tokens))
            self._attempts[path] = self._attempts.get(path, 0) + 1
            attempt = self._attempts[path]
        handler = self.responses.get(path)
        if callable(handler):
            return handler(attempt, path, max_tokens)
        if isinstance(handler, str):
            return (handler, 10, 20)
        # default: a valid, non-empty fenced block for this path
        content = _valid_content_for(path)
        return (f"```{path}\n{content}\n```\n", 10, 20)


def _valid_content_for(path: str) -> str:
    if path.endswith(".test.tsx"):
        return (
            "import { render, fireEvent } from '@testing-library/react';\n"
            "test('adds an item', () => { /* interaction */ });\n"
        )
    if path.endswith(".tsx"):
        return "import React from 'react';\nexport default function C(){ return null; }\n"
    return "export const x = 1;\n"


# ---------------------------------------------------------------------------
# Packet helper
# ---------------------------------------------------------------------------

def _packet(run_id="chunk-run"):
    file_specs = [
        {"path": "src/types.ts", "kind": "config", "description": "types"},
        {"path": "src/App.tsx", "kind": "source", "description": "app", "entity": "Task"},
        {"path": "src/App.test.tsx", "kind": "test", "description": "app test", "entity": "Task"},
        {"path": "src/components/TaskList.tsx", "kind": "source",
         "entity": "Task", "description": "task list"},
        {"path": "src/components/TaskList.test.tsx", "kind": "test",
         "entity": "Task", "description": "task list test"},
    ]
    return {
        "run_id": run_id,
        "generation": {
            "profile": "react-vite",
            "product": "Acme",
            "file_specs": file_specs,
            "entities": [{"name": "Task", "fields": ["id", "title", "status"]}],
            "workflows": [{"name": "create_task", "description": "create"}],
            "acceptance_criteria": [],
            "design_constraints": {"state_management": "zustand"},
            "allowed_paths": ["src/**"],
            "forbidden_paths": [".env", ".signalos/"],
        },
    }


def _patch_llm(monkeypatch, provider, available=True):
    import signalos_lib.product.llm_provider as llm
    import signalos_lib.product.secrets_resolver as sr
    import signalos_lib.harness as h
    monkeypatch.setattr(llm, "is_llm_available", lambda root=None: available)
    monkeypatch.setattr(sr, "is_llm_available", lambda root=None: available)
    monkeypatch.setattr(h, "resolve_model", lambda *a, **k: "claude-opus-4-8")
    # provider passed explicitly to the dispatcher, so _resolve_provider is
    # not exercised; still stub it so an accidental call is loud, not silent.
    monkeypatch.setattr(h, "_resolve_provider", lambda *a, **k: provider)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_one_call_per_file(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider()
        _patch_llm(monkeypatch, provider)
        packet = _packet()
        result = ad.dispatch_build_agent_chunked(
            repo, packet, {}, provider=provider,
        )
        specs = packet["generation"]["file_specs"]
        # #24b: src/types.ts is the FROZEN authoritative contract -- rendered
        # deterministically, never an LLM call -- so it can't drift from what
        # every component prompt was told to conform to. Every OTHER spec is
        # exactly one call.
        llm_specs = [s for s in specs if not str(s["path"]).endswith("types.ts")]
        assert len(provider.calls) == len(llm_specs), provider.calls
        # types.ts is still produced on disk, just deterministically.
        assert (repo / "src/types.ts").exists()
        assert "export interface Task" in (repo / "src/types.ts").read_text(encoding="utf-8")
        # Every call carried an adequate budget (never the old 1024 cap).
        for _prompt, _model, max_tokens in provider.calls:
            assert max_tokens >= 8000, max_tokens
        assert result["status"] == "completed"


def test_concurrent_writes_all_files_present(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider()
        _patch_llm(monkeypatch, provider)
        packet = _packet()
        result = ad.dispatch_build_agent_chunked(
            repo, packet, {}, provider=provider,
        )
        written = set(result["files_written"])
        for spec in packet["generation"]["file_specs"]:
            assert spec["path"] in written, spec["path"]
            assert (repo / spec["path"]).exists()


def test_retry_on_empty_then_success(monkeypatch):
    empties = {"n": 0}

    def flaky(attempt, path, max_tokens):
        if attempt == 1:
            return ("", 1, 0)  # empty first attempt -> no parseable block
        return (f"```{path}\n{_valid_content_for(path)}\n```\n", 5, 5)

    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider(responses={"src/App.tsx": flaky})
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent_chunked(
            repo, _packet(), {}, provider=provider,
        )
        assert result["status"] == "completed", result["errors"]
        assert "src/App.tsx" in result["files_written"]
        assert (repo / "src/App.tsx").exists()


def test_truncation_retried(monkeypatch):
    def truncates_once(attempt, path, max_tokens):
        if attempt == 1:
            raise TruncatedResponseError("cap hit")
        return (f"```{path}\n{_valid_content_for(path)}\n```\n", 5, 5)

    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider(responses={"src/components/TaskList.tsx": truncates_once})
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent_chunked(
            repo, _packet(), {}, provider=provider,
        )
        assert result["status"] == "completed", result["errors"]
        assert "src/components/TaskList.tsx" in result["files_written"]


def test_dead_letter_is_failure_not_completed(monkeypatch):
    def always_empty(attempt, path, max_tokens):
        return ("", 1, 0)

    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider(responses={"src/components/TaskList.tsx": always_empty})
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent_chunked(
            repo, _packet(), {}, provider=provider,
        )
        # Truthful status: one file never produced -> the whole run failed.
        assert result["status"] == "failed", result
        assert any("TaskList.tsx" in e for e in result["errors"]), result["errors"]
        # RESULT.json still written (HC6).
        rj = repo / ".signalos" / "product" / "agent-runs" / "chunk-run" / "RESULT.json"
        assert rj.exists()
        data = json.loads(rj.read_text(encoding="utf-8"))
        assert data["status"] == "failed"


def test_forbidden_path_rejected(monkeypatch):
    def returns_env(attempt, path, max_tokens):
        # Agent tries to write a forbidden .env instead of the target file.
        return ("```.env\nSECRET=1\n```\n", 5, 5)

    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider(responses={"src/App.tsx": returns_env})
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent_chunked(
            repo, _packet(), {}, provider=provider,
        )
        # .env never written; run failed because a required file is missing.
        assert not (repo / ".env").exists()
        assert result["status"] == "failed", result
        assert any("forbidden" in e.lower() or "App.tsx" in e for e in result["errors"])


def test_result_json_contract(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider()
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent_chunked(
            repo, _packet(), {}, provider=provider,
        )
        rj = repo / ".signalos" / "product" / "agent-runs" / "chunk-run" / "RESULT.json"
        assert rj.exists()
        data = json.loads(rj.read_text(encoding="utf-8"))
        for key in ("status", "run_id", "files_written", "actions_taken",
                    "validation_results", "errors", "tokens_in", "tokens_out"):
            assert key in data, key
        assert data["run_id"] == "chunk-run"


def test_no_api_key_short_circuits(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider()
        _patch_llm(monkeypatch, provider, available=False)
        result = ad.dispatch_build_agent_chunked(
            repo, _packet(), {}, provider=provider,
        )
        assert result["status"] == "no_api_key"
        assert provider.calls == []


def test_tokens_aggregated(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider()
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent_chunked(
            repo, _packet(), {}, provider=provider,
        )
        # 4 LLM files x (10 in, 20 out). #24b: src/types.ts is deterministic
        # (frozen contract) so it consumes no tokens -- 5 specs, 4 LLM calls.
        assert result["tokens_in"] == 40
        assert result["tokens_out"] == 80


def test_dispatch_build_agent_delegates_to_chunked(monkeypatch):
    # Back-compat: dispatch_build_agent still works and produces all files
    # (it now delegates to the chunked path).
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = FakeProvider()
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent(
            repo, _packet(), {}, provider=provider,
        )
        written = set(result["files_written"])
        for spec in _packet()["generation"]["file_specs"]:
            assert spec["path"] in written
        assert result["status"] == "completed"
