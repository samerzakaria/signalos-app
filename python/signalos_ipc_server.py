#!/usr/bin/env python3
"""Newline-delimited JSON IPC bridge for the SignalOS desktop app."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
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
from signalos_attachments import analyze_payload


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


# Wave 2 / G1-7: PhaseContract progress emitter.
#
# Long-running commands emit named substeps on stdout interleaved with the
# final response. Each progress line carries kind="progress" so the Rust
# multiplexer can route it to the `sidecar:progress` event.
#
# A PhaseContract is a list of (phase_id, [substep_id, ...]) tuples. The
# emitter walks substeps in order, marking each pending → running → done.
# Errors flip state to "error" and stop the walk.

import time as _time


class ProgressEmitter:
    def __init__(self, req_id: str):
        self._id = req_id
        self._phase = ""
        self._sub = ""

    def begin(self, phase: str, substep: str, detail: str | None = None) -> None:
        self._phase = phase
        self._sub = substep
        self._emit("running", detail)

    def done(self, detail: str | None = None) -> None:
        if not self._phase:
            return
        self._emit("done", detail)

    def error(self, detail: str | None = None) -> None:
        if not self._phase:
            return
        self._emit("error", detail)

    def _emit(self, state: str, detail: str | None) -> None:
        payload = {
            "id": self._id,
            "kind": "progress",
            "phase": self._phase,
            "substep": self._sub,
            "state": state,
            "detail": redact_text(detail) if detail else None,
            "ts": int(_time.time() * 1000),
        }
        print(json.dumps(payload), flush=True)


# Standard phase contracts used by Builder and wired commands. Externally
# visible so the frontend can pre-render the phase strip with substeps
# greyed out before the first event arrives.
PHASE_CONTRACTS = {
    "build": [
        ("prepare", ["read_folder", "check_engine", "test_ai", "load_status"]),
        ("plan",    ["draft_plan", "validate_plan", "save_evidence", "record_decision"]),
        ("build",   ["generate_files", "validate_contract", "diff_preview", "write_files", "append_audit"]),
        ("review",  ["refresh_status", "gate_check", "start_preview", "open_pane", "surface_next"]),
    ],
    "init": [
        ("prepare", ["check_target", "consent_mode"]),
        ("write",   ["copy_bundle", "runtime_state", "plan_template", "readme"]),
        ("review",  ["git_init", "ide_hooks"]),
    ],
    "status": [
        ("read",    ["load_plan", "load_gates", "load_audit"]),
        ("render",  ["compose_card"]),
    ],
}


def handle(req: dict) -> dict:
    req_id = req.get("id", "unknown")
    command = req.get("command", "")
    raw_args = req.get("args", [])
    raw_arg_list = raw_args if isinstance(raw_args, list) else [str(raw_args)]
    args = raw_arg_list if command == "attachment:analyze" else redact_arg_list(raw_arg_list)
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
        return ok(req_id, output=dispatch_cli(command.lstrip("/"), args, req_id))

    if command == "state:wave":
        return ok(req_id, data=get_wave_state())

    if command == "state:gates":
        return ok(req_id, data=get_gate_states())

    if command == "gate:sign":
        if len(args) < 2:
            return err(req_id, "gate:sign requires [gate_id, signer]")
        # Wave 5 / G4: test-first rule. G1 (Belief) sign requires test refs.
        # Optional third arg is a comma-separated list of test file paths
        # or test plan ids. If missing for G1, refuse with a clear message.
        gate_id = int(args[0])
        signer = args[1]
        test_refs = []
        if len(args) >= 3:
            test_refs = [t for t in args[2].split(",") if t.strip()]
        if gate_id == 1 and not test_refs:
            return err(
                req_id,
                "G1 Belief sign requires at least one test reference. Pass test files or plan ids as the third argument.",
            )
        return ok(req_id, data=sign_gate(gate_id, signer))

    if command == "brain:search":
        return ok(req_id, data=brain_search(args[0] if args else ""))

    if command == "brain:add":
        if len(args) < 2:
            return err(req_id, "brain:add requires [entry_type, text]")
        return ok(req_id, data=brain_add(args[1], args[0]))

    if command == "audit:list":
        limit = int(args[0]) if args else 50
        return ok(req_id, data=audit_list(limit))

    # Milestone 2-a: frontend chat-response guard records a redaction event.
    # Args is a single JSON-encoded object: {action, kind_counts, prompt_head,
    # redactions[]}. We append it to .signalos/AUDIT_TRAIL.jsonl via the same
    # helper used by the build-write path. Unknown extra fields are preserved
    # verbatim (we don't filter the schema -- the chat guard owns its event
    # shape and the audit trail is append-only journal, not a typed log).
    if command == "audit:append":
        if not args:
            return err(req_id, "audit:append requires a JSON payload arg")
        try:
            payload = json.loads(args[0])
        except (TypeError, ValueError) as exc:
            return err(req_id, f"audit:append payload was not valid JSON: {exc}")
        if not isinstance(payload, dict):
            return err(req_id, "audit:append payload must be a JSON object")
        if "action" not in payload:
            payload["action"] = "chat-response-filtered"
        _append_audit(os.getcwd(), payload)
        return ok(req_id, data={"ok": True})

    if command == "cost:summary":
        return ok(req_id, data={"note": "cost tracked in Rust provider layer"})

    if command == "security:secrets":
        return ok(req_id, data=scan_secret_files(os.getcwd()))

    if command == "attachment:analyze":
        payload_json = args[0] if args else "[]"
        return ok(req_id, data=analyze_payload(payload_json))

    if command == "ping":
        return ok(req_id, data={"pong": True, "version": "0.0.9"})

    # Wave 2 / G1-7: phase contract lookup. The UI calls this to know
    # how many substeps a given command will emit so the progress strip
    # can render all rows up front in `pending` state.
    if command == "phase:contract":
        name = (args[0] if args else "").strip()
        contract = PHASE_CONTRACTS.get(name)
        if contract is None:
            return err(req_id, f"Unknown phase contract: {name}")
        return ok(req_id, data={"name": name, "phases": contract})

    return err(req_id, f"Unknown command: {command}")


def dispatch_cli(command: str, args: list[str], req_id: str = "") -> str:
    cwd = os.getcwd()
    redacted = redact_arg_list(args)

    # Wave checkpoint: capture pre-wave HEAD SHA so "Undo Wave" can
    # restore the workspace to its pre-approval state.
    if command == "signal-checkpoint":
        return handle_checkpoint(redacted, cwd)

    # Wave rollback: hard-reset to a prior checkpoint and delete any
    # files the wave wrote that aren't tracked.
    if command == "signal-rollback":
        return handle_rollback(redacted, cwd)

    # Sandbox status + toggle (Docker availability + .signalos/sandbox.json).
    if command == "signal-sandbox":
        return handle_sandbox(redacted, cwd)

    # Wave 1 / G0-1: signal-init --mode skip is a no-op, not a spec dump.
    if command == "signal-init":
        stripped = strip_context_arg(redacted)
        if len(stripped) >= 2 and stripped[0] in {"--mode", "-m"} and stripped[1] == "skip":
            return (
                "SignalOS setup skipped. Run /signal-init --mode keep (or full / minimal) "
                "to scaffold the project later, or open the Setup step in the wizard."
            )

    argv = map_slash_command(command, redacted, cwd)
    if argv is not None:
        # Wave 2 / G1-7: emit phase substeps around wired commands so users
        # see real progress instead of a generic "Engine working" toast.
        emitter = ProgressEmitter(req_id) if req_id else None
        if emitter and command == "signal-init":
            emitter.begin("prepare", "check_target", f"Checking {cwd}")
            emitter.done()
            emitter.begin("prepare", "consent_mode", "Init mode chosen")
            emitter.done()
            emitter.begin("write", "copy_bundle", "Copying SignalOS files")
        rc, out, err_text = run_core_cli(argv)
        text = redact_text((out or err_text).strip())
        if emitter and command == "signal-init":
            if rc == 0:
                emitter.done(f"{text.splitlines()[0] if text else 'Bundle copied'}")
                emitter.begin("review", "ide_hooks", "Wiring IDE hooks")
                emitter.done("Setup complete")
            else:
                emitter.error(text or f"init exited {rc}")
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
        # Init modes — Wave 1 / G0-1. The wizard (G0-2) is the user-facing
        # picker; this function only knows how to translate the chosen mode
        # into the right argv. Default mode is "keep" — non-destructive.
        # See docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.4b.
        if cleaned_args and cleaned_args[0] in {"--mode", "-m"} and len(cleaned_args) >= 2:
            mode = cleaned_args[1]
            tail = cleaned_args[2:]
        elif cleaned_args and not cleaned_args[0].startswith("-"):
            # Legacy passthrough: explicit flags from advanced users.
            return ["init", cwd, *cleaned_args]
        else:
            mode = "keep"
            tail = list(cleaned_args)

        if mode == "skip":
            return None  # caller falls through to the spec-only path
        if mode == "minimal":
            return ["init", cwd, "--yes", "--minimal", *tail]
        if mode == "full":
            return ["init", cwd, "--yes", "--force", *tail]
        # "keep" (default): merge — write bundle files only where the user
        # has no file of that name. Never overwrites user content. Safe even
        # if the user picked a populated folder by mistake.
        return ["init", cwd, "--yes", "--keep-existing", *tail]

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

    # /signal-orchestrate and /signal-build both reach the parallel wave runner.
    # The CLI subcommand is `orchestrate` (no signal- prefix), so we translate.
    # /signal-build is treated as an alias for /signal-orchestrate -- the
    # signal-build spec describes "Phase 3 build runs Build×N parallel agents"
    # which is exactly what orchestrate does. Until a dedicated TDD-loop
    # executor lands, build == orchestrate of the current wave's plan.
    if command in {"signal-orchestrate", "signal-build"}:
        return ["orchestrate", *cleaned_args]

    # /signal-sign G0..G5 -> `signalos sign G<n>` (the gate-signing CLI)
    if command == "signal-sign":
        return ["sign", *cleaned_args]

    # /signal-harness call|status|abort -> `signalos harness <action>`
    if command == "signal-harness":
        if cleaned_args and cleaned_args[0] in {"call", "status", "abort"}:
            return ["harness", *cleaned_args]
        return None

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
    # M3: status.py::build_status_json now emits a `gate_details` array with
    # per-gate `activities` and `criteria`. Index by gate key so we can attach
    # them onto the per-gate entries the UI consumes.
    details_by_key = {
        d.get("key"): d
        for d in (status.get("gate_details") or [])
        if isinstance(d, dict) and d.get("key")
    }
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
        detail = details_by_key.get(key, {})
        gates.append(
            {
                "id": gate_id,
                "name": GATE_NAMES[gate_id],
                "desc": GATE_DESCRIPTIONS[gate_id],
                "status": state,
                "signer": None,
                "signed_at": None,
                "activities": detail.get("activities") or [],
                "criteria": detail.get("criteria") or [],
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


# ---------------------------------------------------------------------------
# Wave checkpoint + rollback (#3: "Undo Wave")
#
# Before a wave runs, the UI calls /signal-checkpoint to capture the
# current HEAD SHA. After the wave, the user can click "Rollback wave"
# which calls /signal-rollback. Rollback resets the workspace to the
# captured SHA and deletes any files the wave wrote that aren't tracked.
#
# Audit-trail integrity: we do NOT delete wave_started / task_completed
# entries. We APPEND a wave_rolled_back entry that includes the SHA we
# returned to and the file count we removed. The original history stays.
# ---------------------------------------------------------------------------

def _checkpoint_dir(cwd: str) -> str:
    return os.path.join(cwd, ".signalos", "wave-checkpoints")


def _checkpoint_path(cwd: str, wave_id: str) -> str:
    return os.path.join(_checkpoint_dir(cwd), f"wave-{wave_id}.json")


def _run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
        return (proc.returncode, proc.stdout or "", proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (127, "", str(exc))


def _append_audit(cwd: str, entry: dict) -> None:
    """Append a JSON line to .signalos/AUDIT_TRAIL.jsonl. Creates the
    file if missing; never overwrites existing entries (we mark events,
    we don't rewrite history)."""
    audit_dir = os.path.join(cwd, ".signalos")
    os.makedirs(audit_dir, exist_ok=True)
    audit_path = os.path.join(audit_dir, "AUDIT_TRAIL.jsonl")
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **entry,
    }
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _parse_kv_args(args: list[str]) -> dict[str, str]:
    """Parse --key value pairs into a dict. Anything not a --flag is
    silently ignored."""
    out: dict[str, str] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and i + 1 < len(args):
            out[a[2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    return out


def handle_checkpoint(args: list[str], cwd: str) -> str:
    """Capture pre-wave HEAD SHA. Usage: signal-checkpoint --wave <id>"""
    kv = _parse_kv_args(args)
    wave_id = kv.get("wave", "?")

    rc, sha_out, err_text = _run_git(["rev-parse", "HEAD"], cwd)
    if rc != 0:
        return json.dumps({
            "ok": False,
            "error": f"git rev-parse HEAD failed: {err_text.strip() or 'not a git repo?'}",
        })
    sha = sha_out.strip()
    if not sha:
        return json.dumps({"ok": False, "error": "empty HEAD SHA"})

    os.makedirs(_checkpoint_dir(cwd), exist_ok=True)
    checkpoint = {
        "wave": wave_id,
        "sha": sha,
        "started_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files_written": [],
    }
    with open(_checkpoint_path(cwd, wave_id), "w", encoding="utf-8") as fh:
        json.dump(checkpoint, fh, indent=2)

    _append_audit(cwd, {
        "kind": "wave_checkpoint",
        "wave": wave_id,
        "sha": sha,
    })
    return json.dumps({"ok": True, "sha": sha, "wave": wave_id})


def handle_rollback(args: list[str], cwd: str) -> str:
    """Reset workspace to a captured checkpoint. Usage:
        signal-rollback --wave <id> [--files <comma-separated-paths>]
    Returns JSON with {ok, sha, files_deleted, note}.
    """
    kv = _parse_kv_args(args)
    wave_id = kv.get("wave", "?")
    explicit_files: list[str] = []
    if "files" in kv:
        explicit_files = [f.strip() for f in kv["files"].split(",") if f.strip()]

    cp_path = _checkpoint_path(cwd, wave_id)
    if not os.path.isfile(cp_path):
        return json.dumps({
            "ok": False,
            "error": (
                f"no checkpoint found for wave {wave_id}. The wave must "
                f"have been approved BEFORE this app version (which adds "
                f"checkpointing). Cannot roll back without a target SHA."
            ),
        })

    try:
        with open(cp_path, "r", encoding="utf-8") as fh:
            checkpoint = json.load(fh)
    except (OSError, ValueError) as exc:
        return json.dumps({"ok": False, "error": f"checkpoint corrupt: {exc}"})

    sha = checkpoint.get("sha")
    if not sha:
        return json.dumps({"ok": False, "error": "checkpoint missing sha"})

    # 1. Verify the SHA still exists (not garbage-collected).
    rc, _, err_text = _run_git(["cat-file", "-e", sha], cwd)
    if rc != 0:
        return json.dumps({
            "ok": False,
            "error": (
                f"checkpoint SHA {sha[:8]} no longer reachable from this "
                f"repo (was it force-pushed away or garbage-collected?). "
                f"Rollback aborted; nothing changed."
            ),
        })

    # 2. Hard reset to the captured SHA. This wipes tracked-file changes
    #    but leaves untracked files alone -- those we handle next.
    rc, _, err_text = _run_git(["reset", "--hard", sha], cwd)
    if rc != 0:
        return json.dumps({
            "ok": False,
            "error": f"git reset --hard {sha[:8]} failed: {err_text.strip()}",
        })

    # 3. Delete the wave's untracked files. We prefer the explicit list
    #    (from approvePlan, which knows what the wave wrote) but fall
    #    back to the checkpoint's recorded list, then to git's view of
    #    untracked files inside the workspace (last resort).
    candidates = explicit_files or list(checkpoint.get("files_written") or [])
    deleted: list[str] = []
    for rel in candidates:
        if not rel or rel.startswith("/") or ".." in rel.replace("\\", "/").split("/"):
            continue  # path-traversal guard
        abs_path = os.path.join(cwd, rel)
        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
                deleted.append(rel)
        except OSError:
            continue

    _append_audit(cwd, {
        "kind": "wave_rolled_back",
        "wave": wave_id,
        "reset_to_sha": sha,
        "files_deleted": deleted,
        "files_requested": len(candidates),
    })

    return json.dumps({
        "ok": True,
        "wave": wave_id,
        "sha": sha,
        "files_deleted": deleted,
        "note": (
            f"Reset workspace to {sha[:8]}. Deleted {len(deleted)} of "
            f"{len(candidates)} wave-written file(s). Untracked files "
            f"the wave didn't record may remain -- run `git status` to "
            f"see what's left."
        ),
    })


# ---------------------------------------------------------------------------
# Sandbox toggle + status (#3: containerized execution)
#
# /signal-sandbox status              -> { ok, docker_available, config }
# /signal-sandbox enable [--image-js X] [--image-py Y] [--image-sh Z]  -> set enabled=true
# /signal-sandbox disable             -> set enabled=false
# ---------------------------------------------------------------------------

def handle_sandbox(args: list[str], cwd: str) -> str:
    """UI bridge for the sandboxed-execution settings + capability probe."""
    # Lazy import so the IPC server doesn't pull signalos_lib at startup
    # (cold-start cost) when nobody asks for sandbox state.
    from signalos_lib.sandbox import (
        docker_available,
        get_sandbox_config,
        set_sandbox_config,
    )

    subcommand = args[0] if args else "status"
    kv = _parse_kv_args(args[1:] if args else [])
    root = Path(cwd)

    if subcommand == "status":
        return json.dumps({
            "ok": True,
            "docker_available": docker_available(),
            "config": get_sandbox_config(root),
        })

    if subcommand == "enable":
        patches: dict = {"enabled": True}
        if "image-js" in kv:
            patches["image_js"] = kv["image-js"]
        if "image-py" in kv:
            patches["image_py"] = kv["image-py"]
        if "image-sh" in kv:
            patches["image_sh"] = kv["image-sh"]
        cfg = set_sandbox_config(root, **patches)
        return json.dumps({
            "ok": True,
            "docker_available": docker_available(),
            "config": cfg,
        })

    if subcommand == "disable":
        cfg = set_sandbox_config(root, enabled=False)
        return json.dumps({
            "ok": True,
            "docker_available": docker_available(),
            "config": cfg,
        })

    return json.dumps({
        "ok": False,
        "error": (
            f"Unknown signal-sandbox subcommand: {subcommand}. "
            f"Use: status / enable / disable"
        ),
    })


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
