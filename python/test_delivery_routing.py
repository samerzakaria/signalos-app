# Tests for delivery agent-dispatch routing (Foundry gen fix, STEP 5).
#
# The routing decision: a founder WITH an LLM key (agent_mode auto/remote)
# gets the governed AgentLoop path. A founder WITHOUT a key (or agent_mode ==
# "local") gets the fast, git-free local parallel path for renderable profiles.
# The legacy chunked PER-FILE LLM path is explicit opt-in only.

from __future__ import annotations

from contextlib import nullcontext
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.delivery import (
    _choose_dispatch_route,
    _dispatch_agent_loop_build,
)


SUPPORTED = "react-vite"


def test_key_present_auto_routes_to_agent_loop():
    assert _choose_dispatch_route("auto", SUPPORTED, llm_available=True) == "agent-loop"


def test_key_present_remote_routes_to_agent_loop():
    assert _choose_dispatch_route("remote", SUPPORTED, llm_available=True) == "agent-loop"


def test_no_key_auto_routes_to_local_parallel():
    assert _choose_dispatch_route("auto", SUPPORTED, llm_available=False) == "local-parallel"


def test_explicit_local_always_local_even_with_key():
    assert _choose_dispatch_route("local", SUPPORTED, llm_available=True) == "local-parallel"


def test_no_key_remote_falls_back_to_local_parallel():
    # remote requested but no key -> cannot call LLM; stay buildable locally.
    assert _choose_dispatch_route("remote", SUPPORTED, llm_available=False) == "local-parallel"


def test_unsupported_profile_with_key_still_uses_llm():
    # A profile the local renderer does not support MUST use the LLM path when
    # a key is available (there is no deterministic renderer to fall back to).
    assert _choose_dispatch_route("auto", "django-api", llm_available=True) == "agent-loop"


def test_explicit_legacy_chunked_mode_routes_to_chunked_llm():
    assert _choose_dispatch_route("legacy-chunked", SUPPORTED, llm_available=True) == "chunked-llm"


def test_explicit_chunked_mode_routes_to_chunked_llm():
    assert _choose_dispatch_route("chunked", SUPPORTED, llm_available=True) == "chunked-llm"


def test_orchestrator_alias_routes_to_agent_loop_with_key():
    assert _choose_dispatch_route("orchestrator", SUPPORTED, llm_available=True) == "agent-loop"


def _packet(run_id: str = "agent-loop-test") -> dict:
    return {
        "run_id": run_id,
        "generation": {
            "file_specs": [
                {"path": "src/App.tsx", "kind": "source"},
                {"path": "src/App.test.tsx", "kind": "test"},
            ]
        },
    }


def test_agent_loop_build_without_provider_writes_handoff(tmp_path):
    with patch(
        "signalos_lib.product.secrets_resolver.is_llm_available",
        return_value=False,
    ):
        result = _dispatch_agent_loop_build(
            repo_root=tmp_path,
            packet=_packet(),
            governance={},
            prompt="Build a test app",
            profile=SUPPORTED,
        )

    assert result["status"] == "no_api_key"
    handoff = tmp_path / ".signalos" / "product" / "AGENT_LOOP_HANDOFF.json"
    assert handoff.is_file()
    assert "agent:deliver" in handoff.read_text(encoding="utf-8")


def test_agent_loop_build_requires_signed_prior_gates_before_model_call(tmp_path):
    with patch(
        "signalos_lib.product.secrets_resolver.is_llm_available",
        return_value=True,
    ), patch(
        "signalos_lib.product.provider_adapter.ProviderAdapter",
        side_effect=AssertionError("provider must not be constructed before gates pass"),
    ):
        result = _dispatch_agent_loop_build(
            repo_root=tmp_path,
            packet=_packet(),
            governance={},
            prompt="Build a test app",
            profile=SUPPORTED,
        )

    assert result["status"] == "governance_required"
    assert any("G0 must be signed" in item for item in result["errors"])
    handoff = tmp_path / ".signalos" / "product" / "AGENT_LOOP_HANDOFF.json"
    assert handoff.is_file()


def test_agent_loop_build_success_writes_build_evidence(tmp_path):
    class FakeAdapter:
        supports_tool_calls = True

        def __init__(self, *args, **kwargs):
            pass

    class FakeLoop:
        def __init__(self, *args, **kwargs):
            self.repo_root = Path(kwargs["repo_root"])
            self.emit = kwargs["emit"]

        def run(self, system_prompt, user_message):
            assert "Build agent prompt" in system_prompt
            assert "BUILD_EVIDENCE.md" in user_message
            src = self.repo_root / "src"
            src.mkdir(parents=True, exist_ok=True)
            test_path = src / "App.test.tsx"
            app_path = src / "App.tsx"
            test_path.write_text("test('renders', () => {})\n", encoding="utf-8")
            app_path.write_text("export default function App() { return null }\n", encoding="utf-8")
            self.emit({"type": "diff", "path": "src/App.test.tsx"})
            self.emit({"type": "diff", "path": "src/App.tsx"})
            return SimpleNamespace(status="completed", error=None, tool_calls_made=2)

    with patch(
        "signalos_lib.product.secrets_resolver.is_llm_available",
        return_value=True,
    ), patch(
        "signalos_lib.product.secrets_resolver.apply_product_secrets",
        return_value=nullcontext(),
    ), patch(
        "signalos_lib.product.delivery._signed_prior_gates_for_g4",
        return_value=([0, 1, 2, 3], []),
    ), patch(
        "signalos_lib.harness._resolve_provider_name",
        return_value="test-provider",
    ), patch(
        "signalos_lib.harness.resolve_model",
        return_value="test-model",
    ), patch(
        "signalos_lib.product.provider_adapter.ProviderAdapter",
        FakeAdapter,
    ), patch(
        "signalos_lib.product.agent_loop.AgentLoop",
        FakeLoop,
    ), patch(
        "signalos_lib.agent_loader.load_agent",
        return_value={"content": "Build agent prompt"},
    ):
        result = _dispatch_agent_loop_build(
            repo_root=tmp_path,
            packet=_packet("agent-loop-success"),
            governance={"trust_tier": "T2"},
            prompt="Build a test app",
            profile=SUPPORTED,
        )

    assert result["status"] == "completed"
    assert result["files_written"] == ["src/App.test.tsx", "src/App.tsx"]
    evidence = tmp_path / "core" / "execution" / "BUILD_EVIDENCE.md"
    assert evidence.is_file()
    text = evidence.read_text(encoding="utf-8")
    assert "- status: completed" in text
    assert "`src/App.tsx`" in text
