#!/usr/bin/env python3
"""Newline-delimited JSON IPC bridge for the SignalOS desktop app."""

from __future__ import annotations

import json
import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from importlib import resources
from io import StringIO
from typing import Any

from signalos_secret_guard import (
    redact_arg_list,
    redact_response,
    redact_text,
    scan_secret_files,
)


GATE_NAMES = {
    0: "Constitution",
    1: "Belief",
    2: "Expectation Map",
    3: "Plan",
    4: "Trust Tier",
    5: "Quality Check",
}

GATE_DESCRIPTIONS = {
    0: "Product constitution and first-run governance baseline",
    1: "Signed belief statement for the current wave",
    2: "Measurable success criteria for the wave",
    3: "Approved PLAN.md",
    4: "Trust tier declaration for planned work",
    5: "Quality check and release evidence",
}


def handle(req: dict) -> dict:
    req_id = req.get("id", "unknown")
    command = req.get("command", "")
    raw_args = req.get("args", [])
    args = redact_arg_list(raw_args if isinstance(raw_args, list) else [str(raw_args)])
    cwd = req.get("cwd")

    if cwd and os.path.isdir(cwd):
        os.chdir(cwd)

    try:
        return route(req_id, command, args)
    except Exception as exc:
        return {
            "id": req_id,
            "ok": False,
            "error": redact_text(f"{type(exc).__name__}: {exc}"),
            "trace": redact_text(traceback.format_exc()),
        }


def route(req_id: str, command: str, args: list[str]) -> dict:
    if command.startswith("/signal-") or command.startswith("signal-"):
        return ok(req_id, output=dispatch_cli(command.lstrip("/"), args))

    if command == "state:wave":
        return ok(req_id, data=get_wave_state())

    if command == "state:gates":
        return ok(req_id, data=get_gate_states())

    if command == "gate:sign":
        if len(args) < 2:
            return err(req_id, "gate:sign requires [gate_id, signer]")
        return ok(req_id, data=sign_gate(int(args[0]), args[1]))

    if command == "brain:search":
        return ok(req_id, data=brain_search(args[0] if args else ""))

    if command == "brain:add":
        if len(args) < 2:
            return err(req_id, "brain:add requires [entry_type, text]")
        return ok(req_id, data=brain_add(args[1], args[0]))

    if command == "audit:list":
        limit = int(args[0]) if args else 50
        return ok(req_id, data=audit_list(limit))

    if command == "cost:summary":
        return ok(req_id, data={"note": "cost tracked in Rust provider layer"})

    if command == "security:secrets":
        return ok(req_id, data=scan_secret_files(os.getcwd()))

    if command == "ping":
        return ok(req_id, data={"pong": True, "version": "1.0.0-beta4"})

    return err(req_id, f"Unknown command: {command}")


def dispatch_cli(command: str, args: list[str]) -> str:
    cwd = os.getcwd()
    argv = map_slash_command(command, redact_arg_list(args), cwd)
    if argv is not None:
        rc, out, err_text = run_core_cli(argv)
        text = redact_text((out or err_text).strip())
        if text:
            return text
        return f"Command completed with exit code {rc}."

    spec = read_command_spec(command)
    if spec:
        return (
            f"/{command} is available as a SignalOS protocol command. "
            "This beta shows the command brief here; conversational execution is next.\n\n"
            f"{redact_text(spec)}"
        )

    return f"Unknown SignalOS command: /{command}"


def map_slash_command(command: str, args: list[str], cwd: str) -> list[str] | None:
    cleaned_args = strip_context_arg(args)

    if command == "signal-status":
        return ["status", "--repo-root", cwd]

    if command == "signal-init":
        return ["init", *(cleaned_args or [cwd])]

    if command == "signal-brain":
        if not cleaned_args:
            return ["brain", "list", "--repo-root", cwd]
        action = cleaned_args[0]
        rest = cleaned_args[1:]
        if action == "add":
            text = " ".join(rest).strip()
            return ["brain", "put", text, "--type", "note", "--repo-root", cwd] if text else None
        if action in {"put", "search", "list", "prune", "export", "upgrade"}:
            return ["brain", action, *rest, "--repo-root", cwd]
        return ["brain", "search", " ".join(cleaned_args), "--repo-root", cwd]

    if command == "signal-plan":
        if cleaned_args and cleaned_args[0] in {"render", "validate", "list"}:
            return ["plan", *cleaned_args]
        return None

    if command in {"signal-qa", "signal-qa-only"}:
        return [command, *cleaned_args]

    direct = {
        "signal-learn",
        "signal-cso",
        "signal-autoplan",
        "signal-context-restore",
        "signal-setup-deploy",
        "signal-land-deploy",
        "signal-canary-deploy",
        "signal-benchmark",
        "signal-devex-plan",
        "signal-devex",
        "signal-retro-global",
        "signal-careful",
        "signal-freeze",
        "signal-guard",
        "signal-unfreeze",
        "signal-second-opinion",
        "signal-second-opinion-record",
        "signal-investigate",
    }
    if command in direct:
        return [command, *cleaned_args]

    return None


def strip_context_arg(args: list[str]) -> list[str]:
    if args and args[0].startswith("[SignalOS] "):
        return args[1:]
    return args


