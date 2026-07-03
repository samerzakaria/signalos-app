"""1.11: policy:get / policy:set IPC round-trip -- the real path the founder
settings UI uses."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import signalos_ipc_server as ipc


def test_policy_get_returns_default_when_unset(tmp_path: Path) -> None:
    response = ipc.handle({
        "id": "p1", "command": "policy:get", "args": [], "cwd": str(tmp_path),
    })
    assert response["ok"], response
    assert response["data"]["gate_mode"] == "standard"


def test_policy_set_then_get_roundtrips(tmp_path: Path) -> None:
    original_cwd = os.getcwd()
    try:
        set_response = ipc.handle({
            "id": "p2", "command": "policy:set",
            "args": [json.dumps({
                "gate_mode": "fast-lane", "research_depth": "deep",
                "budget_cap_usd": 15.5, "standards_profile": "strict",
            })],
            "cwd": str(tmp_path),
        })
        assert set_response["ok"], set_response
        assert set_response["data"]["gate_mode"] == "fast-lane"

        get_response = ipc.handle({
            "id": "p3", "command": "policy:get", "args": [], "cwd": str(tmp_path),
        })
        assert get_response["data"]["budget_cap_usd"] == 15.5
        assert get_response["data"]["standards_profile"] == "strict"
    finally:
        os.chdir(original_cwd)


def test_policy_set_rejects_invalid_gate_mode(tmp_path: Path) -> None:
    original_cwd = os.getcwd()
    try:
        response = ipc.handle({
            "id": "p4", "command": "policy:set",
            "args": [json.dumps({"gate_mode": "not-a-real-mode"})],
            "cwd": str(tmp_path),
        })
        assert response["ok"] is False
        assert "invalid policy" in response["error"]
    finally:
        os.chdir(original_cwd)
