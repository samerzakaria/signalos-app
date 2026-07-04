# Tests for TWO-PASS chunked generation (#26: kill test<->component drift by
# construction).
#
# The stance: source specs (kind != "test") are generated in PASS 1, brought to
# tsc-green by the existing #24 repair loop, and written to disk. Only THEN, in
# PASS 2, are the *.test.tsx specs generated -- and each test prompt is handed
# the FINAL on-disk source of the component under test as GROUND TRUTH. A test
# written against real code cannot assume an element/label/initial-state the
# component never renders.
#
# NO real network: a fake provider records calls (with a monotonic sequence so
# we can prove ordering) and returns canned fenced blocks.

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_lib.product.agent_dispatch as ad


# ---------------------------------------------------------------------------
# Ordering-aware fake provider
# ---------------------------------------------------------------------------

class OrderingProvider:
    """Records, per call, the target path AND a global sequence number, plus
    whether the sibling SOURCE file already existed ON DISK at call time.

    That last fact is the crux: a test spec must only be dispatched AFTER its
    source has been written, so when a *.test.tsx call is made the sibling
    *.tsx must already exist on disk.
    """

    def __init__(self, repo: Path):
        self.repo = repo
        self.calls = []  # list of dicts: {path, seq, prompt, sibling_on_disk}
        self._lock = threading.Lock()
        self._seq = 0

    def _target_path(self, prompt: str) -> str:
        marker = "## File To Create"
        tail = prompt.split(marker, 1)[1] if marker in prompt else prompt
        for line in tail.splitlines():
            line = line.strip()
            if line.startswith("### `") and line.endswith("`"):
                return line[len("### `"):-1]
        return "src/unknown.tsx"

    def call(self, prompt, model, *, max_tokens=1024):
        path = self._target_path(prompt)
        sibling_on_disk = None
        if path.endswith(".test.tsx"):
            sib = ad._sibling_source_path(path)
            sibling_on_disk = bool(sib and (self.repo / sib).exists())
        with self._lock:
            self._seq += 1
            self.calls.append({
                "path": path,
                "seq": self._seq,
                "prompt": prompt,
                "sibling_on_disk": sibling_on_disk,
            })
        return (f"```{path}\n{_valid_content_for(path)}\n```\n", 10, 20)

    def seq_for(self, path: str) -> int:
        for c in self.calls:
            if c["path"] == path:
                return c["seq"]
        raise AssertionError(f"no call for {path}: {[c['path'] for c in self.calls]}")

    def prompt_for(self, path: str) -> str:
        for c in self.calls:
            if c["path"] == path:
                return c["prompt"]
        raise AssertionError(f"no call for {path}")


def _valid_content_for(path: str) -> str:
    if path.endswith(".test.tsx"):
        return (
            "import { render, fireEvent } from '@testing-library/react';\n"
            "test('adds an item', () => { /* interaction */ });\n"
        )
    if path.endswith(".tsx"):
        return (
            "import React from 'react';\n"
            "// SENTINEL_SOURCE_MARKER\n"
            "export default function C(){ return <div>hello</div>; }\n"
        )
    return "export const x = 1;\n"


