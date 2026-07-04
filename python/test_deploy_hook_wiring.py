from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from signalos_lib import deploy as deploy_mod
from signalos_lib.commands.deploy import cmd_signal_land_deploy
from signalos_lib.deploy import (
    DEPLOY_INDEX_RELATIVE,
    DeployHookError,
    deploy_list,
    land_deploy,
    setup_deploy,
)


def _install_pre_deploy_hook(root: Path) -> Path:
    hook = root / "core" / "execution" / "hooks" / "pre-deploy"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    return hook


def test_land_deploy_runs_pre_deploy_hook_before_marking_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = setup_deploy(tmp_path, "W1", "production", "ship")
    hook = _install_pre_deploy_hook(tmp_path)
    calls: list[dict] = []

    def fake_run(cmd, cwd, env, text, capture_output, timeout, check):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        return SimpleNamespace(returncode=0, stdout="hook ok", stderr="")

    monkeypatch.setattr(deploy_mod.shutil, "which", lambda name: "bash" if name == "bash" else None)
    monkeypatch.setattr(deploy_mod.subprocess, "run", fake_run)

    landed = land_deploy(
        tmp_path,
        record.id,
        enforce_pre_deploy=True,
        deploy_signer="Dana DevOps",
    )

    assert landed is not None
    assert landed.status == "landed"
    assert calls
    assert calls[0]["cmd"] == ["bash", str(hook)]
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["env"]["DEVOPS_DEPLOY_SIGNER"] == "Dana DevOps"
    assert deploy_list(tmp_path)[0].status == "landed"


def test_pre_deploy_hook_failure_leaves_deploy_record_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = setup_deploy(tmp_path, "W1", "production", "ship")
    _install_pre_deploy_hook(tmp_path)

    def fake_run(cmd, cwd, env, text, capture_output, timeout, check):
        return SimpleNamespace(returncode=1, stdout="blocked by SoD", stderr="")

    monkeypatch.setattr(deploy_mod.shutil, "which", lambda name: "bash" if name == "bash" else None)
    monkeypatch.setattr(deploy_mod.subprocess, "run", fake_run)

    with pytest.raises(DeployHookError, match="blocked by SoD"):
        land_deploy(
            tmp_path,
            record.id,
            enforce_pre_deploy=True,
            deploy_signer="Dana DevOps",
        )

    stored = deploy_list(tmp_path)
    assert len(stored) == 1
    assert stored[0].id == record.id
    assert stored[0].status == "setup"


def test_signal_land_deploy_cli_blocks_before_mutating_when_pre_deploy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = setup_deploy(tmp_path, "W1", "production", "ship")
    _install_pre_deploy_hook(tmp_path)

    def fake_run(cmd, cwd, env, text, capture_output, timeout, check):
        return SimpleNamespace(returncode=1, stdout="", stderr="missing signer")

    monkeypatch.setattr(deploy_mod.shutil, "which", lambda name: "bash" if name == "bash" else None)
    monkeypatch.setattr(deploy_mod.subprocess, "run", fake_run)

    rc = cmd_signal_land_deploy([
        record.id,
        "--repo-root",
        str(tmp_path),
        "--signer",
        "Dana DevOps",
    ])

    assert rc == 1
    assert "pre-deploy blocked" in capsys.readouterr().err
    assert (tmp_path / DEPLOY_INDEX_RELATIVE).read_text(encoding="utf-8").count('"status": "setup"') == 1
