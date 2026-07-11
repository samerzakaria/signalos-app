#!/usr/bin/env python3
"""Newline-delimited JSON IPC bridge for the SignalOS desktop app."""

from __future__ import annotations

import json
import sys

import datetime
import os
import queue
import shlex
import subprocess
import threading
import traceback
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from functools import lru_cache
from io import StringIO
from typing import Any

from signalos_secret_guard import (
    redact_arg_list,
    redact_response,
    redact_text,
    scan_secret_files,
)
from signalos_attachments import analyze_payload


# All stdout writes (init line, progress events, agent events, and command
# responses) must be serialized. With the Claim-11 cancel fast-path (below),
# two threads write to stdout: the worker thread emits progress/agent/response
# lines while inside a long-running handle(), and the stdin-reader thread emits
# the agent:cancel acknowledgement. Without a lock their writes can interleave
# and corrupt a newline-delimited JSON line that the Rust multiplexer parses.
_STDOUT_LOCK = threading.Lock()


def _emit_line(payload: Any) -> None:
    """Write one NDJSON message to stdout atomically under _STDOUT_LOCK.

    Accepts either a pre-serialized string or a JSON-serializable object.
    """
    text = payload if isinstance(payload, str) else json.dumps(payload)
    with _STDOUT_LOCK:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


# Commands the IPC server routes directly. The capability handshake on the
# Rust side (sidecar.rs) probes `capabilities` at startup and refuses to trust
# a bundled binary that does not report agent:deliver in this list — the exact
# staleness signature of the shipped 0.0.9 sidecar.
AGENT_COMMANDS = (
    "agent:run",
    "agent:deliver",
    "agent:launch",
    "agent:verdict",
    "agent:cancel",
    "agent:resume",
    "agent:reopen-gate",
)

ROUTED_COMMANDS = (
    "help",
    "ping",
    "capabilities",
    "phase:contract",
    "state:wave",
    "state:gates",
    "gate:sign",
    "brain:search",
    "brain:add",
    "audit:list",
    "audit:append",
    "audit:replay-timeline",
    "cost:summary",
    "policy:get",
    "policy:set",
    "security:secrets",
    "attachment:analyze",
    "wave:begin",
    "wave:reply",
    "wave:scope-drift-resolve",
    "wave:translate-external",
    "wave:violation-request",
    "wave:violation-confirm",
    "wave:g5-handoff",
    "project:list",
    "project:create",
    "project:switch",
    "share:export",
    "brownfield:audit",
    "competitor:analyze",
    "voice:transcribe",
    *AGENT_COMMANDS,
)


def _capabilities_payload() -> dict:
    """Version + supported-command list for the startup capability handshake."""
    return {
        "version": _app_version(),
        "protocol": 1,
        "commands": list(ROUTED_COMMANDS),
    }


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
# emitter walks substeps in order, marking each pending -> running -> done.
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
        _emit_line(payload)


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
    "deliver": [
        ("intent",     ["extract", "questions", "scope"]),
        ("scaffold",   ["create", "postflight"]),
        ("design",     ["select_system", "preview"]),
        ("acceptance", ["matrix"]),
        ("generation", ["manifest", "packet"]),
        ("validation", ["run_checks", "repair"]),
        ("security",   ["scan"]),
        ("proof",      ["runtime", "ux"]),
        ("deploy",     ["decision", "package"]),
        ("closeout",   ["handoff"]),
    ],
}


