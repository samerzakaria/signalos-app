"""AI usage and budget summary for SignalOS workspaces."""

from __future__ import annotations

__all__ = [
    "DEFAULT_LEDGER_GLOBS",
    "EXIT_BAD_ARGS",
    "EXIT_BUDGET_EXCEEDED",
    "EXIT_OK",
    "PAUSE_THRESHOLD",
    "budget_status",
    "build_cost_report",
    "main",
]

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_BUDGET_EXCEEDED = 1
EXIT_BAD_ARGS = 2

SCHEMA_VERSION = "signalos.ai_cost_report.v1"
PRICE_TABLE_ENV = "SIGNALOS_AI_PRICE_TABLE"
PRICE_TABLE_PATHS = (
    ".signalos/ai-price-table.json",
    ".signalos/product/ai-price-table.json",
)
DEFAULT_LEDGER_GLOBS = (
    ".signalos/sessions/*/metrics.jsonl",
    ".signalos/product/AI_USAGE.jsonl",
    ".signalos/product/ai-usage.jsonl",
    ".signalos/AI_USAGE.jsonl",
    ".signalos/ai-usage.jsonl",
)

# Auto-pause fraction of a run budget (FR-7.2): warn at 90%, hard-stop at 100%.
PAUSE_THRESHOLD = Decimal("0.9")


def budget_status(
    spent: Decimal | None,
    cap: Decimal | None,
    pause_threshold: Decimal = PAUSE_THRESHOLD,
) -> str:
    """Live, fail-closed budget state. ``"halt"`` at or over the cap (a hard stop,
    not a warning), ``"warn"`` at or over the pause threshold, else ``"ok"``.
    ``"unpriced"`` when there is no cap or nothing priced to compare."""
    if cap is None or spent is None:
        return "unpriced"
    if cap <= 0:
        return "halt"  # a zero/negative cap permits no spend
    fraction = spent / cap
    if fraction >= 1:
        return "halt"
    if fraction >= pause_threshold:
        return "warn"
    return "ok"


@dataclass
class UsageRow:
    source_path: str
    provider: str
    model: str
    stage: str
    wave: str | None
    tokens_in: int
    tokens_out: int
    total_tokens: int
    cost_usd: Decimal | None


