"""Offline contract tests for the governed backend model-matrix driver.

The matrix itself is deliberately live and potentially expensive.  This test
module never contacts a provider.  Most tests are pure contract checks; one
starts the source sidecar and runs a disposable, keyless ``signal-init`` so the
preflight boundary itself cannot silently rot.  Together they lock down the
parts that must be trustworthy *before* a paid run starts: the versioned model
catalog, deterministic selection, explicit dotenv loading, credential
redaction, honest aggregate status, and fail-fast CLI behavior.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "scripts" / "backend_matrix" / "driver.py"
MODEL_CONFIG = ROOT / "scripts" / "backend_matrix" / "models.json"

EXPECTED_MODELS = [
    ("fable5", "openrouter", "anthropic/claude-fable-5", "OPENROUTER_API_KEY"),
    ("gpt56solpro", "openrouter", "openai/gpt-5.6-sol-pro", "OPENROUTER_API_KEY"),
    ("grok45", "openrouter", "x-ai/grok-4.5", "OPENROUTER_API_KEY"),
    ("glm52", "openrouter", "z-ai/glm-5.2", "OPENROUTER_API_KEY"),
    ("deepseekv4pro", "openrouter", "deepseek/deepseek-v4-pro", "OPENROUTER_API_KEY"),
    ("qwen37max", "openrouter", "qwen/qwen3.7-max", "OPENROUTER_API_KEY"),
    ("mimov25pro", "openrouter", "xiaomi/mimo-v2.5-pro", "OPENROUTER_API_KEY"),
    ("kimik27code", "openrouter", "moonshotai/kimi-k2.7-code", "OPENROUTER_API_KEY"),
    ("deepseekv4flash", "openrouter", "deepseek/deepseek-v4-flash", "OPENROUTER_API_KEY"),
    ("gptoss120b", "openrouter", "openai/gpt-oss-120b", "OPENROUTER_API_KEY"),
    ("gpt56terrapro", "openrouter", "openai/gpt-5.6-terra-pro", "OPENROUTER_API_KEY"),
    ("sonnet5", "openrouter", "anthropic/claude-sonnet-5", "OPENROUTER_API_KEY"),
    ("qwen37plus", "openrouter", "qwen/qwen3.7-plus", "OPENROUTER_API_KEY"),
    ("mimov25", "openrouter", "xiaomi/mimo-v2.5", "OPENROUTER_API_KEY"),
    ("minimaxm3", "openrouter", "minimax/minimax-m3", "OPENROUTER_API_KEY"),
    ("nemotron3ultra", "openrouter", "nvidia/nemotron-3-ultra-550b-a55b", "OPENROUTER_API_KEY"),
    ("gemini31propreview", "openrouter", "google/gemini-3.1-pro-preview", "OPENROUTER_API_KEY"),
    ("katcoderprov25", "openrouter", "kwaipilot/kat-coder-pro-v2.5", "OPENROUTER_API_KEY"),
]

EXPECTED_COHORTS = {
    **{alias: "primary" for alias in (
        "fable5", "gpt56solpro", "grok45", "glm52", "deepseekv4pro",
        "qwen37max", "mimov25pro", "kimik27code", "deepseekv4flash", "gptoss120b",
    )},
    **{alias: "challenger" for alias in (
        "gpt56terrapro", "sonnet5", "qwen37plus", "mimov25", "minimaxm3", "nemotron3ultra",
    )},
    "gemini31propreview": "exploratory",
    "katcoderprov25": "exploratory",
}


def _load_driver() -> ModuleType:
    """Import the script without relying on ``scripts`` being a package."""

    assert DRIVER_PATH.is_file(), f"backend matrix driver is missing: {DRIVER_PATH}"
    spec = importlib.util.spec_from_file_location("signalos_backend_matrix_driver", DRIVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses and a few runtime type helpers resolve the defining module
    # through sys.modules while the class body executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def driver() -> ModuleType:
    return _load_driver()


def _model_tuple(spec: Any) -> tuple[str, str, str, str]:
    """Keep assertions readable whether ModelSpec is a dataclass or mapping."""

    if isinstance(spec, dict):
        return tuple(spec[key] for key in ("alias", "provider", "model", "key_env"))  # type: ignore[return-value]
    return (spec.alias, spec.provider, spec.model, spec.key_env)


def _offline_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "COHERE_API_KEY",
        "TOGETHER_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "PERPLEXITY_API_KEY",
        "CEREBRAS_API_KEY",
        "DASHSCOPE_API_KEY",
        "SIGNALOS_MATRIX_ENV_FILE",
    ):
        env.pop(key, None)
    # Any accidental HTTP call fails locally and quickly instead of reaching a
    # real provider during the offline suite.
    env.update(
        {
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        }
    )
    return env


def test_versioned_catalog_is_the_requested_openrouter_matrix(driver: ModuleType) -> None:
    catalog = driver.load_model_catalog(MODEL_CONFIG)

    assert [_model_tuple(spec) for spec in catalog] == EXPECTED_MODELS
    assert len({spec[0] for spec in EXPECTED_MODELS}) == len(EXPECTED_MODELS)
    assert {spec.alias: spec.cohort for spec in catalog} == EXPECTED_COHORTS
    # LiteLLM's adapter adds the provider route itself.  Persisting
    # ``openrouter/`` here would double-prefix model IDs at runtime.
    assert all(not model.startswith("openrouter/") for _, _, model, _ in EXPECTED_MODELS)


def test_driver_is_portable_and_does_not_hardcode_the_old_key_source() -> None:
    source = DRIVER_PATH.read_text(encoding="utf-8")

    assert "ClearReq" not in source
    assert r"C:\\Users\\" not in source
    assert "C:/Users/" not in source
    assert "/Users/" not in source


def test_driver_targets_the_real_long_lived_backend_protocol() -> None:
    source = DRIVER_PATH.read_text(encoding="utf-8")

    for protocol_marker in (
        "signalos_ipc_server.py",
        "capabilities",
        "signal-init",
        "agent:deliver",
        "agent:verdict",
        "agent:cancel",
        "agent:resume",
        "state:gates",
        "delivery.json",
    ):
        assert protocol_marker in source
    # The discarded matrix imported the orchestrator directly and therefore
    # skipped the desktop/backend transport seam.  This harness must not
    # regress to that narrower test.
    assert "GateOrchestrator(" not in source
    # A timed-out row owns one sidecar tree; it must never kill every Node
    # process on the developer's machine as the discarded script did.
    assert '"/IM", "node.exe"' not in source
    assert '"gate0:approve"' in source
    assert '"via": "simulation"' in source
    assert "_prepare_local_release_remote" in source


def test_row_local_release_origin_supports_offline_commit_and_push(
    driver: ModuleType, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("baseline\n", encoding="utf-8")

    evidence = driver._prepare_local_release_remote(
        workspace,
        tmp_path / "origin.git",
        env=dict(os.environ),
    )

    assert evidence["kind"] == "row-local-bare-git-origin"
    assert all(command["ok"] for command in evidence["commands"])
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert Path(remote).resolve() == (tmp_path / "origin.git").resolve()


def test_paid_matrix_requires_one_clean_committed_engine_tree(driver: ModuleType) -> None:
    clean = {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "dirty": False,
        "dirty_paths": [],
        "upstream": "origin/main",
        "upstream_commit": "a" * 40,
        "pushed": True,
    }
    driver._require_reproducible_engine(
        clean, live=True, verified_ci_sha=clean["commit"]
    )
    driver._require_reproducible_engine(
        {**clean, "dirty": True, "dirty_paths": ["python/backend.py"]},
        live=False,
    )

    with pytest.raises(driver.InfrastructureError, match="uncommitted engine"):
        driver._require_reproducible_engine(
            {**clean, "dirty": True, "dirty_paths": ["python/backend.py"]}, live=True,
            verified_ci_sha=clean["commit"],
        )
    with pytest.raises(driver.InfrastructureError, match="Git commit and tree"):
        driver._require_reproducible_engine(
            {**clean, "commit": "unknown"}, live=True,
            verified_ci_sha=clean["commit"],
        )
    with pytest.raises(driver.InfrastructureError, match="pushed"):
        driver._require_reproducible_engine(
            {**clean, "pushed": False}, live=True,
            verified_ci_sha=clean["commit"],
        )
    with pytest.raises(driver.InfrastructureError, match="ci-verified-sha"):
        driver._require_reproducible_engine(clean, live=True)
    with pytest.raises(driver.InfrastructureError, match="does not match"):
        driver._require_reproducible_engine(
            clean, live=True, verified_ci_sha="c" * 40
        )

    metadata = driver._engine_metadata()
    assert metadata["commit"] != "unknown"
    assert metadata["tree"] != "unknown"
    assert metadata["upstream"] != "unknown"
    assert metadata["pushed"] is True


def test_live_matrix_output_must_be_outside_engine_tree(
    driver: ModuleType, tmp_path: Path
) -> None:
    assert driver._require_external_output_root(tmp_path) == tmp_path.resolve()
    with pytest.raises(driver.InfrastructureError, match="outside"):
        driver._require_external_output_root(ROOT)
    with pytest.raises(driver.InfrastructureError, match="outside"):
        driver._require_external_output_root(ROOT / "matrix-results")


def test_cost_guard_refuses_a_decreasing_provider_counter(driver: ModuleType) -> None:
    class FakeRouter:
        def __init__(self) -> None:
            self.values = iter((10.0, 9.5))

        def usage(self) -> float:
            return next(self.values)

    guard = driver.CostGuard(FakeRouter(), cap=1.0, interval=0.0)
    with pytest.raises(driver.CostGuardError, match="moved backward"):
        guard.check(force=True)


def test_orchestrator_profile_is_explicit_and_has_one_evidence_value(
    driver: ModuleType,
) -> None:
    """The paid request and recorded evidence must name the same profile.

    The desktop backend intentionally defaults to ``production`` while this
    comparison harness intentionally defaults to ``benchmark``.  Lock down the
    explicit handoff so a backend-default change cannot silently change what a
    matrix row measured or what its evidence claims it measured.
    """
    args = driver._build_parser().parse_args([])
    assert args.orchestrator_profile == driver.DEFAULT_ORCHESTRATOR_PROFILE
    assert args.orchestrator_profile == "benchmark"

    spec = driver.load_model_catalog(MODEL_CONFIG)[0]
    request = driver._delivery_request(
        prompt="Build the fixture",
        spec=spec,
        run_id="matrix-profile-contract",
        orchestrator_profile=args.orchestrator_profile,
    )
    recorded_row = {"orchestrator_profile": args.orchestrator_profile}
    recorded_manifest = {"orchestrator_profile": args.orchestrator_profile}

    assert request["profile"] == recorded_row["orchestrator_profile"]
    assert request["profile"] == recorded_manifest["orchestrator_profile"]
    with pytest.raises(ValueError, match="unknown orchestrator profile"):
        driver._delivery_request(
            prompt="Build the fixture",
            spec=spec,
            run_id="matrix-profile-contract",
            orchestrator_profile="prodution",
        )

    # The live row must also compare that requested value with the backend's
    # durable delivery checkpoint; merely echoing the request is not evidence.
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert 'row["persisted_orchestrator_profile"] = persisted_profile' in source
    assert "persisted_profile != orchestrator_profile" in source


def test_backend_preflight_runs_real_keyless_source_sidecar(
    driver: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "SIGNALOS_LLM_PROVIDER",
        "SIGNALOS_LLM_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    scenario = driver._load_scenario(ROOT / "scripts" / "backend_matrix" / "scenarios" / "expense_tracker.json")
    result = driver._backend_preflight(scenario, init_timeout=120)

    assert result["ready"] is True
    assert result["protocol"] == 1
    assert result["init_complete"] is True
    assert result["signal_init_profile"] == "react-vite"
    assert {"agent:deliver", "agent:verdict", "agent:cancel", "agent:resume"}.issubset(
        result["required_commands"]
    )


def test_model_selection_is_explicit_ordered_and_fail_closed(driver: ModuleType) -> None:
    catalog = driver.load_model_catalog(MODEL_CONFIG)

    assert [_model_tuple(model)[0] for model in driver.select_models(catalog, None)] == [
        row[0] for row in EXPECTED_MODELS
    ]
    assert [_model_tuple(model)[0] for model in driver.select_models(catalog, ["all"])] == [
        row[0] for row in EXPECTED_MODELS
    ]
    assert [_model_tuple(model)[0] for model in driver.select_models(catalog, ["qwen37max", "gpt56solpro"])] == [
        "qwen37max",
        "gpt56solpro",
    ]
    assert [model.alias for model in driver.select_models(catalog, ["primary"])] == [
        alias for alias, cohort in EXPECTED_COHORTS.items() if cohort == "primary"
    ]
    assert [model.alias for model in driver.select_models(catalog, ["challenger"])] == [
        alias for alias, cohort in EXPECTED_COHORTS.items() if cohort == "challenger"
    ]
    assert [model.alias for model in driver.select_models(catalog, ["exploratory"])] == [
        alias for alias, cohort in EXPECTED_COHORTS.items() if cohort == "exploratory"
    ]

    with pytest.raises(ValueError, match="not-configured"):
        driver.select_models(catalog, ["not-configured"])
    with pytest.raises(ValueError):
        driver.select_models(catalog, [])


def test_env_file_parser_is_explicit_non_mutating_and_quote_aware(
    driver: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "matrix.env"
    env_file.write_text(
        """\
