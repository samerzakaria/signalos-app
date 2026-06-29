from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import signalos_ipc_server as ipc


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_cost_summary_ipc_returns_cost_report(tmp_path: Path) -> None:
    original_cwd = os.getcwd()
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [
            {
                "provider": "openai",
                "model": "example",
                "stage": "generation",
                "total_tokens": 120,
                "cost_usd": "0.42",
            },
        ],
    )

    try:
        response = ipc.handle({
            "id": "cost-req",
            "command": "cost:summary",
            "args": [],
            "cwd": str(tmp_path),
        })
    finally:
        os.chdir(original_cwd)

    assert response["ok"], response
    payload = response["data"]
    assert payload["schema_version"] == "signalos.ai_cost_report.v1"
    assert payload["calls"] == 1
    assert payload["known_cost_usd"] == "0.42"
    assert payload["result"] == "within-budget-or-unpriced"
    assert (tmp_path / ".signalos" / "product" / "COST_REPORT.json").is_file()