@lru_cache(maxsize=1)
def _app_version() -> str:
    """Return the desktop app version without hardcoding release numbers."""
    for key in ("SIGNALOS_APP_VERSION", "SIGNALOS_VERSION"):
        value = os.environ.get(key, "").strip()
        if value:
            return value

    # Frozen (PyInstaller) build: __file__ resolves inside the throwaway
    # _MEIPASS extraction dir and cwd may be a bare workspace, so neither walk
    # finds a manifest -> "unknown" whenever the Tauri host didn't inject
    # SIGNALOS_APP_VERSION (e.g. the CLI/benchmark path or a bare spawn).
    # package.json is bundled at the _MEIPASS root (see bundle-sidecar.*), so
    # consult that dir too.
    search_starts = [Path(__file__).resolve(), Path.cwd().resolve()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        search_starts.append(Path(meipass).resolve())
    for start in search_starts:
        for base in (start, *start.parents):
            package_path = base / "package.json"
            if package_path.is_file():
                try:
                    version = json.loads(package_path.read_text(encoding="utf-8")).get("version")
                except (OSError, ValueError, TypeError):
                    version = None
                if isinstance(version, str) and version.strip():
                    return version.strip()

            cargo_path = base / "src-tauri" / "Cargo.toml"
            if cargo_path.is_file():
                try:
                    for line in cargo_path.read_text(encoding="utf-8").splitlines():
                        stripped = line.strip()
                        if stripped.startswith("version") and "=" in stripped:
                            value = stripped.split("=", 1)[1].strip().strip('"')
                            if value:
                                return value
                except OSError:
                    pass

    return "unknown"

def handle(req: dict) -> dict:
    req_id = req.get("id", "unknown")
    command = req.get("command", "")
    raw_args = req.get("args", [])
    raw_arg_list = raw_args if isinstance(raw_args, list) else [str(raw_args)]
    # voice:transcribe carries megabytes of opaque base64 audio — running the
    # secret-shape regex fleet over it is pointless (audio bytes are not text
    # that can leak an env assignment) and expensive. attachment:analyze does
    # its own scanning downstream.
    _redaction_exempt = command in ("attachment:analyze", "voice:transcribe")
    args = raw_arg_list if _redaction_exempt else redact_arg_list(raw_arg_list)
    cwd = req.get("cwd")

    if cwd and os.path.isdir(cwd):
        os.chdir(cwd)

    # WAVE-ENGINE-DESIGN section3.2 / Task #19 - multi-project resolution.
    # Precedence: an explicit `project_id` in the request always wins;
    # otherwise the workspace's active project (from .signalos/projects.json
    # via project:create / project:switch) applies; a workspace without a
    # registry resolves to "default" (today's workspace-root layout).
    # Resolved AFTER the chdir so the registry of the request's workspace
    # (not the previous request's) decides.
    explicit_project = req.get("project_id")
    if explicit_project:
        project_id = str(explicit_project)
    else:
        from signalos_lib.projects import get_active_project

        project_id = get_active_project(Path(os.getcwd()))

    # Phase 3 Stream A: agent:* commands take a SINGLE JSON object argument,
    # not the legacy redacted string list. Route them off the raw payload so
    # the structured object (prompt, run_id, etc.) survives intact.
    if command in AGENT_COMMANDS:
        try:
            return route_agent(req_id, command, raw_args, project_id=project_id)
        except Exception as exc:
            return {
                "id": req_id,
                "ok": False,
                "error": redact_text(f"{type(exc).__name__}: {exc}"),
                "trace": redact_text(traceback.format_exc()),
            }

    try:
        return route(req_id, command, args, project_id=project_id)
    except Exception as exc:
        return {
            "id": req_id,
            "ok": False,
            "error": redact_text(f"{type(exc).__name__}: {exc}"),
            "trace": redact_text(traceback.format_exc()),
        }


def route(req_id: str, command: str, args: list[str], project_id: str = "default") -> dict:
    terminal_alias = command.strip().lower()
    if terminal_alias in {"help", "signalos help"}:
        return ok(req_id, output=terminal_help_text())

    if terminal_alias in {"signalos status", "/signal-status"}:
        return dispatch_cli_response(req_id, "signal-status", args, project_id=project_id)

    if terminal_alias in {"signalos check", "/signal-release-readiness"}:
        return dispatch_cli_response(req_id, "signal-release-readiness", args, project_id=project_id)

    if terminal_alias in {"signalos gates", "/state:gates"}:
        return ok(req_id, output=json.dumps(get_gate_states(project_id=project_id), indent=2))

    signalos_alias = parse_signalos_alias(command)
    if signalos_alias and (
        is_core_cli_command(signalos_alias[0])
        or map_slash_command(signalos_alias[0], [*signalos_alias[1:], *args], os.getcwd()) is not None
    ):
        cli_command = signalos_alias[0]
        inline_args = signalos_alias[1:]
        return dispatch_cli_response(
            req_id,
            cli_command,
            [*inline_args, *args],
            project_id=project_id,
        )

    if terminal_alias == "git status":
        return ok(req_id, output=git_status_text(Path.cwd()))

    if terminal_alias == "npm run dev":
        return ok(
            req_id,
            output=(
                "Use the Preview tab to start the product dev server. "
                "SignalOS runs npm through the governed preview runner, not this diagnostic terminal."
            ),
        )

    normalized_command = command.lstrip("/")
    if is_dispatchable_cli_command(normalized_command, args, os.getcwd()):
        return dispatch_cli_response(req_id, normalized_command, args, project_id=project_id)

    if command.startswith("/") or normalized_command.startswith(("signal-", "signalos-")):
        return err(req_id, f"Unknown SignalOS command: /{normalized_command}")

    if command == "state:wave":
        return ok(req_id, data=get_wave_state(project_id=project_id))

    if command == "state:gates":
        return ok(req_id, data=get_gate_states(project_id=project_id))

    if command == "gate:sign":
        if len(args) < 2:
            return err(req_id, "gate:sign requires [gate_id, signer]")
        # #17 Edit 3.3: arg layout is [gate_id, signer, role?, test_refs_csv?].
        # Rust forwards the real identity role as args[2]. To stay backward-
        # compatible with the historical [gate_id, signer, test_refs_csv] layout,
        # args[2] is treated as a role ONLY when it is a known role; otherwise it
        # is the legacy test_refs CSV. test_refs may also come explicitly at args[3].
        #
        # Wave 5 / G4: G1 (Belief) sign still requires ≥1 test reference.
        from signalos_lib.sign import VALID_ROLES

        gate_id = int(args[0])
        signer = args[1]
        role: str | None = None
        test_refs: list[str] = []
        if len(args) >= 3:
            third = str(args[2])
            if third in VALID_ROLES:
                role = third
            elif third.strip():
                test_refs = [t for t in third.split(",") if t.strip()]
        if len(args) >= 4 and str(args[3]).strip():
            test_refs = [t for t in str(args[3]).split(",") if t.strip()]
        if gate_id == 1 and not test_refs:
            return err(
                req_id,
                "G1 Belief sign requires at least one test reference. Pass test files or plan ids as the third argument.",
            )
        return ok(req_id, data=sign_gate(gate_id, signer, role))

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
        from signalos_lib.commands.cost import build_cost_report

        wave = args[0] if args else None
        budget = args[1] if len(args) > 1 else os.environ.get("SIGNALOS_AI_WAVE_BUDGET_USD")
        return ok(
            req_id,
            data=build_cost_report(
                Path.cwd(),
                wave=wave,
                budget_usd=budget,
                write_evidence=True,
            ),
        )

    if command == "policy:get":
        from signalos_lib.product.policy import load_policy

        return ok(req_id, data=load_policy(Path.cwd()).to_dict())

    if command == "policy:set":
        from signalos_lib.product.policy import FounderPolicy, save_policy

        try:
            payload = json.loads(args[0]) if args else {}
        except (TypeError, ValueError, IndexError) as exc:
            return err(req_id, f"policy:set payload was not valid JSON: {exc}")
        if not isinstance(payload, dict):
            return err(req_id, "policy:set payload must be a JSON object")
        policy = FounderPolicy(
            gate_mode=str(payload.get("gate_mode", "standard")),
            research_depth=str(payload.get("research_depth", "standard")),
            budget_cap_usd=float(payload.get("budget_cap_usd", 0.0) or 0.0),
            standards_profile=str(payload.get("standards_profile", "default")),
            allowed_deploy_targets=list(payload.get("allowed_deploy_targets", []) or []),
        )
        try:
            save_policy(Path.cwd(), policy)
        except ValueError as exc:
            return err(req_id, str(exc))
        return ok(req_id, data=policy.to_dict())

    if command == "security:secrets":
        return ok(req_id, data=scan_secret_files(os.getcwd()))

    if command == "attachment:analyze":
        payload_json = args[0] if args else "[]"
        return ok(req_id, data=analyze_payload(payload_json))

    if command == "ping":
        return ok(req_id, data={"pong": True, "version": _app_version()})

    # Capability handshake (Claim 1): report the version AND the routed
    # command list so the desktop host can verify a freshly-spawned sidecar
    # supports agent:deliver before trusting it. A stale binary that predates
    # this command answers "Unknown command: capabilities" and is flagged
    # incompatible by the Rust client.
    if command == "capabilities":
        return ok(req_id, data=_capabilities_payload())

    # Wave 2 / G1-7: phase contract lookup. The UI calls this to know
    # how many substeps a given command will emit so the progress strip
    # can render all rows up front in `pending` state.
    if command == "phase:contract":
        name = (args[0] if args else "").strip()
        contract = PHASE_CONTRACTS.get(name)
        if contract is None:
            return err(req_id, f"Unknown phase contract: {name}")
        return ok(req_id, data={"name": name, "phases": contract})

    # Wave engine handlers (M-W3..M-W7)
    # The engine is reconstructed per-request from disk inspection (the
    # design's persistence model for v1 - see WAVE-ENGINE-DESIGN section3.1).
    # Each handler builds a fresh WaveEngine, runs the requested action,
    # and returns the structured result for the chat layer to render.

    if command == "wave:begin":
        if not args:
            return err(req_id, "wave:begin requires [user_request]")
        return ok(req_id, data=wave_begin(args[0], project_id=project_id))

    if command == "wave:reply":
        if len(args) < 2:
            return err(req_id, "wave:reply requires [user_reply, current_gate]")
        return ok(req_id, data=wave_reply(
            user_reply=args[0],
            current_gate=args[1],
            project_id=project_id,
        ))

    if command == "wave:scope-drift-resolve":
        if len(args) < 2:
            return err(req_id, "wave:scope-drift-resolve requires [user_request, choice]")
        return ok(req_id, data=wave_scope_drift_resolve(
            user_request=args[0],
            choice=args[1],
            project_id=project_id,
        ))

    if command == "wave:translate-external":
        if not args:
            return err(req_id, "wave:translate-external requires [artifact_path_or_url, optional gate]")
        gate = args[1] if len(args) > 1 else None
        return ok(req_id, data=wave_translate_external(
            artifact=args[0],
            gate=gate,
            project_id=project_id,
        ))

    if command == "wave:violation-request":
        if not args:
            return err(req_id, "wave:violation-request requires [violation_payload_json]")
        try:
            payload = json.loads(args[0])
        except (TypeError, ValueError) as exc:
            return err(req_id, f"wave:violation-request payload was not valid JSON: {exc}")
        return ok(req_id, data=wave_violation_request(payload, project_id=project_id))

    if command == "wave:violation-confirm":
        if not args:
            return err(req_id, "wave:violation-confirm requires [confirm_payload_json]")
        try:
            payload = json.loads(args[0])
        except (TypeError, ValueError) as exc:
            return err(req_id, f"wave:violation-confirm payload was not valid JSON: {exc}")
        return ok(req_id, data=wave_violation_confirm(payload, project_id=project_id))

    if command == "wave:g5-handoff":
        if not args:
            return err(req_id, "wave:g5-handoff requires [wave_id, optional summary_json]")
        summary: dict = {}
        if len(args) > 1:
            try:
                summary = json.loads(args[1])
            except (TypeError, ValueError) as exc:
                return err(req_id, f"wave:g5-handoff summary was not valid JSON: {exc}")
        return ok(req_id, data=wave_g5_handoff(
            wave_id=args[0], summary=summary, project_id=project_id,
        ))

    # Capability wiring: audit replay, share export, brownfield governance,
    # competitor analysis. Contract shapes live in `data` (the frontend reads
    # data.status / data.frames / ...); domain failures the founder can act on
    # are reported as {"status": "error", "error": ...} inside data.

    # Multi-project registry (Task #19). list/create/switch operate on
    # .signalos/projects.json in the current workspace; create switches to
    # the new project; create/switch refuse while a delivery is running.

    if command == "project:list":
        return project_list(req_id)

    if command == "project:create":
        return project_create(req_id, args)

    if command == "project:switch":
        return project_switch(req_id, args)

    if command == "audit:replay-timeline":
        return audit_replay_timeline(req_id, args)

    if command == "share:export":
        return share_export(req_id)

    if command == "brownfield:audit":
        return brownfield_audit(req_id, args)

    if command == "competitor:analyze":
        return competitor_analyze(req_id, args)

    if command == "voice:transcribe":
        return voice_transcribe(req_id, args)

    return err(req_id, f"Unknown command: {command}")


def route_agent(req_id: str, command: str, raw_args: Any, project_id: str = "default") -> dict:
    """Dispatch Phase 3 Stream A agent:* commands.

    These bridge the frontend chat surface to product/agent_loop.AgentLoop.
    Each loop event is wrapped in an agent-event envelope and printed on
    stdout so the Rust multiplexer (Stream B) can route it to the Tauri
    "agent:event" channel. The final SidecarResponse carries the run
    summary. raw_args is a SINGLE JSON object (not the legacy string list).
    """
    if command == "agent:run":
        return agent_run(req_id, raw_args, project_id=project_id)
    if command == "agent:deliver":
        return agent_deliver(req_id, raw_args, project_id=project_id)
    if command == "agent:launch":
        return agent_launch(req_id, raw_args, project_id=project_id)
    if command == "agent:verdict":
        return agent_verdict(req_id, raw_args, project_id=project_id)
    if command == "agent:cancel":
        return agent_cancel(req_id, raw_args, project_id=project_id)
    if command == "agent:resume":
        return agent_resume(req_id, raw_args, project_id=project_id)
    if command == "agent:reopen-gate":
        return agent_reopen_gate(req_id, raw_args, project_id=project_id)
    return err(req_id, f"Unknown command: {command}")


# ---------------------------------------------------------------------------
# Phase 3 Stream A: agent loop bridge
# ---------------------------------------------------------------------------
#
# Injection seam (INV-6, deterministic tests): tests set the module-level
# hooks `_AGENT_ADAPTER_FACTORY` and/or `_AGENT_ENFORCEMENT_FACTORY` so the
# handler builds a deterministic AgentTestProvider + StaticEnforcementProvider
# instead of constructing a live LiteLLM-backed adapter. In production both
# hooks are None and the handler builds the real provider adapter.
#
#   _AGENT_ADAPTER_FACTORY(model: str, provider: str | None = None) -> ProviderAdapter
#   _AGENT_ENFORCEMENT_FACTORY() -> EnforcementProvider
#
# The cancellation registry maps run_id -> bool. agent:cancel sets the flag;
# the AgentLoop's cancel_check polls it between tool calls.

_AGENT_ADAPTER_FACTORY = None       # set by tests; signature: (model: str, provider: str | None = None) -> ProviderAdapter
_AGENT_ENFORCEMENT_FACTORY = None   # set by tests; signature: () -> EnforcementProvider
_AGENT_CANCEL_FLAGS: dict[str, bool] = {}
# Active governed deliveries (gate walk), keyed by run_id. The sidecar
# is long-lived so the orchestrator survives between agent:deliver and
# the later agent:verdict calls that advance the walk.
_ACTIVE_DELIVERIES: dict = {}
_DELIVERY_SIGN_FN = None  # test seam: (root,gate,signer,role,verdict,conditions)->list


def _coerce_agent_args(args: Any) -> dict:
    """agent:* commands take a single JSON object. The IPC transport may hand
    us either the already-parsed dict, a one-element list wrapping a JSON
    string, or a bare JSON string. Normalize all three to a dict."""
    payload = args
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if isinstance(payload, str):
        payload = json.loads(payload) if payload.strip() else {}
    if not isinstance(payload, dict):
        raise ValueError("agent command requires a single JSON object argument")
    return payload


def _build_agent_adapter(model: str, provider: str | None = None):
    """Construct the ProviderAdapter. Honors the test injection seam."""
    if _AGENT_ADAPTER_FACTORY is not None:
        try:
            return _AGENT_ADAPTER_FACTORY(model, provider)
        except TypeError:
            return _AGENT_ADAPTER_FACTORY(model)
    from signalos_lib.product.provider_adapter import ProviderAdapter
    return ProviderAdapter(model=model, provider_name=provider)


def _agent_provider_and_model(payload: dict, command: str) -> tuple[str | None, str]:
    """Resolve the provider/model for live agent commands.

    The desktop app owns provider selection. The sidecar must not silently
    choose Anthropic/OpenAI/etc. when the payload is missing a model; doing so
    calls the wrong account and makes billing/errors misleading.
    """
    provider = str(payload.get("provider") or "").strip().lower() or None
    model = str(payload.get("model") or "").strip()
    if not model:
        raise ValueError(
            f"{command} requires the selected AI model. Pick a provider and model in Settings, then retry."
        )
    return provider, model


def _build_agent_enforcement():
    """Construct the EnforcementProvider. Honors the test injection seam.

    Production (#15): returns a FileEnforcementProvider, which reads the
    Rust-persisted `.signalos/enforcement.json` snapshot so the sidecar's agent
    loop enforces the exact rule modes the user toggled in the app. The provider
    re-applies the core-invariant floor on read (a hand-edited file can never
    disable a core rule). The test seam wins first for deterministic CI."""
    if _AGENT_ENFORCEMENT_FACTORY is not None:
        return _AGENT_ENFORCEMENT_FACTORY()
    from signalos_lib.product.enforcement_state import FileEnforcementProvider

    return FileEnforcementProvider()


def _agent_emit(run_id: str):
    """Build the emit callback for an AgentLoop run.

    Each loop event dict is wrapped in the agent-event envelope and printed
    as one newline-delimited JSON object on stdout (flush=True), mirroring
    the ProgressEmitter._emit pattern. Rust routes kind=="agent-event"
    lines to the Tauri "agent:event" channel (Stream B)."""
    def emit_cb(ev: dict) -> None:
        envelope = {"kind": "agent-event", "run_id": run_id, "type": ev.get("type", "")}
        for k, v in ev.items():
            if k == "type":
                continue
            # Don't let a loop dict's own run_id shadow the authoritative one.
            if k == "run_id":
                continue
            envelope[k] = v
        _emit_line(redact_response(envelope))
    return emit_cb


def _agent_run_dir(repo_root: Path, run_id: str) -> Path:
    return repo_root / ".signalos" / "agent-runs" / run_id


def _agent_cancel_marker(repo_root: Path, run_id: str) -> Path:
    return _agent_run_dir(repo_root, run_id) / "cancel-requested.json"


def _write_agent_cancel_marker(repo_root: Path, run_id: str) -> None:
    marker = _agent_cancel_marker(repo_root, run_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({
            "run_id": run_id,
            "cancel_requested": True,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }, indent=2) + "\n",
        encoding="utf-8",
    )


def _clear_agent_cancel_marker(repo_root: Path, run_id: str) -> None:
    try:
        _agent_cancel_marker(repo_root, run_id).unlink(missing_ok=True)
    except OSError:
        pass


def _agent_cancel_requested(repo_root: Path, run_id: str) -> bool:
    return bool(_AGENT_CANCEL_FLAGS.get(run_id)) or _agent_cancel_marker(repo_root, run_id).is_file()


def agent_deliver(req_id: str, args: Any, project_id: str = "default") -> dict:
    """agent:deliver -> start a governed delivery (the G0->G5 gate walk).

    Builds a GateOrchestrator, runs the current gate, and pauses with a `gate`
    agent-event for user review. The returned run_id is used by later
    agent:verdict calls to advance the walk. INV-3 signing happens inside the
    orchestrator via sign.py."""
    try:
        payload = _coerce_agent_args(args)
    except (TypeError, ValueError) as exc:
        return err(req_id, f"agent:deliver args invalid: {exc}")
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return err(req_id, "agent:deliver requires a non-empty 'prompt'")
    run_id = str(payload.get("run_id") or "").strip() or ("delivery-" + __import__("uuid").uuid4().hex[:8])
    try:
        provider, model = _agent_provider_and_model(payload, "agent:deliver")
    except ValueError as exc:
        _agent_emit(run_id)({"type": "error", "error": str(exc)})
        return err(req_id, str(exc))
    repo_root = Path(os.getcwd())
    # Brownfield auto-detect: pre-existing code with no governance state gets
    # a system event + audit entry BEFORE the orchestrator starts. Best-effort;
    # never blocks or fails the delivery.
    _maybe_emit_brownfield_notice(repo_root, _agent_emit(run_id))
    try:
        adapter = _build_agent_adapter(model, provider)
    except Exception as exc:  # INV-4: surface
        _agent_emit(run_id)({"type": "error", "error": f"provider init failed: {exc}"})
        return err(req_id, f"agent:deliver provider init failed: {type(exc).__name__}: {exc}")
    enforcement = _build_agent_enforcement()
    from signalos_lib.product.gate_orchestrator import GateOrchestrator
    from signalos_lib.product.identity import format_signer, load_identity
    orch = GateOrchestrator(
        repo_root, adapter, _agent_emit(run_id),
        enforcement_provider=enforcement, sign_fn=_DELIVERY_SIGN_FN,
        prompt=prompt, run_id=run_id,
        signer=format_signer(load_identity(repo_root)),
        # §3.2: bind the request's project namespace into the delivery so
        # gate-artifact generation AND signing land under
        # projects.project_governance_dir(root, project_id).
        project_id=project_id,
    )
    _ACTIVE_DELIVERIES[run_id] = orch
    try:
        res = orch.start()
    except Exception as exc:  # INV-4: surface as agent-event AND non-ok
        _agent_emit(run_id)({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        return err(req_id, f"agent:deliver failed: {type(exc).__name__}: {exc}")
    return ok(req_id, output=json.dumps(res), data=res)


def agent_launch(req_id: str, args: Any, project_id: str = "default") -> dict:
    """agent:launch -> 3.4 (C-bridge): start a launch-surface mini-build
    (landing page) that re-enters the SAME G0->G5 gate loop as agent:deliver,
    isolated in its own repo_root under the current product's .signalos/,
    linked back to the parent's closeout. Requires the current workspace to
    already have a real closeout -- nothing to launch otherwise."""
    try:
        payload = _coerce_agent_args(args)
    except (TypeError, ValueError) as exc:
        return err(req_id, f"agent:launch args invalid: {exc}")
    try:
        provider, model = _agent_provider_and_model(payload, "agent:launch")
    except ValueError as exc:
        return err(req_id, str(exc))
    prompt = str(payload.get("prompt") or "").strip() or None
    repo_root = Path(os.getcwd())

    def orchestrator_factory(child_repo_root: Path, child_prompt: str, run_id: str):
        adapter = _build_agent_adapter(model, provider)
        enforcement = _build_agent_enforcement()
        from signalos_lib.product.gate_orchestrator import GateOrchestrator
        from signalos_lib.product.identity import format_signer, load_identity
        # identity was already carried into child_repo_root by
        # start_launch_build (copy_identity_to) before this factory runs,
        # so this reads the SAME founder identity as the parent product.
        orch = GateOrchestrator(
            child_repo_root, adapter, _agent_emit(run_id),
            enforcement_provider=enforcement, sign_fn=_DELIVERY_SIGN_FN,
            prompt=child_prompt, run_id=run_id,
            signer=format_signer(load_identity(child_repo_root)),
        )
        _ACTIVE_DELIVERIES[run_id] = orch
        return orch

    from signalos_lib.product.launch import start_launch_build
    try:
        result = start_launch_build(repo_root, orchestrator_factory, prompt=prompt)
    except ValueError as exc:
        return err(req_id, str(exc))
    except Exception as exc:  # INV-4: surface
        return err(req_id, f"agent:launch failed: {type(exc).__name__}: {exc}")
    return ok(req_id, output=json.dumps(result["gate_result"]), data=result)


def agent_run(req_id: str, args: Any, project_id: str = "default") -> dict:
    """agent:run -> construct an AgentLoop, stream agent-event lines, return
    a summary SidecarResponse {run_id, status, tool_calls_made}."""
    try:
        payload = _coerce_agent_args(args)
    except (TypeError, ValueError) as exc:
        return err(req_id, f"agent:run args invalid: {exc}")

    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return err(req_id, "agent:run requires a non-empty 'prompt'")
    system_prompt = str(payload.get("system_prompt") or "You are SignalOS, a governed build agent.")
    run_id = str(payload.get("run_id") or "").strip() or None
    try:
        provider, model = _agent_provider_and_model(payload, "agent:run")
    except ValueError as exc:
        run_id_for_err = run_id or "agent-unstarted"
        _agent_emit(run_id_for_err)({"type": "error", "error": str(exc)})
        return err(req_id, str(exc))

    from signalos_lib.product.agent_loop import AgentLoop

    repo_root = Path(os.getcwd())
    try:
        adapter = _build_agent_adapter(model, provider)
    except Exception as exc:  # INV-4: surface, do not swallow
        run_id_for_err = run_id or "agent-unstarted"
        _agent_emit(run_id_for_err)({"type": "error", "error": f"provider init failed: {exc}"})
        return err(req_id, f"agent:run provider init failed: {type(exc).__name__}: {exc}")

    enforcement = _build_agent_enforcement()
    loop = AgentLoop(
        adapter=adapter,
        repo_root=repo_root,
        enforcement_provider=enforcement,
        run_id=run_id,
        emit=None,  # replaced below once run_id is finalized
        cancel_check=None,
        execution_context="conversation",
    )
    # AgentLoop assigns its own run_id when None was passed; bind emit + cancel
    # to that final id so envelopes and cancellation share one key.
    final_run_id = loop.run_id
    _clear_agent_cancel_marker(repo_root, final_run_id)
    _AGENT_CANCEL_FLAGS.setdefault(final_run_id, False)
    loop._emit = _agent_emit(final_run_id)
    loop._cancel_check = lambda rid=final_run_id, root=repo_root: _agent_cancel_requested(root, rid)

    try:
        result = loop.run(system_prompt=system_prompt, user_message=prompt)
    except Exception as exc:  # INV-4: surface as agent-event AND non-ok response
        loop._emit({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        return err(req_id, f"agent:run failed: {type(exc).__name__}: {exc}")
    finally:
        _AGENT_CANCEL_FLAGS.pop(final_run_id, None)

    summary = {
        "run_id": result.run_id,
        "status": result.status,
        "tool_calls_made": result.tool_calls_made,
    }
    if result.status == "error":
        # INV-4: a failed run is a non-ok response carrying the run summary.
        return {
            "id": req_id,
            "ok": False,
            "error": redact_text(result.error or "agent run failed"),
            "data": redact_response(summary),
        }
    return ok(req_id, output=json.dumps(summary), data=summary)


def agent_verdict(req_id: str, args: Any, project_id: str = "default") -> dict:
    """agent:verdict -> validate + record a gate verdict via gate_review.

    INV-3: verdict handling routes through gate_review (classify_review +
    handle_request_changes / handle_rejection / record_review_event). We
    NEVER write gate signatures here."""
    try:
        payload = _coerce_agent_args(args)
    except (TypeError, ValueError) as exc:
        return err(req_id, f"agent:verdict args invalid: {exc}")

    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return err(req_id, "agent:verdict requires 'run_id'")
    raw_verdict = str(payload.get("verdict") or "").strip()
    if not raw_verdict:
        return err(req_id, "agent:verdict requires 'verdict'")
    feedback = str(payload.get("feedback") or "")

    # Active governed delivery? Drive the gate walk (apply_verdict signs via
    # sign.py inside the orchestrator and advances to the next gate).
    _orch = _ACTIVE_DELIVERIES.get(run_id)
    if _orch is not None:
        from signalos_lib.product import gate_review as _gr
        _known = {"approve", "approve-with-conditions", "request-changes", "reject", "waive"}
        _v = raw_verdict if raw_verdict in _known else _gr.classify_review(raw_verdict or feedback)["verdict"]
        try:
            _result = _orch.apply_verdict(_v, feedback)
        except Exception as exc:  # INV-4: surface
            return err(req_id, f"agent:verdict (delivery) failed: {type(exc).__name__}: {exc}")
        if getattr(_orch.state, "status", "") in ("complete", "stopped"):
            _ACTIVE_DELIVERIES.pop(run_id, None)
        return ok(req_id, output=json.dumps(_result), data=_result)

    from signalos_lib.product import gate_review

    # Normalize the verdict through classify_review so free-text replies
    # ("looks good", "no, change X") and explicit verdict tokens both resolve
    # to the canonical taxonomy.
    classification = gate_review.classify_review(raw_verdict if raw_verdict else feedback)
    verdict = classification["verdict"]
    repo_root = Path(os.getcwd())
    # gate_id for the run-scoped review is the run_id (review packets are
    # filed under .signalos/product/reviews/<gate_id>/...).
    gate_id = str(payload.get("gate_id") or run_id)

    outcome: dict = {
        "run_id": run_id,
        "verdict": verdict,
        "confidence": classification.get("confidence"),
        "specific_items": classification.get("specific_items", []),
    }
    try:
        if verdict in ("request-changes",):
            # The rework cycle must survive across IPC calls: the review
            # packets on disk are the persisted counter (latest_review_cycle),
            # so repeated request-changes verdicts increment toward the shared
            # gate rework budget instead of restarting at cycle 1 every call.
            # When the budget is exhausted handle_request_changes refuses with
            # status "max_cycles_reached" (the standalone mirror of the
            # orchestrator's "max-rework").
            handled = gate_review.handle_request_changes(
                repo_root=repo_root,
                gate_id=gate_id,
                feedback=feedback or classification.get("feedback", ""),
                specific_items=classification.get("specific_items", []),
                cycle=gate_review.latest_review_cycle(
                    repo_root, gate_id, packet_type="rework"),
            )
            outcome["handled"] = handled
        elif verdict == "reject":
            # Same persistence for rejections: bounded by max_rejections
            # across IPC calls via the regenerate packets already on disk.
            handled = gate_review.handle_rejection(
                repo_root=repo_root,
                gate_id=gate_id,
                reason=feedback or classification.get("feedback", ""),
                rejection_count=gate_review.latest_review_cycle(
                    repo_root, gate_id, packet_type="regenerate"),
            )
            outcome["handled"] = handled
        else:
            # approve / approve-with-conditions / waive: record the event only.
            gate_review.record_review_event(
                repo_root=repo_root,
                gate_id=gate_id,
                verdict=verdict.upper(),
                feedback=feedback or classification.get("feedback", ""),
                cycle=0,
            )
            outcome["handled"] = {"status": "recorded"}
    except Exception as exc:  # INV-4
        return err(req_id, f"agent:verdict failed: {type(exc).__name__}: {exc}")

    return ok(req_id, output=json.dumps({"verdict": verdict}), data=outcome)


def agent_reopen_gate(req_id: str, args: Any, project_id: str = "default") -> dict:
    """agent:reopen-gate -> reopen a previously signed gate of a delivery.

    Args (single JSON object): {run_id, gate, reason, name?, role?}.

    Routed like agent:verdict: an active delivery in _ACTIVE_DELIVERIES is
    mutated in place. A delivery that is no longer in memory (sidecar restart,
    or it completed and was evicted) is loaded one-shot from its persisted
    delivery.json - reopening never runs the gate agent, so no provider/model
    is needed - and the mutated state is persisted back to disk. If neither an
    active nor a persisted delivery exists, this returns a clear error.

    A successful reopen removes the target gate's signature plus every later
    signed gate (and un-waives later waived gates), all audited; threads the
    reason into the gate's feedback; sets current_gate back to the reopened
    gate; and persists status="reopened". It emits `gate_reopened` +
    `system` agent-events but does NOT re-run the gate agent.

    What the frontend should do next (mirrors how apply_verdict's flow
    continues after a rework):
      - send agent:resume {run_id, provider, model} to re-emit the reopened
        gate's checkpoint card and wait for a verdict (same path as a
        sidecar-restart resume); or
      - send agent:verdict {run_id, verdict: "request-changes", feedback}
        to immediately re-run the reopened gate's agent - the reopen reason
        (and any new feedback) is threaded into the rework message.
    """
    try:
        payload = _coerce_agent_args(args)
    except (TypeError, ValueError) as exc:
        return err(req_id, f"agent:reopen-gate args invalid: {exc}")
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return err(req_id, "agent:reopen-gate requires 'run_id'")
    gate = str(payload.get("gate") or "").strip()
    if not gate:
        return err(req_id, "agent:reopen-gate requires 'gate'")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        return err(req_id, "agent:reopen-gate requires a non-empty 'reason'")
    name = str(payload.get("name") or "").strip()
    role = str(payload.get("role") or "").strip()

    repo_root = Path(os.getcwd())
    orch = _ACTIVE_DELIVERIES.get(run_id)
    if orch is None:
        delivery_path = _agent_run_dir(repo_root, run_id) / "delivery.json"
        if not delivery_path.is_file():
            return err(req_id, f"agent:reopen-gate: no active or persisted "
                               f"delivery for run {run_id}")
        from signalos_lib.product.gate_orchestrator import resume_delivery
        try:
            # One-shot resume: adapter=None is safe because reopen_gate never
            # invokes the model; the orchestrator is NOT registered in
            # _ACTIVE_DELIVERIES (a later agent:resume rebuilds it properly
            # with a real adapter from the persisted state).
            orch = resume_delivery(repo_root, run_id, None, _agent_emit(run_id),
                                   sign_fn=_DELIVERY_SIGN_FN)
        except Exception as exc:
            return err(req_id, f"agent:reopen-gate: delivery {run_id} is not "
                               f"resumable: {type(exc).__name__}: {exc}")
    try:
        result = orch.reopen_gate(gate, reason, name=name, role=role)
    except Exception as exc:  # INV-4: surface
        return err(req_id, f"agent:reopen-gate failed: {type(exc).__name__}: {exc}")
    return ok(req_id, output=json.dumps(result), data=result)


def agent_cancel(req_id: str, args: Any, project_id: str = "default") -> dict:
    """agent:cancel -> request cancellation of an in-flight run.

    Sets the in-memory cancel flag and writes a durable marker so a restarted
    sidecar or resumed loop still honors the user's cancel request."""
    try:
        payload = _coerce_agent_args(args)
    except (TypeError, ValueError) as exc:
        return err(req_id, f"agent:cancel args invalid: {exc}")
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return err(req_id, "agent:cancel requires 'run_id'")
    repo_root = Path(os.getcwd())
    _AGENT_CANCEL_FLAGS[run_id] = True
    try:
        _write_agent_cancel_marker(repo_root, run_id)
    except OSError as exc:
        return err(req_id, f"agent:cancel failed to persist marker: {exc}")
    return ok(req_id, output=f"cancellation requested for {run_id}",
              data={"run_id": run_id, "cancel_requested": True})


def agent_resume(req_id: str, args: Any, project_id: str = "default") -> dict:
    """agent:resume -> resume a persisted AgentLoop or gate delivery.

    Plain agent runs continue from conversation.jsonl/state.json. Governed
    deliveries restore delivery.json and re-emit the current gate checkpoint
    so the user can approve/rework without losing the gate walk.
    """
    try:
        payload = _coerce_agent_args(args)
    except (TypeError, ValueError) as exc:
        return err(req_id, f"agent:resume args invalid: {exc}")
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return err(req_id, "agent:resume requires 'run_id'")

    repo_root = Path(os.getcwd())
    run_dir = _agent_run_dir(repo_root, run_id)
    state_path = run_dir / "state.json"
    delivery_path = run_dir / "delivery.json"
    if not state_path.is_file() and not delivery_path.is_file():
        return err(req_id, f"no persisted state for run {run_id}")

    try:
        provider, model = _agent_provider_and_model(payload, "agent:resume")
    except ValueError as exc:
        _agent_emit(run_id)({"type": "error", "error": str(exc)})
        return err(req_id, str(exc))
    try:
        adapter = _build_agent_adapter(model, provider)
    except Exception as exc:  # INV-4: surface, do not swallow
        _agent_emit(run_id)({"type": "error", "error": f"provider init failed: {exc}"})
        return err(req_id, f"agent:resume provider init failed: {type(exc).__name__}: {exc}")

    enforcement = _build_agent_enforcement()

    if delivery_path.is_file():
        from signalos_lib.product.gate_orchestrator import (
            GATE_QUESTIONS,
            GATE_SPECIALISTS,
            resume_delivery,
        )
        try:
            orch = resume_delivery(
                repo_root,
                run_id,
                adapter,
                _agent_emit(run_id),
                enforcement_provider=enforcement,
                sign_fn=_DELIVERY_SIGN_FN,
            )
            gate = (
                orch.state.current_gate
                if orch.state.current_gate in GATE_SPECIALISTS
                else "G0"
            )
            if orch.state.status == "complete":
                ready = len(getattr(orch.state, "waived", [])) == 0
                orch.emit({
                    "type": "delivery_complete",
                    "run_id": orch.state.run_id,
                    "ready": ready,
                    "waived": list(getattr(orch.state, "waived", [])),
                })
                data = {
                    "run_id": orch.state.run_id,
                    "status": "complete",
                    "ready": ready,
                    "waived": list(getattr(orch.state, "waived", [])),
                    "resumed": True,
                }
                _ACTIVE_DELIVERIES.pop(run_id, None)
            elif orch.state.status == "stopped":
                orch.emit({
                    "type": "error",
                    "error": f"Delivery {orch.state.run_id} was stopped at {gate}.",
                })
                data = {
                    "run_id": orch.state.run_id,
                    "gate": gate,
                    "status": "stopped",
                    "resumed": True,
                }
                _ACTIVE_DELIVERIES.pop(run_id, None)
            else:
                _ACTIVE_DELIVERIES[run_id] = orch
                orch.state.current_gate = gate
                orch.state.status = "awaiting-verdict"
                orch.emit({
                    "type": "gate",
                    "gate": gate,
                    "title": f"{GATE_SPECIALISTS[gate]} - {gate}",
                    "question": GATE_QUESTIONS[gate],
                    "specialist": GATE_SPECIALISTS[gate],
                })
                orch._persist()
                data = {
                    "run_id": orch.state.run_id,
                    "gate": gate,
                    "status": orch.state.status,
                    "resumed": True,
                }
        except Exception as exc:
            _agent_emit(run_id)({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
            return err(req_id, f"agent:resume delivery failed: {type(exc).__name__}: {exc}")
        return ok(req_id, output=json.dumps(data), data=data)

    from signalos_lib.product.agent_loop import AgentLoop
    loop = AgentLoop(
        adapter=adapter,
        repo_root=repo_root,
        enforcement_provider=enforcement,
        run_id=run_id,
        emit=_agent_emit(run_id),
        cancel_check=lambda rid=run_id, root=repo_root: _agent_cancel_requested(root, rid),
        execution_context="conversation",
    )
    try:
        result = loop.resume()
    except Exception as exc:  # INV-4: surface as agent-event AND non-ok response
        loop._emit({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        return err(req_id, f"agent:resume failed: {type(exc).__name__}: {exc}")
    finally:
        _AGENT_CANCEL_FLAGS.pop(run_id, None)

    summary = {
        "run_id": result.run_id,
        "resumed": True,
        "status": result.status,
        "tool_calls_made": result.tool_calls_made,
    }
    if result.status == "error":
        return {
            "id": req_id,
            "ok": False,
            "error": redact_text(result.error or "agent resume failed"),
            "data": redact_response(summary),
        }
    return ok(req_id, output=json.dumps(summary), data=summary)


# ---------------------------------------------------------------------------
# Capability wiring: audit replay / share export / brownfield / competitor
# ---------------------------------------------------------------------------


def _parse_object_arg(args: list, command: str) -> dict:
    """Parse an optional single JSON-object argument for a route() command.

    Empty args -> {}. A one-element list holding a JSON string (the legacy
    transport) or an already-parsed dict both normalize to a dict; anything
    else raises ValueError."""
    if not args:
        return {}
    raw = args[0]
    if isinstance(raw, dict):
        return raw
    raw = str(raw).strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{command} payload must be a JSON object")
    return payload


# ---------------------------------------------------------------------------
# Multi-project registry (Task #19 — WAVE-ENGINE-DESIGN §3.2)
# ---------------------------------------------------------------------------


def _active_delivery_refusal(req_id: str) -> dict | None:
    """Return the delivery-active refusal, or None when it is safe to
    change the active project.

    Switching the active project retargets where subsequent state reads
    and writes land; doing that under a running gate walk would split a
    delivery's state across two namespaces. _ACTIVE_DELIVERIES holds the
    orchestrators of in-flight deliveries (agent:verdict / agent:cancel
    pop them when the walk ends)."""
    if _ACTIVE_DELIVERIES:
        return ok(req_id, data={
            "status": "delivery-active",
            "error": (
                "A delivery is still running. Finish or cancel it before "
                "switching projects."
            ),
            "runs": sorted(_ACTIVE_DELIVERIES),
        })
    return None


def project_list(req_id: str) -> dict:
    """project:list -> the registry (implicit default-only when absent)."""
    from signalos_lib.projects import list_projects

    reg = list_projects(Path(os.getcwd()))
    return ok(req_id, data={
        "status": "ok",
        "active": reg["active"],
        "projects": reg["projects"],
    })


def project_create(req_id: str, args: list) -> dict:
    """project:create {"name": ...} -> register + switch to the new project.

    Domain failures the founder can act on (empty/reserved name, running
    delivery) are reported as data.status, matching the capability-wiring
    contract used by audit:replay-timeline and friends."""
    try:
        payload = _parse_object_arg(args, "project:create")
    except (TypeError, ValueError) as exc:
        return err(req_id, f"project:create args invalid: {exc}")
    name = str(payload.get("name") or "").strip()
    if not name:
        return err(req_id, 'project:create requires {"name": ...}')

    refusal = _active_delivery_refusal(req_id)
    if refusal is not None:
        return refusal

    from signalos_lib.projects import create_project

    try:
        project = create_project(Path(os.getcwd()), name)
    except ValueError as exc:
        return ok(req_id, data={"status": "error", "error": str(exc)})
    return ok(req_id, data={
        "status": "ok",
        "project": project,
        "active": project["id"],
    })


def project_switch(req_id: str, args: list) -> dict:
    """project:switch {"project_id": ...} -> set the active project."""
    try:
        payload = _parse_object_arg(args, "project:switch")
    except (TypeError, ValueError) as exc:
        return err(req_id, f"project:switch args invalid: {exc}")
    target = str(payload.get("project_id") or "").strip()
    if not target:
        return err(req_id, 'project:switch requires {"project_id": ...}')

    refusal = _active_delivery_refusal(req_id)
    if refusal is not None:
        return refusal

    from signalos_lib.projects import set_active_project

    try:
        active = set_active_project(Path(os.getcwd()), target)
    except ValueError as exc:
        return ok(req_id, data={"status": "error", "error": str(exc)})
    return ok(req_id, data={"status": "ok", "active": active})


# Hard cap on the number of replay frames returned over IPC (the response is
# a single stdout line; an unbounded trail would be a payload bomb).
_REPLAY_TIMELINE_CAP = 1000


def audit_replay_timeline(req_id: str, args: list) -> dict:
    """audit:replay-timeline -> read-only pass-through over audit_replay.

    Args: {} or {"limit": int} (a bare integer argument is accepted too).
    Returns the LAST ``limit`` frames (default: all), hard-capped at
    ``_REPLAY_TIMELINE_CAP``; ``truncated`` reports whether frames were
    dropped by the limit or the cap. Never writes."""
    limit: int | None = None
    if args and str(args[0]).strip():
        raw = str(args[0]).strip()
        try:
            parsed: Any = json.loads(raw)
        except (TypeError, ValueError):
            parsed = raw
        if isinstance(parsed, dict):
            parsed = parsed.get("limit")
        if parsed is not None:
            try:
                limit = int(parsed)
            except (TypeError, ValueError):
                return err(req_id, "audit:replay-timeline 'limit' must be an integer")
            if limit < 0:
                return err(req_id, "audit:replay-timeline 'limit' must be >= 0")

    from signalos_lib.audit_replay import build_timeline

    frames = build_timeline(os.getcwd())
    total = len(frames)
    effective = _REPLAY_TIMELINE_CAP if limit is None else min(limit, _REPLAY_TIMELINE_CAP)
    out_frames = frames[-effective:] if effective > 0 else []
    return ok(req_id, data={
        "status": "ok",
        "frames": out_frames,
        "truncated": total > len(out_frames),
    })


def share_export(req_id: str) -> dict:
    """share:export -> write the read-only share bundle (share.html + share.json).

    The bundle is redacted by construction: collect_share_data reads ONLY
    .signalos governance artifacts (profile.json, the audit-trail timeline
    summaries, closeout level) -- never product source, .env* files, or vault
    material -- and the IPC envelope additionally passes through
    redact_response. Failure is reported as data.status == "error"."""
    root = Path(os.getcwd())
    try:
        from signalos_lib.product.share_export import write_share_bundle

        rel_paths = write_share_bundle(root)
    except Exception as exc:
        return ok(req_id, data={
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        })
    bundle_dir = (root / ".signalos" / "share").resolve()
    files = sorted({Path(p).name for p in rel_paths.values()})
    return ok(req_id, data={
        "status": "ok",
        "path": str(bundle_dir),
        "files": files,
    })


def brownfield_audit(req_id: str, args: list) -> dict:
    """brownfield:audit -> audit an existing repo; optionally apply governance.

    Args: {} or {"apply": bool}. Always runs the deterministic
    audit_existing_repo; apply_governance (scaffold + baseline + audit-trail
    record) only when apply is true. A failed apply keeps the report and
    reports data.status == "error" honestly."""
    try:
        payload = _parse_object_arg(args, "brownfield:audit")
    except (TypeError, ValueError) as exc:
        return err(req_id, f"brownfield:audit args invalid: {exc}")
    apply_requested = bool(payload.get("apply"))
    root = Path(os.getcwd())

    from signalos_lib.product.brownfield import apply_governance, audit_existing_repo

    try:
        report = audit_existing_repo(root)
    except Exception as exc:
        return ok(req_id, data={
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "applied": False,
        })

    data: dict[str, Any] = {"status": "ok", "report": report, "applied": False}
    if apply_requested:
        try:
            applied = apply_governance(root)
            data["applied"] = True
            data["report"] = applied.get("audit", report)
            data["governance"] = {
                "created": applied.get("created", []),
                "baseline_path": applied.get("baseline_path"),
            }
        except Exception as exc:
            data["status"] = "error"
            data["error"] = f"apply_governance failed: {type(exc).__name__}: {exc}"
    return ok(req_id, data=data)


# Test seam: (url, timeout=...) -> html | None. Production uses the polite
# stdlib fetch_page helper from competitor.py (UA header, per-URL timeout,
# never raises).
_COMPETITOR_FETCH_FN = None
_COMPETITOR_FETCH_TIMEOUT = 10.0


def competitor_analyze(req_id: str, args: list) -> dict:
    """competitor:analyze -> Competitive UX Matrix from competitor URLs.

    LLM-gated: without a configured provider returns
    {"status": "llm-unavailable"} without fetching anything. Per-URL fetch
    failures are collected into data.errors without failing the call. The
    matrix is persisted to .signalos/product/COMPETITORS.json where the
    design phase picks it up as competitive context."""
    try:
        payload = _parse_object_arg(args, "competitor:analyze")
    except (TypeError, ValueError) as exc:
        return err(req_id, f"competitor:analyze args invalid: {exc}")
    urls = payload.get("urls")
    if (
        not isinstance(urls, list)
        or not urls
        or not all(isinstance(u, str) and u.strip() for u in urls)
    ):
        return err(
            req_id,
            "competitor:analyze requires 'urls': a non-empty array of URL strings",
        )
    urls = [u.strip() for u in urls]

    root = Path(os.getcwd())
    from signalos_lib.product import competitor as competitor_mod
    from signalos_lib.product.llm_provider import is_llm_available

    if not is_llm_available(root):
        return ok(req_id, data={"status": "llm-unavailable"})

    fetch = _COMPETITOR_FETCH_FN or competitor_mod.fetch_page
    pages: list[dict] = []
    errors: list[dict] = []
    for url in urls:
        try:
            html = fetch(url, timeout=_COMPETITOR_FETCH_TIMEOUT)
        except Exception as exc:  # fetch_page never raises; a seam might
            errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if html is None:
            errors.append({
                "url": url,
                "error": "fetch failed (unreachable, timed out, or not http/https)",
            })
        else:
            pages.append({"url": url, "html": html})

    try:
        matrix = competitor_mod.build_matrix(pages, root=root, use_llm=True)
    except Exception as exc:
        return ok(req_id, data={
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "errors": errors,
        })

    record = {
        "schema_version": "signalos.competitors.v1",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "urls": urls,
        **matrix,
    }
    out_path = root / ".signalos" / "product" / "COMPETITORS.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return ok(req_id, data={
            "status": "error",
            "error": f"failed to persist COMPETITORS.json: {exc}",
            "matrix": matrix,
            "errors": errors,
        })

    return ok(req_id, data={
        "status": "ok",
        "matrix": matrix,
        "errors": errors,
        "path": str(Path(".signalos") / "product" / "COMPETITORS.json"),
    })


def voice_transcribe(req_id: str, args: list) -> dict:
    """voice:transcribe {"audio_b64": ..., "mime": ...} -> transcript text.

    Thin pass-through to signalos_lib.voice_transcribe.transcribe. Domain
    outcomes (no-capable-provider / too-large / invalid-audio /
    provider-error) come back as data.status per the capability-wiring
    contract; only a malformed request envelope is a transport error.
    The audio payload is never logged, persisted, or echoed back."""
    try:
        payload = _parse_object_arg(args, "voice:transcribe")
    except (TypeError, ValueError) as exc:
        return err(req_id, f"voice:transcribe args invalid: {exc}")
    audio_b64 = payload.get("audio_b64")
    if not isinstance(audio_b64, str) or not audio_b64.strip():
        return err(req_id, 'voice:transcribe requires {"audio_b64": ...}')
    mime = str(payload.get("mime") or "audio/webm")

    from signalos_lib.voice_transcribe import transcribe

    return ok(req_id, data=transcribe(audio_b64, mime))


# ---------------------------------------------------------------------------
# Brownfield auto-detect for agent:deliver
# ---------------------------------------------------------------------------

_BROWNFIELD_SOURCE_EXTS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".rb",
    ".cs", ".php", ".swift", ".kt", ".vue", ".svelte",
})
_BROWNFIELD_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "target", ".venv", "venv",
    "__pycache__", ".signalos",
})
# Governance markers under .signalos/ that mean the workspace is already
# governed (or was already noticed): profile/product state, an applied
# baseline, gate state, or ANY audit trail (the notice itself appends one,
# so a brownfield workspace is only notified once).
_BROWNFIELD_GOVERNANCE_MARKERS = (
    "profile.json",
    "GOVERNANCE_BASELINE.md",
    "product",
    "gates",
    "AUDIT_TRAIL.jsonl",
)


def _has_governance_state(repo_root: Path) -> bool:
    signalos = repo_root / ".signalos"
    if not signalos.is_dir():
        return False
    return any((signalos / marker).exists() for marker in _BROWNFIELD_GOVERNANCE_MARKERS)


def _has_preexisting_code(repo_root: Path, scan_limit: int = 2000) -> bool:
    """Conservative: True only when a real source file exists outside
    vendored/build dirs. Bounded walk; any OS error -> False (silence)."""
    seen = 0
    try:
        for path in repo_root.rglob("*"):
            if seen >= scan_limit:
                return False
            try:
                rel_parts = path.relative_to(repo_root).parts
            except ValueError:
                continue
            if any(part in _BROWNFIELD_SKIP_DIRS for part in rel_parts):
                continue
            if path.is_file():
                seen += 1
                if path.suffix.lower() in _BROWNFIELD_SOURCE_EXTS:
                    return True
    except OSError:
        return False
    return False


def _maybe_emit_brownfield_notice(repo_root: Path, emit) -> None:
    """agent:deliver pre-flight: pre-existing product code + no .signalos
    governance state -> run the deterministic brownfield audit and surface a
    system agent-event plus an audit-trail entry so the founder sees
    "existing code detected, N findings" in chat. NEVER raises and NEVER
    blocks delivery -- errors are recorded honestly and delivery continues."""
    try:
        if _has_governance_state(repo_root) or not _has_preexisting_code(repo_root):
            return
        from signalos_lib.product.brownfield import audit_existing_repo

        report = audit_existing_repo(repo_root)
        summary = report.get("summary", {}) or {}
        total = summary.get("total", 0)
        message = (
            f"Existing code detected in this workspace - brownfield audit found "
            f"{total} governance finding(s) (high {summary.get('high', 0)}, "
            f"medium {summary.get('medium', 0)}, low {summary.get('low', 0)}). "
            f"Delivery continues; run brownfield:audit with apply=true to "
            f"record a governance baseline."
        )
        emit({"type": "system", "message": message, "brownfield": summary})
        _append_audit(str(repo_root), {
            "action": "brownfield.audit-detected",
            "findings": total,
            "high": summary.get("high", 0),
            "medium": summary.get("medium", 0),
            "low": summary.get("low", 0),
        })
    except Exception as exc:
        try:
            _append_audit(str(repo_root), {
                "action": "brownfield.audit-error",
                "error": f"{type(exc).__name__}: {exc}",
            })
        except Exception:
            pass


def terminal_help_text() -> str:
    return "\n".join([
        "Supported commands:",
        "  help              show this list",
        "  signalos status   show governance/workspace status",
        "  signalos check    run release-readiness checks",
        "  signalos gates    show gate status",
        "  signalos cost     summarize AI usage and budget evidence",
        "  signalos test     run the test automation umbrella",
        "  signalos bundle   inspect embedded SignalOS bundle files",
        "  signalos trace    trace governance tickets to evidence",
        "  npm run dev       start the Preview tab dev server",
        "  git status        show branch and working tree status",
        "  clear             clear terminal output",
        "",
        "This terminal is a governed SignalOS command surface, not an unrestricted OS shell.",
    ])


def parse_signalos_alias(command: str) -> list[str] | None:
    try:
        lexer = shlex.shlex(command.strip(), posix=False)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = [
            token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'} else token
            for token in lexer
        ]
    except ValueError:
        return None
    if len(tokens) < 2 or tokens[0].lower() != "signalos":
        return None
    return [tokens[1].lower(), *tokens[2:]]


@lru_cache(maxsize=1)
def core_cli_command_names() -> frozenset[str]:
    choices: dict[str, Any] = {}
    from signalos_lib.cli import _build_parser

    parser = _build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)
    if not choices:
        raise RuntimeError("SignalOS Core CLI command registry is empty.")
    return frozenset(str(name) for name in choices)


def is_core_cli_command(command: str) -> bool:
    return command in core_cli_command_names()


SPECIAL_DISPATCH_CLI_COMMANDS = frozenset({
    "signal-checkpoint",
    "signal-rollback",
    "signal-sandbox",
    "signal-init",
})


def is_dispatchable_cli_command(command: str, args: list[str], cwd: str) -> bool:
    normalized = command.lstrip("/")
    return (
        normalized in SPECIAL_DISPATCH_CLI_COMMANDS
        or is_core_cli_command(normalized)
        or map_slash_command(normalized, args, cwd) is not None
    )


def dispatch_cli_response(
    req_id: str,
    command: str,
    args: list[str],
    *,
    project_id: str = "default",
) -> dict:
    try:
        return ok(req_id, output=dispatch_cli(command, args, req_id, project_id=project_id))
    except Exception as exc:
        return err(req_id, str(exc))


def git_status_text(repo_root: Path) -> str:
    if not (repo_root / ".git").exists():
        return "No git repository is active for this workspace."

    def _git(*argv: str) -> str:
        proc = subprocess.run(
            ["git", *argv],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout or "").strip()
        return (proc.stdout or "").strip()

    branch = _git("branch", "--show-current") or "(detached)"
    short = _git("status", "--short")
    return "\n".join([
        f"branch: {branch}",
        "clean: yes" if not short else "clean: no",
        short or "working tree clean",
    ])


# CLI subcommands that accept --project-id (signalos_lib/commands/status.py
# and commands/orchestrate.py). dispatch_cli appends the resolved project so
# the wave state these commands read/report lands in the right namespace.
_PROJECT_AWARE_CLI_SUBCOMMANDS = {"status", "orchestrate"}


def dispatch_cli(command: str, args: list[str], req_id: str = "", project_id: str = "default") -> str:
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
        # Task #19: thread the resolved project namespace to project-aware
        # subcommands. An explicit --project-id in the user's args wins.
        if argv[0] in _PROJECT_AWARE_CLI_SUBCOMMANDS and "--project-id" not in argv:
            argv = [*argv, "--project-id", project_id]
        # Wave 2 / G1-7: emit phase substeps around wired commands so users
        # see real progress instead of a generic "Engine working" toast.
        emitter = ProgressEmitter(req_id) if req_id else None
        if emitter and command == "signal-init":
            emitter.begin("prepare", "check_target", f"Checking {cwd}")
            emitter.done()
            emitter.begin("prepare", "consent_mode", "Init mode chosen")
            emitter.done()
            emitter.begin("write", "copy_bundle", "Copying SignalOS files")
        rc, out, err_text = run_core_cli(argv, req_id=req_id)
        text = redact_text((out or err_text).strip())
        if emitter and command == "signal-init":
            if rc == 0:
                emitter.done(f"{text.splitlines()[0] if text else 'Bundle copied'}")
                emitter.begin("review", "ide_hooks", "Wiring IDE hooks")
                emitter.done("Setup complete")
            else:
                emitter.error(text or f"init exited {rc}")
        if rc != 0:
            raise RuntimeError(text or f"Command failed with exit code {rc}.")
        if text:
            return text
        return f"Command completed with exit code {rc}."

    raise ValueError(f"Unknown SignalOS CLI command: /{command}")


def map_slash_command(command: str, args: list[str], cwd: str) -> list[str] | None:
    cleaned_args = strip_context_arg(args)

    if command == "signal-status":
        return ["status", "--repo-root", cwd]

    if command == "signal-release-readiness":
        return ["release-readiness", "--repo-root", cwd, *cleaned_args]

    alias_map = {
        "signal-pause": ["pause"],
        "signalos-session": ["session"],
        "harness-call": ["harness", "call"],
        "signalos-install": ["install"],
        "signalos-publish": ["publish"],
        "signalos-verify": ["verify"],
        "context-expand": ["context", "expand"],
        "signalos-orchestrate": ["orchestrate"],
        "signalos-status": ["status", "--repo-root", cwd],
        "plan-schema": ["plan"],
        "validate-cmd": ["validate"],
        "signal-pre-design": ["pre-design"],
        "signal-design": ["design"],
        "signal-design-review": ["design-review"],
        "signal-design-html": ["design-html"],
        "signalos-brain": ["brain"],
        "signal-observe": ["observe"],
        "signal-ship": ["ship"],
    }
    if command in alias_map:
        return [*alias_map[command], *cleaned_args]

    if command == "signal-init":
        # Init modes - Wave 1 / G0-1. The wizard (G0-2) is the user-facing
        # picker; this function only knows how to translate the chosen mode
        # into the right argv. Default mode is "keep" - non-destructive.
        # See docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md section11.4b.
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
        # "keep" (default): merge - write bundle files only where the user
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
        return ["plan", *cleaned_args]

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
        *core_cli_command_names(),
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
        "signal-post-retro",
        "signal-careful",
        "signal-freeze",
        "signal-guard",
        "signal-unfreeze",
        "signal-second-opinion",
        "signal-second-opinion-record",
        "signal-investigate",
        "signal-velocity",
        "deliver",
        "deliver-intent",
        "deliver-design",
        "deliver-design-preview",
    }
    if command in direct:
        return [command, *cleaned_args]

    return None


def strip_context_arg(args: list[str]) -> list[str]:
    if args and args[0].startswith("[SignalOS] "):
        return args[1:]
    return args


def run_core_cli(argv: list[str], req_id: str = "") -> tuple[int, str, str]:
    try:
        from signalos_lib.cli import main as core_main
    except ImportError as exc:
        return (
            127,
            "",
            "SignalOS Core is not bundled in this installer. "
            f"Rebuild the sidecar with scripts/bundle-sidecar.ps1. ({exc})",
        )

    previous_progress_req = os.environ.get("SIGNALOS_PROGRESS_REQ_ID")
    if req_id:
        os.environ["SIGNALOS_PROGRESS_REQ_ID"] = req_id

    out = StringIO()
    err_buf = StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err_buf):
            try:
                rc = core_main(["signalos", *argv])
            except SystemExit as exc:
                rc = int(exc.code or 0) if isinstance(exc.code, int) else 1
    finally:
        if previous_progress_req is None:
            os.environ.pop("SIGNALOS_PROGRESS_REQ_ID", None)
        else:
            os.environ["SIGNALOS_PROGRESS_REQ_ID"] = previous_progress_req
    return rc, out.getvalue(), err_buf.getvalue()


