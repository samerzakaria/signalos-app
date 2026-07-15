from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from signalos_lib.git_process import (
    DISABLED_HOOKS_ENV,
    EXPECTED_REMOTE_ENV,
    GitProcessPolicyError,
    configured_remote_matches_expected,
    funded_expected_remote,
    run_git,
)


def test_backend_has_no_direct_literal_git_subprocesses() -> None:
    python_root = Path(__file__).resolve().parent
    sources = [python_root / "signalos_ipc_server.py"] + list(
        (python_root / "signalos_lib").rglob("*.py")
    )
    offenders: list[str] = []
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (
                not isinstance(owner, ast.Name)
                or owner.id != "subprocess"
                or node.func.attr not in {"run", "Popen", "check_output", "call"}
            ):
                continue
            if any(
                isinstance(value, ast.Constant) and value.value == "git"
                for argument in node.args
                for value in ast.walk(argument)
            ):
                offenders.append(f"{source.relative_to(python_root)}:{node.lineno}")
    assert offenders == []


def _funded_environment(
    monkeypatch: pytest.MonkeyPatch,
    hooks: Path,
    remote: Path,
) -> None:
    monkeypatch.setenv("SIGNALOS_SANDBOX_PROFILE", "funded")
    monkeypatch.setenv(DISABLED_HOOKS_ENV, str(hooks))
    monkeypatch.setenv(EXPECTED_REMOTE_ENV, str(remote))


def test_funded_git_uses_minimal_environment_and_disables_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    hooks = tmp_path / "disabled-hooks"
    remote = tmp_path / "origin.git"
    workspace.mkdir()
    hooks.mkdir()
    remote.mkdir()
    _funded_environment(monkeypatch, hooks, remote)
    monkeypatch.setenv("OPENROUTER_API_KEY", "provider-secret")
    monkeypatch.setenv(
        "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY", "attestation-secret"
    )
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "desktop-ssh-agent"))
    monkeypatch.setenv("HTTPS_PROXY", "https://user:password@example.invalid")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker-git-dir"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "attacker-hooks"))
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_runner(argv: list[str], **kwargs: Any) -> Any:
        calls.append((list(argv), dict(kwargs["env"])))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    run_git(
        ["status", "--porcelain"],
        cwd=workspace,
        runner=fake_runner,
        extra_env={"GIT_INDEX_FILE": str(tmp_path / "controlled-index")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert len(calls) == 1
    argv, child = calls[0]
    assert argv[0] == "git"
    assert f"core.hooksPath={hooks.resolve()}" in argv
    assert "credential.helper=" in argv
    assert "protocol.allow=never" in argv
    assert "protocol.file.allow=always" in argv
    assert argv[-2:] == ["status", "--porcelain"]
    assert child["GIT_INDEX_FILE"] == str(tmp_path / "controlled-index")
    assert child["GIT_CONFIG_NOSYSTEM"] == "1"
    assert child["GIT_TERMINAL_PROMPT"] == "0"
    assert child["GCM_INTERACTIVE"] == "Never"
    for forbidden in (
        "OPENROUTER_API_KEY",
        "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY",
        "SSH_AUTH_SOCK",
        "HTTPS_PROXY",
        "GIT_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    ):
        assert forbidden not in child


def test_normal_git_still_scrubs_backend_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SIGNALOS_SANDBOX_PROFILE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "provider-secret")
    monkeypatch.setenv(
        "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY", "attestation-secret"
    )
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "desktop-ssh-agent"))
    captured: dict[str, str] = {}

    def fake_runner(argv: list[str], **kwargs: Any) -> Any:
        captured.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    run_git(["status"], cwd=tmp_path, runner=fake_runner, check=False)

    assert "OPENROUTER_API_KEY" not in captured
    assert "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY" not in captured
    assert captured["SSH_AUTH_SOCK"] == str(tmp_path / "desktop-ssh-agent")
    assert captured.get("PATH") == os.environ.get("PATH")


def test_funded_remote_is_absolute_local_and_independently_owned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    hooks = tmp_path / "disabled-hooks"
    remote = tmp_path / "origin.git"
    workspace.mkdir()
    hooks.mkdir()
    remote.mkdir()
    _funded_environment(monkeypatch, hooks, remote)

    expected = funded_expected_remote(workspace)
    assert expected == remote.resolve()
    assert configured_remote_matches_expected(str(remote), expected)
    assert not configured_remote_matches_expected(
        "https://github.com/example/repo.git", expected
    )

    monkeypatch.setenv(EXPECTED_REMOTE_ENV, "https://example.invalid/repo.git")
    with pytest.raises(GitProcessPolicyError, match="filesystem path"):
        funded_expected_remote(workspace)


def test_funded_git_rejects_nonempty_or_workspace_owned_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    hooks = workspace / "hooks"
    remote = tmp_path / "origin.git"
    hooks.mkdir(parents=True)
    remote.mkdir()
    _funded_environment(monkeypatch, hooks, remote)

    with pytest.raises(GitProcessPolicyError, match="independently owned"):
        run_git(["status"], cwd=workspace, runner=lambda *args, **kwargs: None)

    outside_hooks = tmp_path / "outside-hooks"
    outside_hooks.mkdir()
    (outside_hooks / "post-commit").write_text("malicious", encoding="utf-8")
    monkeypatch.setenv(DISABLED_HOOKS_ENV, str(outside_hooks))
    with pytest.raises(GitProcessPolicyError, match="not empty"):
        run_git(["status"], cwd=workspace, runner=lambda *args, **kwargs: None)


def test_funded_execution_never_invokes_workspace_hook_scripts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import signalos_lib.sign as sign_module
    from signalos_lib.commands import init as init_module

    monkeypatch.setenv("SIGNALOS_SANDBOX_PROFILE", "funded")
    artifact = tmp_path / "core" / "governance" / "QUALITY_CHECK.md"
    brain_hook = (
        tmp_path
        / "core"
        / "execution"
        / "hooks"
        / "_lib"
        / "brain-auto-ingest.sh"
    )
    emitter = (
        tmp_path
        / "core"
        / "tool-adapters"
        / "emitters"
        / "claude-code"
        / "register-hooks.sh"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text("approved", encoding="utf-8")
    brain_hook.parent.mkdir(parents=True)
    brain_hook.write_text("malicious", encoding="utf-8")
    emitter.parent.mkdir(parents=True)
    emitter.write_text("malicious", encoding="utf-8")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("workspace hook subprocess must not start")

    monkeypatch.setattr(sign_module.subprocess, "run", forbidden)
    monkeypatch.setattr(init_module.subprocess, "run", forbidden)
    sign_module._call_brain_ingest(artifact, "G4")
    init_module._register_ide_hooks(tmp_path, "claude-code")