def run_core_cli(argv: list[str]) -> tuple[int, str, str]:
    try:
        from signalos_lib.cli import main as core_main
    except ImportError as exc:
        return (
            127,
            "",
            "SignalOS Core is not bundled in this installer. "
            f"Rebuild the sidecar with scripts/bundle-sidecar.ps1. ({exc})",
        )

    out = StringIO()
    err_buf = StringIO()
    with redirect_stdout(out), redirect_stderr(err_buf):
        try:
            rc = core_main(["signalos", *argv])
        except SystemExit as exc:
            rc = int(exc.code or 0) if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err_buf.getvalue()


def read_command_spec(command: str) -> str:
    for base, rel in (
        ("signalos_lib._bundle.core.execution.commands", f"{command}.md"),
        ("signalos_lib._bundle.integrations.rules", f"{command}.mdc"),
    ):
        try:
            text = resources.files(base).joinpath(rel).read_text(encoding="utf-8")
        except Exception:
            continue
        text = text.strip()
        return text[:5000] + ("\n\n[trimmed]" if len(text) > 5000 else "")
    return ""


def get_wave_state() -> dict:
    status = get_status_json()
    wave_id = str(status.get("wave_id") or "-").strip()
    phase_name = str(status.get("phase") or "ONBOARDING")
    gates = status.get("gates") or {}
    signed_count = sum(1 for signed in gates.values() if signed)
    has_wave = any(ch.isalnum() for ch in wave_id)
    return {
        "name": f"Wave {wave_id}" if has_wave else "No active wave",
        "phase": 0 if not has_wave else signed_count,
        "phase_name": phase_name.replace("_", " ").title(),
        "progress_pct": int((signed_count / 6) * 100) if gates else 0,
        "belief_conf": 0,
    }


def get_gate_states() -> list[dict]:
    status = get_status_json()
    gate_status = status.get("gates") or {}
    first_open_seen = False
    gates: list[dict] = []
    for gate_id in range(6):
        key = f"G{gate_id}"
        signed = bool(gate_status.get(key))
        if signed:
            state = "signed"
        elif not first_open_seen:
            state = "current"
            first_open_seen = True
        else:
            state = "locked"
        gates.append(
            {
                "id": gate_id,
                "name": GATE_NAMES[gate_id],
                "desc": GATE_DESCRIPTIONS[gate_id],
                "status": state,
                "signer": None,
                "signed_at": None,
            }
        )
    return gates


def sign_gate(gate_id: int, signer: str) -> dict:
    rc, out, err_text = run_core_cli(
        [
            "sign",
            f"G{gate_id}",
            "--signer",
            signer,
            "--role",
            "PO",
            "--verdict",
            "APPROVED",
            "--repo-root",
            os.getcwd(),
        ]
    )
    if rc != 0:
        raise RuntimeError((err_text or out or f"sign exited {rc}").strip())
    return {"gate_id": gate_id, "signer": signer, "ok": True, "output": out}


def brain_search(query: str) -> list[dict]:
    argv = ["brain", "search" if query else "list"]
    if query:
        argv.append(query)
    argv += ["--repo-root", os.getcwd(), "--json"]
    rc, out, _ = run_core_cli(argv)
    if rc != 0 or not out.strip():
        return []
    try:
        entries = json.loads(out)
    except json.JSONDecodeError:
        return []
    return [normalize_brain_entry(e) for e in entries]


def brain_add(text: str, entry_type: str) -> dict:
    text = redact_text(text)
    rc, out, err_text = run_core_cli(
        [
            "brain",
            "put",
            text,
            "--type",
            entry_type if entry_type in {"artifact", "decision", "qa", "session", "note"} else "note",
            "--repo-root",
            os.getcwd(),
            "--json",
        ]
    )
    if rc != 0:
        raise RuntimeError(redact_text((err_text or out or f"brain put exited {rc}").strip()))
    return redact_response(json.loads(out)) if out.strip() else {"text": text, "type": entry_type}


def audit_list(limit: int) -> list[dict]:
    for name in ("AUDIT_TRAIL.jsonl", "audit.jsonl"):
        audit_path = os.path.join(os.getcwd(), ".signalos", name)
        if os.path.exists(audit_path):
            break
    else:
        return []

    entries = []
    with open(audit_path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(entries))[:limit]


def get_status_json() -> dict:
    fallback = {"wave_id": "-", "phase": "ONBOARDING", "gates": {f"G{i}": False for i in range(6)}}
    rc, out, _ = run_core_cli(["status", "--repo-root", os.getcwd(), "--json"])
    if rc != 0 or not out.strip():
        return fallback
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return fallback


def normalize_brain_entry(entry: dict) -> dict:
    return {
        "id": entry.get("id", ""),
        "text": redact_text(entry.get("content") or entry.get("text") or ""),
        "type": entry.get("type") or "note",
        "ts": entry.get("created_at") or entry.get("ts") or "",
        "wave": entry.get("wave") or "",
        "gate": entry.get("gate") or "",
        "source": entry.get("source") or "",
    }


def ok(req_id: str, output: str | None = None, data: Any = None) -> dict:
    return {
        "id": req_id,
        "ok": True,
        "output": redact_text(output) if output is not None else None,
        "data": redact_response(data),
    }


def err(req_id: str, message: str) -> dict:
    return {"id": req_id, "ok": False, "error": redact_text(message)}


def main() -> None:
    print(json.dumps({"id": "init", "ok": True, "data": {"ready": True}}), flush=True)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
            resp = handle(req)
        except json.JSONDecodeError as exc:
            resp = err("parse-error", f"Invalid JSON: {exc}")
        except Exception as exc:
            resp = err("runtime-error", f"Unhandled exception: {exc}")

        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