def _packet(run_id="two-pass-run"):
    file_specs = [
        {"path": "src/types.ts", "kind": "config", "description": "types"},
        {"path": "src/App.tsx", "kind": "registration", "description": "app"},
        {"path": "src/App.test.tsx", "kind": "test", "description": "app test"},
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
            "component_manifest": [
                {"filePath": "src/components/TaskList.tsx",
                 "componentName": "TaskList",
                 "importPath": "./components/TaskList"},
            ],
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
    monkeypatch.setattr(h, "_resolve_provider", lambda *a, **k: provider)


# ---------------------------------------------------------------------------
# DISCIPLINE test (1): TEST specs dispatched in a SECOND pass, AFTER sources
# are on disk.
# ---------------------------------------------------------------------------

def test_tests_dispatched_after_sources_on_disk(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = OrderingProvider(repo)
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent_chunked(
            repo, _packet(), {}, provider=provider,
        )
        assert result["status"] == "completed", result["errors"]

        # Every source (non-test) call happens strictly before EVERY test call.
        source_seqs = [c["seq"] for c in provider.calls
                       if not c["path"].endswith(".test.tsx")]
        test_seqs = [c["seq"] for c in provider.calls
                     if c["path"].endswith(".test.tsx")]
        assert source_seqs and test_seqs, provider.calls
        assert max(source_seqs) < min(test_seqs), (
            "test specs must be dispatched only after ALL source specs: "
            f"source_seqs={source_seqs} test_seqs={test_seqs}"
        )

        # And when each test was dispatched, its sibling source was on disk.
        for c in provider.calls:
            if c["path"].endswith(".test.tsx"):
                assert c["sibling_on_disk"] is True, (
                    f"{c['path']} dispatched before its source existed on disk"
                )

        # All files still produced -- source<->test pairing preserved.
        written = set(result["files_written"])
        for spec in _packet()["generation"]["file_specs"]:
            assert spec["path"] in written, spec["path"]


# ---------------------------------------------------------------------------
# DISCIPLINE test (2): a kind=="test" prompt embeds the sibling component's
# ACTUAL source text (ground truth), not just the spec.
# ---------------------------------------------------------------------------

def test_test_prompt_embeds_actual_sibling_source(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = OrderingProvider(repo)
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent_chunked(
            repo, _packet(), {}, provider=provider,
        )
        assert result["status"] == "completed", result["errors"]

        # The TaskList.test.tsx prompt must contain the EXACT source text that
        # TaskList.tsx was written with (the sentinel marker proves it's the
        # real on-disk source, not a paraphrase or the spec).
        test_prompt = provider.prompt_for("src/components/TaskList.test.tsx")
        assert "SENTINEL_SOURCE_MARKER" in test_prompt, (
            "test prompt did not embed the actual component source"
        )
        # And it must carry the ground-truth framing.
        low = test_prompt.lower()
        assert "final" in low and "source" in low
        assert "do not assume" in low or "do not assume any" in low


# ---------------------------------------------------------------------------
# DISCIPLINE test (3): a MISSING sibling source falls back gracefully (never
# crashes; still emits a spec-based test).
# ---------------------------------------------------------------------------

def test_missing_sibling_source_falls_back(monkeypatch):
    # A test spec whose sibling source is NOT part of the packet (so it never
    # lands on disk). Generation must still complete for it via the spec-based
    # prompt -- no crash, no dead-letter for the test itself.
    packet = _packet()
    packet["generation"]["file_specs"] = [
        {"path": "src/types.ts", "kind": "config", "description": "types"},
        {"path": "src/components/Orphan.test.tsx", "kind": "test",
         "entity": "Task", "description": "orphan test (no source sibling)"},
    ]
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        provider = OrderingProvider(repo)
        _patch_llm(monkeypatch, provider)
        result = ad.dispatch_build_agent_chunked(
            repo, packet, {}, provider=provider,
        )
        assert result["status"] == "completed", result["errors"]
        assert "src/components/Orphan.test.tsx" in result["files_written"]
        # The prompt for the orphan test cannot embed a source that doesn't
        # exist, and must NOT contain a bogus sentinel from another file.
        prompt = provider.prompt_for("src/components/Orphan.test.tsx")
        assert "SENTINEL_SOURCE_MARKER" not in prompt


# ---------------------------------------------------------------------------
# Unit tests for the pairing + loading helpers.
# ---------------------------------------------------------------------------

def test_sibling_source_path_maps_component_and_app():
    assert ad._sibling_source_path("src/components/Foo.test.tsx") == \
        "src/components/Foo.tsx"
    assert ad._sibling_source_path("src/App.test.tsx") == "src/App.tsx"
    # Non-test paths have no sibling-source mapping.
    assert ad._sibling_source_path("src/components/Foo.tsx") is None
    assert ad._sibling_source_path("src/types.ts") is None


def test_load_source_under_test_reads_disk():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        src = repo / "src" / "components" / "Foo.tsx"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("export default function Foo(){ return null; }\n",
                       encoding="utf-8")
        spec = {"path": "src/components/Foo.test.tsx", "kind": "test"}
        loaded = ad._load_source_under_test(repo, spec)
        assert loaded is not None
        rel, text = loaded
        assert rel == "src/components/Foo.tsx"
        assert "function Foo" in text


def test_load_source_under_test_missing_returns_none():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        spec = {"path": "src/components/Gone.test.tsx", "kind": "test"}
        assert ad._load_source_under_test(repo, spec) is None
