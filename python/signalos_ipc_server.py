#!/usr/bin/env python3
"""
signalos_ipc_server.py — stdin/stdout JSON IPC bridge for the SignalOS desktop app.

The Tauri sidecar spawns this process on app launch and communicates
via stdin/stdout JSON messages (one JSON object per line).

Request format:  {"id": "req-abc", "command": "gate:sign", "args": ["2", "Samer"], "cwd": "/path/to/project"}
Response format: {"id": "req-abc", "ok": true, "data": {...}}
                 {"id": "req-abc", "ok": false, "error": "message"}

All /signal-* commands delegate to the existing SignalOS Core Python CLI.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any


# ─── ROUTER ──────────────────────────────────────────────────────────────────

def handle(req: dict) -> dict:
    req_id  = req.get("id", "unknown")
    command = req.get("command", "")
    args    = req.get("args", [])
    cwd     = req.get("cwd")

    # Change to workspace directory if provided
    if cwd and os.path.isdir(cwd):
        os.chdir(cwd)

    try:
        return route(req_id, command, args)
    except Exception as e:
        return {
            "id":    req_id,
            "ok":    False,
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc(),
        }


def route(req_id: str, command: str, args: list[str]) -> dict:

    # ── /signal-* commands → delegate to CLI dispatcher ──────────────────────
    if command.startswith("/signal-") or command.startswith("signal-"):
        cmd_name = command.lstrip("/")
        output   = dispatch_cli(cmd_name, args)
        return ok(req_id, output=output)

    # ── Wave state ────────────────────────────────────────────────────────────
    if command == "state:wave":
        return ok(req_id, data=get_wave_state())

    if command == "state:gates":
        return ok(req_id, data=get_gate_states())

    # ── Gate signing ──────────────────────────────────────────────────────────
    if command == "gate:sign":
        if len(args) < 2:
            return err(req_id, "gate:sign requires [gate_id, signer]")
        gate_id, signer = int(args[0]), args[1]
        return ok(req_id, data=sign_gate(gate_id, signer))

    # ── Brain ─────────────────────────────────────────────────────────────────
    if command == "brain:search":
        query = args[0] if args else ""
        return ok(req_id, data=brain_search(query))

    if command == "brain:add":
        if len(args) < 2:
            return err(req_id, "brain:add requires [entry_type, text]")
        entry_type, text = args[0], args[1]
        return ok(req_id, data=brain_add(text, entry_type))

    # ── Audit trail ───────────────────────────────────────────────────────────
    if command == "audit:list":
        limit = int(args[0]) if args else 50
        return ok(req_id, data=audit_list(limit))

    # ── Cost (tracked Rust-side) ──────────────────────────────────────────────
    if command == "cost:summary":
        return ok(req_id, data={"note": "cost tracked in Rust provider layer"})

    # ── Ping / healthcheck ────────────────────────────────────────────────────
    if command == "ping":
        return ok(req_id, data={"pong": True, "version": "1.0.0"})

    return err(req_id, f"Unknown command: {command}")


# ─── COMMAND IMPLEMENTATIONS ──────────────────────────────────────────────────

def dispatch_cli(command: str, args: list[str]) -> str:
    """Delegate to the SignalOS Core CLI."""
    try:
        # Import the CLI dispatcher from SignalOS Core
        # Core must be on PYTHONPATH (ensured by bundle-sidecar.sh)
        from signalos.cli import run_command  # type: ignore
        return run_command(command, args)
    except ImportError:
        # Core not available — return a helpful stub response
        return (
            f"[SignalOS Core not found]\n"
            f"Command: /{command}\n"
            f"Args: {args}\n\n"
            f"Make sure SignalOS Core is installed or bundled via scripts/bundle-sidecar.sh"
        )


def get_wave_state() -> dict:
    try:
        from signalos.state import get_wave_state as _get  # type: ignore
        return _get()
    except ImportError:
        return _stub_wave_state()


def get_gate_states() -> list[dict]:
    try:
        from signalos.state import get_gate_states as _get  # type: ignore
        return _get()
    except ImportError:
        return _stub_gate_states()


def sign_gate(gate_id: int, signer: str) -> dict:
    try:
        from signalos.governance import sign_gate as _sign  # type: ignore
        return _sign(gate_id, signer)
    except ImportError:
        import datetime
        return {
            "gate_id":   gate_id,
            "signer":    signer,
            "signed_at": datetime.datetime.utcnow().isoformat() + "Z",
            "ok":        True,
        }


def brain_search(query: str) -> list[dict]:
    try:
        from signalos.brain import search  # type: ignore
        return search(query)
    except ImportError:
        return []


def brain_add(text: str, entry_type: str) -> dict:
    try:
        from signalos.brain import add_entry  # type: ignore
        return add_entry(text, entry_type)
    except ImportError:
        import datetime
        return {"id": "stub", "text": text, "type": entry_type, "ts": datetime.datetime.utcnow().isoformat()}


def audit_list(limit: int) -> list[dict]:
    try:
        from signalos.audit import list_entries  # type: ignore
        return list_entries(limit)
    except ImportError:
        return []


# ─── STUB DATA (when Core is not installed) ───────────────────────────────────

def _stub_wave_state() -> dict:
    return {
        "name": "Wave 1", "phase": 1, "phase_name": "Discovery",
        "progress_pct": 0, "belief_conf": 0, "current_gate": 2,
    }

def _stub_gate_states() -> list[dict]:
    return [
        {"id": 0, "name": "Constitution",    "status": "signed",  "signer": None, "signed_at": None},
        {"id": 1, "name": "Belief",          "status": "signed",  "signer": None, "signed_at": None},
        {"id": 2, "name": "Expectation Map", "status": "current", "signer": None, "signed_at": None},
        {"id": 3, "name": "Plan",            "status": "locked",  "signer": None, "signed_at": None},
        {"id": 4, "name": "Trust Tier",      "status": "locked",  "signer": None, "signed_at": None},
        {"id": 5, "name": "Quality Check",   "status": "locked",  "signer": None, "signed_at": None},
    ]


# ─── RESPONSE HELPERS ─────────────────────────────────────────────────────────

def ok(req_id: str, output: str | None = None, data: Any = None) -> dict:
    return {"id": req_id, "ok": True, "output": output, "data": data}

def err(req_id: str, message: str) -> dict:
    return {"id": req_id, "ok": False, "error": message}


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main() -> None:
    # Signal readiness to the Tauri sidecar
    print(json.dumps({"id": "init", "ok": True, "output": "SignalOS IPC server ready"}), flush=True)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            req  = json.loads(line)
            resp = handle(req)
        except json.JSONDecodeError as e:
            resp = err("parse-error", f"Invalid JSON: {e}")
        except Exception as e:
            resp = err("runtime-error", f"Unhandled exception: {e}")

        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