# comments and blank lines are allowed

export OPENROUTER_API_KEY = "sk-or-test#literal"
LABEL='value with spaces'
PLAIN=value # trailing comment
EMPTY=
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "ambient-wins-later")

    parsed = driver.load_env_file(env_file)

    assert parsed == {
        "OPENROUTER_API_KEY": "sk-or-test#literal",
        "LABEL": "value with spaces",
        "PLAIN": "value",
        "EMPTY": "",
    }
    # Parsing is not credential installation.  The caller can enforce the
    # documented precedence (ambient environment before explicit env file).
    assert os.environ["OPENROUTER_API_KEY"] == "ambient-wins-later"


def test_env_file_parser_rejects_malformed_non_comment_lines(
    driver: ModuleType, tmp_path: Path
) -> None:
    env_file = tmp_path / "broken.env"
    env_file.write_text("OPENROUTER_API_KEY=good\nthis is not an assignment\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"(?i)(line|assignment|malformed)"):
        driver.load_env_file(env_file)


def test_explicit_key_source_wins_and_repository_env_is_never_implicit(
    driver: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = driver.load_model_catalog(MODEL_CONFIG)
    selected = catalog[0]
    explicit = tmp_path / "selected.env"
    explicit.write_text("OPENROUTER_API_KEY=explicit-benchmark-key\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "stale-ambient-key")

    key, source = driver._resolve_api_key(selected, explicit)

    assert key == "explicit-benchmark-key"
    assert source == "explicit-env-file"

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SIGNALOS_MATRIX_ENV_FILE", raising=False)
    with pytest.raises(ValueError, match="missing API key"):
        driver._resolve_api_key(selected, None)


def test_recursive_redaction_never_serializes_a_supplied_secret(driver: ModuleType) -> None:
    secret = "sk-or-v1-super-secret-value"
    original = {
        "authorization": f"Bearer {secret}",
        "nested": [secret, {"url": f"https://example.invalid/?token={secret}"}],
        "ordinary": "keep me",
    }

    cleaned = driver.redact(original, secrets=(secret,))
    serialized = json.dumps(cleaned, sort_keys=True)

    assert secret not in serialized
    assert cleaned["ordinary"] == "keep me"
    assert original["nested"][0] == secret  # evidence redaction must not mutate its input


def test_secret_scan_streams_exact_key_through_large_files(
    driver: ModuleType, tmp_path: Path
) -> None:
    secret = "sk-or-v1-large-file-secret-sentinel"
    large = tmp_path / "large-build-artifact.bin"
    with large.open("wb") as handle:
        handle.seek(21 * 1024 * 1024)
        handle.write(secret.encode("utf-8"))

    result = driver._secret_scan(tmp_path, secret)

    assert result["ok"] is False
    assert result["hits"] == [
        {"path": "large-build-artifact.bin", "kind": "exact-selected-key"}
    ]


def test_live_provider_preflight_refuses_an_uncapped_key(driver: ModuleType) -> None:
    selected = driver.load_model_catalog(MODEL_CONFIG)[:1]

    class FakeRouter:
        def key_info(self) -> dict[str, object]:
            return {"usage": 1.25, "limit": None, "limit_remaining": None}

        def usage(self) -> float:
            return 1.25

        def models(self) -> dict[str, dict[str, object]]:
            return {
                selected[0].model: {
                    "id": selected[0].model,
                    "supported_parameters": ["tools"],
                }
            }

    with pytest.raises(driver.InfrastructureError, match="provider-side spending limit"):
        driver._provider_preflight(
            FakeRouter(), selected, required_remaining=1.0, require_provider_limit=True
        )


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([{"status": "pass"}], 0),
        ([{"status": "pass"}, {"status": "pass"}], 0),
        ([], 1),
        ([{"status": "skip"}], 1),
        ([{"status": "fail"}], 1),
        ([{"status": "error"}], 1),
        ([{"status": "pass"}, {"status": "fail"}], 1),
        ([{}], 1),
    ],
)
def test_aggregate_exit_code_passes_only_a_nonempty_all_pass_matrix(
    driver: ModuleType, results: list[dict[str, str]], expected: int
) -> None:
    assert driver.results_exit_code(results) == expected


def test_list_models_cli_needs_no_key_and_no_network() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(DRIVER_PATH),
            "--list-models",
            "--models-config",
            str(MODEL_CONFIG),
        ],
        cwd=str(ROOT),
        env=_offline_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    output = f"{proc.stdout}\n{proc.stderr}"
    for alias, provider, model, _key_env in EXPECTED_MODELS:
        assert alias in output
        assert provider in output
        assert model in output


def test_list_models_cli_never_echoes_an_ambient_key() -> None:
    env = _offline_env()
    sentinel_secret = "sk-or-v1-must-never-appear-in-cli-output"
    env["OPENROUTER_API_KEY"] = sentinel_secret

    proc = subprocess.run(
        [sys.executable, str(DRIVER_PATH), "--list-models"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert sentinel_secret not in f"{proc.stdout}\n{proc.stderr}"


def test_unknown_cli_model_fails_before_key_lookup_or_live_work() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(DRIVER_PATH),
            "--models",
            "definitely-not-configured",
            "--live",
            "--max-cost-per-model",
            "1",
            "--acknowledge-key-exposure",
        ],
        cwd=str(ROOT),
        env=_offline_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )

    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode != 0
    assert "definitely-not-configured" in combined
    assert "missing api key" not in combined.lower()
    assert "api key is required" not in combined.lower()


def test_default_cli_invocation_cannot_start_a_paid_run() -> None:
    proc = subprocess.run(
        [sys.executable, str(DRIVER_PATH)],
        cwd=str(ROOT),
        env=_offline_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )

    combined = f"{proc.stdout}\n{proc.stderr}".lower()
    assert proc.returncode != 0
    assert "--live" in combined