def get_wave_state(project_id: str = "default") -> dict:
    status = get_status_json(project_id=project_id)
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


def get_gate_states(project_id: str = "default") -> list[dict]:
    status = get_status_json(project_id=project_id)
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


def sign_gate(gate_id: int, signer: str, role: str | None = None) -> dict:
    # #17 Edit 3.3: use the REAL role, never a hardcoded "PO". Rust forwards the
    # identity role as args[2]; if it is somehow absent, fall back to the
    # workspace identity's role rather than defaulting to PO (which would let a
    # non-PO caller sign PO gates). This closes the "always signs as PO" bypass.
    if not role:
        from signalos_lib.product.identity import load_identity

        identity = load_identity(Path(os.getcwd()))
        role = str((identity or {}).get("role") or "").strip()
    if not role:
        raise RuntimeError(
            "Cannot sign: no role provided and no workspace identity is set. "
            "Set your role in Settings and retry."
        )
    rc, out, err_text = run_core_cli(
        [
            "sign",
            f"G{gate_id}",
            "--signer",
            signer,
            "--role",
            role,
            "--verdict",
            "APPROVED",
            "--repo-root",
            os.getcwd(),
        ]
    )
    if rc != 0:
        raise RuntimeError((err_text or out or f"sign exited {rc}").strip())
    return {"gate_id": gate_id, "signer": signer, "role": role, "ok": True, "output": out}


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
            "error": f"restoring checkpoint {sha[:8]} failed: {err_text.strip()}",
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

