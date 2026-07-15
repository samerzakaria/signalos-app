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
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "scripts" / "backend_matrix" / "driver.py"
MODEL_CONFIG = ROOT / "scripts" / "backend_matrix" / "models.json"
DEPENDENCY_ROOT = ROOT / "scripts" / "backend_matrix" / "dependencies"

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


def test_funded_react_dependency_manifest_matches_owned_scaffold(tmp_path: Path) -> None:
    from signalos_lib.product.stacks import get_adapter

    get_adapter("react-vite").scaffold(tmp_path, {"product_name": "test"})
    scaffold_text = (tmp_path / "package.json").read_text(encoding="utf-8")
    scaffold_package = json.loads(scaffold_text)
    funded_text = (DEPENDENCY_ROOT / "react-vite" / "package.json").read_text(
        encoding="utf-8"
    )
    funded_package = json.loads(
        funded_text
    )
    policy = json.loads((DEPENDENCY_ROOT / "policy.json").read_text(encoding="utf-8"))

    assert funded_package == scaffold_package
    assert funded_text == scaffold_text
    assert policy["profile"] == "react-vite"
    assert policy["platform"] == "linux/amd64"
    assert policy["buildImage"].startswith("docker.io/library/node:20-bookworm@sha256:")
    assert len(policy["buildImage"].rsplit(":", 1)[-1]) == 64
    assert policy["installCommand"] == [
        "npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"
    ]


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


def test_fresh_release_checkout_is_bound_to_exact_remote_commit(
    driver: ModuleType, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("baseline\n", encoding="utf-8")
    env = dict(os.environ)
    evidence = driver._prepare_local_release_remote(
        workspace, tmp_path / "origin.git", env=env
    )
    (workspace / "src").mkdir()
    (workspace / "src" / "app.js").write_text(
        "export const ready = true;\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Release product"], cwd=workspace, check=True,
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "push", "origin", "HEAD"], cwd=workspace, check=True,
        capture_output=True, text=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=workspace, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    finalization = {
        "outcome": {
            "commit": {"status": "committed", "sha": sha},
            "push": {
                "status": "ok",
                "verified": True,
                "remote": "origin",
                "ref": f"refs/heads/{branch}",
                "sha": sha,
            },
        }
    }

    checkout = tmp_path / "release-checkout"
    result = driver._checkout_pushed_release(
        Path(evidence["path"]), checkout, finalization, env=env
    )

    assert result["verified"] is True
    assert result["commit"] == sha
    assert (checkout / "src" / "app.js").read_text(encoding="utf-8") == (
        "export const ready = true;\n"
    )

    moved = subprocess.run(
        ["git", "rev-parse", "HEAD^"], cwd=workspace, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git", f"--git-dir={evidence['path']}", "update-ref",
            f"refs/heads/{branch}", moved,
        ],
        check=True, capture_output=True, text=True,
    )
    with pytest.raises(driver.ProductFailure, match="exact G5 commit"):
        driver._checkout_pushed_release(
            Path(evidence["path"]),
            tmp_path / "moved-checkout",
            finalization,
            env=env,
        )


def test_paid_matrix_requires_one_clean_committed_engine_tree(driver: ModuleType) -> None:
    clean = {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "branch": "main",
        "dirty": False,
        "dirty_paths": [],
        "upstream": "origin/main",
        "upstream_commit": "a" * 40,
        "pushed": True,
    }
    driver._require_reproducible_engine(clean, live=True)
    driver._require_reproducible_engine(
        {**clean, "dirty": True, "dirty_paths": ["python/backend.py"]},
        live=False,
    )

    with pytest.raises(driver.InfrastructureError, match="uncommitted engine"):
        driver._require_reproducible_engine(
            {**clean, "dirty": True, "dirty_paths": ["python/backend.py"]}, live=True,
        )
    with pytest.raises(driver.InfrastructureError, match="Git commit and tree"):
        driver._require_reproducible_engine(
            {**clean, "commit": "unknown"}, live=True,
        )
    with pytest.raises(driver.InfrastructureError, match="main upstream"):
        driver._require_reproducible_engine(
            {**clean, "pushed": False}, live=True,
        )
    with pytest.raises(driver.InfrastructureError, match="main branch"):
        driver._require_reproducible_engine(
            {**clean, "branch": "feature"}, live=True,
        )
    with pytest.raises(driver.InfrastructureError, match="main upstream"):
        driver._require_reproducible_engine(
            {**clean, "upstream_commit": "c" * 40}, live=True,
        )
    with pytest.raises(driver.InfrastructureError, match="main upstream"):
        driver._require_reproducible_engine(
            {**clean, "upstream": "fork/main"}, live=True,
        )

    git_values = {
        ("status", "--porcelain"): "",
        ("rev-parse", "HEAD"): clean["commit"],
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): (
            clean["upstream"]
        ),
        ("rev-parse", "@{upstream}"): clean["upstream_commit"],
        ("rev-parse", "HEAD^{tree}"): clean["tree"],
        ("branch", "--show-current"): clean["branch"],
    }
    metadata = driver._engine_metadata(git_reader=git_values.__getitem__)
    assert metadata["commit"] == clean["commit"]
    assert metadata["tree"] == clean["tree"]
    assert metadata["branch"] == "main"
    assert metadata["upstream"] == "origin/main"
    assert metadata["upstream_commit"] == metadata["commit"]
    assert metadata["pushed"] is True


def _green_engine() -> dict[str, Any]:
    return {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "branch": "main",
        "dirty": False,
        "dirty_paths": [],
        "upstream": "origin/main",
        "upstream_commit": "a" * 40,
        "pushed": True,
    }


def _green_github_responses(
    driver: ModuleType, engine: dict[str, Any]
) -> dict[str, Any]:
    repository = driver.CI_REPOSITORY_FULL_NAME
    responses: dict[str, Any] = {
        f"/repos/{repository}": {
            "node_id": driver.CI_REPOSITORY_NODE_ID,
            "full_name": repository,
            "default_branch": "main",
        },
        f"/repos/{repository}/git/ref/heads/main": {
            "ref": "refs/heads/main",
            "object": {"type": "commit", "sha": engine["commit"]},
        },
    }
    policy = driver._load_ci_policy()
    for workflow_index, workflow in enumerate(policy["workflows"], start=1):
        workflow_id = workflow["id"]
        workflow_endpoint = f"/repos/{repository}/actions/workflows/{workflow_id}"
        run_id = 900_000_000 + workflow_index
        responses[workflow_endpoint] = {
            "id": workflow_id,
            "name": workflow["name"],
            "path": workflow["path"],
            "state": "active",
        }
        responses[workflow_endpoint + "/runs"] = {
            "total_count": 1,
            "workflow_runs": [
                {
                    "id": run_id,
                    "run_attempt": 1,
                    "workflow_id": workflow_id,
                    "name": workflow["name"],
                    "event": "push",
                    "head_branch": "main",
                    "head_sha": engine["commit"],
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": "2026-07-15T00:00:00Z",
                    "updated_at": "2026-07-15T00:10:00Z",
                    "html_url": f"https://github.com/{repository}/actions/runs/{run_id}",
                }
            ],
        }
        run = responses[workflow_endpoint + "/runs"]["workflow_runs"][0]
        responses[f"/repos/{repository}/actions/runs/{run_id}"] = json.loads(
            json.dumps(run)
        )
        responses[
            f"/repos/{repository}/actions/runs/{run_id}/attempts/1/jobs"
        ] = {
            "total_count": len(workflow["required_jobs"]),
            "jobs": [
                {
                    "id": run_id * 100 + job_index,
                    "name": job_name,
                    "run_id": run_id,
                    "head_sha": engine["commit"],
                    "workflow_name": workflow["name"],
                    "head_branch": "main",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-07-15T00:00:00Z",
                    "completed_at": "2026-07-15T00:09:00Z",
                    "html_url": (
                        f"https://github.com/{repository}/actions/runs/{run_id}"
                        f"/job/{run_id * 100 + job_index}"
                    ),
                }
                for job_index, job_name in enumerate(
                    workflow["required_jobs"], start=1
                )
            ],
        }
    return responses


