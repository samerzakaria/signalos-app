"""App-native observability command surface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from signalos_lib.product.observability import (
    append_observability_journal,
    close_listening_window,
    create_listening_window,
    evaluate_listening_window,
    get_deployment_signal,
    list_deployment_signals,
    load_observability_journal,
    load_listening_window,
    open_listening_window,
    record_deployment_signal,
    record_window_reading,
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos observe",
        description="Observe signal windows and draft evidence-backed verdicts.",
    )
    sub = parser.add_subparsers(dest="area")
    window = sub.add_parser("window", help="Listening-window lifecycle")
    window_sub = window.add_subparsers(dest="action")
    signal = sub.add_parser("signal", help="Deployment signal records")
    signal_sub = signal.add_subparsers(dest="action")
    journal = sub.add_parser("journal", help="Structured observability journal")
    journal_sub = journal.add_subparsers(dest="action")

    p_create = window_sub.add_parser("create", help="Create a Pending listening window")
    _add_repo_root(p_create)
    _add_wave(p_create)
    p_create.add_argument("--belief-id", required=True)
    p_create.add_argument("--opens-at", required=True)
    p_create.add_argument("--closes-at", required=True)
    p_create.add_argument("--expected-outcome", required=True)
    p_create.add_argument("--metric", required=True, dest="metric_name")
    p_create.add_argument("--threshold", required=True, type=float)
    p_create.add_argument("--direction", required=True, choices=["up", "down"])
    p_create.add_argument("--minimum-cohort", type=int, default=0)
    p_create.add_argument(
        "--threshold-signed", action="store_true",
        help="mark this threshold as a founder-signed success criterion "
             "(3.2: unsigned thresholds block KEEP/KILL verdicts)",
    )
    p_create.add_argument("--force", action="store_true")
    _add_json(p_create)

    p_show = window_sub.add_parser("show", help="Show the persisted listening window")
    _add_repo_root(p_show)
    _add_wave(p_show)
    _add_json(p_show)

    p_open = window_sub.add_parser("open", help="Open a due listening window")
    _add_repo_root(p_open)
    _add_wave(p_open)
    p_open.add_argument("--now", default=None)
    _add_json(p_open)

    p_reading = window_sub.add_parser("reading", help="Record one primary metric reading")
    _add_repo_root(p_reading)
    _add_wave(p_reading)
    p_reading.add_argument("--value", required=True, type=float)
    p_reading.add_argument("--metric", default=None, dest="metric_name")
    p_reading.add_argument("--cohort", type=int, default=0)
    p_reading.add_argument("--slo-breach", action="store_true")
    p_reading.add_argument("--source", default="manual")
    p_reading.add_argument("--message", default="")
    p_reading.add_argument("--ts", default=None)
    _add_json(p_reading)

    p_evaluate = window_sub.add_parser("evaluate", help="Evaluate a listening window")
    _add_repo_root(p_evaluate)
    _add_wave(p_evaluate)
    p_evaluate.add_argument("--now", default=None)
    p_evaluate.add_argument("--stale-after-hours", type=float, default=4.0)
    p_evaluate.add_argument("--no-evidence", action="store_true")
    _add_json(p_evaluate)

    p_close = window_sub.add_parser("close", help="Close a listening window and evaluate it")
    _add_repo_root(p_close)
    _add_wave(p_close)
    p_close.add_argument("--reason", default="window-closed")
    p_close.add_argument("--now", default=None)
    p_close.add_argument("--no-evidence", action="store_true")
    _add_json(p_close)

    p_signal_record = signal_sub.add_parser("record", help="Record one deployment signal")
    _add_repo_root(p_signal_record)
    p_signal_record.add_argument("--belief-id", required=True)
    p_signal_record.add_argument("--reading", required=True)
    p_signal_record.add_argument(
        "--outcome",
        required=True,
        choices=["NoSignal", "MetPositive", "MetNegative"],
    )
    p_signal_record.add_argument("--collected-at", default=None)
    p_signal_record.add_argument("--listening-window-id", default=None)
    _add_json(p_signal_record)

    p_signal_get = signal_sub.add_parser("get", help="Show one deployment signal")
    _add_repo_root(p_signal_get)
    p_signal_get.add_argument("signal_id")
    _add_json(p_signal_get)

    p_signal_list = signal_sub.add_parser("list", help="List deployment signals")
    _add_repo_root(p_signal_list)
    p_signal_list.add_argument("--belief-id", default=None)
    p_signal_list.add_argument("--listening-window-id", default=None)
    _add_json(p_signal_list)

    p_journal_append = journal_sub.add_parser("append", help="Append one journal event")
    _add_repo_root(p_journal_append)
    p_journal_append.add_argument("event_type")
    p_journal_append.add_argument("--payload", default="{}")
    p_journal_append.add_argument("--ts", default=None)
    _add_json(p_journal_append)

    p_journal_list = journal_sub.add_parser("list", help="List journal events")
    _add_repo_root(p_journal_list)
    p_journal_list.add_argument("--event-type", default=None)
    _add_json(p_journal_list)

    args = parser.parse_args(argv)
    if args.area not in {"window", "signal", "journal"} or args.action is None:
        parser.print_help()
        return 1

    try:
        payload = _run(args)
    except (FileExistsError, FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"signalos observe: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_human(args.action, payload)

    if args.area == "window" and args.action in {"evaluate", "close"}:
        return 0 if payload.get("ok") else 1
    if args.area == "signal" and args.action == "get":
        return 0 if payload.get("signal") else 1
    return 0


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.area == "window":
        return _run_window(args)
    if args.area == "signal":
        return _run_signal(args)
    if args.area == "journal":
        return _run_journal(args)
    raise ValueError(f"unknown observe area: {args.area}")


def _run_window(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    action = args.action
    if action == "create":
        return create_listening_window(
            root,
            wave=args.wave,
            belief_id=args.belief_id,
            opens_at=args.opens_at,
            closes_at=args.closes_at,
            expected_outcome=args.expected_outcome,
            metric_name=args.metric_name,
            threshold=args.threshold,
            direction=args.direction,
            minimum_cohort=args.minimum_cohort,
            force=args.force,
            threshold_signed=args.threshold_signed,
        )
    if action == "show":
        return load_listening_window(root, args.wave)
    if action == "open":
        return open_listening_window(root, args.wave, now=args.now)
    if action == "reading":
        return record_window_reading(
            root,
            args.wave,
            value=args.value,
            metric_name=args.metric_name,
            cohort=args.cohort,
            slo_breach=args.slo_breach,
            source=args.source,
            message=args.message,
            ts=args.ts,
        )
    if action == "evaluate":
        return evaluate_listening_window(
            root,
            args.wave,
            now=args.now,
            stale_after_hours=args.stale_after_hours,
            write_evidence=not args.no_evidence,
        )
    if action == "close":
        return close_listening_window(
            root,
            args.wave,
            reason=args.reason,
            now=args.now,
            write_evidence=not args.no_evidence,
        )
    raise ValueError(f"unknown window action: {action}")


def _run_signal(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    if args.action == "record":
        signal = record_deployment_signal(
            root,
            belief_id=args.belief_id,
            reading=args.reading,
            outcome=args.outcome,
            collected_at=args.collected_at,
            listening_window_id=args.listening_window_id,
        )
        return {"ok": True, "signal": signal}
    if args.action == "get":
        signal = get_deployment_signal(root, args.signal_id)
        return {"ok": signal is not None, "signal": signal, "id": args.signal_id}
    if args.action == "list":
        signals = list_deployment_signals(
            root,
            belief_id=args.belief_id,
            listening_window_id=args.listening_window_id,
        )
        return {"ok": True, "signals": signals, "count": len(signals)}
    raise ValueError(f"unknown signal action: {args.action}")


def _run_journal(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    if args.action == "append":
        payload = json.loads(args.payload)
        if not isinstance(payload, dict):
            raise ValueError("--payload must be a JSON object")
        row = append_observability_journal(root, args.event_type, payload, ts=args.ts)
        return {"ok": True, "event": row}
    if args.action == "list":
        events = load_observability_journal(root, event_type=args.event_type)
        return {"ok": True, "events": events, "count": len(events)}
    raise ValueError(f"unknown journal action: {args.action}")


def _add_repo_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=None)


def _add_wave(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--wave", required=True)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def _print_human(action: str, payload: dict[str, Any]) -> None:
    if "signal" in payload:
        signal = payload.get("signal") or {}
        if not signal:
            print("signalos observe signal: none")
            return
        print(
            "signalos observe signal: "
            f"{signal.get('outcome')} belief={signal.get('belief_id')} "
            f"collected_at={signal.get('collected_at')}"
        )
        return
    if "signals" in payload:
        print(f"signalos observe signal list: {payload.get('count', 0)}")
        for signal in payload.get("signals", []):
            print(
                f"- {signal.get('collected_at')} {signal.get('belief_id')} "
                f"{signal.get('outcome')}"
            )
        return
    if "event" in payload:
        event = payload.get("event") or {}
        print(f"signalos observe journal: {event.get('event_type')} {event.get('ts')}")
        return
    if "events" in payload:
        print(f"signalos observe journal list: {payload.get('count', 0)}")
        for event in payload.get("events", []):
            print(f"- {event.get('ts')} {event.get('event_type')}")
        return
    if action in {"create", "show", "open", "reading"}:
        metric = payload.get("metric", {})
        print(
            "signalos observe window "
            f"{action}: {payload.get('status')} wave={payload.get('wave')} "
            f"metric={metric.get('name')}"
        )
        return
    print(
        "signalos observe window "
        f"{action}: {payload.get('status')} wave={payload.get('wave')} "
        f"verdict={payload.get('proposed_verdict')}"
    )
    for blocker in payload.get("blockers", []):
        print(f"- {blocker.get('kind')}: {blocker.get('message')}")
    if payload.get("evidence_path"):
        print(f"evidence: {payload['evidence_path']}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
