"""Drift protection for the three universal consult-panel copies."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_consult_panel as syncer  # noqa: E402


def test_synchronizer_copies_exact_bytes_and_detects_drift(tmp_path):
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(b"print('canonical')\n")
    targets = [tmp_path / "codex" / "panel.py", tmp_path / "claude" / "panel.py"]

    matched, rows = syncer.synchronize(canonical, targets, write=True)
    assert matched is True
    assert {row["status"] for row in rows} == {"match"}
    assert all(target.read_bytes() == canonical.read_bytes() for target in targets)

    targets[0].write_bytes(b"print('drift')\n")
    matched, rows = syncer.synchronize(canonical, targets, write=False)
    assert matched is False
    assert [row["status"] for row in rows] == ["drift", "match"]


def test_cli_check_and_write_exit_codes(tmp_path):
    canonical = tmp_path / "canonical.py"
    canonical.write_text("# core\n", encoding="utf-8")
    target = tmp_path / "target" / "panel.py"

    assert syncer.main(["--check", "--canonical", str(canonical), "--target", str(target)]) == 1
    assert syncer.main(["--write", "--canonical", str(canonical), "--target", str(target)]) == 0
    assert syncer.main(["--check", "--canonical", str(canonical), "--target", str(target)]) == 0


def test_installed_copies_match_canonical_when_present():
    if os.environ.get("SIGNALOS_CHECK_INSTALLED_PANEL") != "1":
        pytest.skip("installed-copy check is an explicit deployment gate")
    canonical = ROOT / "python" / "signalos_lib" / "panel.py"
    targets = syncer.installed_targets(Path.home())
    existing = [target for target in targets if target.is_file()]
    if not existing:
        pytest.skip("global Codex/Claude skill copies are not installed in this environment")
    expected = hashlib.sha256(canonical.read_bytes()).hexdigest()
    assert len(existing) == len(targets), "only one global consult-panel copy is installed"
    assert {hashlib.sha256(path.read_bytes()).hexdigest() for path in existing} == {expected}


def test_synchronizer_refuses_symlink_target(tmp_path):
    canonical = tmp_path / "canonical.py"
    canonical.write_text("# canonical\n", encoding="utf-8")
    real = tmp_path / "real.py"
    real.write_text("# original\n", encoding="utf-8")
    linked = tmp_path / "linked.py"
    try:
        linked.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(OSError, match="symlink/reparse-point"):
        syncer.synchronize(canonical, [linked], write=True)
    assert real.read_text(encoding="utf-8") == "# original\n"


def test_synchronizer_rolls_back_all_targets_if_install_fails(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical.py"
    canonical.write_text("# canonical\n", encoding="utf-8")
    targets = [tmp_path / "one.py", tmp_path / "two.py"]
    targets[0].write_text("# one\n", encoding="utf-8")
    targets[1].write_text("# two\n", encoding="utf-8")
    real_replace = syncer.os.replace
    install_replacements = 0

    def fail_second_install(source, destination):
        nonlocal install_replacements
        if Path(source).name.endswith(".tmp") and Path(destination) in targets:
            install_replacements += 1
            if install_replacements == 2:
                raise OSError("simulated second-target failure")
        return real_replace(source, destination)

    monkeypatch.setattr(syncer.os, "replace", fail_second_install)
    with pytest.raises(OSError, match="simulated second-target failure"):
        syncer.synchronize(canonical, targets, write=True)
    assert targets[0].read_text(encoding="utf-8") == "# one\n"
    assert targets[1].read_text(encoding="utf-8") == "# two\n"