def _offline_github_fetch(
    responses: dict[str, Any], calls: list[tuple[str, dict[str, str] | None]] | None = None
):
    def fetch(endpoint: str, query: dict[str, str] | None = None) -> Any:
        if calls is not None:
            calls.append((endpoint, query))
        assert endpoint in responses, f"unexpected GitHub endpoint: {endpoint}"
        return json.loads(json.dumps(responses[endpoint]))

    return fetch


def test_github_collection_accepts_one_fixed_multi_page_snapshot(
    driver: ModuleType,
) -> None:
    pages = {
        1: {
            "total_count": 101,
            "items": [{"id": item_id} for item_id in range(1, 101)],
        },
        2: {"total_count": 101, "items": [{"id": 101}]},
    }

    def fetch(_endpoint: str, query: dict[str, str] | None) -> Any:
        assert query is not None
        return pages[int(query["page"])]

    rows = driver._github_collection(fetch, "/fixed", "items")
    assert [row["id"] for row in rows] == list(range(1, 102))


def test_github_collection_rejects_truncated_pages(driver: ModuleType) -> None:
    def fetch(_endpoint: str, _query: dict[str, str] | None) -> Any:
        return {"total_count": 2, "items": [{"id": 1}]}

    with pytest.raises(driver.InfrastructureError, match="truncated"):
        driver._github_collection(fetch, "/truncated", "items")


def test_github_collection_rejects_duplicate_ids(driver: ModuleType) -> None:
    pages = {
        1: {
            "total_count": 101,
            "items": [{"id": item_id} for item_id in range(1, 101)],
        },
        2: {"total_count": 101, "items": [{"id": 100}]},
    }

    def fetch(_endpoint: str, query: dict[str, str] | None) -> Any:
        assert query is not None
        return pages[int(query["page"])]

    with pytest.raises(driver.InfrastructureError, match="duplicate/invalid"):
        driver._github_collection(fetch, "/duplicate", "items")


def test_github_collection_rejects_totals_that_change_between_pages(
    driver: ModuleType,
) -> None:
    pages = {
        1: {
            "total_count": 101,
            "items": [{"id": item_id} for item_id in range(1, 101)],
        },
        2: {"total_count": 100, "items": [{"id": 101}]},
    }

    def fetch(_endpoint: str, query: dict[str, str] | None) -> Any:
        assert query is not None
        return pages[int(query["page"])]

    with pytest.raises(driver.InfrastructureError, match="total changed"):
        driver._github_collection(fetch, "/changed-total", "items")


@pytest.mark.parametrize("total", [True, -1, "101", 1_001])
def test_github_collection_rejects_invalid_or_over_cap_totals(
    driver: ModuleType, total: Any
) -> None:
    def fetch(_endpoint: str, _query: dict[str, str] | None) -> Any:
        return {"total_count": total, "items": []}

    with pytest.raises(driver.InfrastructureError, match="invalid items total"):
        driver._github_collection(fetch, "/invalid-total", "items")


def test_authoritative_ci_attestation_binds_exact_green_main_runs(
    driver: ModuleType,
) -> None:
    engine = _green_engine()
    responses = _green_github_responses(driver, engine)
    calls: list[tuple[str, dict[str, str] | None]] = []

    attestation = driver._verify_ci_attestation(
        engine, fetch_json=_offline_github_fetch(responses, calls)
    )

    evidence = attestation["evidence"]
    assert attestation["schema"] == "signalos.backend-matrix.ci-attestation.v1"
    assert attestation["evidence_sha256"] == driver._canonical_json_sha256(evidence)
    assert evidence["subject"]["commit"] == engine["commit"]
    assert evidence["subject"]["tree"] == engine["tree"]
    assert evidence["repository"] == {
        "node_id": "R_kgDOSSqeCA",
        "full_name": "samerzakaria/signalos-app",
        "default_branch": "main",
        "remote_ref": "refs/heads/main",
        "remote_sha": engine["commit"],
    }
    assert {item["id"] for item in evidence["workflows"]} == {
        277295597,
        270226986,
    }
    assert all(
        job["status"] == "completed" and job["conclusion"] == "success"
        for workflow in evidence["workflows"]
        for job in workflow["jobs"]
    )
    assert calls[0] == ("/repos/samerzakaria/signalos-app", None)
    for workflow in evidence["workflows"]:
        run_id = workflow["run"]["id"]
        attempt = workflow["run"]["attempt"]
        jobs_endpoint = (
            f"/repos/samerzakaria/signalos-app/actions/runs/{run_id}"
            f"/attempts/{attempt}/jobs"
        )
        run_endpoint = (
            f"/repos/samerzakaria/signalos-app/actions/runs/{run_id}"
        )
        assert next(
            index for index, call in enumerate(calls) if call[0] == jobs_endpoint
        ) < next(index for index, call in enumerate(calls) if call[0] == run_endpoint)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("repository", "repository identity"),
        ("remote_ref", "refs/heads/main"),
        ("workflow", "workflow identity"),
        ("run_event", "no exact main/push run"),
        ("run_sha", "no exact main/push run"),
        ("run_status", "not completed and successful"),
        ("run_failure", "not completed and successful"),
        ("job_missing", "job set drifted"),
        ("job_extra", "job set drifted"),
        ("job_unbound", "unbound job"),
        ("job_failed", "job did not pass"),
        ("run_reread", "changed during attestation"),
    ],
)
def test_ci_attestation_fails_closed_on_github_evidence_drift(
    driver: ModuleType, mutation: str, match: str
) -> None:
    engine = _green_engine()
    responses = _green_github_responses(driver, engine)
    repository = driver.CI_REPOSITORY_FULL_NAME
    policy = driver._load_ci_policy()
    first = policy["workflows"][0]
    workflow_endpoint = f"/repos/{repository}/actions/workflows/{first['id']}"
    runs_endpoint = workflow_endpoint + "/runs"
    run = responses[runs_endpoint]["workflow_runs"][0]
    jobs_endpoint = (
        f"/repos/{repository}/actions/runs/{run['id']}"
        f"/attempts/{run['run_attempt']}/jobs"
    )

    if mutation == "repository":
        responses[f"/repos/{repository}"]["node_id"] = "R_wrong"
    elif mutation == "remote_ref":
        responses[f"/repos/{repository}/git/ref/heads/main"]["object"]["sha"] = "c" * 40
    elif mutation == "workflow":
        responses[workflow_endpoint]["name"] = "renamed"
    elif mutation == "run_event":
        run["event"] = "workflow_dispatch"
    elif mutation == "run_sha":
        run["head_sha"] = "c" * 40
    elif mutation == "run_status":
        run["status"] = "in_progress"
        run["conclusion"] = None
    elif mutation == "run_failure":
        run["conclusion"] = "failure"
    elif mutation == "job_missing":
        responses[jobs_endpoint]["jobs"].pop()
        responses[jobs_endpoint]["total_count"] -= 1
    elif mutation == "job_extra":
        responses[jobs_endpoint]["jobs"].append(
            {
                "id": 999999,
                "name": "unexpected job",
                "run_id": run["id"],
                "head_sha": engine["commit"],
                "workflow_name": first["name"],
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
            }
        )
        responses[jobs_endpoint]["total_count"] += 1
    elif mutation == "job_unbound":
        responses[jobs_endpoint]["jobs"][0]["run_id"] = run["id"] + 1
    elif mutation == "job_failed":
        responses[jobs_endpoint]["jobs"][0]["conclusion"] = "failure"
    elif mutation == "run_reread":
        responses[f"/repos/{repository}/actions/runs/{run['id']}"][
            "run_attempt"
        ] = run["run_attempt"] + 1

    with pytest.raises(driver.InfrastructureError, match=match):
        driver._verify_ci_attestation(
            engine, fetch_json=_offline_github_fetch(responses)
        )