def handle_sandbox(args, cwd):
    """UI bridge for the sandboxed-execution settings + capability probe."""
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
        patches = {"enabled": True}
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


def audit_list(limit):
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


def _build_engine(project_id="default"):
    from pathlib import Path as _Path
    from signalos_lib.wave_engine import WaveEngine
    from signalos_lib.wave_engine_judge import build_llm_judge, llm_judge_enabled

    judge = build_llm_judge() if llm_judge_enabled() else None
    return WaveEngine(
        _Path(os.getcwd()).resolve(),
        project_id=project_id,
        llm_judge=judge,
    )


def _serialize_engine_result(result):
    def _walk(obj):
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(v) for v in obj]
        if hasattr(obj, "__fspath__"):
            return str(obj)
        return obj
    return _walk(result)


def wave_begin(user_request, project_id="default"):
    eng = _build_engine(project_id)
    result = eng.begin(user_request)
    eng.persist()
    return _serialize_engine_result(result)


def wave_reply(user_reply, current_gate, project_id="default"):
    from signalos_lib.wave_engine import WaveState as _WaveState

    eng = _build_engine(project_id)
    if eng.state is _WaveState.ENTRY:
        try:
            eng.resume_at_dispatch(current_gate)
        except (RuntimeError, ValueError) as exc:
            return {"action": "error", "error": f"resume_at_dispatch: {exc}"}
    else:
        eng.current_gate = current_gate
    result = eng.handle_user_reply(user_reply)
    eng.persist()
    return _serialize_engine_result(result)


