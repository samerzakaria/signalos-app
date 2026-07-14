"""Drift protection for the three universal consult-panel copies."""
from __future__ import annotations

import hashlib
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
    canonical = ROOT / "python" / "signalos_lib" / "panel.py"
    targets = syncer.installed_targets(Path.home())
    existing = [target for target in targets if target.is_file()]
    if not existing:
        pytest.skip("global Codex/Claude skill copies are not installed in this environment")
    expected = hashlib.sha256(canonical.read_bytes()).hexdigest()
    assert len(existing) == len(targets), "only one global consult-panel copy is installed"
    assert {hashlib.sha256(path.read_bytes()).hexdigest() for path in existing} == {expected}
