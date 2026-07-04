from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from signalos_lib import retro as retro_mod
from signalos_lib.cli import main as cli_main
from signalos_lib.commands.post_retro import cmd_signal_post_retro
from signalos_lib.retro import PostRetroHookError, run_post_retro_hook


def _install_post_retro_hook(root: Path) -> Path:
    hook = root / "core" / "execution" / "hooks" / "post-retro"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    return hook


def test_run_post_retro_hook_invokes_installed_hook_with_wave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook = _install_post_retro_hook(tmp_path)
    calls: list[dict] = []

    def fake_run(cmd, cwd, text, capture_output, timeout, check):
        calls.append({"cmd": cmd, "cwd": cwd})
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(retro_mod.shutil, "which", lambda name: "bash" if name == "bash" else None)
    monkeypatch.setattr(retro_mod.subprocess, "run", fake_run)

    run_post_retro_hook(tmp_path, "wave-03-checkout")

    assert calls == [{"cmd": ["bash", str(hook), "wave-03-checkout"], "cwd": tmp_path}]


def test_run_post_retro_hook_failure_is_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_post_retro_hook(tmp_path)

    def fake_run(cmd, cwd, text, capture_output, timeout, check):
        return SimpleNamespace(returncode=1, stdout="", stderr="missing Constitution delta")

    monkeypatch.setattr(retro_mod.shutil, "which", lambda name: "bash" if name == "bash" else None)
    monkeypatch.setattr(retro_mod.subprocess, "run", fake_run)

    with pytest.raises(PostRetroHookError, match="missing Constitution delta"):
        run_post_retro_hook(tmp_path, "wave-03-checkout")


def test_signal_post_retro_cli_surfaces_failure_as_readable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_post_retro_hook(tmp_path)

    def fake_run(cmd, cwd, text, capture_output, timeout, check):
        return SimpleNamespace(returncode=1, stdout="retro missing", stderr="")

    monkeypatch.setattr(retro_mod.shutil, "which", lambda name: "bash" if name == "bash" else None)
    monkeypatch.setattr(retro_mod.subprocess, "run", fake_run)

    rc = cmd_signal_post_retro([
        "wave-03-checkout",
        "--repo-root",
        str(tmp_path),
        "--json",
    ])

    captured = capsys.readouterr()
    assert rc == 1
    assert "post-retro blocked" in captured.err
    assert "retro missing" in captured.err
    assert json.loads(captured.out) == {
        "ok": False,
        "wave": "wave-03-checkout",
        "error": "retro missing",
    }


def test_signal_post_retro_cli_passes_when_hook_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_post_retro_hook(tmp_path)

    def fake_run(cmd, cwd, text, capture_output, timeout, check):
        return SimpleNamespace(returncode=0, stdout="passed", stderr="")

    monkeypatch.setattr(retro_mod.shutil, "which", lambda name: "bash" if name == "bash" else None)
    monkeypatch.setattr(retro_mod.subprocess, "run", fake_run)

    rc = cmd_signal_post_retro([
        "wave-03-checkout",
        "--repo-root",
        str(tmp_path),
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "post-retro passed wave-03-checkout\n"
    assert captured.err == ""


def test_signalos_main_dispatches_signal_post_retro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_post_retro_hook(tmp_path)

    def fake_run(cmd, cwd, text, capture_output, timeout, check):
        return SimpleNamespace(returncode=0, stdout="passed", stderr="")

    monkeypatch.setattr(retro_mod.shutil, "which", lambda name: "bash" if name == "bash" else None)
    monkeypatch.setattr(retro_mod.subprocess, "run", fake_run)

    rc = cli_main([
        "signalos",
        "signal-post-retro",
        "wave-03-checkout",
        "--repo-root",
        str(tmp_path),
        "--json",
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out) == {"ok": True, "wave": "wave-03-checkout"}
    assert captured.err == ""