def wave_scope_drift_resolve(user_request, choice, project_id="default"):
    eng = _build_engine(project_id)
    begin_result = eng.begin(user_request)
    from signalos_lib.wave_engine import WaveState as _WaveState
    if eng.state is not _WaveState.SCOPE_DRIFT:
        return {
            "action": "no-longer-drifted",
            "begin_result": _serialize_engine_result(begin_result),
        }
    try:
        result = eng.resolve_scope_drift(choice)
    except ValueError as exc:
        return {"action": "error", "error": str(exc)}
    return _serialize_engine_result(result)


def wave_translate_external(artifact, gate=None, project_id="default"):
    eng = _build_engine(project_id)
    return _serialize_engine_result(eng.translate_external(artifact, gate=gate))


def wave_violation_request(payload, project_id="default"):
    eng = _build_engine(project_id)
    kind = str(payload.get("violation_kind") or "").strip()
    if not kind:
        return {"action": "error", "error": "missing violation_kind"}
    findings = payload.get("findings") or []
    gate = payload.get("gate")
    return _serialize_engine_result(eng.request_violation_confirmation(
        violation_kind=kind, findings=findings, gate=gate,
    ))


def wave_violation_confirm(payload, project_id="default"):
    eng = _build_engine(project_id)
    kind = str(payload.get("violation_kind") or "").strip()
    choice = str(payload.get("choice") or "").strip()
    user_reply = str(payload.get("user_reply") or "")
    findings = payload.get("findings") or []
    gate = payload.get("gate")
    if gate:
        eng.current_gate = gate
    if not kind or not choice:
        return {"action": "error", "error": "missing violation_kind or choice"}
    try:
        result = eng.confirm_violation(
            violation_kind=kind, choice=choice,
            user_reply=user_reply, findings=findings,
        )
    except ValueError as exc:
        return {"action": "error", "error": str(exc)}

    audit_entry = result.get("audit_entry") or {}
    if audit_entry:
        _append_audit(os.getcwd(), audit_entry)
    return _serialize_engine_result(result)