def test_ci_attestation_never_accepts_an_older_success_over_a_newer_attempt(
    driver: ModuleType,
) -> None:
    engine = _green_engine()
    responses = _green_github_responses(driver, engine)
    repository = driver.CI_REPOSITORY_FULL_NAME
    first = driver._load_ci_policy()["workflows"][0]
    endpoint = (
        f"/repos/{repository}/actions/workflows/{first['id']}/runs"
    )
    old_success = responses[endpoint]["workflow_runs"][0]
    newer_failure = json.loads(json.dumps(old_success))
    newer_failure.update(
        {
            "id": old_success["id"] + 100,
            "run_attempt": 2,
            "created_at": "2026-07-15T01:00:00Z",
            "updated_at": "2026-07-15T01:10:00Z",
            "conclusion": "failure",
        }
    )
    responses[endpoint] = {
        "total_count": 2,
        "workflow_runs": [old_success, newer_failure],
    }

    with pytest.raises(driver.InfrastructureError, match="not completed and successful"):
        driver._verify_ci_attestation(
            engine, fetch_json=_offline_github_fetch(responses)
        )


def test_live_main_verifies_ci_before_provider_key_lookup(
    driver: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key_lookup_called = False

    def refuse_ci(_engine: dict[str, Any]) -> dict[str, Any]:
        raise driver.InfrastructureError("authoritative CI is not green")

    def forbidden_key_lookup(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        nonlocal key_lookup_called
        key_lookup_called = True
        raise AssertionError("provider key lookup happened before CI verification")

    monkeypatch.setattr(driver, "_engine_metadata", _green_engine)
    monkeypatch.setattr(
        driver,
        "_committed_file_bytes",
        lambda path, **kwargs: Path(path).read_bytes(),
    )
    monkeypatch.setattr(driver, "_verify_ci_attestation", refuse_ci)
    monkeypatch.setattr(driver, "_resolve_api_key", forbidden_key_lookup)

    exit_code = driver.main(
        [
            "--live",
            "--models",
            "fable5",
            "--max-cost-per-model",
            "1",
            "--acknowledge-key-exposure",
            "--output-root",
            str(tmp_path / "outside-engine"),
        ]
    )

    assert exit_code == 2
    assert key_lookup_called is False


def test_github_transport_errors_never_echo_authorization_secret(
    driver: ModuleType,
) -> None:
    secret = "github-token-must-never-leak"

    def fail_transport(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(f"arbitrary protocol failure: Bearer {secret}")

    with pytest.raises(driver.InfrastructureError) as captured:
        driver._github_rest_json(
            "/repos/samerzakaria/signalos-app",
            token=secret,
            urlopen=fail_transport,
        )
    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "final_url",
    [
        "https://api.github.com/redirected",
        "https://attacker.invalid/repos/samerzakaria/signalos-app",
    ],
)
def test_github_transport_rejects_redirected_or_noncanonical_responses(
    driver: ModuleType, final_url: str
) -> None:
    class RedirectedResponse:
        def __enter__(self) -> "RedirectedResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def geturl(self) -> str:
            return final_url

        def read(self, _size: int) -> bytes:
            raise AssertionError("a redirected response body must not be read")

    def redirected(*_args: Any, **_kwargs: Any) -> RedirectedResponse:
        return RedirectedResponse()

    with pytest.raises(driver.InfrastructureError, match="redirect.*origin") as captured:
        driver._github_rest_json(
            "/repos/samerzakaria/signalos-app",
            token="github-token-must-never-leak",
            urlopen=redirected,
        )
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("token", ["token\nsmuggled", "token\x7fsmuggled"])
def test_github_transport_rejects_control_characters_before_open(
    driver: ModuleType, token: str
) -> None:
    opened = False

    def forbidden_open(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal opened
        opened = True
        raise AssertionError("invalid token reached the transport")

    with pytest.raises(driver.InfrastructureError, match="invalid bearer token"):
        driver._github_rest_json(
            "/repos/samerzakaria/signalos-app",
            token=token,
            urlopen=forbidden_open,
        )
    assert opened is False


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


def test_provider_failures_are_not_graded_as_product_failures(driver: ModuleType) -> None:
    with pytest.raises(driver.InfrastructureError, match="provider init"):
        driver._require_ok(
            "agent:deliver",
            {
                "ok": False,
                "error": "agent:deliver provider init failed",
                "error_code": "provider-init",
            },
        )

    state = {
        "run_id": "matrix-run",
        "current_gate": "G0",
        "status": "blocked",
        "signed": [],
        "last_outcome": {
            "gate": "G0",
            "ok": False,
            "reason": "Provider call failed: timeout",
            "failure_type": "provider-transport",
        },
    }
    with pytest.raises(driver.InfrastructureError, match="provider-transport"):
        driver._validate_review_checkpoint(
            state,
            run_id="matrix-run",
            gate="G0",
            signed_before=[],
        )

    with pytest.raises(driver.InfrastructureError, match="sandbox-unavailable"):
        driver._validate_review_checkpoint(
            {
                **state,
                "last_outcome": {
                    "gate": "G0",
                    "ok": False,
                    "reason": "container daemon unavailable",
                    "failure_type": "sandbox-unavailable",
                },
            },
            run_id="matrix-run",
            gate="G0",
            signed_before=[],
        )

    with pytest.raises(driver.InfrastructureError, match="dependency broker"):
        driver._require_ok(
            "agent:verdict",
            {
                "ok": False,
                "error": "dependency broker unavailable",
                "error_code": "dependency-broker-unavailable",
            },
        )

    with pytest.raises(driver.ProductFailure, match="ordinary product failure"):
        driver._require_ok(
            "agent:deliver",
            {"ok": False, "error": "ordinary product failure"},
        )


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
        provider_context_length=200_000,
    )
    recorded_row = {"orchestrator_profile": args.orchestrator_profile}
    recorded_manifest = {"orchestrator_profile": args.orchestrator_profile}

    assert request["profile"] == recorded_row["orchestrator_profile"]
    assert request["profile"] == recorded_manifest["orchestrator_profile"]
    assert request["provider_context_length"] == 200_000
    with pytest.raises(ValueError, match="unknown orchestrator profile"):
        driver._delivery_request(
            prompt="Build the fixture",
            spec=spec,
            run_id="matrix-profile-contract",
            orchestrator_profile="prodution",
            provider_context_length=200_000,
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

    class FakeFundedContext:
        materialized = False

        def sidecar_environment(self, runtime_home: Path) -> dict[str, str]:
            return driver._isolated_subprocess_env(runtime_home)

        def redaction_secrets(self) -> tuple[str, ...]:
            return ()

        def attestation_scan_needles(self) -> tuple[tuple[str, bytes], ...]:
            return ()

        def materialize_after_init(self, workspace: Path) -> dict[str, Any]:
            assert (workspace / ".signalos" / "INIT_COMPLETE.json").is_file()
            assert (workspace / "package.json").is_file()
            self.materialized = True
            return {"status": "verified", "receipt_sha256": "materialized"}

        def verify_materialized_after_init(self, workspace: Path) -> dict[str, Any]:
            assert self.materialized
            assert (workspace / "package.json").is_file()
            return {"status": "verified", "receipt_sha256": "verified"}

        def offline_probe(self, workspace: Path, *, timeout: float) -> dict[str, Any]:
            assert self.materialized
            assert timeout == 120
            assert (workspace / "package.json").is_file()
            return {"ok": True, "network": "none", "pull": "never"}

    scenario = driver._load_scenario(
        ROOT / "scripts" / "backend_matrix" / "scenarios" / "expense_tracker.json"
    )
    funded_context = FakeFundedContext()
    result = driver._backend_preflight(
        scenario,
        init_timeout=120,
        dependency_timeout=120,
        funded_context=funded_context,
    )

    assert result["ready"] is True
    assert result["protocol"] == 1
    assert result["init_complete"] is True
    assert result["scaffold"]["can_deliver_runnable"] is True
    assert result["funded_dependencies"]["offline_probe"]["network"] == "none"
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
                    "context_length": 200_000,
                }
            }

    with pytest.raises(driver.InfrastructureError, match="provider-side spending limit"):
        driver._provider_preflight(
            FakeRouter(), selected, required_remaining=1.0, require_provider_limit=True
        )


def test_provider_stack_preflight_uses_catalog_context_and_exact_route(
    driver: ModuleType,
) -> None:
    selected = driver.load_model_catalog(MODEL_CONFIG)[:1]

    class FakeLiteLLM:
        __version__ = "test-1.0"
        model_list: list[str] = []

        @staticmethod
        def supports_function_calling(*, model: str) -> bool:
            return True

        @staticmethod
        def get_model_info(*, model: str) -> dict[str, object]:
            return {}

    rows = [
        {
            "id": selected[0].model,
            "tool_calling": True,
            "context_length": 262_144,
        }
    ]
    result = driver._provider_stack_preflight(
        selected, rows, litellm_module=FakeLiteLLM
    )

    assert result["ready"] is True
    assert result["litellm_version"] == "test-1.0"
    assert result["routes"] == [
        {
            "alias": selected[0].alias,
            "model": selected[0].model,
            "routed_model": f"openrouter/{selected[0].model}",
            "context_length": 262_144,
            "tool_calling": True,
        }
    ]


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


def _fake_dependency_receipt() -> dict[str, Any]:
    return {
        "schema": "signalos.dependency-receipt.v3",
        "status": "ready",
        "profile": "react-vite",
        "platform": "linux/amd64",
        "image": "docker.io/library/node:20-bookworm@sha256:" + "a" * 64,
        "policy_sha256": "1" * 64,
        "broker_sha256": "2" * 64,
        "attestation_key_id": "3" * 64,
        "receipt_sha256": "4" * 64,
        "provenance_hmac_sha256": "must-never-be-persisted",
        "inputs": {
            "package_json_sha256": "5" * 64,
            "package_lock_sha256": "6" * 64,
        },
        "provisioner": {
            "cleanup_verified": True,
            "proxy_script_sha256": "7" * 64,
            "host_trust_profile": "trusted-local-docker-desktop-v1",
            "docker_endpoint": "npipe:////./pipe/dockerDesktopLinuxEngine",
            "daemon_os_type": "linux",
        },
        "bundle": {
            "archive_sha256": "8" * 64,
            "tree_sha256": "9" * 64,
            "file_count": 123,
            "total_bytes": 456,
        },
    }


def _direct_funded_context(
    driver: ModuleType,
    tmp_path: Path,
    *,
    key: bytes = b"K" * 32,
) -> Any:
    scratch = tmp_path / "funded-scratch"
    bundle = scratch / "bundle"
    bundle.mkdir(parents=True)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{}\n", encoding="utf-8")
    policy = SimpleNamespace(
        profile="react-vite",
        image="docker.io/library/node:20-bookworm@sha256:" + "a" * 64,
        platform="linux/amd64",
    )
    return driver.FundedRunContext(
        policy_path=policy_path.resolve(),
        policy=policy,
        scratch_root=scratch,
        bundle_dir=bundle,
        _receipt=_fake_dependency_receipt(),
        _attestation_key=bytearray(key),
    )


def test_funded_context_uses_public_broker_api_and_zeroes_its_key(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, Any]] = []
    receipt = _fake_dependency_receipt()
    policy = SimpleNamespace(
        profile="react-vite",
        image=receipt["image"],
        platform=receipt["platform"],
    )

    class FakeBroker:
        def load_dependency_policy(self, path: Path) -> Any:
            calls.append(("load", Path(path)))
            return policy

        def prepare_dependency_bundle(
            self,
            policy_path: Path,
            bundle_dir: Path,
            *,
            engine: str,
            timeout: float,
            attestation_key: bytes,
        ) -> dict[str, Any]:
            calls.append(
                (
                    "prepare",
                    {
                        "policy": Path(policy_path),
                        "engine": engine,
                        "timeout": timeout,
                        "key": attestation_key,
                    },
                )
            )
            Path(bundle_dir).mkdir(parents=True, exist_ok=True)
            (Path(bundle_dir) / "safe.txt").write_text("bundle\n", encoding="utf-8")
            return dict(receipt)

        def verify_dependency_bundle(
            self,
            policy_path: Path,
            bundle_dir: Path,
            *,
            attestation_key: bytes,
        ) -> dict[str, Any]:
            assert Path(bundle_dir).is_dir()
            calls.append(
                (
                    "verify",
                    {"policy": Path(policy_path), "key": attestation_key},
                )
            )
            return dict(receipt)

    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{}\n", encoding="utf-8")
    fixed_key = bytes(range(32))
    monkeypatch.setattr(driver, "_dependency_broker_module", lambda: FakeBroker())
    monkeypatch.setattr(driver.secrets, "token_bytes", lambda length: fixed_key)

    context = driver.FundedRunContext.prepare(policy_path, timeout=77)
    owned_key = context._attestation_key
    public = context.public_evidence()
    serialized = json.dumps(public, sort_keys=True)

    assert calls[0] == ("load", policy_path.resolve())
    assert calls[1][0] == "prepare"
    assert calls[1][1]["engine"] == "docker"
    assert calls[1][1]["timeout"] == 77
    assert calls[1][1]["key"] == fixed_key
    assert calls[2][1]["key"] == fixed_key
    assert len(calls[2][1]["key"]) == 32
    assert "provenance_hmac_sha256" not in serialized
    assert fixed_key.hex() not in serialized
    assert context.evidence_hashes()["dependency_package_lock_sha256"] == "6" * 64

    first_close = context.close()
    second_close = context.close()
    assert first_close == second_close
    assert first_close["scratch_removed"] is True
    assert first_close["key_zeroed"] is True
    assert all(value == 0 for value in owned_key)


