"""Layer 0 -- capability-detection contract (cheapest, highest-value gate).

Locks in the fix for our worst harness bug. `provider_adapter.detect_capabilities`
trusted litellm's STATIC registry, whose `supports_function_calling` FALSE-NEGATIVES
new model ids (z-ai/glm-5.2, qwen/qwen3.7-max, openai/gpt-oss-120b,
anthropic/claude-sonnet-4.6). A False there made the harness offer the model NO
tools -> it could only narrate -> it wrote nothing -> it scored 0 on a funded run.

The fix: trust a registry `True`, but on `False`/unknown/raise DEFER to a name
heuristic (tool-capable unless the id is marked tools-less: embedding/instruct/
whisper). These tests stub ONLY the registry lookup so they are deterministic and
offline, while exercising the REAL detect_capabilities logic. They FAIL if anyone
reintroduces the "trust litellm's False" bug -- see
`test_reintroducing_the_trust_false_bug_would_regress`, which runs the OLD logic
inline and shows it disagrees with the (correct) current behavior.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.provider_adapter import detect_capabilities  # noqa: E402

# The ACTUAL pinned model ids the harness routes today, in both the bare and
# the openrouter/-prefixed forms they flow through (capability detection runs on
# the litellm-normalized id, which keeps the openrouter/ prefix).
PINNED_TOOL_MODELS = [
    "z-ai/glm-5.2",
    "qwen/qwen3.7-max",
    "openai/gpt-oss-120b",
    "anthropic/claude-sonnet-4.6",
    "openrouter/z-ai/glm-5.2",
    "openrouter/qwen/qwen3.7-max",
    "openrouter/openai/gpt-oss-120b",
    "openrouter/anthropic/claude-sonnet-4.6",
]

# Known tools-LESS ids (name marks them non-tool: embedding models).
TOOLLESS_MODELS = [
    "text-embedding-3-small",
    "openai/text-embedding-3-large",
    "openrouter/qwen/qwen3-embedding-8b",
]


def _fake_litellm(*, supports, info=None, model_list=None, raises=False):
    """A stub litellm module exposing ONLY the registry lookups
    detect_capabilities uses. Deterministic + offline: no network, no real
    litellm registry. `supports` is what the stale registry reports for
    supports_function_calling (or set raises=True to model a registry that
    throws)."""

    def _sff(model=None):
        if raises:
            raise RuntimeError("simulated litellm registry lookup failure")
        return supports

    return types.SimpleNamespace(
        supports_function_calling=_sff,
        get_model_info=lambda model=None: (info or {}),
        model_list=list(model_list or []),
    )


class TestPinnedModelsAlwaysGetTools:
    """The core lock-in: with the registry reporting the BUGGY False (or
    raising), every pinned model id must STILL be offered tools."""

    @pytest.mark.parametrize("model", PINNED_TOOL_MODELS)
    def test_registry_false_still_yields_tools(self, model):
        # The exact stale-registry false-negative that caused the 0-score run.
        lm = _fake_litellm(supports=False)
        caps = detect_capabilities(model, litellm_module=lm)
        assert caps.supports_tool_calls is True, (
            f"{model} must be offered tools even when litellm's registry "
            f"false-negatives supports_function_calling"
        )

    @pytest.mark.parametrize("model", PINNED_TOOL_MODELS)
    def test_registry_raises_still_yields_tools(self, model):
        lm = _fake_litellm(supports=False, raises=True)
        caps = detect_capabilities(model, litellm_module=lm)
        assert caps.supports_tool_calls is True

    @pytest.mark.parametrize("model", PINNED_TOOL_MODELS)
    def test_registry_true_is_trusted(self, model):
        lm = _fake_litellm(supports=True)
        caps = detect_capabilities(model, litellm_module=lm)
        assert caps.supports_tool_calls is True


class TestToollessModelsStayToolless:
    """A genuinely tools-less id (embedding) must resolve to False: the name
    heuristic marks it non-tool, so neither the registry nor the fallback
    should hand it tools."""

    @pytest.mark.parametrize("model", TOOLLESS_MODELS)
    def test_embedding_false_when_registry_false(self, model):
        lm = _fake_litellm(supports=False)
        caps = detect_capabilities(model, litellm_module=lm)
        assert caps.supports_tool_calls is False

    @pytest.mark.parametrize("model", TOOLLESS_MODELS)
    def test_embedding_false_when_registry_raises(self, model):
        lm = _fake_litellm(supports=False, raises=True)
        caps = detect_capabilities(model, litellm_module=lm)
        assert caps.supports_tool_calls is False


class TestNoLitellmFallback:
    """With NO litellm at all, detection must still be offline-safe and apply
    the same name heuristic (tool-capable unless the id is marked tools-less)."""

    @pytest.mark.parametrize("model", PINNED_TOOL_MODELS)
    def test_no_litellm_pinned_get_tools(self, model):
        caps = detect_capabilities(model, litellm_module=None)
        # Real litellm IS importable in this env, but it false-negatives these
        # ids -> the fix's heuristic still yields True. This asserts the
        # end-to-end (real-registry) contract that the funded run depends on.
        assert caps.supports_tool_calls is True

    @pytest.mark.parametrize("model", TOOLLESS_MODELS)
    def test_no_litellm_embeddings_toolless(self, model):
        caps = detect_capabilities(model, litellm_module=None)
        assert caps.supports_tool_calls is False


def _old_buggy_supports(litellm_module, model):
    """The ORIGINAL (buggy) logic: trust litellm's supports_function_calling
    verbatim, including its False. Kept here ONLY to prove the regression net."""
    return bool(litellm_module.supports_function_calling(model=model))


class TestReintroducingTheBugWouldRegress:
    """Explicit proof that Layer 0 catches the bug if it is reintroduced.

    We reproduce the OLD "trust litellm's False" logic inline and show it
    disagrees with the current detect_capabilities on exactly the pinned ids.
    The current code returns True; the old code returns False. If someone
    reverts detect_capabilities to the old logic, the `is True` assertions in
    this file (and this contrast) start failing."""

    @pytest.mark.parametrize("model", PINNED_TOOL_MODELS)
    def test_old_logic_disagrees_and_current_is_correct(self, model):
        lm = _fake_litellm(supports=False)  # stale registry false-negative
        old = _old_buggy_supports(lm, model)
        current = detect_capabilities(model, litellm_module=lm).supports_tool_calls
        assert old is False, "sanity: the reintroduced bug would deny tools"
        assert current is True, "the fix keeps offering tools"
        assert current != old, (
            "Layer 0 regression net: current behavior must differ from the "
            "reintroduced trust-False bug"
        )


if __name__ == "__main__":  # pragma: no cover - manual run convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
