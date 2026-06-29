from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_module_entrypoint_dispatches_cli() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "python")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "signalos_lib.cli",
            "deliver-intent",
            "--prompt",
            "Build a task board for support teams",
            "--json",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["intent"]["product_name"]
    assert payload["blueprint_id"] == "task-management"


def test_module_entrypoint_preserves_capability_choices() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "python")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "signalos_lib.cli",
            "deliver-intent",
            "--prompt",
            "Build a REST API for support tickets",
            "--technology",
            "node",
            "--database",
            "postgresql",
            "--cache",
            "redis",
            "--json",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    intent = payload["intent"]
    assert intent["capability_preferences"]["technologies"] == ["node"]
    assert intent["capability_preferences"]["database"] == "postgresql"
    assert intent["capability_preferences"]["cache"] == "redis"