def test_funded_and_tool_environments_are_separate_and_exact(
    driver: ModuleType,
    tmp_path: Path,
) -> None:
    context = _direct_funded_context(driver, tmp_path)
    spec = driver.load_model_catalog(MODEL_CONFIG)[0]
    provider_key = "provider-key-environment-sentinel"
    sidecar_env = context.sidecar_environment(
        tmp_path / "sidecar-home",
        spec=spec,
        provider_key=provider_key,
        expected_git_remote=tmp_path / "release-origin.git",
    )
    tool_env = driver._tool_subprocess_env(tmp_path / "tool-home")

    assert sidecar_env["SIGNALOS_SANDBOX"] == "docker"
    assert sidecar_env["SIGNALOS_SANDBOX_PROFILE"] == "funded"
    assert sidecar_env["SIGNALOS_SANDBOX_IMAGE"] == context.policy.image
    assert sidecar_env["SIGNALOS_SANDBOX_NETWORK"] == "none"
    assert sidecar_env["SIGNALOS_SANDBOX_PULL"] == "never"
    assert sidecar_env["SIGNALOS_SANDBOX_READONLY"] == "1"
    assert sidecar_env["SIGNALOS_SANDBOX_STRICT"] == "1"
    assert sidecar_env["SIGNALOS_DEPENDENCY_POLICY"] == str(context.policy_path)
    assert sidecar_env["SIGNALOS_DEPENDENCY_BUNDLE"] == str(context.bundle_dir)
    assert sidecar_env["SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY"] == (b"K" * 32).hex()
    assert sidecar_env["SIGNALOS_FUNDED_GIT_HOOKS_DIR"] == str(
        (tmp_path / "sidecar-home" / "git-hooks-disabled").resolve()
    )
    assert sidecar_env["SIGNALOS_FUNDED_EXPECTED_GIT_REMOTE"] == str(
        (tmp_path / "release-origin.git").resolve()
    )
    assert sidecar_env["DOCKER_HOST"] == "npipe:////./pipe/dockerDesktopLinuxEngine"
    assert sidecar_env[spec.key_env] == provider_key
    assert sidecar_env["SIGNALOS_LLM_PROVIDER"] == spec.provider
    assert sidecar_env["SIGNALOS_LLM_MODEL"] == spec.model

    assert provider_key not in tool_env.values()
    assert not any(name in tool_env for name in driver.PROVIDER_KEY_ENVS)
    assert not any(name.startswith("SIGNALOS_DEPENDENCY_") for name in tool_env)
    assert tool_env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert tool_env["GIT_TERMINAL_PROMPT"] == "0"
    assert tool_env["GCM_INTERACTIVE"] == "Never"
    driver._clear_parent_environment(sidecar_env)
    assert sidecar_env == {}
    context.close()


