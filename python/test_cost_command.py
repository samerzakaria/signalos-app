from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.cli import _build_parser, main as cli_main
from signalos_lib.commands import cost


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_cost_command_is_registered_in_cli() -> None:
    parser = _build_parser()
    choices = {}
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)
    assert "cost" in choices


def test_cost_report_summarizes_metrics_jsonl(tmp_path: Path) -> None:
    _append_jsonl(
        tmp_path / ".signalos" / "sessions" / "s1" / "metrics.jsonl",
        [
            {
                "ts": "2026-06-29T00:00:00Z",
                "schema_version": 1,
                "session_id": "s1",
                "step_id": "T-W01-001",
                "tool": "harness",
                "duration_ms": 10,
                "tokens_in": 100,
                "tokens_out": 50,
                "cost_usd": 0.25,
                "wave_id": "W01",
                "phase": "build",
            },
            {
                "ts": "2026-06-29T00:00:01Z",
                "schema_version": 1,
                "session_id": "s1",
                "step_id": "T-W01-002",
                "tool": "harness",
                "duration_ms": 10,
                "tokens_in": 10,
                "tokens_out": 5,
                "phase": "review",
            },
        ],
    )

    payload = cost.build_cost_report(tmp_path, budget_usd="1.00")

    assert payload["calls"] == 2
    assert payload["total_tokens"] == 165
    assert payload["known_cost_usd"] == "0.25"
    assert payload["costed_rows"] == 1
    assert payload["result"] == "within-budget-or-unpriced"
    assert (tmp_path / ".signalos" / "product" / "COST_REPORT.json").is_file()


def test_budget_exceeded_returns_exit_one(tmp_path: Path) -> None:
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [
            {
                "provider": "openai",
                "model": "example",
                "stage": "design",
                "total_tokens": 100,
                "cost_usd": "2.50",
            },
        ],
    )

    rc = cost.main([
        "--repo-root",
        str(tmp_path),
        "--budget-usd",
        "1.00",
        "--json",
    ])

    assert rc == cost.EXIT_BUDGET_EXCEEDED
    payload = json.loads((tmp_path / ".signalos" / "product" / "COST_REPORT.json").read_text(encoding="utf-8"))
    assert payload["result"] == "over-budget"
    assert payload["remaining_budget_usd"] == "-1.50"


def test_wave_filter_matches_normalized_wave_ids(tmp_path: Path) -> None:
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [
            {"wave": "1", "total_tokens": 10, "cost_usd": "0.10"},
            {"wave": "W02", "total_tokens": 20, "cost_usd": "0.20"},
        ],
    )

    payload = cost.build_cost_report(tmp_path, wave="W01")

    assert payload["calls"] == 1
    assert payload["known_cost_usd"] == "0.10"


def test_cost_report_reads_desktop_provider_ledger_rows(tmp_path: Path) -> None:
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [
            {
                "source": "foundry-desktop-provider",
                "provider": "openai",
                "model": "gpt-test",
                "stage": "chat-stream",
                "wave": "W03",
                "tokens_in": 100,
                "tokens_out": 50,
                "total_tokens": 150,
                "cost_usd": "0.005",
            },
            {
                "source": "foundry-desktop-provider",
                "provider": "openrouter",
                "model": "custom",
                "stage": "chat",
                "wave": "W03",
                "tokens_in": 10,
                "tokens_out": 20,
                "total_tokens": 30,
                "cost_basis": "unpriced-provider-config",
            },
        ],
    )

    payload = cost.build_cost_report(tmp_path, wave="3")

    assert payload["calls"] == 2
    assert payload["total_tokens"] == 180
    assert payload["known_cost_usd"] == "0.005"
    assert payload["costed_rows"] == 1
    assert payload["by_stage"][0]["stage"] == "chat"
    assert payload["by_stage"][1]["stage"] == "chat-stream"


def test_env_budget_is_used_by_cli(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIGNALOS_AI_WAVE_BUDGET_USD", "0.05")
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [{"total_tokens": 10, "cost_usd": "0.10"}],
    )

    rc = cost.main(["--repo-root", str(tmp_path), "--json"])

    assert rc == cost.EXIT_BUDGET_EXCEEDED


def test_invalid_rows_are_counted_but_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"total_tokens": 5}\nnot-json\n{"provider": "x"}\n', encoding="utf-8")

    payload = cost.build_cost_report(tmp_path)

    assert payload["calls"] == 1
    assert payload["invalid_rows"] == 2


def test_price_config_prices_unpriced_row_and_trips_budget(tmp_path: Path) -> None:
    # Item 6a: a configured provider/model price computes the cost of an
    # otherwise-unpriced row and can trip the budget gate (exit 1).
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [
            {
                "provider": "openai",
                "model": "gpt-test",
                "stage": "design",
                "tokens_in": 1000,
                "tokens_out": 500,
            },
        ],
    )
    _write_json(
        tmp_path / ".signalos" / "ai-price-table.json",
        {
            "openai/gpt-test": {
                "input_per_token": "0.001",
                "output_per_token": "0.002",
            }
        },
    )

    payload = cost.build_cost_report(tmp_path, budget_usd="1.00")

    # 1000 * 0.001 + 500 * 0.002 = 2.000
    assert payload["costed_rows"] == 1
    assert payload["known_cost_usd"] == "2.000"
    assert payload["result"] == "over-budget"

    rc = cost.main(["--repo-root", str(tmp_path), "--budget-usd", "1.00", "--json"])
    assert rc == cost.EXIT_BUDGET_EXCEEDED


def test_price_config_from_env_var(tmp_path: Path, monkeypatch) -> None:
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [{"provider": "anthropic", "model": "x", "total_tokens": 100}],
    )
    monkeypatch.setenv(
        "SIGNALOS_AI_PRICE_TABLE",
        json.dumps({"anthropic/x": {"per_token": "0.01"}}),
    )

    payload = cost.build_cost_report(tmp_path)

    assert payload["costed_rows"] == 1
    assert payload["known_cost_usd"] == "1.00"


def test_no_price_config_leaves_row_unpriced(tmp_path: Path) -> None:
    # Item 6b: with no config, the unpriced row stays unpriced.
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [{"provider": "openai", "model": "gpt-test", "tokens_in": 1000, "tokens_out": 500}],
    )

    payload = cost.build_cost_report(tmp_path)

    assert payload["calls"] == 1
    assert payload["costed_rows"] == 0
    assert payload["known_cost_usd"] is None


def test_price_config_no_matching_key_leaves_row_unpriced(tmp_path: Path) -> None:
    # Negative: a price table that does not match the row never guesses a cost.
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [{"provider": "openai", "model": "gpt-test", "tokens_in": 1000, "tokens_out": 500}],
    )
    _write_json(
        tmp_path / ".signalos" / "ai-price-table.json",
        {"some-other-provider/other-model": {"per_token": "0.5"}},
    )

    payload = cost.build_cost_report(tmp_path)

    assert payload["costed_rows"] == 0
    assert payload["known_cost_usd"] is None


def test_top_level_cli_reaches_cost_command(tmp_path: Path) -> None:
    _append_jsonl(
        tmp_path / ".signalos" / "product" / "AI_USAGE.jsonl",
        [{"provider": "anthropic", "model": "x", "total_tokens": 10, "cost_usd": "0.01"}],
    )

    rc = cli_main(["signalos", "cost", "--repo-root", str(tmp_path), "--budget-usd", "1"])

    assert rc == cost.EXIT_OK
    assert (tmp_path / ".signalos" / "product" / "COST_REPORT.json").is_file()
