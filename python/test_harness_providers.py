# Tests for the multi-provider harness (AMD-CORE-007.1).
#
# NO real network: env vars are monkeypatched and the SDK / list_models
# discovery surface is stubbed. These tests lock in the contract that the
# harness is NOT Claude-locked — it auto-detects from whichever provider
# key is present, routes OpenAI-compatible endpoints correctly, and
# discovers the model from the provider instead of using a hardcoded id.

from __future__ import annotations

import pytest

import signalos_lib.harness as h


# All provider-selecting env vars we must clear so a developer's real keys
# never leak into auto-detect assertions.
_ALL_PROVIDER_ENV = [
    *h.PROVIDER_ENV_VARS,
    "GOOGLE_API_KEY",
    "SIGNALOS_LLM_PROVIDER",
    "SIGNALOS_LLM_MODEL",
    "SIGNALOS_HARNESS_TEST",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ALL_PROVIDER_ENV:
        monkeypatch.delenv(var, raising=False)
    h.clear_model_cache()
    yield
    h.clear_model_cache()


# ---------------------------------------------------------------------------
# Auto-detect
# ---------------------------------------------------------------------------

def test_autodetect_openai_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert h._resolve_provider_name() == "openai"
    provider = h._resolve_provider()
    assert isinstance(provider, h.OpenAICompatibleProvider)
    # canonical OpenAI endpoint → no base_url override
    assert provider.base_url is None
    assert provider.api_key_env == "OPENAI_API_KEY"


def test_autodetect_groq_only_routes_openai_compatible(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    assert h._resolve_provider_name() == "groq"
    provider = h._resolve_provider()
    assert isinstance(provider, h.OpenAICompatibleProvider)
    assert provider.base_url == "https://api.groq.com/openai/v1"
    assert provider.api_key_env == "GROQ_API_KEY"


def test_autodetect_gemini_only(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    assert h._resolve_provider_name() == "gemini"
    assert isinstance(h._resolve_provider(), h.GeminiProvider)


def test_autodetect_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert h._resolve_provider_name() == "anthropic"
    assert isinstance(h._resolve_provider(), h.AnthropicProvider)


def test_autodetect_priority_anthropic_wins_over_groq(monkeypatch):
    # Multiple keys present → table order decides (anthropic precedes groq).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    assert h._resolve_provider_name() == "anthropic"


def test_signalos_llm_provider_overrides_autodetect(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")  # would auto-detect openai
    monkeypatch.setenv("SIGNALOS_LLM_PROVIDER", "groq")
    assert h._resolve_provider_name() == "groq"


def test_no_keys_falls_back_to_anthropic(monkeypatch):
    assert h._resolve_provider_name() == "anthropic"


def test_unknown_provider_name_raises(monkeypatch):
    with pytest.raises(RuntimeError) as exc:
        h._resolve_provider("not-a-provider")
    msg = str(exc.value)
    # Error must enumerate the valid names (fail-closed, clear error).
    assert "not-a-provider" in msg
    for name in ("anthropic", "openai", "gemini", "groq", "ollama"):
        assert name in msg


def test_dashscope_alias_resolves(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ds-x")
    spec = h._spec_for("qwen")  # alias for dashscope
    assert spec.name == "dashscope"
    assert spec.base_url.startswith("https://dashscope-intl.aliyuncs.com")


# ---------------------------------------------------------------------------
# pick_best_model — generic, version-aware, provider-agnostic
# ---------------------------------------------------------------------------

def test_pick_best_openai_family():
    assert h.pick_best_model(["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]) == "gpt-4o"


def test_pick_best_claude_family_excludes_haiku():
    ids = ["claude-haiku-4-5", "claude-opus-4-8", "claude-sonnet-4-6"]
    assert h.pick_best_model(ids) == "claude-opus-4-8"


def test_pick_best_excludes_flash_mini_embed():
    ids = ["gemini-2.5-flash", "gemini-2.5-pro", "text-embedding-004", "gpt-4o-mini"]
    assert h.pick_best_model(ids) == "gemini-2.5-pro"


def test_pick_best_newest_wins_on_version_tuple():
    # Same family, only the version differs → highest numeric tuple wins.
    ids = ["model-v2-1", "model-v2-10", "model-v2-2"]
    assert h.pick_best_model(ids) == "model-v2-10"


def test_pick_best_empty_pool_falls_back_to_full_list():
    # Every id is filtered out → still return the newest of the full list.
    ids = ["tiny-mini-1", "tiny-mini-2"]
    assert h.pick_best_model(ids) == "tiny-mini-2"


def test_pick_best_none_on_empty():
    assert h.pick_best_model([]) is None


# ---------------------------------------------------------------------------
# Model resolution order: explicit → SIGNALOS_LLM_MODEL → discovery
# ---------------------------------------------------------------------------

def test_resolve_model_explicit_wins(monkeypatch):
    monkeypatch.setenv("SIGNALOS_LLM_MODEL", "from-env")
    monkeypatch.setattr(h, "discover_model", lambda *a, **k: "from-discovery")
    assert h.resolve_model("explicit-model", "openai") == "explicit-model"


def test_resolve_model_env_wins_over_discovery(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("SIGNALOS_LLM_MODEL", "from-env")
    monkeypatch.setattr(h, "discover_model", lambda *a, **k: "from-discovery")
    assert h.resolve_model(None, "openai") == "from-env"


def test_resolve_model_uses_discovery_when_both_absent(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setattr(
        h, "list_models", lambda name=None: ["gpt-4o-mini", "gpt-4o"],
    )
    assert h.resolve_model(None, "openai") == "gpt-4o"


def test_resolve_model_discovery_failure_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")

    def _boom(name=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(h, "list_models", _boom)
    with pytest.raises(RuntimeError) as exc:
        h.resolve_model(None, "openai")
    assert "SIGNALOS_LLM_MODEL" in str(exc.value)


def test_discover_model_caches(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    calls = {"n": 0}

    def _list(name=None):
        calls["n"] += 1
        return ["gpt-4o", "gpt-4o-mini"]

    monkeypatch.setattr(h, "list_models", _list)
    assert h.discover_model("openai") == "gpt-4o"
    assert h.discover_model("openai") == "gpt-4o"
    assert calls["n"] == 1  # second call served from cache


# ---------------------------------------------------------------------------
# list_models routing (no network): assert each kind calls the right surface
# ---------------------------------------------------------------------------

def test_list_models_openai_compatible_uses_base_url(monkeypatch):
    captured = {}

    class _FakeModel:
        def __init__(self, mid):
            self.id = mid

    class _FakeModels:
        def list(self):
            return [_FakeModel("a"), _FakeModel("b")]

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.models = _FakeModels()

    import types
    fake_openai = types.SimpleNamespace(OpenAI=_FakeClient)
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")

    ids = h.list_models("groq")
    assert ids == ["a", "b"]
    assert captured["base_url"] == "https://api.groq.com/openai/v1"
    assert captured["api_key"] == "gsk-x"


# ---------------------------------------------------------------------------
# Test mode + run_step still work offline
# ---------------------------------------------------------------------------

def test_test_mode_returns_test_provider(monkeypatch):
    monkeypatch.setenv("SIGNALOS_HARNESS_TEST", "1")
    # even with a real-looking key set, test mode wins
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert isinstance(h._resolve_provider(), h.TestProvider)


def test_run_step_test_provider_no_network(monkeypatch, tmp_path):
    (tmp_path / ".signalos").mkdir()
    # No keys, no model, explicit TestProvider → must NOT attempt discovery.
    result = h.run_step(
        step_id="t1",
        prompt="hello",
        session_id="sess-x",
        cwd=tmp_path,
        provider=h.TestProvider(),
    )
    assert result["status"] == "completed", result
    assert result["tokens_in"] is not None


# ---------------------------------------------------------------------------
# Single source of truth: product list derived from the harness table
# ---------------------------------------------------------------------------

def test_product_env_vars_derived_from_harness_table():
    from signalos_lib.product.llm_provider import _PROVIDER_ENV_VARS

    # The product list is exactly the harness key list plus the selector.
    assert _PROVIDER_ENV_VARS == [*h.PROVIDER_ENV_VARS, "SIGNALOS_LLM_PROVIDER"]
    # And it covers all 11 advertised provider keys.
    assert len(h.PROVIDER_ENV_VARS) == 11


def test_provider_table_covers_all_twelve_plus_local_and_test():
    # 11 network providers + ollama + test = 13 canonical names.
    assert h.PROVIDER_NAMES == [
        "anthropic", "openai", "gemini", "groq", "mistral", "deepseek",
        "openrouter", "xai", "together", "cerebras", "dashscope",
        "ollama", "test",
    ]