def test_funded_model_environment_requires_attested_local_git_remote(
    driver: ModuleType,
    tmp_path: Path,
) -> None:
    context = _direct_funded_context(driver, tmp_path)
    spec = driver.load_model_catalog(MODEL_CONFIG)[0]

    with pytest.raises(
        driver.InfrastructureError,
        match="expected local Git remote",
    ):
        context.sidecar_environment(
            tmp_path / "sidecar-home",
            spec=spec,
            provider_key="provider-key-sentinel",
        )

    context.close()


def test_sidecar_destroys_parent_environment_and_immutable_secrets(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(driver, "_windows_job_enabled", lambda: False)
    context = _direct_funded_context(driver, tmp_path)
    child_env = context.sidecar_environment(tmp_path / "runtime-home")

    class FakeProcess:
        pid = 123
        returncode = 0
        stdin = SimpleNamespace(close=lambda: None)
        stdout: tuple[str, ...] = ()
        stderr: tuple[str, ...] = ()

        def poll(self) -> int:
            return 0

    class FakeThread:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(driver.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(driver.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        driver.SidecarClient,
        "_wait_for",
        lambda self, req_id, timeout, guard: (
            [],
            {"ok": True, "data": {"ready": True}},
        ),
    )

    client = driver.SidecarClient(
        tmp_path,
        child_env,
        context.redaction_secrets(),
    )
    assert child_env == {}
    assert isinstance(client.secrets, tuple)
    assert client.secrets
    client._stderr.append("raw-provider-secret")
    client._stdout.put("raw-provider-secret")
    client.close()
    assert client.secrets == ()
    assert list(client._stderr) == []
    assert client._stdout.empty()
    context.close()


def test_sidecar_constructor_failure_still_destroys_environment_and_secrets(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(driver, "_windows_job_enabled", lambda: False)
    context = _direct_funded_context(driver, tmp_path)
    child_env = context.sidecar_environment(tmp_path / "runtime-home")
    client = object.__new__(driver.SidecarClient)

    def fail_popen(*args: Any, **kwargs: Any) -> Any:
        raise OSError("deterministic Popen failure")

    monkeypatch.setattr(driver.subprocess, "Popen", fail_popen)
    with pytest.raises(OSError, match="deterministic Popen failure"):
        client.__init__(tmp_path, child_env, context.redaction_secrets())

    assert child_env == {}
    assert client.secrets == ()
    context.close()


def test_sidecar_constructor_reports_unverified_process_tree_cleanup(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(driver, "_windows_job_enabled", lambda: False)
    context = _direct_funded_context(driver, tmp_path)
    child_env = context.sidecar_environment(tmp_path / "runtime-home")

    class FakeStream:
        def close(self) -> None:
            return None

    class RefusesToExit:
        pid = 12345
        returncode = None
        stdin = FakeStream()
        stdout = FakeStream()
        stderr = FakeStream()

        def poll(self) -> None:
            return None

        def wait(self, timeout: float) -> None:
            raise TimeoutError("still running")

        def kill(self) -> None:
            return None

    class FakeThread:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def start(self) -> None:
            return None

    monkeypatch.setattr(
        driver.subprocess,
        "Popen",
        lambda *args, **kwargs: RefusesToExit(),
    )
    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(driver.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        driver.SidecarClient,
        "_wait_for",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            driver.InfrastructureError("deterministic handshake failure")
        ),
    )

    with pytest.raises(
        driver.InfrastructureError,
        match="process-tree cleanup could not be verified",
    ) as captured:
        driver.SidecarClient(
            tmp_path,
            child_env,
            context.redaction_secrets(),
        )

    assert isinstance(captured.value.__cause__, driver.InfrastructureError)
    assert child_env == {}
    context.close()


def test_windows_sidecar_is_job_owned_before_bootstrap_release(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    gate = tmp_path / "job-gate" / "release"

    class FakeJob:
        def __init__(self) -> None:
            events.append("job-created")

        def assign(self, process: Any) -> None:
            assert process is fake_process
            events.append("assigned")

        def terminate(self) -> None:
            events.append("terminated")

        def close(self) -> None:
            events.append("job-closed")

    class FakeProcess:
        pid = 123
        returncode = 0
        stdin = SimpleNamespace(close=lambda: None)
        stdout: tuple[str, ...] = ()
        stderr: tuple[str, ...] = ()

        def poll(self) -> int:
            return 0

    class FakeThread:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            pass

    fake_process = FakeProcess()

    def fake_popen(argv: list[str], **kwargs: Any) -> FakeProcess:
        assert str(driver.WINDOWS_JOB_BOOTSTRAP) in argv
        assert str(driver.SIDECAR) in argv
        events.append("popen")
        return fake_process

    def fake_release(actual_gate: Path, token: str, process_id: int) -> None:
        assert actual_gate == gate
        assert len(token) == 64
        assert process_id == fake_process.pid
        assert events[-1] == "assigned"
        events.append("released")

    monkeypatch.setattr(driver, "_windows_job_enabled", lambda: True)
    monkeypatch.setattr(driver, "_new_windows_kill_on_close_job", FakeJob)
    monkeypatch.setattr(
        driver, "_new_windows_sidecar_gate", lambda workspace: (gate, "a" * 64)
    )
    monkeypatch.setattr(driver, "_release_windows_sidecar_gate", fake_release)
    monkeypatch.setattr(
        driver,
        "_remove_windows_sidecar_gate",
        lambda actual_gate: events.append("gate-removed"),
    )
    monkeypatch.setattr(driver.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(driver.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        driver.SidecarClient,
        "_wait_for",
        lambda self, req_id, timeout, guard: (
            [],
            {"ok": True, "data": {"ready": True}},
        ),
    )

    child_env = {"PATH": "trusted-test-path"}
    client = driver.SidecarClient(tmp_path, child_env, ())
    assert child_env == {}
    assert events == ["job-created", "popen", "assigned", "released"]

    # Job cleanup must still occur when the bootstrap root has already exited;
    # surviving descendants remain members until the Job handle is closed.
    client.close()
    assert events[-3:] == ["terminated", "job-closed", "gate-removed"]


def test_windows_sidecar_popen_failure_closes_empty_job_and_gate(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    gate = tmp_path / "job-gate" / "release"

    class FakeJob:
        def __init__(self) -> None:
            events.append("job-created")

        def close(self) -> None:
            events.append("job-closed")

    monkeypatch.setattr(driver, "_windows_job_enabled", lambda: True)
    monkeypatch.setattr(driver, "_new_windows_kill_on_close_job", FakeJob)
    monkeypatch.setattr(
        driver, "_new_windows_sidecar_gate", lambda workspace: (gate, "b" * 64)
    )
    monkeypatch.setattr(
        driver,
        "_remove_windows_sidecar_gate",
        lambda actual_gate: events.append("gate-removed"),
    )
    monkeypatch.setattr(
        driver.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("popen failed")),
    )

    child_env = {"PATH": "trusted-test-path"}
    with pytest.raises(OSError, match="popen failed"):
        driver.SidecarClient(tmp_path, child_env, ())

    assert child_env == {}
    assert events == ["job-created", "job-closed", "gate-removed"]


def test_windows_sidecar_assignment_failure_never_releases_gate(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    gate = tmp_path / "job-gate" / "release"

    class FakeJob:
        def assign(self, process: Any) -> None:
            events.append("assign-refused")
            raise driver.InfrastructureError("assignment refused")

        def terminate(self) -> None:
            events.append("job-terminated")

        def close(self) -> None:
            events.append("job-closed")

    class FakeProcess:
        pid = 123
        returncode: int | None = None
        stdin = SimpleNamespace(close=lambda: None)
        stdout: tuple[str, ...] = ()
        stderr: tuple[str, ...] = ()

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.returncode = 1

        def wait(self, timeout: float) -> int:
            if self.returncode is None:
                raise TimeoutError("still running")
            return self.returncode

    monkeypatch.setattr(driver, "_windows_job_enabled", lambda: True)
    monkeypatch.setattr(driver, "_new_windows_kill_on_close_job", FakeJob)
    monkeypatch.setattr(
        driver, "_new_windows_sidecar_gate", lambda workspace: (gate, "c" * 64)
    )
    monkeypatch.setattr(
        driver,
        "_release_windows_sidecar_gate",
        lambda *args: events.append("released"),
    )
    monkeypatch.setattr(
        driver,
        "_remove_windows_sidecar_gate",
        lambda actual_gate: events.append("gate-removed"),
    )
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )

    child_env = {"PATH": "trusted-test-path"}
    with pytest.raises(driver.InfrastructureError, match="assignment refused"):
        driver.SidecarClient(tmp_path, child_env, ())

    assert child_env == {}
    assert "released" not in events
    assert events == [
        "assign-refused",
        "job-terminated",
        "job-closed",
        "gate-removed",
    ]


def test_windows_job_bootstrap_is_hash_bound_and_release_order_is_locked() -> None:
    source = DRIVER_PATH.read_text(encoding="utf-8")
    start = source.index("class SidecarClient:")
    end = source.index("def _event_evidence", start)
    constructor = source[start:end]

    assert constructor.index("self._windows_job.assign(self.proc)") < constructor.index(
        "_release_windows_sidecar_gate("
    )
    assert '"-S",' in constructor and '"-B",' in constructor
    assert '"windows_job_bootstrap_sha256"' in source


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_real_windows_job_terminates_bootstrap_and_descendant(
    driver: ModuleType,
    tmp_path: Path,
) -> None:
    import ctypes
    from ctypes import wintypes

    pid_file = tmp_path / "descendant.pid"
    fixture = tmp_path / "job_sidecar_fixture.py"
    fixture.write_text(
        "\n".join(
            (
                "import os",
                "import subprocess",
                "import sys",
                "import time",
                "from pathlib import Path",
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])",
                "Path(os.environ['SIGNALOS_JOB_TEST_PID_FILE']).write_text(str(child.pid), encoding='utf-8')",
                "while True:",
                "    time.sleep(1)",
            )
        ),
        encoding="utf-8",
    )
    gate, token = driver._new_windows_sidecar_gate(tmp_path / "workspace")
    job = driver._new_windows_kill_on_close_job()
    env = dict(os.environ)
    env["SIGNALOS_JOB_TEST_PID_FILE"] = str(pid_file)
    process = subprocess.Popen(
        [
            sys.executable,
            "-S",
            "-B",
            "-u",
            str(driver.WINDOWS_JOB_BOOTSTRAP),
            "--gate",
            str(gate),
            "--token",
            token,
            "--sidecar",
            str(fixture),
        ],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    descendant_handle = None
    try:
        job.assign(process)
        driver._release_windows_sidecar_gate(gate, token, process.pid)
        deadline = driver.time.monotonic() + 15
        while not pid_file.is_file() and driver.time.monotonic() < deadline:
            driver.time.sleep(0.02)
        assert pid_file.is_file(), "bootstrap fixture did not create its descendant"
        descendant_pid = int(pid_file.read_text(encoding="utf-8"))
        descendant_handle = kernel32.OpenProcess(0x00100001, False, descendant_pid)
        assert descendant_handle, "could not open descendant process for synchronization"

        job.terminate()
        job.close()
        process.wait(timeout=10)
        assert kernel32.WaitForSingleObject(descendant_handle, 10_000) == 0
    finally:
        try:
            job.terminate()
        except Exception:
            pass
        try:
            job.close()
        except Exception:
            pass
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if descendant_handle:
            if kernel32.WaitForSingleObject(descendant_handle, 0) != 0:
                kernel32.TerminateProcess(descendant_handle, 1)
                kernel32.WaitForSingleObject(descendant_handle, 5_000)
            kernel32.CloseHandle(descendant_handle)
        try:
            driver._remove_windows_sidecar_gate(gate)
        except Exception:
            pass


def test_dependency_parser_is_fixed_and_timeout_is_bounded(driver: ModuleType) -> None:
    parser = driver._build_parser()
    args = parser.parse_args([])

    assert args.dependency_policy == driver.DEFAULT_DEPENDENCY_POLICY
    assert args.dependency_timeout == 900.0
    assert parser.parse_args(["--dependency-timeout", "1"]).dependency_timeout == 1.0
    assert parser.parse_args(["--dependency-timeout", "3600"]).dependency_timeout == 3600.0
    for invalid in ("0", "3600.01", "nan", "inf", "not-a-number"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--dependency-timeout", invalid])


def test_live_rejects_an_arbitrary_dependency_policy_before_work(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        driver,
        "_engine_metadata",
        lambda: (_ for _ in ()).throw(
            AssertionError("engine/subprocess work must not start")
        ),
    )
    exit_code = driver.main(
        [
            "--live",
            "--models",
            "fable5",
            "--max-cost-per-model",
            "1",
            "--acknowledge-key-exposure",
            "--dependency-policy",
            str(tmp_path / "unreviewed-policy.json"),
        ]
    )

    assert exit_code == 2


def test_live_activation_orders_keyless_gates_before_key_lookup(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class FakeFundedContext:
        def __enter__(self) -> "FakeFundedContext":
            events.append("enter")
            return self

        def __exit__(self, *args: Any) -> None:
            events.append("close")

        def redaction_secrets(self, provider_key: str = "") -> tuple[str, ...]:
            return (provider_key,) if provider_key else ()

        def public_evidence(self) -> dict[str, Any]:
            return {
                "policy_sha256": driver._sha256_file(
                    driver.DEFAULT_DEPENDENCY_POLICY
                )
            }

    def fake_prepare(
        cls: Any,
        policy_path: Path,
        *,
        timeout: float,
        expected_profile: str | None = None,
    ) -> Any:
        assert Path(policy_path) == driver.DEFAULT_DEPENDENCY_POLICY.resolve()
        assert timeout == 900.0
        assert expected_profile == "react-vite"
        events.append("bundle")
        return FakeFundedContext()

    def fake_backend(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert isinstance(kwargs["funded_context"], FakeFundedContext)
        events.append("backend")
        return {"ready": True}

    def stop_at_key(*args: Any, **kwargs: Any) -> tuple[str, str]:
        events.append("key")
        raise driver.InfrastructureError("intentional stop after ordering proof")

    monkeypatch.setattr(driver, "_engine_metadata", _green_engine)
    monkeypatch.setattr(
        driver,
        "_committed_file_bytes",
        lambda path, **kwargs: Path(path).read_bytes(),
    )
    monkeypatch.setattr(
        driver,
        "_verify_ci_attestation",
        lambda engine: events.append("ci") or {"ok": True},
    )
    monkeypatch.setattr(
        driver,
        "_require_external_output_root",
        lambda path: events.append("output") or Path(path).resolve(),
    )
    monkeypatch.setattr(
        driver,
        "_local_preflight",
        lambda: events.append("local") or {"ready": True},
    )
    monkeypatch.setattr(
        driver.FundedRunContext,
        "prepare",
        classmethod(fake_prepare),
    )
    monkeypatch.setattr(driver, "_backend_preflight", fake_backend)
    monkeypatch.setattr(driver, "_resolve_api_key", stop_at_key)

    exit_code = driver.main(
        [
            "--live",
            "--models",
            "fable5",
            "--max-cost-per-model",
            "1",
            "--acknowledge-key-exposure",
            "--output-root",
            str(tmp_path / "outside-engine"),
        ]
    )

    assert exit_code == 2
    assert events == [
        "ci",
        "output",
        "local",
        "bundle",
        "enter",
        "backend",
        "key",
        "close",
    ]


def test_exact_secret_scans_include_git_and_node_modules(
    driver: ModuleType,
    tmp_path: Path,
) -> None:
    provider_key = "provider-key-retention-sentinel"
    attestation_key = bytearray(b"A" * 32)
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".git" / "config").write_text(provider_key, encoding="utf-8")
    (tmp_path / "node_modules" / "binary.dat").write_bytes(bytes(attestation_key))
    (tmp_path / "attestation.txt").write_text(
        bytes(attestation_key).hex(),
        encoding="ascii",
    )

    result = driver._secret_scan(
        tmp_path,
        provider_key,
        exact_values=driver._attestation_needles(attestation_key),
    )

    assert result["ok"] is False
    assert {hit["kind"] for hit in result["hits"]} == {
        "exact-selected-key",
        "exact-dependency-attestation-key",
        "exact-dependency-attestation-key-hex",
    }
    assert {hit["path"] for hit in result["hits"]} == {
        ".git/config",
        "node_modules/binary.dat",
        "attestation.txt",
    }


def test_real_rows_verify_funded_receipt_at_first_g4_checkpoint() -> None:
    source = DRIVER_PATH.read_text(encoding="utf-8")
    start = source.index("def _run_row(")
    end = source.index("def _bounded_dependency_timeout", start)
    run_row = source[start:end]
    g4 = run_row.index('if gate == "G4":')
    verify = run_row.index("funded_context.verify_materialized_after_init", g4)
    checkpoint = run_row.index("_validate_review_checkpoint", verify)

    assert g4 < verify < checkpoint
    assert "funded_context.materialize_after_init" not in run_row


def test_local_and_engine_subprocesses_never_inherit_ambient_credentials(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "ambient-provider-secret")
    monkeypatch.setenv("SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY", "ab" * 32)
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy-user:proxy-password@example.invalid")
    popen_envs: list[dict[str, str]] = []

    class FakePopen:
        returncode = 0

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            popen_envs.append(dict(kwargs["env"]))

        def communicate(self, timeout: float) -> tuple[str, str]:
            return "ok", ""

    monkeypatch.setattr(driver.subprocess, "Popen", FakePopen)
    result = driver._run_command(["fake-tool"], cwd=tmp_path)
    assert result["ok"] is True
    assert len(popen_envs) == 1
    assert "OPENROUTER_API_KEY" not in popen_envs[0]
    assert "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY" not in popen_envs[0]
    assert "HTTPS_PROXY" not in popen_envs[0]

    git_envs: list[dict[str, str]] = []

    def fake_git(argv: list[str], **kwargs: Any) -> Any:
        git_envs.append(dict(kwargs["env"]))
        args = tuple(argv[1:])
        outputs = {
            ("status", "--porcelain"): "",
            ("rev-parse", "HEAD"): "a" * 40,
            (
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ): "origin/main",
            ("rev-parse", "@{upstream}"): "a" * 40,
            ("rev-parse", "HEAD^{tree}"): "b" * 40,
            ("branch", "--show-current"): "main",
        }
        return SimpleNamespace(returncode=0, stdout=outputs[args] + "\n")

    monkeypatch.setattr(driver.subprocess, "run", fake_git)
    metadata = driver._engine_metadata()
    assert metadata["commit"] == "a" * 40
    assert git_envs
    for env in git_envs:
        assert "OPENROUTER_API_KEY" not in env
        assert "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY" not in env
        assert "HTTPS_PROXY" not in env
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_failed_prepare_removes_scratch_and_rejects_profile_before_runner(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "prepare-scratch"
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{}\n", encoding="utf-8")

    class FakeBroker:
        def load_dependency_policy(self, path: Path) -> Any:
            return SimpleNamespace(profile="wrong-profile")

        def prepare_dependency_bundle(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("runner must not start for a profile mismatch")

    monkeypatch.setattr(driver, "_dependency_broker_module", lambda: FakeBroker())
    monkeypatch.setattr(
        driver.tempfile,
        "mkdtemp",
        lambda **kwargs: scratch.mkdir() or str(scratch),
    )
    with pytest.raises(driver.InfrastructureError, match="scenario profile"):
        driver.FundedRunContext.prepare(
            policy_path,
            timeout=10,
            expected_profile="react-vite",
        )
    assert not scratch.exists()


def test_failed_broker_prepare_removes_secret_bearing_scratch(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "failed-broker-scratch"
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{}\n", encoding="utf-8")

    class FakeBroker:
        def load_dependency_policy(self, path: Path) -> Any:
            return SimpleNamespace(profile="react-vite")

        def prepare_dependency_bundle(
            self,
            policy_path: Path,
            bundle_dir: Path,
            **kwargs: Any,
        ) -> dict[str, Any]:
            Path(bundle_dir).mkdir(parents=True)
            (Path(bundle_dir) / "leak.txt").write_text(
                bytes(kwargs["attestation_key"]).hex(),
                encoding="ascii",
            )
            raise RuntimeError("deterministic broker failure")

    monkeypatch.setattr(driver, "_dependency_broker_module", lambda: FakeBroker())
    monkeypatch.setattr(
        driver.tempfile,
        "mkdtemp",
        lambda **kwargs: scratch.mkdir() or str(scratch),
    )
    with pytest.raises(driver.InfrastructureError, match="unverifiable secret evidence"):
        driver.FundedRunContext.prepare(
            policy_path,
            timeout=10,
            expected_profile="react-vite",
        )
    assert not scratch.exists()


def test_context_purges_retained_root_and_zeroes_every_owned_secret_on_leak(
    driver: ModuleType,
    tmp_path: Path,
) -> None:
    context = _direct_funded_context(driver, tmp_path)
    retained = tmp_path / "retained-run"
    retained.mkdir()
    provider = "provider-secret-that-must-be-purged"
    (retained / "leak.txt").write_text(
        provider + "\n" + context._key_hex(),
        encoding="utf-8",
    )
    context.register_scan_root(retained)
    context.register_exact_secret("exact-selected-key", provider)
    owned_key = context._attestation_key
    registered = context._registered_secrets[0][1]

    with pytest.raises(driver.InfrastructureError, match="retained output was removed"):
        context.close()

    assert not retained.exists()
    assert not context.scratch_root.exists()
    assert all(value == 0 for value in owned_key)
    assert all(value == 0 for value in registered)


def test_exact_scanner_fails_closed_on_unreadable_file(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "owned.txt").write_text("ordinary", encoding="utf-8")
    monkeypatch.setattr(
        driver,
        "_file_contains_bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("locked")),
    )
    result = driver._scan_exact_secret_values(
        (tmp_path,),
        (("exact-test-secret", b"secret"),),
    )
    assert result["ok"] is False
    assert result["hits"] == []
    assert result["errors"]


def test_directory_cleanup_retries_and_reads_back_absence(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "transient-lock"
    target.mkdir()
    (target / "file.txt").write_text("safe", encoding="utf-8")
    original = driver.shutil.rmtree
    calls = 0

    def flaky(path: Path, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("transient Windows lock")
        original(path, **kwargs)

    monkeypatch.setattr(driver.shutil, "rmtree", flaky)
    removed, error = driver._remove_directory_with_readback(target)
    assert removed is True
    assert error == ""
    assert calls == 2
    assert not target.exists()


def test_sidecar_termination_failure_is_not_suppressed(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RefusesToExit:
        pid = 12345

        def poll(self) -> None:
            return None

        def wait(self, timeout: float) -> None:
            raise TimeoutError("still running")

        def kill(self) -> None:
            return None

    client = object.__new__(driver.SidecarClient)
    client.proc = RefusesToExit()
    monkeypatch.setattr(driver.os, "name", "nt")
    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    with pytest.raises(driver.InfrastructureError, match="did not terminate"):
        client.terminate_tree()


def test_offline_probe_binds_every_docker_call_to_attested_endpoint(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from signalos_lib.product import sandbox

    context = _direct_funded_context(driver, tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / ".signalos" / "dependencies").mkdir(parents=True)
    (workspace / ".signalos" / "INIT_COMPLETE.json").write_text(
        "{}\n", encoding="utf-8"
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "ambient-provider-secret")
    monkeypatch.setenv("DOCKER_HOST", "tcp://attacker.invalid:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "attacker")
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "attacker-config"))
    docker_calls: list[tuple[list[str], dict[str, str]]] = []

    class FakeBroker:
        def verify_materialized_dependencies(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return _fake_dependency_receipt()

    def fake_runtime(argv: list[str], **kwargs: Any) -> Any:
        docker_calls.append((list(argv), dict(kwargs["env"])))
        stdout = "linux\n" if "info" in argv else ""
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    class FakeContainerRunner:
        def __init__(self, workspace: Path, **kwargs: Any) -> None:
            assert kwargs["runner"] is not None
            self.runtime = kwargs["runner"]

        def run(self, *args: Any, **kwargs: Any) -> tuple[int, Any]:
            self.runtime(
                ["docker", "version"],
                capture_output=True,
                text=True,
                check=False,
            )
            return 0, sandbox.CommandOutput(
                stdout="SIGNALOS_DEPENDENCIES_OK",
                stderr="",
            )

    monkeypatch.setattr(driver, "_dependency_broker_module", lambda: FakeBroker())
    monkeypatch.setattr(driver.subprocess, "run", fake_runtime)
    monkeypatch.setattr(sandbox, "ContainerRunner", FakeContainerRunner)

    result = context.offline_probe(workspace, timeout=30)
    expected = "npipe:////./pipe/dockerDesktopLinuxEngine"
    assert result["docker_endpoint"] == expected
    assert len(docker_calls) == 2
    for argv, env in docker_calls:
        assert argv[:3] == ["docker", "--host", expected]
        assert env["DOCKER_HOST"] == expected
        assert "DOCKER_CONTEXT" not in env
        assert "DOCKER_CONFIG" not in env
        assert "OPENROUTER_API_KEY" not in env
    context.close()


def test_teardown_failure_can_never_leave_a_pass_manifest(
    driver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy_sha = driver._sha256_file(driver.DEFAULT_DEPENDENCY_POLICY)

    class FakeFundedContext:
        def __enter__(self) -> "FakeFundedContext":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def redaction_secrets(self, provider_key: str = "") -> tuple[str, ...]:
            return ((provider_key,) if provider_key else ()) + ("ab" * 32,)

        def public_evidence(self) -> dict[str, Any]:
            return {"policy_sha256": policy_sha}

        def register_exact_secret(self, *args: Any) -> None:
            return None

        def register_scan_root(self, root: Path) -> None:
            self.run_root = Path(root)

        def evidence_hashes(self) -> dict[str, str]:
            return {"dependency_policy_sha256": policy_sha}

        def attestation_scan_needles(self) -> tuple[tuple[str, bytes], ...]:
            return (("exact-dependency-attestation-key-hex", ("ab" * 32).encode()),)

        def close(self) -> dict[str, Any]:
            raise driver.InfrastructureError("deterministic teardown failure")

    context = FakeFundedContext()
    monkeypatch.setattr(driver, "_engine_metadata", _green_engine)
    monkeypatch.setattr(driver, "_verify_ci_attestation", lambda engine: {"ok": True})
    monkeypatch.setattr(driver, "_require_engine_unchanged", lambda engine: engine)
    monkeypatch.setattr(
        driver,
        "_trusted_oracle_asset",
        lambda *args, **kwargs: {
            "name": "oracle.mjs",
            "source": b"export {};",
            "sha256": "a" * 64,
            "repository_path": "scripts/backend_matrix/oracles/oracle.mjs",
        },
    )
    monkeypatch.setattr(
        driver,
        "_committed_file_bytes",
        lambda path, **kwargs: Path(path).read_bytes(),
    )
    monkeypatch.setattr(driver, "_local_preflight", lambda: {"ready": True})
    monkeypatch.setattr(
        driver.FundedRunContext,
        "prepare",
        classmethod(lambda cls, *args, **kwargs: context),
    )
    monkeypatch.setattr(driver, "_backend_preflight", lambda *args, **kwargs: {"ready": True})
    monkeypatch.setattr(driver, "_resolve_api_key", lambda *args: ("provider-secret", "test"))
    selected = driver.load_model_catalog(MODEL_CONFIG)[0]
    monkeypatch.setattr(
        driver,
        "_provider_preflight",
        lambda *args, **kwargs: {
            "models": [{"id": selected.model, "context_length": 200_000}]
        },
    )
    monkeypatch.setattr(driver, "_provider_stack_preflight", lambda *args: {"ready": True})
    monkeypatch.setattr(
        driver,
        "_run_row",
        lambda *args, **kwargs: {"status": "pass"},
    )
    output_root = tmp_path / "matrix-output"
    monkeypatch.setattr(
        driver,
        "_require_external_output_root",
        lambda path: output_root,
    )

    exit_code = driver.main(
        [
            "--live",
            "--models",
            selected.alias,
            "--max-cost-per-model",
            "1",
            "--acknowledge-key-exposure",
            "--output-root",
            str(output_root),
        ]
    )

    assert exit_code == 2
    manifests = list(output_root.glob("*/matrix-result.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["status"] == "finalizing"
    assert manifest["status"] != "pass"
    assert manifest["hashes"]["windows_job_bootstrap_sha256"] == driver._sha256_file(
        driver.WINDOWS_JOB_BOOTSTRAP
    )


def test_scenario_and_oracle_must_be_repository_owned(
    driver: ModuleType,
    tmp_path: Path,
) -> None:
    scenario = driver._load_scenario(
        ROOT / "scripts" / "backend_matrix" / "scenarios" / "expense_tracker.json"
    )
    outside = tmp_path / "scenario.json"
    outside.write_text(json.dumps(scenario), encoding="utf-8")
    with pytest.raises(driver.InfrastructureError, match="matrix scenario"):
        driver._trusted_oracle_asset(
            outside,
            scenario,
            engine=_green_engine(),
            live=False,
        )