def build_cost_report(
    repo_root: Path | str | None = None,
    *,
    wave: str | None = None,
    budget_usd: Decimal | str | float | int | None = None,
    ledger_globs: tuple[str, ...] = DEFAULT_LEDGER_GLOBS,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Summarize known AI usage rows and enforce an optional USD budget."""

    root = Path(repo_root or Path.cwd()).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"repo-root not found: {root}")
    budget = _decimal_or_none(budget_usd)
    rows, invalid_rows = _read_usage_rows(root, ledger_globs)
    if wave:
        rows = [row for row in rows if _same_wave(row.wave, wave)]

    # SignalOS never guesses a cost: a row without a recorded cost is priced
    # only when an explicit provider/model price configuration matches it.
    price_table = _load_price_table(root)
    if price_table:
        for row in rows:
            if row.cost_usd is None:
                row.cost_usd = _price_row(row, price_table)

    known_costs = [row.cost_usd for row in rows if row.cost_usd is not None]
    known_cost = sum(known_costs, Decimal("0"))
    over_budget = bool(budget is not None and known_costs and known_cost > budget)
    remaining = budget - known_cost if budget is not None and known_costs else None

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "repo_root": str(root),
        "wave": wave,
        "ledger_globs": list(ledger_globs),
        "ledger_paths": sorted({row.source_path for row in rows}),
        "calls": len(rows),
        "total_tokens": sum(row.total_tokens for row in rows),
        "tokens_in": sum(row.tokens_in for row in rows),
        "tokens_out": sum(row.tokens_out for row in rows),
        "known_cost_usd": _decimal_to_json(known_cost) if known_costs else None,
        "costed_rows": len(known_costs),
        "budget_usd": _decimal_to_json(budget),
        "remaining_budget_usd": _decimal_to_json(remaining),
        "result": "over-budget" if over_budget else "within-budget-or-unpriced",
        "budget_state": budget_status(known_cost if known_costs else None, budget),
        "invalid_rows": invalid_rows,
        "by_provider": _summarize(rows, key_fn=lambda row: (row.provider, row.model), labels=("provider", "model")),
        "by_stage": _summarize(rows, key_fn=lambda row: (row.stage,), labels=("stage",)),
        "evidence_path": None,
    }
    if write_evidence:
        evidence = root / ".signalos" / "product" / "COST_REPORT.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        payload["evidence_path"] = _display_path(evidence, root)
        evidence.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _read_usage_rows(root: Path, ledger_globs: tuple[str, ...]) -> tuple[list[UsageRow], int]:
    rows: list[UsageRow] = []
    invalid = 0
    paths: list[Path] = []
    for pattern in ledger_globs:
        paths.extend(sorted(root.glob(pattern)))
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if not isinstance(data, dict):
                invalid += 1
                continue
            row = _row_from_mapping(data, source_path=_display_path(path, root))
            if row is None:
                invalid += 1
                continue
            rows.append(row)
    return rows, invalid


def _row_from_mapping(data: dict[str, Any], *, source_path: str) -> UsageRow | None:
    tokens_in = _read_int(data, "tokens_in", "input_tokens", "prompt_tokens")
    tokens_out = _read_int(data, "tokens_out", "output_tokens", "completion_tokens")
    total_tokens = _read_int(data, "total_tokens")
    if total_tokens == 0:
        total_tokens = tokens_in + tokens_out
    cost_usd = _read_cost_usd(data)
    if total_tokens == 0 and cost_usd is None:
        return None
    provider = _read_str(data, "provider") or _read_str(data, "tool") or "unknown"
    model = _read_str(data, "model") or "unknown"
    stage = (
        _read_str(data, "stage")
        or _read_str(data, "phase")
        or _read_str(data, "hook")
        or _read_str(data, "step_id")
        or "unknown"
    )
    wave = _read_str(data, "wave") or _read_str(data, "wave_id")
    return UsageRow(
        source_path=source_path,
        provider=provider,
        model=model,
        stage=stage,
        wave=wave,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
    )


def _summarize(
    rows: list[UsageRow],
    *,
    key_fn: Any,
    labels: tuple[str, ...],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[UsageRow]] = {}
    for row in rows:
        groups.setdefault(tuple(str(part) for part in key_fn(row)), []).append(row)
    out: list[dict[str, Any]] = []
    for key, group in sorted(groups.items(), key=lambda item: item[0]):
        known_costs = [row.cost_usd for row in group if row.cost_usd is not None]
        item = {label: value for label, value in zip(labels, key)}
        item.update({
            "calls": len(group),
            "total_tokens": sum(row.total_tokens for row in group),
            "tokens_in": sum(row.tokens_in for row in group),
            "tokens_out": sum(row.tokens_out for row in group),
            "known_cost_usd": _decimal_to_json(sum(known_costs, Decimal("0"))) if known_costs else None,
            "costed_rows": len(known_costs),
        })
        out.append(item)
    return out


def _read_int(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        if isinstance(value, str):
            try:
                return max(0, int(value))
            except ValueError:
                continue
    return 0


def _read_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_cost_usd(data: dict[str, Any]) -> Decimal | None:
    direct = _decimal_or_none(data.get("cost_usd"))
    if direct is not None:
        return direct
    currency = (_read_str(data, "currency") or "USD").upper()
    if currency != "USD":
        return None
    return _decimal_or_none(data.get("cost_amount"))


def _load_price_table(root: Path) -> dict[str, dict[str, Decimal]]:
    """Load an optional provider/model -> USD-per-token price configuration.

    Source precedence: the ``SIGNALOS_AI_PRICE_TABLE`` env var (inline JSON)
    first, then a ``.signalos/ai-price-table.json`` config file. Returns an
    empty mapping when nothing is configured or the config is unusable; in that
    case rows stay unpriced (SignalOS never guesses a cost).
    """

    raw = os.environ.get(PRICE_TABLE_ENV)
    data: Any = None
    if raw and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        for rel in PRICE_TABLE_PATHS:
            path = root / rel
            if not path.is_file():
                continue
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(loaded, dict):
                data = loaded
                break
    if not isinstance(data, dict):
        return {}

    # Allow an optional top-level {"prices": {...}} wrapper.
    entries = data.get("prices") if isinstance(data.get("prices"), dict) else data
    table: dict[str, dict[str, Decimal]] = {}
    for key, spec in entries.items():
        if not isinstance(key, str) or not isinstance(spec, dict):
            continue
        parsed = _parse_price_spec(spec)
        if parsed:
            table[key.strip().lower()] = parsed
    return table


def _parse_price_spec(spec: dict[str, Any]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    input_price = _first_decimal(spec, "input_per_token", "input", "prompt", "prompt_per_token")
    output_price = _first_decimal(spec, "output_per_token", "output", "completion", "completion_per_token")
    flat = _first_decimal(spec, "per_token", "usd_per_token", "price_per_token")
    if input_price is not None:
        out["input"] = input_price
    if output_price is not None:
        out["output"] = output_price
    if flat is not None:
        out["flat"] = flat
    return out


def _first_decimal(spec: dict[str, Any], *keys: str) -> Decimal | None:
    for key in keys:
        if key in spec:
            value = _decimal_or_none(spec.get(key))
            if value is not None:
                return value
    return None


def _price_row(row: "UsageRow", table: dict[str, dict[str, Decimal]]) -> Decimal | None:
    """Compute a row cost from the price table, or None when no key matches."""

    provider = (row.provider or "").strip().lower()
    model = (row.model or "").strip().lower()
    candidates = [
        f"{provider}/{model}",
        f"{provider}:{model}",
        model,
        provider,
    ]
    spec: dict[str, Decimal] | None = None
    for candidate in candidates:
        if candidate and candidate in table:
            spec = table[candidate]
            break
    if spec is None:
        return None

    if "input" in spec or "output" in spec:
        in_rate = spec.get("input", spec.get("flat", Decimal("0")))
        out_rate = spec.get("output", spec.get("flat", Decimal("0")))
        cost = (Decimal(row.tokens_in) * in_rate) + (Decimal(row.tokens_out) * out_rate)
        return cost
    if "flat" in spec:
        return Decimal(row.total_tokens) * spec["flat"]
    return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _decimal_to_json(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _same_wave(row_wave: str | None, requested: str) -> bool:
    if row_wave is None:
        return False
    return _normalize_wave(row_wave) == _normalize_wave(requested)


def _normalize_wave(value: str) -> str:
    raw = value.strip().upper()
    if raw.startswith("W") and raw[1:].isdigit():
        return f"W{int(raw[1:]):02d}"
    if raw.isdigit():
        return f"W{int(raw):02d}"
    return raw


def _budget_from_environment() -> Decimal | None:
    return _decimal_or_none(os.environ.get("SIGNALOS_AI_WAVE_BUDGET_USD"))


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos cost",
        description="Summarize AI usage/cost rows and fail when budget is exceeded.",
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--wave", default=None)
    parser.add_argument("--budget-usd", default=None)
    parser.add_argument("--ledger", action="append", default=[], help="Additional JSONL ledger path or glob.")
    parser.add_argument("--no-evidence", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    budget = _decimal_or_none(args.budget_usd)
    if args.budget_usd is not None and budget is None:
        print("signalos cost: --budget-usd must be a non-negative number", file=sys.stderr)
        return EXIT_BAD_ARGS
    if budget is None:
        budget = _budget_from_environment()
    ledger_globs = DEFAULT_LEDGER_GLOBS + tuple(args.ledger)
    try:
        payload = build_cost_report(
            args.repo_root,
            wave=args.wave,
            budget_usd=budget,
            ledger_globs=ledger_globs,
            write_evidence=not args.no_evidence,
        )
    except (FileNotFoundError, OSError) as exc:
        print(f"signalos cost: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGS

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(payload)
    return EXIT_BUDGET_EXCEEDED if payload["result"] == "over-budget" else EXIT_OK


def _print_human(payload: dict[str, Any]) -> None:
    print("signalos cost")
    print(f"  calls      : {payload['calls']}")
    print(f"  tokens     : {payload['total_tokens']}")
    print(f"  cost       : {payload['known_cost_usd'] or 'unavailable'} USD")
    print(f"  cost rows  : {payload['costed_rows']}/{payload['calls']}")
    print(f"  budget     : {payload['budget_usd'] or 'not set'}")
    print(f"  remaining  : {payload['remaining_budget_usd'] or 'unavailable'}")
    print(f"  result     : {payload['result']}")
    if payload.get("evidence_path"):
        print(f"  evidence   : {payload['evidence_path']}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