def wave_g5_handoff(wave_id, summary=None, project_id="default"):
    eng = _build_engine(project_id)
    return _serialize_engine_result(eng.run_g5_handoff(
        wave_id=wave_id, summary=summary or {},
    ))


def get_status_json(project_id="default"):
    fallback = {
        "wave_id": "-",
        "phase": "ONBOARDING",
        "gates": {f"G{i}": False for i in range(6)},
        "project_id": project_id,
    }
    rc, out, _ = run_core_cli([
        "status", "--repo-root", os.getcwd(), "--json",
        "--project-id", project_id,
    ])
    if rc != 0 or not out.strip():
        return fallback
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return fallback


def normalize_brain_entry(entry):
    return {
        "id": entry.get("id", ""),
        "text": redact_text(entry.get("content") or entry.get("text") or ""),
        "type": entry.get("type") or "note",
        "ts": entry.get("created_at") or entry.get("ts") or "",
        "wave": entry.get("wave") or "",
        "gate": entry.get("gate") or "",
        "source": entry.get("source") or "",
    }


def ok(req_id, output=None, data=None):
    return {
        "id": req_id,
        "ok": True,
        "output": redact_text(output) if output is not None else None,
        "data": redact_response(data),
    }


def err(req_id, message):
    return {"id": req_id, "ok": False, "error": redact_text(message)}


def _try_intercept_cancel(line: str) -> bool:
    """Fast-path an agent:cancel request on the stdin-reader thread.

    Claim 11: the worker thread runs handle(req) to completion before the next
    stdin line is read, so an agent:cancel sent DURING a long agent:deliver
    would sit unread until the delivery finished. Cancellation is cooperative
    (agent_cancel sets an in-memory flag + writes a marker; the AgentLoop polls
    it between tool calls), so honoring the flag promptly is enough to stop an
    in-flight delivery.

    This runs on the reader thread and handles ONLY agent:cancel \u2014 every other
    line (and anything unparseable) returns False and is queued for the worker
    thread, preserving the existing in-order response contract. The in-memory
    _AGENT_CANCEL_FLAGS entry is process-global and cwd-independent, so setting
    it here (without the worker's per-request chdir) still cancels the delivery.
    """
    try:
        req = json.loads(line)
    except (TypeError, ValueError):
        return False
    if not isinstance(req, dict) or req.get("command") != "agent:cancel":
        return False
    req_id = req.get("id", "unknown")
    try:
        resp = agent_cancel(req_id, req.get("args", []))
    except Exception as exc:  # never let the reader thread die on a cancel
        resp = err(req_id, f"agent:cancel failed: {type(exc).__name__}: {exc}")
    _emit_line(resp)
    return True


def main() -> None:
    _emit_line({"id": "init", "ok": True, "data": {"ready": True}})

    # The worker (main) thread processes requests serially from this queue,
    # preserving response ordering. The reader thread below feeds it every line
    # except agent:cancel, which it handles inline so a cancel is honored while
    # the worker is blocked inside a long-running handle().
    request_queue: "queue.Queue[str | None]" = queue.Queue()

    def _stdin_reader() -> None:
        try:
            for raw_line in sys.stdin:
                line = raw_line.replace("\x00", "").lstrip("\ufeff").strip()
                if not line:
                    continue
                if _try_intercept_cancel(line):
                    continue
                request_queue.put(line)
        finally:
            # Sentinel: unblock the worker so it exits when stdin closes.
            request_queue.put(None)

    reader = threading.Thread(
        target=_stdin_reader, name="signalos-stdin-reader", daemon=True
    )
    reader.start()

    while True:
        line = request_queue.get()
        if line is None:
            break

        try:
            req = json.loads(line)
            resp = handle(req)
        except json.JSONDecodeError as exc:
            resp = err("parse-error", f"Invalid JSON: {exc}")
        except Exception as exc:
            resp = err("runtime-error", f"Unhandled exception: {exc}")

        _emit_line(resp)


if __name__ == "__main__":
    main()
